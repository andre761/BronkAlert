"""
BronqAlert — exporta os dados reais do Oracle (schema FIAP_OCI_GOLD) direto para
data/gold/bronquiolite.json, que o site já sabe ler sozinho.

Rode ISSO NA SUA MÁQUINA sempre que quiser atualizar os números do site. A senha
é digitada aqui (oculta) e nunca sai da sua máquina.

Uso:
    pip install oracledb
    python scripts\\export_gold_to_json.py

No final, também imprime o conteúdo bruto de VW_PERFIL_PACIENTE e
VW_HOSPITAL_DESEMPENHO (faixa etária e zona ainda não mapeadas) — cole essa
parte de volta na conversa para eu terminar de conectar essas duas partes.
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


def dump_view(connection, view_name, limit=30):
    """Mostra a estrutura crua de uma view — usado pra descobrir colunas de faixa
    etária/zona, que ainda não sabemos o nome certo."""
    print(f"\n=== {view_name} (até {limit} linhas) ===", file=sys.stderr)
    try:
        cursor = connection.cursor()
        cursor.execute(f"SELECT * FROM {view_name} FETCH FIRST {limit} ROWS ONLY")
        cols = [d[0] for d in cursor.description]
        print("Colunas:", cols, file=sys.stderr)
        for row in cursor.fetchall():
            print(" ", row, file=sys.stderr)
        cursor.close()
    except Exception as e:
        print(f"[erro ao consultar {view_name}: {e}]", file=sys.stderr)


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

    # Ainda não sabemos a estrutura exata dessas duas — mostra o conteúdo cru
    # pra eu terminar de mapear faixa etária e zona na próxima rodada.
    dump_view(connection, "vw_perfil_paciente")
    dump_view(connection, "vw_hospital_desempenho")

    connection.close()

    dataset = {
        "generated_at": date.today().isoformat(),
        "source": "Oracle Autonomous Database (FIAP_OCI_GOLD) — exportado manualmente",
        "monthly_labels": monthly_labels or [f"{i:02d}/2026" for i in range(3, 9)],
        "monthly_casos": monthly_casos or [118, 165, 245, 312, 288, 214],
        # Faixa etária e zona ainda seguem simuladas até mapear as views acima.
        "age_data": FALLBACK_AGE,
        "sp_zones": FALLBACK_ZONES,
        "used_fallback": not monthly_casos,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ok] Gravado em {OUT_PATH}")
    print("\nCole a partir de '=== VW_PERFIL_PACIENTE' até o final na conversa, por favor.")


if __name__ == "__main__":
    main()
