"""Copy the daily CHU source files to the local raw Lake directory."""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path


DATASETS = (
    "diagnostics",
    "monitoring",
    "patients",
    "referentiels",
    "sejours",
)
SENSITIVE_PATIENT_COLUMNS = {"nir", "nom", "prenom"}


def is_up_to_date(source: Path, destination: Path) -> bool:
    if not destination.is_file():
        return False

    source_stat = source.stat()
    destination_stat = destination.stat()
    return (
        source_stat.st_size == destination_stat.st_size
        and source_stat.st_mtime_ns == destination_stat.st_mtime_ns
    )


def is_sanitized_patient_file(destination: Path) -> bool:
    if not destination.is_file():
        return False

    with destination.open("r", encoding="utf-8-sig", newline="") as file:
        header = next(csv.reader(file), [])
    return SENSITIVE_PATIENT_COLUMNS.isdisjoint(header)


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
                writer.writerow({column: row[column] for column in output_columns})

        temporary_destination.replace(destination)
        shutil.copystat(source, destination)
    finally:
        temporary_destination.unlink(missing_ok=True)


def copy_to_lake(source_root: Path, lake_root: Path) -> tuple[int, int]:
    copied = 0
    skipped = 0

    if not source_root.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source_root}")

    lake_root.mkdir(parents=True, exist_ok=True)

    for dataset in DATASETS:
        source_dataset = source_root / dataset
        if not source_dataset.is_dir():
            print(f"WARNING: missing dataset: {source_dataset}")
            continue

        for source_file in sorted(path for path in source_dataset.rglob("*") if path.is_file()):
            relative_path = source_file.relative_to(source_root)
            destination_file = lake_root / relative_path
                        
            is_patient_csv = dataset == "patients" and source_file.suffix.lower() == ".csv"
            destination_is_current = is_up_to_date(source_file, destination_file)
            if is_patient_csv:
                destination_is_current = (
                    is_sanitized_patient_file(destination_file)
                    and source_file.stat().st_mtime_ns
                    == destination_file.stat().st_mtime_ns
                )

            if destination_is_current:
                skipped += 1
                continue

            destination_file.parent.mkdir(parents=True, exist_ok=True)
            if is_patient_csv:
                copy_sanitized_patients(source_file, destination_file)
            else:
                shutil.copy2(source_file, destination_file)
            copied += 1
            print(f"COPIED: {relative_path}")

    return copied, skipped


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    
    try:
        copied, skipped = copy_to_lake(
            args.source.resolve(), args.destination.resolve()
        )
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1

    print(f"Completed: {copied} copied, {skipped} already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
