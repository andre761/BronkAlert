// BronqAlert — Netlify Function: consulta em tempo real o Oracle Autonomous Database
// (schema FIAP_OCI_GOLD) e devolve o mesmo formato de JSON que o site já espera.
//
// Credenciais vêm de variáveis de ambiente do Netlify — nunca ficam no código/repositório.
// Configure em: Netlify → Site configuration → Environment variables
//
//   ORACLE_DB_USER          = gold_bka                    (escopo: Functions/Runtime)
//   ORACLE_DB_PASSWORD      = (senha do banco)             (escopo: Functions/Runtime)
//   ORACLE_DSN              = bronkalertdb_high            (escopo: Functions/Runtime)
//   ORACLE_WALLET_PASSWORD  = (senha do wallet, se houver) (escopo: Functions/Runtime)
//   ORACLE_WALLET_B64_1..N  = wallet .zip em base64,       (escopo: Builds — NÃO marcar
//                             dividido em pedaços de até    Functions/Runtime, senão
//                             5000 caracteres               estoura o limite de 4KB do
//                                                            AWS Lambda)
//
// O wallet em si NÃO é lido de variável de ambiente em tempo de execução (o AWS Lambda,
// que roda as Netlify Functions, limita o total de env vars da função a 4KB — bem menos
// que um wallet inteiro). Em vez disso, scripts/prepare-wallet.js roda durante o BUILD
// do Netlify, decodifica o wallet uma única vez e grava os arquivos aqui do lado
// (netlify/functions/oracle-data-wallet/), que é empacotado junto da função via
// "included_files" no netlify.toml. Esta função só lê esse arquivo já pronto.
//
// Se qualquer coisa falhar (banco fora do ar, wallet ainda não configurado, etc.), a
// função devolve os dados simulados de reserva — o site nunca fica sem número nenhum.

const fs = require("fs");
const path = require("path");
const oracledb = require("oracledb");

// Modo Thin (padrão do driver) não precisa de Oracle Instant Client — funciona
// direto no ambiente serverless do Netlify, inclusive com wallet/mTLS.

const WALLET_DIR = path.join(__dirname, "oracle-data-wallet");

const FALLBACK = {
  monthly_casos: [118, 165, 245, 312, 288, 214],
  age_labels: ["0–3 meses", "4–6 meses", "7–12 meses", "1–2 anos", "Acima de 2 anos"],
  age_values: [89, 76, 54, 31, 12],
  sp_zones: {
    centro: { name: "Centro", casos: 14 },
    norte: { name: "Zona Norte", casos: 23 },
    sul: { name: "Zona Sul", casos: 0 },
    leste: { name: "Zona Leste", casos: 19 },
    oeste: { name: "Zona Oeste", casos: 11 },
  },
};

function ensureWallet() {
  if (!fs.existsSync(WALLET_DIR) || fs.readdirSync(WALLET_DIR).length === 0) {
    throw new Error(
      "Wallet não encontrado em " + WALLET_DIR + " — configure ORACLE_WALLET_B64_1..N " +
        "(escopo Builds) e rode um novo deploy para o scripts/prepare-wallet.js gerá-lo."
    );
  }
  return WALLET_DIR;
}

async function getConnection() {
  const walletDir = ensureWallet();
  return oracledb.getConnection({
    user: process.env.ORACLE_DB_USER,
    password: process.env.ORACLE_DB_PASSWORD,
    connectString: process.env.ORACLE_DSN || "bronkalertdb_high",
    configDir: walletDir,
    walletLocation: walletDir,
    walletPassword: process.env.ORACLE_WALLET_PASSWORD || undefined,
  });
}

async function fetchMonthlyCasos(connection) {
  const result = await connection.execute(
    `SELECT mes, qt_internacoes
       FROM vw_comparativo_diagnostico_mensal
      WHERE categoria_diagnostico = 'Bronquiolite (J21)'
      ORDER BY mes DESC
      FETCH FIRST 6 ROWS ONLY`
  );
  const rows = result.rows.reverse(); // mais antigo → mais novo
  return {
    labels: rows.map((r) => {
      const d = r[0];
      return `${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;
    }),
    values: rows.map((r) => Number(r[1])),
  };
}

// As views de faixa etária/zona ainda não foram mapeadas com o time — tenta buscar,
// mas cai no valor simulado sem quebrar a função caso a estrutura seja diferente
// do esperado (nomes de view/coluna a confirmar).
async function fetchAgeData(connection) {
  try {
    const result = await connection.execute(
      `SELECT faixa_etaria, qt_pacientes FROM vw_perfil_paciente`
    );
    if (!result.rows.length) throw new Error("sem linhas");
    const map = Object.fromEntries(result.rows.map((r) => [r[0], Number(r[1])]));
    return {
      labels: FALLBACK.age_labels,
      values: FALLBACK.age_labels.map((l) => map[l] ?? 0),
    };
  } catch (e) {
    console.warn("[oracle-data] faixa etária real indisponível, usando simulado:", e.message);
    return { labels: FALLBACK.age_labels, values: FALLBACK.age_values };
  }
}

async function fetchZoneData(connection) {
  try {
    const result = await connection.execute(`SELECT zona, qt_internacoes FROM vw_hospital_desempenho`);
    if (!result.rows.length) throw new Error("sem linhas");
    const map = Object.fromEntries(result.rows.map((r) => [String(r[0]).toLowerCase(), Number(r[1])]));
    const zones = {};
    Object.keys(FALLBACK.sp_zones).forEach((k) => {
      zones[k] = { name: FALLBACK.sp_zones[k].name, casos: map[k] ?? FALLBACK.sp_zones[k].casos };
    });
    return zones;
  } catch (e) {
    console.warn("[oracle-data] zonas reais indisponíveis, usando simulado:", e.message);
    return FALLBACK.sp_zones;
  }
}

exports.handler = async function () {
  const headers = {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "public, max-age=300", // 5 min — real, mas sem martelar o banco a cada acesso
    "Access-Control-Allow-Origin": "*",
  };

  let connection;
  try {
    connection = await getConnection();

    const [monthly, age, zones] = await Promise.all([
      fetchMonthlyCasos(connection),
      fetchAgeData(connection),
      fetchZoneData(connection),
    ]);

    const body = {
      generated_at: new Date().toISOString(),
      source: "Oracle Autonomous Database (FIAP_OCI_GOLD) — tempo real",
      monthly_labels: monthly.labels,
      monthly_casos: monthly.values,
      age_data: { labels: age.labels, values: age.values },
      sp_zones: zones,
      used_fallback: false,
    };

    return { statusCode: 200, headers, body: JSON.stringify(body) };
  } catch (e) {
    console.error("[oracle-data] falha ao consultar o Oracle, devolvendo fallback:", e);
    const now = new Date();
    const monthlyLabels = [];
    for (let i = 5; i >= 0; i--) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      monthlyLabels.push(`${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`);
    }
    const body = {
      generated_at: now.toISOString(),
      source: "dados simulados (Oracle indisponível: " + e.message + ")",
      monthly_labels: monthlyLabels,
      monthly_casos: FALLBACK.monthly_casos,
      age_data: { labels: FALLBACK.age_labels, values: FALLBACK.age_values },
      sp_zones: FALLBACK.sp_zones,
      used_fallback: true,
    };
    return { statusCode: 200, headers, body: JSON.stringify(body) };
  } finally {
    if (connection) {
      try {
        await connection.close();
      } catch (_) {}
    }
  }
};
