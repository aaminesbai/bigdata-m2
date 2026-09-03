from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import copy_to_lake as copy_module
from scripts.run_pipeline import PipelineLock, PipelineLockError


class CopyToLakeTests(unittest.TestCase):
    def write_patient_source(self, source_root: Path, partition: str) -> None:
        source_file = source_root / "patients" / partition / "patients.csv"
        source_file.parent.mkdir(parents=True)
        with source_file.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=(
                    "patient_id",
                    "nir",
                    "nom",
                    "prenom",
                    "birth_date",
                    "sex",
                    "region_code",
                ),
            )
            writer.writeheader()
            writer.writerow(
                {
                    "patient_id": "IPP0000001",
                    "nir": "1234567890123",
                    "nom": "Dupont",
                    "prenom": "Alice",
                    "birth_date": "1980-01-02",
                    "sex": "F",
                    "region_code": "75",
                }
            )

    def test_copy_is_sanitized_and_incremental(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            lake = root / "lake"
            self.write_patient_source(source, "2026-08-01")

            copied, skipped = copy_module.copy_to_lake(source, lake)
            self.assertEqual((copied, skipped), (1, 0))

            destination = lake / "patients" / "2026-08-01" / "patients.csv"
            with destination.open("r", encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(
                    reader.fieldnames,
                    ["patient_id", "birth_date", "sex", "region_code"],
                )
                self.assertEqual(len(list(reader)), 1)

            copied, skipped = copy_module.copy_to_lake(source, lake)
            self.assertEqual((copied, skipped), (0, 1))

    def test_failed_copy_does_not_publish_partial_partition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            lake = root / "lake"
            self.write_patient_source(source, "2026-08-01")

            with patch.object(
                copy_module,
                "copy_sanitized_patients",
                side_effect=OSError("simulated copy failure"),
            ):
                with self.assertRaises(OSError):
                    copy_module.copy_to_lake(source, lake)

            partition = lake / "patients" / "2026-08-01"
            self.assertFalse(partition.exists())


class PipelineLockTests(unittest.TestCase):
    def test_second_pipeline_cannot_acquire_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "pipeline.lock"

            with PipelineLock(lock_path):
                with self.assertRaises(PipelineLockError):
                    with PipelineLock(lock_path):
                        self.fail("The second lock should not be acquired")

            with PipelineLock(lock_path):
                pass


if __name__ == "__main__":
    unittest.main()
