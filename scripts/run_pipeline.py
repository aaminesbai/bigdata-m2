"""Run the complete CHU ingestion and transformation pipeline."""

from __future__ import annotations

import argparse
import contextvars
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

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

try:
    from .copy_to_lake import copy_to_lake
except ImportError:
    from copy_to_lake import copy_to_lake


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STAGES = ("collecte", "bronze", "silver", "gold")
LAYER_TABLES = {
    "bronze": ("patient", "sejour", "diagnostic", "monitoring", "service", "cim10"),
    "silver": (
        "dim_patient",
        "dim_service",
        "dim_cim10",
        "fact_sejour",
        "fact_diag",
        "fact_monitoring",
    ),
    "gold": (
        "dms_par_service",
        "activite_urgences_par_jour",
        "taux_readmission_30_jours",
        "alertes_constantes_par_jour",
        "prevalence_par_pathologie",
        "distribution_cohorte_age_sexe",
    ),
}

RUN_ID = contextvars.ContextVar("run_id", default="-")
STAGE = contextvars.ContextVar("stage", default="initialisation")
LOGGER = logging.getLogger("chu_pipeline")


class PipelineLockError(RuntimeError):
    """Raised when another pipeline process already owns the lock."""


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = RUN_ID.get()
        record.stage = STAGE.get()
        return True


class UTCFormatter(logging.Formatter):
    converter = time.gmtime


