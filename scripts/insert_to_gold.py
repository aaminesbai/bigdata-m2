"""Create and populate the ClickHouse Gold layer from gold.sql."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import clickhouse_connect
    import sqlparse
except ImportError as error:
    missing_package = error.name or "a required package"
    print(
        f"ERROR: missing {missing_package}. "
        "Install dependencies with: python -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1) from error


TABLES = (
    "activite_urgences_par_jour",
    "alertes_constantes_par_jour",
    "distribution_cohorte_age_sexe",
    "dms_par_service",
    "prevalence_par_pathologie",
    "taux_readmission_30_jours",
)


def environment_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Create and populate the ClickHouse Gold tables."
    )
    parser.add_argument(
        "--sql",
        type=Path,
        default=project_root / "sql" / "gold.sql",
        help="SQL file containing the Gold DDL and INSERT statements",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("CLICKHOUSE_HOST", "localhost"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CLICKHOUSE_PORT", "8123")),
    )
    parser.add_argument(
        "--user",
        default=os.getenv("CLICKHOUSE_USER", "admin"),
    )
    parser.add_argument(
        "--password",
        default=os.getenv("CLICKHOUSE_PASSWORD", "clickhouse"),
    )
    parser.add_argument(
        "--secure",
        action="store_true",
        default=environment_bool("CLICKHOUSE_SECURE"),
    )
    return parser.parse_args()


def create_clickhouse_client(args: argparse.Namespace):
    client = clickhouse_connect.get_client(
        host=args.host,
        port=args.port,
        username=args.user,
        password=args.password,
        secure=args.secure,
    )
    version = client.command("SELECT version()")
    print(f"Connected to ClickHouse {version} at {args.host}:{args.port}.")
    return client


def statement_label(statement: str) -> str:
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return " ".join(stripped.split()[:5])
    return "SQL statement"


def execute_sql_file(client, sql_path: Path) -> int:
    if not sql_path.is_file():
        raise FileNotFoundError(f"SQL file not found: {sql_path}")

    statements = [
        statement
        for statement in sqlparse.split(sql_path.read_text(encoding="utf-8"))
        if statement.strip()
    ]
    if not statements:
        raise ValueError(f"No SQL statement found in {sql_path}")

    print(f"Executing {len(statements)} statements from {sql_path.name}...")
    for index, statement in enumerate(statements, start=1):
        print(f"[{index}/{len(statements)}] {statement_label(statement)}")
        client.command(statement)

    return len(statements)


def print_gold_counts(client) -> None:
    selects = " UNION ALL ".join(
        f"SELECT '{table}' AS table_name, count() AS row_count "
        f"FROM gold.{table}"
        for table in TABLES
    )
    result = client.query(f"SELECT * FROM ({selects}) ORDER BY table_name")

    print("Gold row counts:")
    for table_name, row_count in result.result_rows:
        print(f"  {table_name}: {row_count}")


def main() -> int:
    args = parse_args()
    client = None

    try:
        client = create_clickhouse_client(args)
        statement_count = execute_sql_file(client, args.sql.resolve())
        print_gold_counts(client)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()

    print(f"Completed: {statement_count} SQL statements executed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
