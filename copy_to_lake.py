"""Copy the daily CHU source files to the local raw Lake directory."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


DATASETS = (
    "diagnostics",
    "monitoring",
    "patients",
    "referentiels",
    "sejours",
)


def is_up_to_date(source: Path, destination: Path) -> bool:
    if not destination.is_file():
        return False

    source_stat = source.stat()
    destination_stat = destination.stat()
    return (
        source_stat.st_size == destination_stat.st_size
        and source_stat.st_mtime_ns == destination_stat.st_mtime_ns
    )


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
                        
            if is_up_to_date(source_file, destination_file):
                skipped += 1
                continue

            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination_file)
            copied += 1
            print(f"COPIED: {relative_path}")

    return copied, skipped


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Copy source-filestorage datasets to Bronze while preserving dates."
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
