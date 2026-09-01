"""
BronqAlert — exporta os dados reais do Oracle (schema FIAP_OCI_GOLD) para
data/gold/bronquiolite.json, consumido direto pelo site (sem servidor).

Rode ISSO NA SUA MÁQUINA sempre que quiser atualizar os números do site. A
senha é digitada aqui (oculta) e nunca sai da sua máquina.

Uso:
    pip install oracledb
    python scripts\\export_gold_to_json.py

Cada seção (evolução, faixa etária, zona, alerta) é buscada e tratada de forma
independente: se uma falhar, as outras continuam, e só aquela parte cai pro
valor simulado — o site nunca fica sem dado nenhum por causa de uma view só.
"""
import getpass
import json
import sys
import tempfile
import unicodedata
import zipfile
from datetime import date
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "gold" / "bronquiolite.json"

FALLBACK = {
    "monthly_labels": None,
    "monthly_casos": [118, 165, 245, 312, 288, 214],
    "weekly_casos": [112, 98, 84, 71],
    "daily_casos": [22, 20, 19, 17, 16, 15, 14],
    "age_labels": ["Menor de 1 ano", "1 a 2 anos", "Maior de 2 anos"],
    "age_values": [134, 41, 25],
    "sp_zones": {
        "centro": {"name": "Centro", "casos": 14},
        "norte": {"name": "Zona Norte", "casos": 23},
        "sul": {"name": "Zona Sul", "casos": 0},
        "leste": {"name": "Zona Leste", "casos": 19},
        "oeste": {"name": "Zona Oeste", "casos": 11},
    },
    "alert_status": None,
    "alert_casos": None,
}

# Mapa de bairro -> zona de São Paulo (aproximado; cobre os bairros mais comuns
# nos hospitais com atendimento relevante). Bairros fora dessa lista são
# ignorados no total por zona, em vez de arriscar um chute errado.
BAIRRO_ZONA = {
    "se": "centro", "republica": "centro", "bela vista": "centro", "consolacao": "centro",
    "santa cecilia": "centro", "campos eliseos": "centro", "bom retiro": "centro",
    "liberdade": "centro", "cambuci": "centro", "higienopolis": "centro", "barra funda": "centro",
    "santana": "norte", "tucuruvi": "norte", "casa verde": "norte", "freguesia do o": "norte",
    "vila maria": "norte", "vila guilherme": "norte", "jacana": "norte", "mandaqui": "norte",
    "tremembe": "norte", "brasilandia": "norte", "limao": "norte", "vila medeiros": "norte",
    "vila mariana": "sul", "moema": "sul", "santo amaro": "sul", "campo belo": "sul",
    "saude": "sul", "ipiranga": "sul", "jabaquara": "sul", "cidade ademar": "sul",
    "capela do socorro": "sul", "grajau": "sul", "interlagos": "sul", "vila andrade": "sul",
    "campo grande": "sul", "americanopolis": "sul",
    "tatuape": "leste", "mooca": "leste", "belenzinho": "leste", "penha": "leste",
    "itaquera": "leste", "sao miguel paulista": "leste", "vila matilde": "leste",
    "vila formosa": "leste", "vila prudente": "leste", "sapopemba": "leste",
    "cidade tiradentes": "leste", "guaianases": "leste", "ermelino matarazzo": "leste",
    "parque sao lucas": "leste", "sao mateus": "leste", "aricanduva": "leste",
    "pinheiros": "oeste", "perdizes": "oeste", "lapa": "oeste", "butanta": "oeste",
    "vila madalena": "oeste", "alto de pinheiros": "oeste", "jardins": "oeste",
    "vila romana": "oeste", "rio pequeno": "oeste", "raposo tavares": "oeste",
    "jaguare": "oeste", "morumbi": "oeste", "itaim bibi": "oeste", "vila leopoldina": "oeste",
}

STATUS_MAP = {"NORMAL": 0, "ATENCAO": 1, "MODERADO": 1, "ALERTA": 3, "ALTO": 3, "CRITICO": 3}


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")


def connect():
    wallet_path = input("Caminho do wallet (.zip ou pasta já extraída) [C:\\FIAP\\Challenge\\Wallet_bronkalertdb]: ").strip().strip('"')
    wallet_path = wallet_path or r"C:\FIAP\Challenge\Wallet_bronkalertdb"
    user = input("Usuário [gold_bka]: ").strip() or "gold_bka"
    password = getpass.getpass("Senha do banco (não aparece na tela): ")
    service = input("Serviço [bronkalertdb_high]: ").strip() or "bronkalertdb_high"

    wallet_dir = wallet_path
    if wallet_path.lower().endswith(".zip"):
        wallet_dir = str(Path(tempfile.mkdtemp(prefix="bronqalert_wallet_")))
        with zipfile.ZipFile(wallet_path) as zf:
            zf.extractall(wallet_dir)

    try:
        return oracledb.connect(user=user, password=password, dsn=service, config_dir=wallet_dir)
    except Exception:
        wallet_password = getpass.getpass("Senha do wallet: ")
        return oracledb.connect(
            user=user, password=password, dsn=service,
            config_dir=wallet_dir, wallet_location=wallet_dir, wallet_password=wallet_password,
        )


def safe(label, fn):
    try:
        result = fn()
        print(f"[ok] {label}", file=sys.stderr)
        return result
    except Exception as e:
        print(f"[aviso] {label} falhou, mantendo simulado: {e}", file=sys.stderr)
        return None


