"""Validate and migrate the supplied CSV to MongoDB without losing source rows."""

import argparse
import csv
import hashlib
import json
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bson.decimal128 import Decimal128
from pymongo import MongoClient, ReplaceOne

from P05_Profilage_source import DEFAULT_SOURCE, profile_csv


PROJECT_DIR = Path(__file__).resolve().parent
EXPECTED_SHA256 = "d8c23c7dddaf0e1f5daf26182eb908c219d28a63bb574377c3ba6cb09be8a342"
EXPECTED_ROWS = 55500
EXPECTED_DUPLICATE_EXTRA_ROWS = 534
EXPECTED_NEGATIVE_AMOUNTS = 108
HEADERS = (
    "Name", "Age", "Gender", "Blood Type", "Medical Condition",
    "Date of Admission", "Doctor", "Hospital", "Insurance Provider",
    "Billing Amount", "Room Number", "Admission Type", "Discharge Date",
    "Medication", "Test Results",
)
TEXT_FIELDS = {
    "Name": "patient_name",
    "Gender": "gender",
    "Blood Type": "blood_type",
    "Medical Condition": "medical_condition",
    "Doctor": "doctor",
    "Hospital": "hospital",
    "Insurance Provider": "insurance_provider",
    "Admission Type": "admission_type",
    "Medication": "medication",
    "Test Results": "test_results",
}


class DataContractError(Exception):
    """The input or migrated data does not match the documented contract."""


