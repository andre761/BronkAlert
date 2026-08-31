"""
BronqAlert — exploração do schema FIAP_OCI_GOLD (Oracle Autonomous DB via wallet).

Rode ISSO NA SUA MÁQUINA. A senha é digitada aqui (escondida, sem eco no terminal)
e nunca é salva, enviada ou registrada em lugar nenhum — só fica na memória
deste processo enquanto ele roda.

Uso:
    pip install oracledb
    python scripts/explore_gold.py

Ele vai pedir:
  - Caminho do arquivo do wallet (o .zip, ex: C:\\Users\\Andre_Aragon\\Downloads\\Wallet_bronkalertdb.zip)
  - Usuário (padrão: gold_bka)
  - Senha (oculta)
  - Nome do serviço (padrão: bronkalertdb_high)

E imprime, para cada view do Gold: as colunas e até 3 linhas de exemplo.
Copie SÓ a saída (texto) e me envie — ela não contém sua senha.
"""
import getpass
import sys
import tempfile
import zipfile
from pathlib import Path

import oracledb

VIEWS = [
    "VW_ALERTA_RISCO_SEMANAL",
    "VW_CASOS_DIARIOS",
    "VW_CASOS_SEMANAIS",
    "VW_COMPARATIVO_DIAGNOSTICO_MENSAL",
    "VW_HOSPITAL_DESEMPENHO",
    "VW_PERFIL_PACIENTE",
    "VW_PERFIL_PACIENTE_MENSAL",
    "VW_SAZONALIDADE_MENSAL",
]


def main():
    wallet_path = input("Caminho do wallet (.zip ou pasta já extraída): ").strip().strip('"')
    user = input("Usuário [gold_bka]: ").strip() or "gold_bka"
    password = getpass.getpass("Senha do banco (não aparece na tela): ")
    service = input("Serviço [bronkalertdb_high]: ").strip() or "bronkalertdb_high"

    wallet_dir = wallet_path
    if wallet_path.lower().endswith(".zip"):
        wallet_dir = str(Path(tempfile.mkdtemp(prefix="bronqalert_wallet_")))
        print(f"Extraindo wallet em pasta temporária: {wallet_dir}", file=sys.stderr)
        with zipfile.ZipFile(wallet_path) as zf:
            zf.extractall(wallet_dir)

    print("\nConectando (auto-login wallet, sem senha de wallet)...", file=sys.stderr)
    try:
        connection = oracledb.connect(
            user=user,
            password=password,
            dsn=service,
            config_dir=wallet_dir,
        )
    except Exception as e1:
        print(f"[auto-login falhou: {e1}]\nTentando com senha do wallet...", file=sys.stderr)
        wallet_password = getpass.getpass(
            "Senha do wallet — a que você definiu ao baixar o wallet no console da Oracle Cloud: "
        )
        try:
            connection = oracledb.connect(
                user=user,
                password=password,
                dsn=service,
                config_dir=wallet_dir,
                wallet_location=wallet_dir,
                wallet_password=wallet_password,
            )
        except Exception as e2:
            print(f"Erro ao conectar: {e2}")
            sys.exit(1)

    print("Conectado!\n" + "=" * 70)
    cursor = connection.cursor()

    for view in VIEWS:
        print(f"\n### {view}")
        try:
            cursor.execute(f"SELECT * FROM {view} FETCH FIRST 3 ROWS ONLY")
            cols = [d[0] for d in cursor.description]
            print("Colunas:", ", ".join(cols))
            for row in cursor.fetchall():
                print(" ", row)
        except Exception as e:
            print(f"  [erro ao consultar {view}: {e}]")

    cursor.close()
    connection.close()
    print("\n" + "=" * 70)
    print("Pronto — copie tudo o que apareceu acima (a partir de 'Conectado!') e me envie.")


if __name__ == "__main__":
    main()
