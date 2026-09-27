"""Contract tests and a full-size in-memory rehearsal of the CSV import."""

import csv
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import yaml
import mongomock

from P05_Migration import (
    DEFAULT_SOURCE,
    EXPECTED_ROWS,
    EXPECTED_SHA256,
    HEADERS,
    DataContractError,
    build_document,
    migrate,
    preflight,
    source_rows,
    verify,
)
from P05_Demo_CRUD import run_crud
from P05_Backup import backup_collection, make_key, restore_collection


class MemoryCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, field, direction):
        return iter(sorted(self.documents, key=lambda item: item[field], reverse=direction < 0))


class MemoryCollection:
    """Small PyMongo-facing test double, not a substitute for MongoDB testing."""

    def __init__(self):
        self.documents = {}

    def bulk_write(self, operations, ordered=True):
        for operation in operations:
            self.documents[operation._filter["_id"]] = operation._doc.copy()

    def count_documents(self, query):
        return sum(doc["source_sha256"] == query["source_sha256"] for doc in self.documents.values())

    def find(self, query):
        return MemoryCursor([
            doc for doc in self.documents.values()
            if doc["source_sha256"] == query["source_sha256"]
        ])


def sample_row():
    return dict(zip(HEADERS, (
        "Test Patient", "35", "Female", "O+", "Asthma", "2024-08-14",
        "Test Doctor", "Test Hospital", "Test Insurer", "-12.123456789012345",
        "101", "Urgent", "2024-08-16", "Test Medication", "Normal",
    )))


class MigrationTests(unittest.TestCase):
    def test_document_preserves_decimal_and_negative_amount(self):
        document = build_document(sample_row(), EXPECTED_SHA256, 1)
        self.assertEqual(document["billing_amount"].to_decimal(), Decimal("-12.123456789012345"))
        self.assertEqual(document["admission_date"], "2024-08-14")
        self.assertEqual(document["age"], 35)
        self.assertEqual(document["source_row"], 1)

    def test_row_identity_keeps_source_duplicates(self):
        first = build_document(sample_row(), EXPECTED_SHA256, 1)
        second = build_document(sample_row(), EXPECTED_SHA256, 2)
        self.assertNotEqual(first["_id"], second["_id"])
        self.assertEqual(first["patient_name"], second["patient_name"])

    def test_invalid_date_and_amount_are_rejected(self):
        row = sample_row()
        row["Discharge Date"] = "2024-08-13"
        with self.assertRaises(DataContractError):
            build_document(row, EXPECTED_SHA256, 1)
        row = sample_row()
        row["Billing Amount"] = "NaN"
        with self.assertRaises(DataContractError):
            build_document(row, EXPECTED_SHA256, 1)

    def test_small_import_is_idempotent_and_verifiable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=HEADERS)
                writer.writeheader()
                writer.writerows([sample_row(), sample_row()])
            target = MemoryCollection()
            self.assertEqual(migrate(target, path, EXPECTED_SHA256, batch_size=1), 2)
            self.assertEqual(migrate(target, path, EXPECTED_SHA256, batch_size=1), 2)
            self.assertEqual(verify(target, path, EXPECTED_SHA256), 2)
            self.assertEqual(len(target.documents), 2)
            target.documents[next(iter(target.documents))]["age"] = 99
            with self.assertRaises(DataContractError):
                verify(target, path, EXPECTED_SHA256)

    def test_all_source_rows_import_twice_without_count_drift(self):
        self.assertEqual(preflight(DEFAULT_SOURCE)["source_rows"], EXPECTED_ROWS)
        target = MemoryCollection()
        self.assertEqual(migrate(target, DEFAULT_SOURCE, EXPECTED_SHA256), EXPECTED_ROWS)
        self.assertEqual(verify(target, DEFAULT_SOURCE, EXPECTED_SHA256), EXPECTED_ROWS)
        self.assertEqual(migrate(target, DEFAULT_SOURCE, EXPECTED_SHA256), EXPECTED_ROWS)
        self.assertEqual(verify(target, DEFAULT_SOURCE, EXPECTED_SHA256), EXPECTED_ROWS)
        self.assertEqual(len(target.documents), EXPECTED_ROWS)

    def test_compose_declares_secret_roles_and_persistent_storage(self):
        compose = yaml.safe_load((Path(__file__).resolve().parent / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertIn("mongo_data", compose["volumes"])
        self.assertEqual(compose["services"]["migrator"]["depends_on"]["mongo"]["condition"], "service_healthy")
        self.assertTrue(compose["services"]["migrator"]["read_only"])
        self.assertEqual(len(compose["secrets"]), 3)
        self.assertEqual(compose["services"]["mongo"]["ports"], ["127.0.0.1:27017:27017"])

    def test_synthetic_crud_cleans_up_after_itself(self):
        collection = mongomock.MongoClient().p05_medical.demo_crud
        self.assertEqual(run_crud(collection), {
            "create": True, "read": True, "update": True, "delete": True,
        })
        self.assertEqual(collection.count_documents({}), 0)

    def test_encrypted_backup_restores_complete_source(self):
        target = MemoryCollection()
        migrate(target, DEFAULT_SOURCE, EXPECTED_SHA256)
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key.txt"
            archive = Path(directory) / "admissions.fernet"
            self.assertTrue(make_key(key))
            self.assertFalse(make_key(key))
            self.assertEqual(backup_collection(target, archive, key)["rows"], EXPECTED_ROWS)
            self.assertNotIn(b"Test Patient", archive.read_bytes())
            restored = MemoryCollection()
            self.assertEqual(restore_collection(restored, archive, key), EXPECTED_ROWS)
            self.assertEqual(len(restored.documents), EXPECTED_ROWS)
            tampered = bytearray(archive.read_bytes())
            tampered[-10] ^= 1
            archive.write_bytes(tampered)
            with self.assertRaises(DataContractError):
                restore_collection(MemoryCollection(), archive, key)


if __name__ == "__main__":
    unittest.main()
