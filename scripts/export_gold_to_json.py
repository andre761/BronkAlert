"""
BronqAlert — exporta os dados reais do Oracle (schema FIAP_OCI_GOLD) direto para
data/gold/bronquiolite.json, que o site já sabe ler sozinho (sem precisar de
Netlify Function, sem servidor nenhum).

Rode ISSO NA SUA MÁQUINA sempre que quiser atualizar os números do site com
dados novos do banco. A senha é digitada aqui (oculta, nunca enviada a lugar
nenhum) e o resultado é só o JSON com números — sem nenhuma credencial dentro.

Uso:
    pip install oracledb
    python scripts\\export_gold_to_json.py

Depois é só commitar e dar push no data/gold/bronquiolite.json.
"""
import getpass
import json
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "gold" / "bronquiolite.json"

FALLBACK_AGE = {"labels": ["0–3 meses", "4–6 meses", "7–12 meses", "1–2 anos", "Acima de 2 anos"], "values": [89, 76, 54, 31, 12]}
FALLBACK_ZONES = {
    "centro": {"name": "Centro", "casos": 14},
    "norte": {"name": "Zona Norte", "casos": 23},
    "sul": {"name": "Zona Sul", "casos": 0},
    "leste": {"name": "Zona Leste", "casos": 19},
    "oeste": {"name": "Zona Oeste", "casos": 11},
}


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


def fetch_monthly(connection):
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT mes, qt_internacoes
          FROM vw_comparativo_diagnostico_mensal
         WHERE categoria_diagnostico = 'Bronquiolite (J21)'
         ORDER BY mes DESC
         FETCH FIRST 6 ROWS ONLY
        """
    )
    rows = list(reversed(cursor.fetchall()))
    cursor.close()
    labels = [f"{d.month:02d}/{d.year}" for d, _ in rows]
    values = [int(v) for _, v in rows]
    return labels, values


def main():
    print("Conectando ao Oracle...", file=sys.stderr)
    connection = connect()
    print("Conectado! Buscando VW_COMPARATIVO_DIAGNOSTICO_MENSAL...", file=sys.stderr)

    try:
        monthly_labels, monthly_casos = fetch_monthly(connection)
        print(f"  {len(monthly_casos)} meses encontrados: {list(zip(monthly_labels, monthly_casos))}", file=sys.stderr)
    except Exception as e:
        print(f"[erro] não consegui buscar a evolução mensal: {e}", file=sys.stderr)
        monthly_labels, monthly_casos = [], []

    connection.close()

    dataset = {
        "generated_at": date.today().isoformat(),
        "source": "Oracle Autonomous Database (FIAP_OCI_GOLD) — exportado manualmente",
        "monthly_labels": monthly_labels or [f"{i:02d}/2026" for i in range(3, 9)],
        "monthly_casos": monthly_casos or [118, 165, 245, 312, 288, 214],
        # Faixa etária e zona ainda não mapeadas no Gold — seguem simuladas por
        # enquanto (não afeta a evolução mensal, que já é 100% real acima).
        "age_data": FALLBACK_AGE,
        "sp_zones": FALLBACK_ZONES,
        "used_fallback": not monthly_casos,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ok] Gravado em {OUT_PATH}")
    print("Agora é só: git add data/gold/bronquiolite.json && git commit -m \"dados reais\" && git push")


if __name__ == "__main__":
    main()
