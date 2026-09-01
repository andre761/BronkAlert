// BronqAlert — Netlify Function: consulta em tempo real o Oracle Autonomous
// Database (schema FIAP_OCI_GOLD) e devolve o JSON que o site consome.
//
// Credenciais em variáveis de ambiente do Netlify (nunca no código):
//   ORACLE_DB_USER, ORACLE_DB_PASSWORD, ORACLE_DSN, WALLET_DECRYPT_PASSPHRASE
//
// O wallet em si vem de secrets/wallet.enc (criptografado, comitado no repo)
// + scripts/prepare-wallet.js, que roda no BUILD do Netlify e descriptografa
// pra netlify/functions/oracle-data-wallet/ — empacotado junto da função via
// "included_files" no netlify.toml. Isso evita o limite de 4KB de variáveis
// de ambiente do AWS Lambda (o wallet nunca cabe ali).
//
// Se qualquer parte falhar (banco fora do ar, wallet não configurado, etc.),
// aquela parte específica cai pro valor simulado — o site nunca fica sem
// número nenhum por causa de uma view fora do ar.
const fs = require("fs");
const path = require("path");
const oracledb = require("oracledb");

const WALLET_DIR = path.join(__dirname, "oracle-data-wallet");

const FALLBACK = {
  monthly_casos: [118, 165, 245, 312, 288, 214],
  daily_casos: [22, 20, 19, 17, 16, 15, 14],
  weekly_casos: [112, 98, 84, 71],
  age_labels: ["Menor de 1 ano", "1 a 2 anos", "Maior de 2 anos"],
  age_values: [134, 41, 25],
  sp_zones: {
    centro: { name: "Centro", casos: 14 },
    norte: { name: "Zona Norte", casos: 23 },
    sul: { name: "Zona Sul", casos: 0 },
    leste: { name: "Zona Leste", casos: 19 },
    oeste: { name: "Zona Oeste", casos: 11 },
  },
};

// Bairro -> zona de São Paulo (aproximado; cobre os bairros mais comuns).
const BAIRRO_ZONA = {
  se: "centro", republica: "centro", "bela vista": "centro", consolacao: "centro",
  "santa cecilia": "centro", "campos eliseos": "centro", "bom retiro": "centro",
  liberdade: "centro", cambuci: "centro", higienopolis: "centro", "barra funda": "centro",
  santana: "norte", tucuruvi: "norte", "casa verde": "norte", "freguesia do o": "norte",
  "vila maria": "norte", "vila guilherme": "norte", jacana: "norte", mandaqui: "norte",
  tremembe: "norte", brasilandia: "norte", limao: "norte", "vila medeiros": "norte",
  "vila mariana": "sul", moema: "sul", "santo amaro": "sul", "campo belo": "sul",
  saude: "sul", ipiranga: "sul", jabaquara: "sul", "cidade ademar": "sul",
  "capela do socorro": "sul", grajau: "sul", interlagos: "sul", "vila andrade": "sul",
  "campo grande": "sul", americanopolis: "sul",
  tatuape: "leste", mooca: "leste", belenzinho: "leste", penha: "leste",
  itaquera: "leste", "sao miguel paulista": "leste", "vila matilde": "leste",
  "vila formosa": "leste", "vila prudente": "leste", sapopemba: "leste",
  "cidade tiradentes": "leste", guaianases: "leste", "ermelino matarazzo": "leste",
  "parque sao lucas": "leste", "sao mateus": "leste", aricanduva: "leste",
  pinheiros: "oeste", perdizes: "oeste", lapa: "oeste", butanta: "oeste",
  "vila madalena": "oeste", "alto de pinheiros": "oeste", jardins: "oeste",
  "vila romana": "oeste", "rio pequeno": "oeste", "raposo tavares": "oeste",
  jaguare: "oeste", morumbi: "oeste", "itaim bibi": "oeste", "vila leopoldina": "oeste",
};

