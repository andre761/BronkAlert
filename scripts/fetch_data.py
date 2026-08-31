"""
BronqAlert — ingestão de dados reais (sem Oracle, 100% gratuito).

Fontes:
  - DATASUS/SIH (internações hospitalares, CID J21 = Bronquiolite aguda), via pysus
  - CNES (endereço/CEP dos hospitais), via pysus, para estimar a zona de SP
  - INMET (clima), via API pública apitempo.inmet.gov.br

Saída: data/gold/bronquiolite.json — consumido diretamente pelo site (fetch()),
com fallback para os números simulados caso alguma etapa falhe, para o site
nunca quebrar por causa de uma fonte de dados externa fora do ar.

Uso local:
    pip install -r scripts/requirements.txt
    python scripts/fetch_data.py

Na automação (GitHub Actions), este script roda sozinho toda semana.
"""
import json
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "gold" / "bronquiolite.json"

CID_BRONQUIOLITE = "J21"          # CID-10: Bronquiolite aguda (J21.0, J21.8, J21.9)
MUNIC_SP_CAPITAL_PREFIX = "355030"  # código IBGE (6 dígitos, padrão SIH) de São Paulo capital

# Números usados apenas se a busca real falhar por completo (site nunca fica sem dado).
FALLBACK = {
    "monthly_labels": None,  # preenchido em tempo de execução com os últimos 6 meses
    "monthly_casos": [118, 165, 245, 312, 288, 214],
    "age_labels": ["0–3 meses", "4–6 meses", "7–12 meses", "1–2 anos", "Acima de 2 anos"],
    "age_values": [89, 76, 54, 31, 12],
    "sp_zones": {
        "centro": {"name": "Centro", "casos": 14},
        "norte": {"name": "Zona Norte", "casos": 23},
        "sul": {"name": "Zona Sul", "casos": 0},
        "leste": {"name": "Zona Leste", "casos": 19},
        "oeste": {"name": "Zona Oeste", "casos": 11},
    },
}


def last_n_months(n, from_date=None):
    """Retorna [(ano, mes), ...] dos últimos n meses fechados, do mais antigo ao mais novo."""
    d = from_date or date.today().replace(day=1)
    out = []
    for i in range(n, 0, -1):
        m = (d.month - i - 1) % 12 + 1
        y = d.year + (d.month - i - 1) // 12
        out.append((y, m))
    return out


def zone_from_cep(cep):
    """Mesma heurística de prefixo de CEP usada no front-end (index.html/zoneFromCep)."""
    digits = "".join(c for c in str(cep) if c.isdigit())
    if len(digits) < 5:
        return None
    p = int(digits[:2])
    if p == 1:
        return "centro"
    if p == 2:
        return "norte"
    if p in (3, 8):
        return "leste"
    if p == 4:
        return "sul"
    if p in (5, 6, 7):
        return "oeste"
    return None


def fetch_sih_month(year, month):
    """Baixa a AIH reduzida (RD) de SP para um mês e devolve só as internações por bronquiolite."""
    from pysus.online_data.SIH import download

    parquets = download(states=["SP"], years=[year], months=[month], group="RD")
    df = parquets.to_dataframe()

    if year == FIRST_DEBUG_YEAR_MONTH[0] and month == FIRST_DEBUG_YEAR_MONTH[1]:
        print(f"[debug] colunas SIH: {list(df.columns)}", file=sys.stderr)

    if "DIAG_PRINC" not in df.columns:
        raise KeyError("coluna DIAG_PRINC não encontrada no retorno do SIH")

    df["DIAG_PRINC"] = df["DIAG_PRINC"].astype(str).str.strip().str.upper()
    bronq = df[df["DIAG_PRINC"].str.startswith(CID_BRONQUIOLITE)].copy()

    if "MUNIC_MOV" in bronq.columns:
        bronq = bronq[bronq["MUNIC_MOV"].astype(str).str.startswith(MUNIC_SP_CAPITAL_PREFIX)]

    return bronq


def age_bucket_months(idade, cod_idade):
    """
    Layout padrão AIH: COD_IDADE 2=dias, 3=meses, 4=anos.
    Converte para idade em meses; qualquer código não mapeado é tratado como anos
    (mais comum em internações pediátricas antigas) para não descartar o registro.
    """
    try:
        idade = float(idade)
    except (TypeError, ValueError):
        return None
    cod = str(cod_idade).strip()
    if cod == "2":
        return idade / 30.0
    if cod == "3":
        return idade
    return idade * 12.0


def bucket_label(months):
    if months is None:
        return None
    if months <= 3:
        return "0–3 meses"
    if months <= 6:
        return "4–6 meses"
    if months <= 12:
        return "7–12 meses"
    if months <= 24:
        return "1–2 anos"
    return "Acima de 2 anos"


def fetch_cnes_ceps():
    """CNES: mapa {codigo_cnes: cep} dos hospitais de SP, para estimar a zona da internação."""
    from pysus.online_data.CNES import download

    parquets = download(group="ST", states=["SP"], years=[date.today().year], months=[date.today().month])
    df = parquets.to_dataframe()
    cep_col = next((c for c in ["CEP", "NU_CEP", "CO_CEP"] if c in df.columns), None)
    code_col = next((c for c in ["CNES", "CO_CNES"] if c in df.columns), None)
    if not cep_col or not code_col:
        raise KeyError("colunas de CEP/CNES não encontradas no cadastro CNES")
    return dict(zip(df[code_col].astype(str), df[cep_col].astype(str)))