class PipelineLock:
    """Cross-platform process lock released automatically when the process exits."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"1")
            self.handle.flush()
        self.handle.seek(0)

        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            self.handle = None
            raise PipelineLockError(
                "Another pipeline execution is already running."
            ) from error

        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.handle is None:
            return

        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def environment_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run collection, Bronze, Silver and Gold in order."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT / "source-filestorage",
    )
    parser.add_argument(
        "--lake",
        type=Path,
        default=PROJECT_ROOT / "lake",
    )
    parser.add_argument(
        "--sql-dir",
        type=Path,
        default=PROJECT_ROOT / "sql",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=PROJECT_ROOT / "logs",
    )
    parser.add_argument(
        "--start-at",
        choices=STAGES,
        default="collecte",
        help="resume the pipeline from this stage",
    )
    parser.add_argument("--host", default=os.getenv("CLICKHOUSE_HOST", "localhost"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CLICKHOUSE_PORT", "8123")),
    )
    parser.add_argument("--user", default=os.getenv("CLICKHOUSE_USER", "admin"))
    parser.add_argument(
        "--password",
        default=os.getenv("CLICKHOUSE_PASSWORD", "clickhouse"),
    )
    parser.add_argument(
        "--secure",
        action="store_true",
        default=environment_bool("CLICKHOUSE_SECURE"),
    )
    parser.add_argument(
        "--connection-attempts",
        type=int,
        default=int(os.getenv("CLICKHOUSE_CONNECTION_ATTEMPTS", "6")),
    )
    parser.add_argument(
        "--connection-delay",
        type=float,
        default=float(os.getenv("CLICKHOUSE_CONNECTION_DELAY", "10")),
    )
    return parser.parse_args()


def configure_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "pipeline.log"
    formatter = UTCFormatter(
        "%(asctime)s level=%(levelname)s run_id=%(run_id)s "
        "stage=%(stage)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    context_filter = ContextFilter()

    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    LOGGER.propagate = False

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(context_filter)

    rotating_file = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    rotating_file.setFormatter(formatter)
    rotating_file.addFilter(context_filter)

    LOGGER.addHandler(console)
    LOGGER.addHandler(rotating_file)

    copy_logger = logging.getLogger("copy_to_lake")
    copy_logger.setLevel(logging.INFO)
    copy_logger.handlers.clear()
    copy_logger.propagate = False
    copy_logger.addHandler(console)
    copy_logger.addHandler(rotating_file)

    return log_path


def validate_inputs(args: argparse.Namespace, selected_stages: tuple[str, ...]) -> None:
    if "collecte" in selected_stages and not args.source.resolve().is_dir():
        raise FileNotFoundError(f"Source directory not found: {args.source.resolve()}")
    if "collecte" not in selected_stages and "bronze" in selected_stages:
        if not args.lake.resolve().is_dir():
            raise FileNotFoundError(f"Lake directory not found: {args.lake.resolve()}")

    for stage in selected_stages:
        if stage == "collecte":
            continue
        sql_path = args.sql_dir.resolve() / f"{stage}.sql"
        if not sql_path.is_file():
            raise FileNotFoundError(f"SQL file not found: {sql_path}")


def create_clickhouse_client(args: argparse.Namespace):
    if args.connection_attempts < 1:
        raise ValueError("--connection-attempts must be at least 1")
    if args.connection_delay < 0:
        raise ValueError("--connection-delay cannot be negative")

    last_error = None
    for attempt in range(1, args.connection_attempts + 1):
        client = None
        try:
            client = clickhouse_connect.get_client(
                host=args.host,
                port=args.port,
                username=args.user,
                password=args.password,
                secure=args.secure,
                connect_timeout=5,
            )
            version = client.command("SELECT version()")
            LOGGER.info(
                "Connected to ClickHouse version=%s host=%s port=%d",
                version,
                args.host,
                args.port,
            )
            return client
        except Exception as error:
            last_error = error
            if client is not None:
                client.close()
            if attempt == args.connection_attempts:
                break
            LOGGER.warning(
                "ClickHouse unavailable attempt=%d/%d retry_in_seconds=%s error=%s",
                attempt,
                args.connection_attempts,
                args.connection_delay,
                error,
            )
            time.sleep(args.connection_delay)

    raise ConnectionError(
        f"Cannot connect to ClickHouse after {args.connection_attempts} attempts: "
        f"{last_error}"
    )


def ensure_audit_schema(client) -> None:
    client.command("CREATE DATABASE IF NOT EXISTS audit")
    client.command(
        """
        CREATE TABLE IF NOT EXISTS audit.pipeline_runs (
            run_id String,
            started_at DateTime64(3, 'UTC'),
            finished_at Nullable(DateTime64(3, 'UTC')),
            status LowCardinality(String),
            start_stage LowCardinality(String),
            failed_stage Nullable(String),
            copied_files UInt64,
            skipped_partitions UInt64,
            error_message String,
            updated_at DateTime64(3, 'UTC'),
            version UInt64
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY run_id
        """
    )
    client.command(
        """
        CREATE TABLE IF NOT EXISTS audit.pipeline_stages (
            run_id String,
            stage LowCardinality(String),
            started_at DateTime64(3, 'UTC'),
            finished_at Nullable(DateTime64(3, 'UTC')),
            status LowCardinality(String),
            details String,
            updated_at DateTime64(3, 'UTC'),
            version UInt64
        )
        ENGINE = ReplacingMergeTree(version)
        ORDER BY (run_id, stage)
        """
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def record_run(
    client,
    *,
    run_id: str,
    started_at: datetime,
    finished_at: datetime | None,
    status: str,
    start_stage: str,
    failed_stage: str | None,
    copied_files: int,
    skipped_partitions: int,
    error_message: str,
) -> None:
    updated_at = utc_now()
    client.insert(
        "audit.pipeline_runs",
        [[
            run_id,
            started_at,
            finished_at,
            status,
            start_stage,
            failed_stage,
            copied_files,
            skipped_partitions,
            error_message[:4000],
            updated_at,
            time.time_ns(),
        ]],
        column_names=[
            "run_id",
            "started_at",
            "finished_at",
            "status",
            "start_stage",
            "failed_stage",
            "copied_files",
            "skipped_partitions",
            "error_message",
            "updated_at",
            "version",
        ],
    )


def record_stage(
    client,
    *,
    run_id: str,
    stage: str,
    started_at: datetime,
    finished_at: datetime | None,
    status: str,
    details: dict,
) -> None:
    updated_at = utc_now()
    client.insert(
        "audit.pipeline_stages",
        [[
            run_id,
            stage,
            started_at,
            finished_at,
            status,
            json.dumps(details, ensure_ascii=True, sort_keys=True),
            updated_at,
            time.time_ns(),
        ]],
        column_names=[
            "run_id",
            "stage",
            "started_at",
            "finished_at",
            "status",
            "details",
            "updated_at",
            "version",
        ],
    )


def statement_label(statement: str) -> str:
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return " ".join(stripped.split()[:5])
    return "SQL statement"


def execute_sql_file(client, sql_path: Path) -> int:
    statements = [
        statement
        for statement in sqlparse.split(sql_path.read_text(encoding="utf-8"))
        if statement.strip()
    ]
    if not statements:
        raise ValueError(f"No SQL statement found in {sql_path}")

    for index, statement in enumerate(statements, start=1):
        LOGGER.info(
            "Executing SQL statement=%d/%d label=%s",
            index,
            len(statements),
            statement_label(statement),
        )
        client.command(statement)
    return len(statements)


def layer_row_counts(client, database: str) -> dict[str, int]:
    expected_tables = LAYER_TABLES[database]
    result = client.query(
        "SELECT table, ifNull(total_rows, 0) "
        "FROM system.tables "
        f"WHERE database = '{database}' "
        "ORDER BY table"
    )
    available = {table: int(rows) for table, rows in result.result_rows}
    return {table: available.get(table, 0) for table in expected_tables}


def run_stage(client, args: argparse.Namespace, stage: str) -> dict:
    if stage == "collecte":
        copied, skipped = copy_to_lake(args.source.resolve(), args.lake.resolve())
        return {"copied_files": copied, "skipped_partitions": skipped}

    sql_path = args.sql_dir.resolve() / f"{stage}.sql"
    statement_count = execute_sql_file(client, sql_path)
    return {
        "sql_statements": statement_count,
        "table_rows": layer_row_counts(client, stage),
    }


def run_pipeline(args: argparse.Namespace) -> int:
    selected_stages = STAGES[STAGES.index(args.start_at) :]
    run_id = str(uuid4())
    RUN_ID.set(run_id)
    started_at = utc_now()
    copied_files = 0
    skipped_partitions = 0
    failed_stage = None
    client = None

    LOGGER.info(
        "Pipeline started start_stage=%s stages=%s",
        args.start_at,
        ",".join(selected_stages),
    )

    try:
        validate_inputs(args, selected_stages)
        client = create_clickhouse_client(args)
        ensure_audit_schema(client)
        record_run(
            client,
            run_id=run_id,
            started_at=started_at,
            finished_at=None,
            status="running",
            start_stage=args.start_at,
            failed_stage=None,
            copied_files=0,
            skipped_partitions=0,
            error_message="",
        )

        for stage in selected_stages:
            failed_stage = stage
            STAGE.set(stage)
            stage_started_at = utc_now()
            record_stage(
                client,
                run_id=run_id,
                stage=stage,
                started_at=stage_started_at,
                finished_at=None,
                status="running",
                details={},
            )
            LOGGER.info("Stage started")

            try:
                details = run_stage(client, args, stage)
            except Exception as error:
                finished_at = utc_now()
                failure_details = {"error": str(error)[:4000]}
                record_stage(
                    client,
                    run_id=run_id,
                    stage=stage,
                    started_at=stage_started_at,
                    finished_at=finished_at,
                    status="failed",
                    details=failure_details,
                )
                LOGGER.exception("Stage failed")
                raise

            if stage == "collecte":
                copied_files = int(details["copied_files"])
                skipped_partitions = int(details["skipped_partitions"])

            stage_finished_at = utc_now()
            record_stage(
                client,
                run_id=run_id,
                stage=stage,
                started_at=stage_started_at,
                finished_at=stage_finished_at,
                status="success",
                details=details,
            )
            LOGGER.info(
                "Stage completed duration_seconds=%.3f details=%s",
                (stage_finished_at - stage_started_at).total_seconds(),
                json.dumps(details, ensure_ascii=True, sort_keys=True),
            )

        finished_at = utc_now()
        failed_stage = None
        STAGE.set("finalisation")
        record_run(
            client,
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            status="success",
            start_stage=args.start_at,
            failed_stage=None,
            copied_files=copied_files,
            skipped_partitions=skipped_partitions,
            error_message="",
        )
        LOGGER.info(
            "Pipeline completed duration_seconds=%.3f",
            (finished_at - started_at).total_seconds(),
        )
        return 0
    except Exception as error:
        STAGE.set("finalisation")
        if client is not None:
            try:
                record_run(
                    client,
                    run_id=run_id,
                    started_at=started_at,
                    finished_at=utc_now(),
                    status="failed",
                    start_stage=args.start_at,
                    failed_stage=failed_stage,
                    copied_files=copied_files,
                    skipped_partitions=skipped_partitions,
                    error_message=str(error),
                )
            except Exception:
                LOGGER.exception("Could not persist the failed run in ClickHouse audit")
        LOGGER.error("Pipeline failed failed_stage=%s error=%s", failed_stage, error)
        return 1
    finally:
        if client is not None:
            client.close()


def main() -> int:
    args = parse_args()
    log_path = configure_logging(args.log_dir.resolve())
    LOGGER.info("Local log initialized path=%s", log_path)

    lock_path = args.log_dir.resolve() / "pipeline.lock"
    try:
        with PipelineLock(lock_path):
            return run_pipeline(args)
    except PipelineLockError as error:
        LOGGER.error("%s", error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
