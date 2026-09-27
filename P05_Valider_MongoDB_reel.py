"""Validate real MongoDB access, export, indexes and isolated encrypted recovery."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pymongo import MongoClient
from pymongo.errors import OperationFailure

from P05_Migration import DEFAULT_SOURCE, EXPECTED_SHA256, DataContractError, preflight, verify
from P05_Demo_CRUD import run_crud
from P05_Exporter import export_collection
from P05_Backup import backup_collection, make_key, restore_collection

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("P05_MONGO_HOST", "127.0.0.1")
PORT = int(os.environ.get("P05_MONGO_PORT", "27017"))


def connect(user, secret, auth_db="p05_medical"):
    return MongoClient(
        HOST, PORT, username=user,
        password=(ROOT / "secrets" / secret).read_text().strip(),
        authSource=auth_db, serverSelectionTimeoutMS=10000,
    )


def main():
    result = {"executed_at_utc": datetime.now(timezone.utc).isoformat(), "environment": "real_mongodb", "source": preflight(DEFAULT_SOURCE)}
    reader = connect("p05_reader", "mongo_reader_password.txt")
    writer = connect("p05_ingest", "mongo_ingest_password.txt")
    admin = connect("p05_admin", "mongo_root_password.txt", "admin")
    admin.admin.command("ping")
    isolated = "p05_restore_test_" + uuid4().hex
    try:
        result["mongodb_version"] = admin.server_info()["version"]
        collection = reader.p05_medical.admissions
        result["verified_documents"] = verify(collection, DEFAULT_SOURCE, EXPECTED_SHA256)
        result["total_documents"] = collection.count_documents({})
        assert result["total_documents"] == 55500
        probe = "p05_permission_" + uuid4().hex
        try:
            reader.p05_medical.demo_crud.insert_one({"_id": probe, "synthetic": True})
        except OperationFailure as exc:
            if exc.code != 13:
                raise
            result["reader_write_denied"] = True
        else:
            writer.p05_medical.demo_crud.delete_one({"_id": probe})
            raise AssertionError("Reader unexpectedly has write permission")
        unauthenticated = MongoClient(HOST, PORT, serverSelectionTimeoutMS=10000)
        try:
            unauthenticated.p05_medical.admissions.count_documents({})
        except OperationFailure as exc:
            if exc.code != 13:
                raise
            result["unauthenticated_read_denied"] = True
        else:
            raise AssertionError("Unauthenticated read unexpectedly permitted")
        finally:
            unauthenticated.close()
        result["writer_crud"] = run_crud(writer.p05_medical.demo_crud)
        result["indexes"] = sorted(collection.index_information())
        assert {"_id_", "ux_source_row", "ix_admission_date", "ix_condition_admission"} <= set(result["indexes"])
        plan = collection.find({"admission_date": "2024-01-01"}).explain()
        stats = plan["executionStats"]
        result["indexed_query"] = {"returned": stats["nReturned"], "documents_examined": stats["totalDocsExamined"], "keys_examined": stats["totalKeysExamined"], "uses_index": "IXSCAN" in json.dumps(plan["queryPlanner"]["winningPlan"])}
        assert result["indexed_query"]["uses_index"]
        result["exported_rows"] = export_collection(collection, ROOT / "exports" / "healthcare_dataset_export.csv")
        result["export_content_check"] = "passed"
        make_key()
        archive = ROOT / "backups" / ("p05_real_" + uuid4().hex + ".fernet")
        result["backup"] = backup_collection(collection, archive)
        result["backup"]["filename"] = archive.name
        target = admin[isolated].admissions
        assert target.count_documents({}) == 0
        result["restored_documents"] = restore_collection(target, archive)
        result["restore_target"] = isolated
        result["restore_isolation"] = "separate_temporary_database_same_server"
        with tempfile.TemporaryDirectory(prefix="p05_tamper_") as tmp:
            altered = Path(tmp) / "altered.fernet"
            encrypted = bytearray(archive.read_bytes())
            encrypted[len(encrypted) // 2] ^= 1
            altered.write_bytes(encrypted)
            try:
                restore_collection(target, altered)
            except DataContractError:
                result["altered_backup_rejected"] = True
            else:
                raise AssertionError("Altered backup was accepted")
        result["restore_unchanged_after_rejection"] = verify(target, DEFAULT_SOURCE, EXPECTED_SHA256)
        result["source_unchanged_after_tests"] = preflight(DEFAULT_SOURCE)["source_sha256"] == EXPECTED_SHA256
        result["production_verified_after_tests"] = verify(collection, DEFAULT_SOURCE, EXPECTED_SHA256)
        result["status"] = "passed"
    finally:
        # Only this run's uniquely named disposable restore database is removed.
        admin.drop_database(isolated)
        reader.close()
        writer.close()
        admin.close()
    result["temporary_restore_database_removed"] = True
    (ROOT / "P05_Controle_validation_reelle.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