const STATUS_ALERTA_MAP = { NORMAL: 0, ATENCAO: 1, MODERADO: 1, ALERTA: 3, ALTO: 3, CRITICO: 3 };

function stripAccents(s) {
  const str = (s || "").normalize("NFD");
  let out = "";
  for (let i = 0; i < str.length; i++) {
    const code = str.charCodeAt(i);
    if (code >= 0x0300 && code <= 0x036f) continue; // marcas diacríticas combinantes
    out += str[i];
  }
  return out;
}

// Se a rede do Netlify/AWS não conseguir alcançar o Oracle (ex.: bloqueada por
// uma Access Control List do Autonomous Database), a tentativa de conexão pode
// FICAR PENDURADA em vez de falhar rápido — e o Lambda mata a função inteira
// aos 30s (tela feia de "Invocation Failed"). Por isso corremos a conexão
// contra um cronômetro próprio de 10s: se estourar, desistimos a tempo de
// ainda devolver um JSON com os dados simulados de reserva.
function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(label + " excedeu " + ms + "ms")), ms)),
  ]);
}

async function getConnection() {
  if (!fs.existsSync(WALLET_DIR) || fs.readdirSync(WALLET_DIR).length === 0) {
    throw new Error("wallet não encontrado — configure secrets/wallet.enc e WALLET_DECRYPT_PASSPHRASE, e rode um novo deploy");
  }
  return withTimeout(
    oracledb.getConnection({
      user: process.env.ORACLE_DB_USER,
      password: process.env.ORACLE_DB_PASSWORD,
      connectString: process.env.ORACLE_DSN || "bronkalertdb_high",
      configDir: WALLET_DIR,
    }),
    10000,
    "conexão com o Oracle"
  );
}

async function fetchMonthly(connection) {
  const r = await connection.execute(
    `SELECT mes, qt_internacoes FROM vw_comparativo_diagnostico_mensal
      WHERE categoria_diagnostico = 'Bronquiolite (J21)'
      ORDER BY mes DESC FETCH FIRST 6 ROWS ONLY`
  );
  const rows = r.rows.reverse();
  if (!rows.length) throw new Error("sem linhas");
  return {
    labels: rows.map(([d]) => `${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`),
    values: rows.map(([, v]) => Number(v)),
  };
}

async function fetchDaily(connection) {
  const r = await connection.execute(
    `SELECT dia, qt_internacoes_bq FROM vw_casos_diarios ORDER BY dia DESC FETCH FIRST 7 ROWS ONLY`
  );
  const rows = r.rows.reverse();
  if (!rows.length) throw new Error("sem linhas");
  return rows.map(([, v]) => Number(v));
}

async function fetchWeekly(connection) {
  const r = await connection.execute(
    `SELECT semana, qt_internacoes_bronq FROM vw_casos_semanais ORDER BY semana DESC FETCH FIRST 4 ROWS ONLY`
  );
  const rows = r.rows.reverse();
  if (!rows.length) throw new Error("sem linhas");
  return rows.map(([, v]) => Number(v));
}

async function fetchAge(connection) {
  const r = await connection.execute(
    `SELECT faixa_etaria, SUM(qt_internacoes)
       FROM vw_perfil_paciente_mensal
      WHERE mes_ref = (
        SELECT mes_ref FROM vw_perfil_paciente_mensal
        ORDER BY TO_DATE(mes_ref,'MM/YYYY') DESC FETCH FIRST 1 ROW ONLY
      )
      GROUP BY faixa_etaria`
  );
  const byLabel = Object.fromEntries(r.rows.map(([label, total]) => [label, Number(total)]));
  const values = FALLBACK.age_labels.map((l) => byLabel[l] || 0);
  if (!values.some((v) => v > 0)) throw new Error("faixas zeradas");
  return { labels: FALLBACK.age_labels, values };
}

async function fetchAlert(connection) {
  const r = await connection.execute(
    `SELECT qt_internacoes, status_alerta FROM vw_alerta_risco_semanal
      ORDER BY semana DESC FETCH FIRST 1 ROW ONLY`
  );
  if (!r.rows.length) throw new Error("sem linhas");
  const [casos, status] = r.rows[0];
  return { status: String(status).trim().toUpperCase(), casos: Number(casos) };
}

