"""Profile the supplied medical CSV without exporting patient-level values."""

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = Path(os.environ.get(
    "P05_DATASET_PATH",
    "/data/healthcare_dataset.csv" if Path("/data/healthcare_dataset.csv").is_file()
    else str(PROJECT_DIR / "data" / "healthcare_dataset.csv"),
))
DEFAULT_OUTPUT = Path(__file__).with_name("P05_Profilage_source.json")
DATE_COLUMNS = ("Date of Admission", "Discharge Date")
INTEGER_COLUMNS = ("Age", "Room Number")
AMOUNT_COLUMN = "Billing Amount"
SENTINELS = {"n/a", "na", "none", "null", "unknown", "-"}


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_csv(path):
    blanks = Counter()
    sentinels = Counter()
    distinct = {}
    row_hashes = Counter()
    row_widths = Counter()
    invalid_types = Counter()
    negative = Counter()
    zero = Counter()
    min_values = {}
    max_values = {}
    date_values = {}
    max_decimal_places = 0
    rows = 0
    rows_with_blanks = 0
    discharge_before_admission = 0

    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        distinct = {column: set() for column in header}
        for record in reader:
            rows += 1
            row_widths[len(record)] += 1
            if len(record) != len(header):
                continue

            row_hash = hashlib.sha256(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).digest()
            row_hashes[row_hash] += 1
            values = dict(zip(header, record))
            rows_with_blanks += any(not value.strip() for value in record)

            for column, value in values.items():
                stripped = value.strip()
                distinct[column].add(stripped)
                blanks[column] += not stripped
                sentinels[column] += stripped.lower() in SENTINELS

            for column in INTEGER_COLUMNS:
                try:
                    parsed = int(values[column].strip())
                except (ValueError, KeyError):
                    invalid_types[column] += 1
                    continue
                min_values[column] = min(parsed, min_values.get(column, parsed))
                max_values[column] = max(parsed, max_values.get(column, parsed))
                negative[column] += parsed < 0
                zero[column] += parsed == 0

            try:
                parsed = Decimal(values[AMOUNT_COLUMN].strip())
                if not parsed.is_finite():
                    raise InvalidOperation
            except (InvalidOperation, KeyError):
                invalid_types[AMOUNT_COLUMN] += 1
            else:
                min_values[AMOUNT_COLUMN] = min(
                    parsed, min_values.get(AMOUNT_COLUMN, parsed)
                )
                max_values[AMOUNT_COLUMN] = max(
                    parsed, max_values.get(AMOUNT_COLUMN, parsed)
                )
                negative[AMOUNT_COLUMN] += parsed < 0
                zero[AMOUNT_COLUMN] += parsed == 0
                max_decimal_places = max(max_decimal_places, -parsed.as_tuple().exponent)

            parsed_dates = {}
            for column in DATE_COLUMNS:
                try:
                    parsed = date.fromisoformat(values[column].strip())
                except (ValueError, KeyError):
                    invalid_types[column] += 1
                    continue
                parsed_dates[column] = parsed
                date_values.setdefault(column, []).append(parsed)
            if len(parsed_dates) == 2:
                discharge_before_admission += (
                    parsed_dates["Discharge Date"] < parsed_dates["Date of Admission"]
                )

    for column, values in date_values.items():
        min_values[column] = min(values)
        max_values[column] = max(values)

    duplicate_groups = [count for count in row_hashes.values() if count > 1]
    return {
        "source": {
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
            "encoding": "utf-8-sig",
        },
        "shape": {
            "rows": rows,
            "columns": len(header),
            "headers": header,
            "duplicate_headers": len(header) - len(set(header)),
            "row_widths": dict(sorted(row_widths.items())),
        },
        "completeness": {
            "rows_with_blank_field": rows_with_blanks,
            "blank_fields_by_column": dict(blanks),
            "sentinel_values_by_column": dict(sentinels),
        },
        "uniqueness": {
            "exact_duplicate_groups": len(duplicate_groups),
            "exact_duplicate_extra_rows": sum(count - 1 for count in duplicate_groups),
            "exact_duplicate_affected_rows": sum(duplicate_groups),
            "distinct_values_by_column": {
                column: len(values) for column, values in distinct.items()
            },
            "source_record_id_available": False,
        },
        "validity": {
            "invalid_types_by_column": dict(invalid_types),
            "negative_values_by_numeric_column": dict(negative),
            "zero_values_by_numeric_column": dict(zero),
            "discharge_before_admission": discharge_before_admission,
            "minimums": {column: str(value) for column, value in min_values.items()},
            "maximums": {column: str(value) for column, value in max_values.items()},
            "billing_amount_max_decimal_places": max_decimal_places,
        },
        "privacy": {
            "raw_records_exported": False,
            "individual_field_values_exported": False,
            "note": "Aggregate metadata only; do not commit the source CSV or patient records.",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    result = profile_csv(args.source)
    if args.expected_sha256 and result["source"]["sha256"] != args.expected_sha256.lower():
        parser.error("source SHA-256 differs from the expected input")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Profile written: {args.output} ({result['shape']['rows']} rows)")


if __name__ == "__main__":
    main()