def fetch_inmet_recent_temperature():
    """Temperatura média recente (estação automática de São Paulo capital) — contexto sazonal."""
    station = "A701"  # Mirante de Santana, São Paulo capital
    end = date.today()
    start = end - timedelta(days=7)
    url = f"https://apitempo.inmet.gov.br/estacao/{start.isoformat()}/{end.isoformat()}/{station}"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    temps = [float(r["TEM_INS"]) for r in rows if r.get("TEM_INS") not in (None, "", "null")]
    if not temps:
        return None
    return round(sum(temps) / len(temps), 1)


FIRST_DEBUG_YEAR_MONTH = last_n_months(6)[0]


def build_dataset():
    months = last_n_months(6)
    monthly_labels = [f"{y}-{m:02d}" for y, m in months]
    monthly_casos = []
    age_counter = {"0–3 meses": 0, "4–6 meses": 0, "7–12 meses": 0, "1–2 anos": 0, "Acima de 2 anos": 0}
    zone_counter = {"centro": 0, "norte": 0, "sul": 0, "leste": 0, "oeste": 0}
    zone_names = {k: v["name"] for k, v in FALLBACK["sp_zones"].items()}

    cnes_ceps = {}
    try:
        cnes_ceps = fetch_cnes_ceps()
        print(f"[info] CNES: {len(cnes_ceps)} estabelecimentos carregados", file=sys.stderr)
    except Exception as e:
        print(f"[aviso] CNES indisponível, zonas ficarão sem dado real: {e}", file=sys.stderr)

    for year, month in months:
        try:
            bronq = fetch_sih_month(year, month)
            monthly_casos.append(int(len(bronq)))
            print(f"[info] {year}-{month:02d}: {len(bronq)} internações por bronquiolite (SP capital)", file=sys.stderr)

            if "IDADE" in bronq.columns and "COD_IDADE" in bronq.columns:
                for idade, cod in zip(bronq["IDADE"], bronq["COD_IDADE"]):
                    label = bucket_label(age_bucket_months(idade, cod))
                    if label:
                        age_counter[label] += 1

            if cnes_ceps and "CNES" in bronq.columns:
                for cnes_code in bronq["CNES"].astype(str):
                    cep = cnes_ceps.get(cnes_code)
                    zone = zone_from_cep(cep) if cep else None
                    if zone:
                        zone_counter[zone] += 1
        except Exception as e:
            print(f"[erro] falha ao buscar SIH {year}-{month:02d}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            monthly_casos.append(None)  # marcado como indisponível; tratado na validação abaixo

    temperature = None
    try:
        temperature = fetch_inmet_recent_temperature()
        print(f"[info] INMET: temperatura média últimos 7 dias = {temperature}°C", file=sys.stderr)
    except Exception as e:
        print(f"[aviso] INMET indisponível: {e}", file=sys.stderr)

    real_months_ok = sum(1 for v in monthly_casos if v is not None)
    age_total = sum(age_counter.values())
    zone_total = sum(zone_counter.values())

    dataset = {
        "generated_at": date.today().isoformat(),
        "source": "DATASUS/SIH + CNES + INMET (dados públicos, sem Oracle)",
        "data_quality": {
            "monthly_months_ok": real_months_ok,
            "monthly_months_total": len(months),
            "age_sample_size": age_total,
            "zone_sample_size": zone_total,
        },
        "monthly_labels": monthly_labels,
        "monthly_casos": monthly_casos if real_months_ok == len(months) else FALLBACK["monthly_casos"],
        "age_data": {
            "labels": FALLBACK["age_labels"],
            "values": [age_counter[l] for l in FALLBACK["age_labels"]] if age_total > 0 else FALLBACK["age_values"],
        },
        "sp_zones": {
            k: {"name": zone_names[k], "casos": zone_counter[k] if zone_total > 0 else FALLBACK["sp_zones"][k]["casos"]}
            for k in zone_counter
        },
        "temperature_c_7d": temperature,
        "used_fallback": real_months_ok < len(months),
    }
    return dataset


def main():
    try:
        dataset = build_dataset()
    except Exception as e:
        print(f"[erro fatal] usando fallback completo: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        months = last_n_months(6)
        dataset = {
            "generated_at": date.today().isoformat(),
            "source": "dados simulados (falha ao buscar dados reais)",
            "data_quality": {"monthly_months_ok": 0, "monthly_months_total": 6, "age_sample_size": 0, "zone_sample_size": 0},
            "monthly_labels": [f"{y}-{m:02d}" for y, m in months],
            "monthly_casos": FALLBACK["monthly_casos"],
            "age_data": {"labels": FALLBACK["age_labels"], "values": FALLBACK["age_values"]},
            "sp_zones": FALLBACK["sp_zones"],
            "temperature_c_7d": None,
            "used_fallback": True,
        }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] escrito em {OUT_PATH}")


if __name__ == "__main__":
    main()