async function fetchZones(connection) {
  const r = await connection.execute(
    `SELECT no_bairro, qt_internacoes, pct_bronquiolite FROM vw_hospital_desempenho WHERE qt_internacoes > 0`
  );
  const totals = Object.fromEntries(Object.keys(FALLBACK.sp_zones).map((k) => [k, 0]));
  let matched = 0;
  for (const [bairro, qt, pct] of r.rows) {
    const key = stripAccents(String(bairro || "").trim().toLowerCase());
    const zona = BAIRRO_ZONA[key];
    if (!zona || pct == null) continue;
    totals[zona] += Number(qt) * (Number(pct) / 100);
    matched++;
  }
  if (!matched) throw new Error("nenhum bairro reconhecido");
  return Object.fromEntries(
    Object.entries(totals).map(([k, v]) => [k, { name: FALLBACK.sp_zones[k].name, casos: Math.round(v) }])
  );
}

exports.handler = async function () {
  const headers = {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "public, max-age=300",
    "Access-Control-Allow-Origin": "*",
  };

  let connection;
  const now = new Date();
  const result = {
    generated_at: now.toISOString(),
    source: "dados simulados (Oracle indisponível)",
    monthly_labels: null,
    monthly_casos: FALLBACK.monthly_casos,
    daily_casos: FALLBACK.daily_casos,
    weekly_casos: FALLBACK.weekly_casos,
    age_data: { labels: FALLBACK.age_labels, values: FALLBACK.age_values },
    sp_zones: FALLBACK.sp_zones,
    alert_status: null,
    alert_casos: null,
    used_fallback: true,
  };

  try {
    connection = await getConnection();

    const outcomes = await withTimeout(
      Promise.allSettled([
        fetchMonthly(connection),
        fetchDaily(connection),
        fetchWeekly(connection),
        fetchAge(connection),
        fetchAlert(connection),
        fetchZones(connection),
      ]),
      15000,
      "consultas ao Oracle"
    );
    const [monthly, daily, weekly, age, alert, zones] = outcomes;

    if (monthly.status === "fulfilled") {
      result.monthly_labels = monthly.value.labels;
      result.monthly_casos = monthly.value.values;
      result.used_fallback = false;
      result.source = "Oracle Autonomous Database (FIAP_OCI_GOLD) — tempo real";
    } else {
      result.monthly_labels = [-5, -4, -3, -2, -1, 0].map((i) => {
        const d = new Date(now.getFullYear(), now.getMonth() + i, 1);
        return `${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;
      });
      console.warn("[oracle-data] evolução mensal falhou:", monthly.reason?.message);
    }
    if (daily.status === "fulfilled") result.daily_casos = daily.value;
    else console.warn("[oracle-data] diário falhou:", daily.reason?.message);

    if (weekly.status === "fulfilled") result.weekly_casos = weekly.value;
    else console.warn("[oracle-data] semanal falhou:", weekly.reason?.message);

    if (age.status === "fulfilled") result.age_data = age.value;
    else console.warn("[oracle-data] faixa etária falhou:", age.reason?.message);

    if (alert.status === "fulfilled") {
      result.alert_status = alert.value.status;
      result.alert_casos = alert.value.casos;
    } else console.warn("[oracle-data] alerta falhou:", alert.reason?.message);

    if (zones.status === "fulfilled") result.sp_zones = zones.value;
    else console.warn("[oracle-data] zonas falharam:", zones.reason?.message);
  } catch (e) {
    console.error("[oracle-data] conexão falhou, devolvendo tudo simulado:", e.message);
    result.source = "dados simulados (Oracle indisponível: " + e.message + ")";
  } finally {
    if (connection) {
      try {
        await connection.close();
      } catch (_) {}
    }
  }

  return { statusCode: 200, headers, body: JSON.stringify(result) };
};