def source_rows(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        header = tuple(next(reader))
        if header != HEADERS:
            raise DataContractError("CSV headers differ from the approved 15-column source")
        for source_row, values in enumerate(reader, start=1):
            if len(values) != len(HEADERS):
                raise DataContractError(f"CSV row {source_row} has an unexpected width")
            if any(not value.strip() for value in values):
                raise DataContractError(f"CSV row {source_row} has a blank field")
            yield source_row, dict(zip(HEADERS, values))


def strict_date(raw, source_row, field):
    try:
        parsed = date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise DataContractError(f"invalid {field} at source row {source_row}") from exc
    if parsed.isoformat() != raw.strip():
        raise DataContractError(f"non-ISO {field} at source row {source_row}")
    return parsed.isoformat()


def build_document(raw, source_sha256, source_row):
    try:
        age = int(raw["Age"].strip())
        room_number = int(raw["Room Number"].strip())
    except ValueError as exc:
        raise DataContractError(f"invalid integer at source row {source_row}") from exc
    try:
        amount = Decimal(raw["Billing Amount"].strip())
        if not amount.is_finite():
            raise InvalidOperation
        billing_amount = Decimal128(raw["Billing Amount"].strip())
        if billing_amount.to_decimal() != amount:
            raise InvalidOperation
    except (InvalidOperation, ValueError) as exc:
        raise DataContractError(f"invalid billing amount at source row {source_row}") from exc
    admission_date = strict_date(raw["Date of Admission"], source_row, "admission date")
    discharge_date = strict_date(raw["Discharge Date"], source_row, "discharge date")
    if discharge_date < admission_date:
        raise DataContractError(f"discharge before admission at source row {source_row}")

    technical_id = hashlib.sha256(
        f"{source_sha256}:{source_row}".encode("ascii")
    ).hexdigest()
    document = {
        "_id": technical_id,
        "source_sha256": source_sha256,
        "source_row": source_row,
        "age": age,
        "room_number": room_number,
        "admission_date": admission_date,
        "discharge_date": discharge_date,
        "billing_amount": billing_amount,
    }
    document.update({target: raw[source] for source, target in TEXT_FIELDS.items()})
    return document


def preflight(path, expected_sha256=EXPECTED_SHA256):
    profile = profile_csv(path)
    shape = profile["shape"]
    if profile["source"]["sha256"] != expected_sha256:
        raise DataContractError("CSV SHA-256 differs from the approved source")
    if tuple(shape["headers"]) != HEADERS or shape["rows"] != EXPECTED_ROWS:
        raise DataContractError("CSV shape differs from the approved source")
    if shape["row_widths"] != {len(HEADERS): EXPECTED_ROWS}:
        raise DataContractError("CSV has a row-width anomaly")
    if profile["completeness"]["rows_with_blank_field"]:
        raise DataContractError("CSV has a blank field")
    if profile["validity"]["invalid_types_by_column"]:
        raise DataContractError("CSV has an invalid type")
    if profile["validity"]["discharge_before_admission"]:
        raise DataContractError("CSV has a discharge before admission")
    if profile["uniqueness"]["exact_duplicate_extra_rows"] != EXPECTED_DUPLICATE_EXTRA_ROWS:
        raise DataContractError("CSV duplicate count differs from the approved source")
    if (
        profile["validity"]["negative_values_by_numeric_column"]["Billing Amount"]
        != EXPECTED_NEGATIVE_AMOUNTS
    ):
        raise DataContractError("CSV negative-amount count differs from the approved source")
    return {
        "source_sha256": expected_sha256,
        "source_rows": EXPECTED_ROWS,
        "exact_duplicate_extra_rows": EXPECTED_DUPLICATE_EXTRA_ROWS,
        "negative_billing_amount_rows": EXPECTED_NEGATIVE_AMOUNTS,
    }


def migrate(collection, path, source_sha256, batch_size=500):
    if batch_size < 1 or batch_size > 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    operations = []
    written = 0
    for source_row, raw in source_rows(path):
        document = build_document(raw, source_sha256, source_row)
        operations.append(ReplaceOne({"_id": document["_id"]}, document, upsert=True))
        if len(operations) == batch_size:
            collection.bulk_write(operations, ordered=True)
            written += len(operations)
            operations.clear()
    if operations:
        collection.bulk_write(operations, ordered=True)
        written += len(operations)
    return written


def verify(collection, path, source_sha256):
    expected = sum(1 for _ in source_rows(path))
    actual = collection.count_documents({"source_sha256": source_sha256})
    if actual != expected:
        raise DataContractError(f"target count {actual} differs from source count {expected}")
    cursor = collection.find({"source_sha256": source_sha256}).sort("source_row", 1)
    checked = 0
    for (source_row, raw), stored in zip(source_rows(path), cursor):
        if stored != build_document(raw, source_sha256, source_row):
            raise DataContractError(f"target content differs at source row {source_row}")
        checked += 1
    if checked != expected:
        raise DataContractError("target cursor ended before the source")
    return checked


def mongo_collection():
    password_file = Path(os.environ["P05_MONGO_PASSWORD_FILE"])
    password = password_file.read_text(encoding="utf-8").strip()
    if not password:
        raise ValueError("empty MongoDB password file")
    db_name = os.environ.get("P05_MONGO_DB", "p05_medical")
    client = MongoClient(
        host=os.environ.get("P05_MONGO_HOST", "127.0.0.1"),
        port=int(os.environ.get("P05_MONGO_PORT", "27017")),
        username=os.environ.get("P05_MONGO_USER", "p05_ingest"),
        password=password,
        authSource=db_name,
        serverSelectionTimeoutMS=10000,
    )
    client[db_name].command("ping")
    return client, client[db_name]["admissions"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("preflight", "migrate", "verify"), default="migrate")
    parser.add_argument("--csv", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    result = {"mode": args.mode, "preflight": preflight(args.csv)}
    if args.mode == "preflight":
        result["target_verification"] = "not_run"
    else:
        client, collection = mongo_collection()
        try:
            if args.mode == "migrate":
                result["write_attempts"] = migrate(
                    collection, args.csv, EXPECTED_SHA256, args.batch_size
                )
            result["verified_documents"] = verify(collection, args.csv, EXPECTED_SHA256)
            result["target_verification"] = "passed"
        finally:
            client.close()

    report = args.report or PROJECT_DIR / (
        "P05_Controle_pre_migration.json" if args.mode == "preflight"
        else "P05_Controle_migration.json"
    )
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.mode}: {result['preflight']['source_rows']} source rows; report {report.name}")


if __name__ == "__main__":
    main()
