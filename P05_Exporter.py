"""Export the complete MongoDB admission collection to an ignored local CSV."""

import argparse
import csv
import json
from pathlib import Path

from P05_Migration import (
    DEFAULT_SOURCE,
    EXPECTED_ROWS,
    EXPECTED_SHA256,
    HEADERS,
    TEXT_FIELDS,
    DataContractError,
    mongo_collection,
    source_rows,
)


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_EXPORT = PROJECT_DIR / "exports" / "healthcare_dataset_export.csv"
REVERSE_TEXT_FIELDS = {target: source for source, target in TEXT_FIELDS.items()}


def export_collection(collection, destination):
    if destination.resolve() == DEFAULT_SOURCE.resolve():
        raise DataContractError("refusing to overwrite the supplied source CSV")
    destination.parent.mkdir(parents=True, exist_ok=True)
    exported = 0
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=HEADERS)
        writer.writeheader()
        cursor = collection.find({"source_sha256": EXPECTED_SHA256}).sort("source_row", 1)
        for document in cursor:
            exported += 1
            if document["source_row"] != exported:
                raise DataContractError(f"missing or unexpected source row at export position {exported}")
            row = {source: document[target] for target, source in REVERSE_TEXT_FIELDS.items()}
            row.update({
                "Age": str(document["age"]),
                "Room Number": str(document["room_number"]),
                "Billing Amount": str(document["billing_amount"].to_decimal()),
                "Date of Admission": document["admission_date"],
                "Discharge Date": document["discharge_date"],
            })
            writer.writerow(row)
    if exported != EXPECTED_ROWS:
        raise DataContractError(f"exported {exported} rows, expected {EXPECTED_ROWS}")
    for (source_row, original), (_, exported_row) in zip(
        source_rows(DEFAULT_SOURCE), source_rows(destination)
    ):
        if original != exported_row:
            raise DataContractError(f"export content differs at source row {source_row}")
    return exported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_EXPORT)
    args = parser.parse_args()
    client, collection = mongo_collection()
    try:
        count = export_collection(collection, args.out)
    finally:
        client.close()
    report = {"exported_rows": count, "source_sha256": EXPECTED_SHA256, "content_check": "passed"}
    report_path = PROJECT_DIR / "P05_Controle_export.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"export verified: {count} rows; output {args.out.name}")


if __name__ == "__main__":
    main()
