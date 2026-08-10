"""Diagnostico de conexao Oracle - executa fora do framework para isolar o problema.

Ajuste HOST/PORT/SERVICE/KEYRING_SERVICE/USERNAMES abaixo conforme o ambiente
que o technicalcatalogpipeline vai usar para extrair metadados.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    package_parent = Path(__file__).resolve().parents[2]
    if str(package_parent) not in sys.path:
        sys.path.insert(0, str(package_parent))

HOST = "cpscanx.sefaz-ce.gov.br"
PORT = 1521
SERVICE = "EXAPROD"
KEYRING_SERVICE = "dataquality-oracle"

# Testa ambas as grafias do username
USERNAMES = ["sefaz2\\49756615", "SEFAZ2\\49756615"]


def _try_connect_direct(user: str, password: str, label: str) -> bool:
    try:
        import oracledb
        conn = oracledb.connect(user=user, password=password, host=HOST, port=PORT, service_name=SERVICE)
        row = conn.cursor().execute("SELECT USER FROM DUAL").fetchone()
        print(f"    OK ({label}) — USER no banco: {row[0]}")
        conn.close()
        return True
    except Exception as exc:
        print(f"    FALHOU ({label}): {exc}")
        return False


def _try_connect_sqlalchemy(user: str, password: str, label: str) -> bool:
    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(
            "oracle+oracledb://",
            connect_args={"user": user, "password": password, "host": HOST, "port": PORT, "service_name": SERVICE},
        )
        with engine.connect() as conn:
            result = conn.execute(text("SELECT USER FROM DUAL")).scalar()
            print(f"    OK ({label}) — USER no banco: {result}")
        engine.dispose()
        return True
    except Exception as exc:
        print(f"    FALHOU ({label}): {exc}")
        return False


def main() -> None:
    import keyring

    print("=" * 60)
    print("DIAGNÓSTICO DE CONEXÃO ORACLE")
    print("=" * 60)

    # ── 1. Keyring ─────────────────────────────────────────────────────────
    print("\n[1] Verificando keyring")
    found_passwords: dict[str, str] = {}
    for uname in USERNAMES:
        pwd = keyring.get_password(KEYRING_SERVICE, uname)
        if pwd:
            masked = pwd[:2] + "*" * (len(pwd) - 2) if len(pwd) > 2 else "**"
            print(f"    ENCONTRADA  username={uname!r}  senha={masked}  len={len(pwd)}")
            found_passwords[uname] = pwd
        else:
            print(f"    não encontrada  username={uname!r}")

    if not found_passwords:
        print("\n    NENHUMA SENHA NO KEYRING. Grave com:")
        print("    python scripts/store_keyring_secret.py --service dataquality-oracle --username 'sefaz2\\\\49756615' --show-check")
        sys.exit(1)

    # ── 2. Conexão direta oracledb (sem SQLAlchemy) ────────────────────────
    print("\n[2] Conexão direta oracledb.connect (sem SQLAlchemy)")
    for uname, pwd in found_passwords.items():
        _try_connect_direct(uname, pwd, f"user={uname!r}")

    # ── 3. Conexão via SQLAlchemy + connect_args ───────────────────────────
    print("\n[3] SQLAlchemy oracle+oracledb:// com connect_args separados")
    for uname, pwd in found_passwords.items():
        _try_connect_sqlalchemy(uname, pwd, f"user={uname!r}")

    # ── 4. Autenticação externa (OS) — sem senha ───────────────────────────
    print("\n[4] Autenticação externa / OS (sem senha)")
    for uname in USERNAMES:
        try:
            import oracledb
            conn = oracledb.connect(user=uname, externalauth=True, host=HOST, port=PORT, service_name=SERVICE)
            row = conn.cursor().execute("SELECT USER FROM DUAL").fetchone()
            print(f"    OK (OS auth, user={uname!r}) — USER no banco: {row[0]}")
            conn.close()
        except Exception as exc:
            print(f"    FALHOU (OS auth, user={uname!r}): {exc}")

    print("\n[5] Autenticação externa via DSN")
    for uname in USERNAMES:
        try:
            import oracledb
            dsn = f"{HOST}:{PORT}/{SERVICE}"
            conn = oracledb.connect(user=uname, externalauth=True, dsn=dsn)
            row = conn.cursor().execute("SELECT USER FROM DUAL").fetchone()
            print(f"    OK (OS auth DSN, user={uname!r}) — USER no banco: {row[0]}")
            conn.close()
        except Exception as exc:
            print(f"    FALHOU (OS auth DSN, user={uname!r}): {exc}")

    # ── 6. Thick mode — tenta localizar Oracle Client ─────────────────────
    print("\n[6] Thick mode (Oracle Client)")
    print("    Procurando Oracle Client no PATH e locais comuns...")
    import os, glob as _glob
    candidates: list[str] = []
    for env_var in ("ORACLE_HOME", "ORACLE_BASE", "PATH"):
        for part in os.environ.get(env_var, "").split(os.pathsep):
            if "oracle" in part.lower() or "instantclient" in part.lower():
                candidates.append(part)
    for pattern in [
        r"C:\oracle\*\bin",
        r"C:\app\*\product\*\client_*\bin",
        r"C:\app\*\product\*\dbhome_*\bin",
        r"C:\instantclient*",
        r"C:\oracle\instantclient*",
    ]:
        candidates.extend(_glob.glob(pattern))
    seen = list(dict.fromkeys(candidates))  # dedup mantendo ordem
    if seen:
        print(f"    Candidatos encontrados: {seen}")
    else:
        print("    Nenhum Oracle Client encontrado no sistema.")
        print("    Instale Oracle Instant Client: https://www.oracle.com/database/technologies/instant-client/winx64-64-downloads.html")
        print("    E adicione a pasta ao PATH.")

    # Testa thick mode com OS auth (sem senha)
    import oracledb
    for lib_dir in (seen or [None]):
        label = lib_dir or "PATH padrão"
        try:
            if lib_dir:
                oracledb.init_oracle_client(lib_dir=lib_dir)
            else:
                oracledb.init_oracle_client()
            # OS auth: "/" como username significa "usar credencial Windows atual"
            dsn = f"{HOST}:{PORT}/{SERVICE}"
            conn = oracledb.connect(user="/", externalauth=True, dsn=dsn)
            row = conn.cursor().execute("SELECT USER FROM DUAL").fetchone()
            print(f"    OK (thick OS auth, lib={label!r}) — USER no banco: {row[0]}")
            conn.close()
            break
        except Exception as exc:
            print(f"    FALHOU (thick OS auth, lib={label!r}): {exc}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