def fetch_monthly(cur):
    cur.execute(
        """
        SELECT mes, qt_internacoes FROM vw_comparativo_diagnostico_mensal
        WHERE categoria_diagnostico = 'Bronquiolite (J21)'
        ORDER BY mes DESC FETCH FIRST 6 ROWS ONLY
        """
    )
    rows = list(reversed(cur.fetchall()))
    if not rows:
        raise ValueError("sem linhas")
    return [f"{d.month:02d}/{d.year}" for d, _ in rows], [int(v) for _, v in rows]


def fetch_daily(cur):
    cur.execute("SELECT dia, qt_internacoes_bq FROM vw_casos_diarios ORDER BY dia DESC FETCH FIRST 7 ROWS ONLY")
    rows = list(reversed(cur.fetchall()))
    if not rows:
        raise ValueError("sem linhas")
    return [int(v) for _, v in rows]


def fetch_weekly(cur):
    cur.execute("SELECT semana, qt_internacoes_bronq FROM vw_casos_semanais ORDER BY semana DESC FETCH FIRST 4 ROWS ONLY")
    rows = list(reversed(cur.fetchall()))
    if not rows:
        raise ValueError("sem linhas")
    return [int(v) for _, v in rows]


def fetch_age(cur):
    cur.execute(
        """
        SELECT faixa_etaria, SUM(qt_internacoes)
        FROM vw_perfil_paciente_mensal
        WHERE mes_ref = (
            SELECT mes_ref FROM vw_perfil_paciente_mensal
            ORDER BY TO_DATE(mes_ref,'MM/YYYY') DESC FETCH FIRST 1 ROW ONLY
        )
        GROUP BY faixa_etaria
        """
    )
    by_label = {row[0]: int(row[1]) for row in cur.fetchall()}
    labels = FALLBACK["age_labels"]
    values = [by_label.get(l, 0) for l in labels]
    if sum(values) == 0:
        raise ValueError("todas as faixas vieram zeradas")
    return labels, values


def fetch_alert(cur):
    cur.execute(
        """
        SELECT semana, qt_internacoes, status_alerta
        FROM vw_alerta_risco_semanal
        ORDER BY semana DESC FETCH FIRST 1 ROW ONLY
        """
    )
    row = cur.fetchone()
    if not row:
        raise ValueError("sem linhas")
    _, casos, status = row
    return str(status).strip(), int(casos)


def fetch_zones(cur):
    cur.execute(
        """
        SELECT no_bairro, qt_internacoes, pct_bronquiolite
        FROM vw_hospital_desempenho
        WHERE qt_internacoes > 0
        """
    )
    totals = {k: 0.0 for k in FALLBACK["sp_zones"]}
    matched_rows = 0
    for bairro, qt, pct in cur.fetchall():
        zona = BAIRRO_ZONA.get(strip_accents((bairro or "").strip().lower()))
        if not zona or pct is None:
            continue
        totals[zona] += float(qt) * float(pct) / 100.0
        matched_rows += 1
    if matched_rows == 0:
        raise ValueError("nenhum bairro reconhecido")
    return {k: {"name": FALLBACK["sp_zones"][k]["name"], "casos": round(v)} for k, v in totals.items()}


def main():
    print("Conectando ao Oracle...", file=sys.stderr)
    connection = connect()
    cur = connection.cursor()
    print("Conectado! Buscando cada parte...\n", file=sys.stderr)

    monthly = safe("evolução mensal (VW_COMPARATIVO_DIAGNOSTICO_MENSAL)", lambda: fetch_monthly(cur))
    daily = safe("últimos 7 dias (VW_CASOS_DIARIOS)", lambda: fetch_daily(cur))
    weekly = safe("últimas 4 semanas (VW_CASOS_SEMANAIS)", lambda: fetch_weekly(cur))
    age = safe("faixa etária (VW_PERFIL_PACIENTE_MENSAL)", lambda: fetch_age(cur))
    alert = safe("status de alerta (VW_ALERTA_RISCO_SEMANAL)", lambda: fetch_alert(cur))
    zones = safe("zonas de SP (VW_HOSPITAL_DESEMPENHO)", lambda: fetch_zones(cur))

    cur.close()
    connection.close()

    monthly_labels, monthly_casos = monthly if monthly else (
        [f"{i:02d}/2026" for i in range(3, 9)], FALLBACK["monthly_casos"]
    )
    age_labels, age_values = age if age else (FALLBACK["age_labels"], FALLBACK["age_values"])
    alert_status, alert_casos = alert if alert else (None, None)

    dataset = {
        "generated_at": date.today().isoformat(),
        "source": "Oracle Autonomous Database (FIAP_OCI_GOLD) — exportado manualmente",
        "monthly_labels": monthly_labels,
        "monthly_casos": monthly_casos,
        "daily_casos": daily or FALLBACK["daily_casos"],
        "weekly_casos": weekly or FALLBACK["weekly_casos"],
        "age_data": {"labels": age_labels, "values": age_values},
        "sp_zones": zones or FALLBACK["sp_zones"],
        "alert_status": alert_status,
        "alert_casos": alert_casos,
        "used_fallback": monthly is None,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ok] Gravado em {OUT_PATH}")


if __name__ == "__main__":
    main()
