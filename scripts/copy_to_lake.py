"""Copy the daily CHU source files to the local raw Lake directory."""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import logging
import shutil
from datetime import date
from pathlib import Path


DATASETS = (
    "diagnostics",
    "monitoring",
    "patients",
    "referentiels",
    "sejours",
)
SENSITIVE_PATIENT_COLUMNS = {"nir", "nom", "prenom"}
INCOMPLETE_MARKER = "_INCOMPLETE"
SUCCESS_MARKER = "_SUCCESS"
LOGGER = logging.getLogger(__name__)

# Exercise-only key. In production, load this secret from a secret manager or
# an environment variable and never commit it to the source repository.
PSEUDONYMIZATION_KEY = b"eds-chu-exercice-hmac-key-2026"


def pseudonymize_patient_id(patient_id: str) -> str:
    if not patient_id:
        raise ValueError("Missing patient_id")
    digest = hmac.new(
        PSEUDONYMIZATION_KEY,
        patient_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"P_{digest}"


def copy_sanitized_patients(source: Path, destination: Path) -> None:
    temporary_destination = destination.with_suffix(destination.suffix + ".tmp")

    try:
        with (
            source.open("r", encoding="utf-8-sig", newline="") as source_file,
            temporary_destination.open("w", encoding="utf-8", newline="") as destination_file,
        ):
            reader = csv.DictReader(source_file)
            if reader.fieldnames is None:
                raise ValueError(f"Missing CSV header: {source}")

            missing_columns = SENSITIVE_PATIENT_COLUMNS.difference(reader.fieldnames)
            if missing_columns:
                missing = ", ".join(sorted(missing_columns))
                raise ValueError(f"Missing expected columns in {source}: {missing}")

            output_columns = [
                column
                for column in reader.fieldnames
                if column not in SENSITIVE_PATIENT_COLUMNS
            ]
            writer = csv.DictWriter(destination_file, fieldnames=output_columns)
            writer.writeheader()
            for row in reader:
                output_row = {column: row[column] for column in output_columns}
                output_row["patient_id"] = pseudonymize_patient_id(row["patient_id"])
                writer.writerow(output_row)

        temporary_destination.replace(destination)
        shutil.copystat(source, destination)
    finally:
        temporary_destination.unlink(missing_ok=True)


def copy_pseudonymized_sejours(source: Path, destination: Path) -> None:
    temporary_destination = destination.with_suffix(destination.suffix + ".tmp")

    try:
        with (
            source.open("r", encoding="utf-8-sig", newline="") as source_file,
            temporary_destination.open("w", encoding="utf-8", newline="") as destination_file,
        ):
            reader = csv.DictReader(source_file)
            if reader.fieldnames is None or "patient_id" not in reader.fieldnames:
                raise ValueError(f"Missing patient_id column in {source}")

            writer = csv.DictWriter(destination_file, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                row["patient_id"] = pseudonymize_patient_id(row["patient_id"])
                writer.writerow(row)

        temporary_destination.replace(destination)
        shutil.copystat(source, destination)
    finally:
        temporary_destination.unlink(missing_ok=True)


def copy_to_lake(source_root: Path, lake_root: Path) -> tuple[int, int]:
    copied = 0
    skipped_dates = 0

    if not source_root.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source_root}")

    lake_root.mkdir(parents=True, exist_ok=True)

    for dataset in DATASETS:
        source_dataset = source_root / dataset

        if not source_dataset.is_dir():
            LOGGER.warning("Missing dataset: %s", source_dataset)
            continue

        source_dates = sorted(path for path in source_dataset.iterdir() if path.is_dir())
        for source_date in source_dates:
            try:
                partition_date = date.fromisoformat(source_date.name)
            except ValueError:
                LOGGER.warning("Invalid date directory: %s", source_date)
                continue

            destination_dataset = lake_root / dataset
            destination_date = destination_dataset / partition_date.isoformat()
            if destination_date.is_dir():
                success_marker = destination_date / SUCCESS_MARKER
                incomplete_marker = destination_date / INCOMPLETE_MARKER

                if success_marker.is_file():
                    skipped_dates += 1
                    continue

                if not incomplete_marker.exists():
                    # Partitions created before markers were introduced are complete.
                    success_marker.touch()
                    skipped_dates += 1
                    LOGGER.info(
                        "Marked legacy partition as complete: %s/%s",
                        dataset,
                        source_date.name,
                    )
                    continue

                LOGGER.warning(
                    "Removing incomplete partition before retry: %s/%s",
                    dataset,
                    source_date.name,
                )
                shutil.rmtree(destination_date)

            source_files = sorted(path for path in source_date.rglob("*") if path.is_file())
            if not source_files:
                LOGGER.warning("Empty source partition: %s", source_date)
                continue

            destination_dataset.mkdir(parents=True, exist_ok=True)
            destination_date.mkdir()
            incomplete_marker = destination_date / INCOMPLETE_MARKER
            incomplete_marker.touch()

            try:
                for source_file in source_files:
                    relative_path = source_file.relative_to(source_date)
                    destination_file = destination_date / relative_path
                    destination_file.parent.mkdir(parents=True, exist_ok=True)

                    if dataset == "patients":
                        copy_sanitized_patients(source_file, destination_file)
                    elif dataset == "sejours":
                        copy_pseudonymized_sejours(source_file, destination_file)
                    else:
                        shutil.copy2(source_file, destination_file)

                incomplete_marker.unlink()
                (destination_date / SUCCESS_MARKER).touch()
            except Exception:
                try:
                    shutil.rmtree(destination_date)
                except OSError:
                    LOGGER.exception(
                        "Could not remove incomplete partition: %s",
                        destination_date,
                    )
                raise

            copied += len(source_files)
            LOGGER.info(
                "Copied partition %s/%s (%d file(s))",
                dataset,
                source_date.name,
                len(source_files),
            )

    return copied, skipped_dates


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Copy source-filestorage datasets to the Lake while preserving dates."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=project_root / "source-filestorage",
        help="source filestorage directory",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=project_root / "lake",
        help="raw Lake destination directory",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="delete and rebuild the Lake (required after changing the HMAC key)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        source = args.source.resolve()
        destination = args.destination.resolve()
        if args.rebuild and destination.exists():
            if (
                destination == source
                or destination in source.parents
                or source in destination.parents
            ):
                raise ValueError("Refusing to delete the source directory")
            shutil.rmtree(destination)
        copied, skipped_dates = copy_to_lake(source, destination)
    except (OSError, ValueError) as error:
        LOGGER.error("%s", error)
        return 1

    LOGGER.info(
        "Completed: %d files copied, %d dates skipped.",
        copied,
        skipped_dates,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
