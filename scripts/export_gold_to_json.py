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
import zlib
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

# Centro aproximado de cada zona (mesmas coordenadas usadas no mapa do site) —
# usado só pra posicionar hospitais sem endereço geocodificado.
ZONE_COORDS = {
    "centro": (-23.5505, -46.6333),
    "norte": (-23.4936, -46.6291),
    "sul": (-23.6398, -46.6396),
    "leste": (-23.5400, -46.4900),
    "oeste": (-23.5620, -46.7211),
}


def _jitter(seed_text, spread=0.025):
    """Pequeno deslocamento determinístico (mesmo hospital sempre cai no mesmo
    ponto) só pra não empilhar vários hospitais do mesmo bairro exatamente um
    em cima do outro no mapa. Não é a coordenada real do endereço — é uma
    aproximação dentro da zona da cidade (mesmo nível de precisão já usado
    para as zonas de SP)."""
    h = zlib.crc32(seed_text.encode("utf-8"))
    dx = ((h % 1000) / 1000.0 - 0.5) * 2 * spread
    dy = (((h // 1000) % 1000) / 1000.0 - 0.5) * 2 * spread
    return dx, dy


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
    # Até 12 meses (o ano todo) — se algum mês não vier na consulta, o site
    # simplesmente mostra um vão ali no gráfico de "Ano" em vez de inventar
    # um número; os períodos de 3/6 meses continuam pegando só os últimos
    # meses reais (a ponta mais recente costuma vir completa).
    cur.execute(
        """
        SELECT mes, qt_internacoes FROM vw_comparativo_diagnostico_mensal
        WHERE categoria_diagnostico = 'Bronquiolite (J21)'
        ORDER BY mes DESC FETCH FIRST 12 ROWS ONLY
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
    # Datas reais junto do valor — sem isso o site mostraria o número real
    # sob uma data calculada "a partir de hoje", o que seria enganoso.
    return [f"{d.day:02d}/{d.month:02d}/{d.year}" for d, _ in rows], [int(v) for _, v in rows]


def fetch_weekly(cur):
    cur.execute("SELECT semana, qt_internacoes_bronq FROM vw_casos_semanais ORDER BY semana DESC FETCH FIRST 4 ROWS ONLY")
    rows = list(reversed(cur.fetchall()))
    if not rows:
        raise ValueError("sem linhas")
    return [f"{d.day:02d}/{d.month:02d}/{d.year}" for d, _ in rows], [int(v) for _, v in rows]


def fetch_age(cur):
    # Soma TODOS os meses disponíveis na view (não só o mais recente) — uma
    # distribuição por faixa etária baseada no ano inteiro é mais
    # representativa do que um único mês, que pode ser atípico.
    cur.execute(
        """
        SELECT faixa_etaria, SUM(qt_internacoes)
        FROM vw_perfil_paciente_mensal
        GROUP BY faixa_etaria
        """
    )
    by_label = {row[0]: int(row[1]) for row in cur.fetchall()}
    labels = FALLBACK["age_labels"]
    values = [by_label.get(l, 0) for l in labels]
    if sum(values) == 0:
        raise ValueError("todas as faixas vieram zeradas")

    cur.execute(
        """
        SELECT MIN(TO_DATE(mes_ref,'MM/YYYY')), MAX(TO_DATE(mes_ref,'MM/YYYY'))
        FROM vw_perfil_paciente_mensal
        """
    )
    ini, fim = cur.fetchone() or (None, None)
    period_label = None
    if ini and fim:
        ini_txt, fim_txt = f"{ini.month:02d}/{ini.year}", f"{fim.month:02d}/{fim.year}"
        period_label = ini_txt if ini_txt == fim_txt else f"{ini_txt} a {fim_txt}"
    return labels, values, period_label


def fetch_hospitals(cur):
    # Só hospitais com internação e percentual de bronquiolite conhecidos —
    # ordenado por volume, os postos/clínicas pequenas com tudo zerado (a
    # maioria dos registros do CNES) ficam de fora naturalmente.
    cur.execute(
        """
        SELECT no_fantasia, no_bairro, qt_internacoes, pct_bronquiolite
        FROM vw_hospital_desempenho
        WHERE qt_internacoes > 0 AND pct_bronquiolite IS NOT NULL
        ORDER BY qt_internacoes DESC
        FETCH FIRST 12 ROWS ONLY
        """
    )
    rows = cur.fetchall()
    if not rows:
        raise ValueError("sem linhas")
    hospitals = []
    for nome, bairro, qt, pct in rows:
        zona = BAIRRO_ZONA.get(strip_accents((bairro or "").strip().lower()))
        if not zona:
            continue
        base_lat, base_lng = ZONE_COORDS[zona]
        dx, dy = _jitter(str(nome))
        hospitals.append({
            "name": str(nome).strip(),
            "bairro": str(bairro).strip().title(),
            "casos": round(float(qt) * float(pct) / 100.0),
            "lat": base_lat + dx,
            "lng": base_lng + dy,
        })
    if not hospitals:
        raise ValueError("nenhum bairro reconhecido")
    return hospitals


def fetch_daily_climate(cur):
    cur.execute(
        "SELECT dia, temp_max, temp_min, precipitacao FROM vw_casos_diarios ORDER BY dia DESC FETCH FIRST 7 ROWS ONLY"
    )
    rows = list(reversed(cur.fetchall()))
    if not rows:
        raise ValueError("sem linhas")
    return {
        "temp_max": [float(r[1]) for r in rows],
        "temp_min": [float(r[2]) for r in rows],
        "precip": [float(r[3]) if r[3] is not None else 0.0 for r in rows],
    }


def fetch_weekly_climate(cur):
    # Mesmas 4 semanas de fetch_weekly (mesmo ORDER BY/LIMIT) — dá pra cruzar
    # com weekly_casos pelo índice, igual já se faz com daily_climate.
    cur.execute(
        "SELECT semana, media_temp_max, media_temp_min FROM vw_casos_semanais ORDER BY semana DESC FETCH FIRST 4 ROWS ONLY"
    )
    rows = list(reversed(cur.fetchall()))
    if not rows:
        raise ValueError("sem linhas")
    return {
        "temp_max": [float(r[1]) for r in rows],
        "temp_min": [float(r[2]) for r in rows],
    }


def fetch_weekly_alert_trend(cur):
    cur.execute(
        """
        SELECT c.semana, c.qt_internacoes, a.status_alerta, a.queda_temp_min_2sem
        FROM vw_casos_semanais c
        JOIN vw_alerta_risco_semanal a ON c.semana = a.semana
        ORDER BY c.semana DESC FETCH FIRST 8 ROWS ONLY
        """
    )
    rows = list(reversed(cur.fetchall()))
    if not rows:
        raise ValueError("sem linhas")
    return {
        "labels": [f"{s.day:02d}/{s.month:02d}/{s.year}" for s, *_ in rows],
        "casos": [int(v) for _, v, *_ in rows],
        "status": [str(s).strip().upper() for *_, s, _ in rows],
        "queda_temp": [float(q) if q is not None else None for *_, q in rows],
    }


def fetch_seasonality(cur):
    cur.execute(
        """
        SELECT mes_ref, qt_internacoes, media_temp_max, media_temp_min, soma_precipitacao
        FROM vw_sazonalidade_mensal
        ORDER BY mes FETCH FIRST 24 ROWS ONLY
        """
    )
    rows = cur.fetchall()
    if not rows:
        raise ValueError("sem linhas")
    return {
        "labels": [str(r[0]) for r in rows],
        "casos": [int(r[1]) for r in rows],
        "temp_max": [float(r[2]) for r in rows],
        "temp_min": [float(r[3]) for r in rows],
        "precip": [float(r[4]) if r[4] is not None else 0.0 for r in rows],
    }


def fetch_diagnostic_monthly(cur):
    # Só entram os meses com as 3 categorias completas (Bronquiolite, Outras
    # doenças respiratórias, Outras causas) — um mês com dado parcial fica de
    # fora do gráfico em vez de aparecer com uma fatia inventada.
    cur.execute(
        """
        SELECT mes_ref, categoria_diagnostico, qt_internacoes
        FROM vw_comparativo_diagnostico_mensal
        ORDER BY mes, categoria_diagnostico
        """
    )
    by_month, order = {}, []
    for mes_ref, categoria, qt in cur.fetchall():
        if mes_ref not in by_month:
            by_month[mes_ref] = {}
            order.append(mes_ref)
        by_month[mes_ref][categoria] = int(qt)
    labels, bronq, resp, outras = [], [], [], []
    for mes_ref in order:
        cats = by_month[mes_ref]
        if {"Bronquiolite (J21)", "Outras doenças respiratórias (J)", "Outras causas"} <= cats.keys():
            labels.append(mes_ref)
            bronq.append(cats["Bronquiolite (J21)"])
            resp.append(cats["Outras doenças respiratórias (J)"])
            outras.append(cats["Outras causas"])
    if not labels:
        raise ValueError("nenhum mês com as 3 categorias completas")
    return {"labels": labels, "bronquiolite": bronq, "respiratorias": resp, "outras": outras}


def fetch_hospital_ranking(cur):
    # FETCH FIRST em vez do ROWNUM<=10 do rascunho original: ROWNUM é
    # atribuído ANTES do ORDER BY, então "WHERE ROWNUM<=10 ORDER BY ..."
    # ordena só 10 linhas arbitrárias, não as 10 de maior volume.
    # Só nome, bairro e internações — não temos (ainda) % de bronquiolite real
    # por hospital, então o site não mostra mais essa coluna nem óbitos/
    # casos-por-leito por hospital (evita number ao lado de um nome real que
    # não é realmente daquele hospital).
    cur.execute(
        """
        SELECT no_fantasia, no_bairro, qt_internacoes
        FROM vw_hospital_desempenho
        WHERE qt_internacoes > 0
        ORDER BY qt_internacoes DESC
        FETCH FIRST 10 ROWS ONLY
        """
    )
    rows = cur.fetchall()
    if not rows:
        raise ValueError("sem linhas")
    return [
        {
            "name": str(nome).strip(),
            "bairro": str(bairro).strip().title() if bairro else "",
            "internacoes": int(qt),
        }
        for nome, bairro, qt in rows
    ]


def fetch_bairro_concentration(cur):
    # Mesma fórmula (qt_internacoes × pct_bronquiolite) usada em fetch_zones/
    # fetch_hospitals — QT_INTERNACOES nesta view é geral, não só bronquiolite.
    cur.execute(
        """
        SELECT no_bairro, qt_internacoes, pct_bronquiolite
        FROM vw_hospital_desempenho
        WHERE qt_internacoes > 0 AND pct_bronquiolite IS NOT NULL
        """
    )
    totals = {}
    for bairro, qt, pct in cur.fetchall():
        if not bairro:
            continue
        key = str(bairro).strip().title()
        totals[key] = totals.get(key, 0.0) + float(qt) * float(pct) / 100.0
    if not totals:
        raise ValueError("nenhum bairro com internação")
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:12]
    return [{"bairro": k, "casos": round(v)} for k, v in ranked]


def fetch_patient_profile(cur):
    # VW_PERFIL_PACIENTE_MENSAL (não VW_PERFIL_PACIENTE, que é geral/todas as
    # causas) — mesma view e mesmo mês de fetch_age, só aberta por sexo em
    # vez de só somada, pra bater exatamente com age_data.
    cur.execute(
        """
        SELECT faixa_etaria, sexo_desc, qt_internacoes
        FROM vw_perfil_paciente_mensal
        WHERE mes_ref = (
            SELECT mes_ref FROM vw_perfil_paciente_mensal
            ORDER BY TO_DATE(mes_ref,'MM/YYYY') DESC FETCH FIRST 1 ROW ONLY
        )
        """
    )
    rows = cur.fetchall()
    if not rows:
        raise ValueError("sem linhas")
    by_key = {}
    for faixa, sexo, qt in rows:
        by_key[(faixa, sexo)] = by_key.get((faixa, sexo), 0) + int(qt)
    labels = FALLBACK["age_labels"]
    masc = [by_key.get((l, "Masculino"), 0) for l in labels]
    fem = [by_key.get((l, "Feminino"), 0) for l in labels]
    if sum(masc) + sum(fem) == 0:
        raise ValueError("sem dados de perfil")

    cur.execute(
        """
        SELECT mes_ref FROM vw_perfil_paciente_mensal
        ORDER BY TO_DATE(mes_ref,'MM/YYYY') DESC FETCH FIRST 1 ROW ONLY
        """
    )
    row = cur.fetchone()
    period_label = row[0] if row else None
    return {"labels": labels, "masculino": masc, "feminino": fem, "period_label": period_label}


def fetch_cost_share(cur):
    cur.execute(
        """
        SELECT semana, soma_val_tot, soma_val_tot_bronq
        FROM vw_casos_semanais
        ORDER BY semana DESC FETCH FIRST 4 ROWS ONLY
        """
    )
    rows = list(reversed(cur.fetchall()))
    if not rows:
        raise ValueError("sem linhas")
    labels, pct, valor_bronq = [], [], []
    for semana, total, bronq in rows:
        if not total:
            continue
        labels.append(f"{semana.day:02d}/{semana.month:02d}/{semana.year}")
        pct.append(round(float(bronq or 0) / float(total) * 100, 3))
        valor_bronq.append(round(float(bronq or 0), 2))
    if not labels:
        raise ValueError("sem semanas com custo total")
    # valor_bronq (R$ reais por semana) permite ao site estimar um custo
    # mensal (casos reais do mês × custo médio real por caso) — a view só
    # tem esse valor em R$ na granularidade semanal, não mensal.
    return {"labels": labels, "pct": pct, "valor_bronq": valor_bronq}


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
    hospitals = safe("hospitais de SP (VW_HOSPITAL_DESEMPENHO)", lambda: fetch_hospitals(cur))
    daily_climate = safe("clima diário (VW_CASOS_DIARIOS)", lambda: fetch_daily_climate(cur))
    weekly_climate = safe("clima semanal (VW_CASOS_SEMANAIS)", lambda: fetch_weekly_climate(cur))
    weekly_alert_trend = safe("tendência semanal com alerta (VW_CASOS_SEMANAIS + VW_ALERTA_RISCO_SEMANAL)", lambda: fetch_weekly_alert_trend(cur))
    seasonality = safe("sazonalidade mensal (VW_SAZONALIDADE_MENSAL)", lambda: fetch_seasonality(cur))
    diagnostic_monthly = safe("comparativo de diagnósticos (VW_COMPARATIVO_DIAGNOSTICO_MENSAL)", lambda: fetch_diagnostic_monthly(cur))
    hospital_ranking = safe("ranking de hospitais (VW_HOSPITAL_DESEMPENHO)", lambda: fetch_hospital_ranking(cur))
    bairro_concentration = safe("concentração por bairro (VW_HOSPITAL_DESEMPENHO)", lambda: fetch_bairro_concentration(cur))
    patient_profile = safe("perfil de paciente (VW_PERFIL_PACIENTE)", lambda: fetch_patient_profile(cur))
    cost_share = safe("custo bronquiolite vs. total (VW_CASOS_SEMANAIS)", lambda: fetch_cost_share(cur))

    cur.close()
    connection.close()

    monthly_labels, monthly_casos = monthly if monthly else (
        [f"{i:02d}/2026" for i in range(3, 9)], FALLBACK["monthly_casos"]
    )
    daily_labels, daily_casos = daily if daily else (None, FALLBACK["daily_casos"])
    weekly_labels, weekly_casos = weekly if weekly else (None, FALLBACK["weekly_casos"])
    age_labels, age_values, age_period = age if age else (FALLBACK["age_labels"], FALLBACK["age_values"], None)
    alert_status, alert_casos = alert if alert else (None, None)

    dataset = {
        "generated_at": date.today().isoformat(),
        "source": "Oracle Autonomous Database (FIAP_OCI_GOLD) — exportado manualmente",
        "monthly_labels": monthly_labels,
        "monthly_casos": monthly_casos,
        "daily_labels": daily_labels,
        "daily_casos": daily_casos,
        "weekly_labels": weekly_labels,
        "weekly_casos": weekly_casos,
        "age_data": {"labels": age_labels, "values": age_values, "period_label": age_period},
        "sp_zones": zones or FALLBACK["sp_zones"],
        "alert_status": alert_status,
        "alert_casos": alert_casos,
        "used_fallback": monthly is None,
    }
    if hospitals:
        dataset["hospitals"] = hospitals
    if daily_climate:
        dataset["daily_climate"] = daily_climate
    if weekly_climate:
        dataset["weekly_climate"] = weekly_climate
    if weekly_alert_trend:
        dataset["weekly_alert_trend"] = weekly_alert_trend
    if seasonality:
        dataset["seasonality"] = seasonality
    if diagnostic_monthly:
        dataset["diagnostic_monthly"] = diagnostic_monthly
    if hospital_ranking:
        dataset["hospital_ranking"] = hospital_ranking
    if bairro_concentration:
        dataset["bairro_concentration"] = bairro_concentration
    if patient_profile:
        dataset["patient_profile"] = patient_profile
    if cost_share:
        dataset["cost_share"] = cost_share

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ok] Gravado em {OUT_PATH}")


if __name__ == "__main__":
    main()
