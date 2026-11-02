#!/usr/bin/env python3
"""Validate generated fictional property data without external dependencies."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from generate_synthetic_data import CITY_CONFIG, MARKERS, PROPERTY_TYPES

EXPECTED_FIELDS = {
    "propertyId",
    "propertyName",
    "propertyType",
    "propertyAddress",
    "propertyFeatures",
    "propertyCoordinates",
    "propertyPrice",
    "propertyBedrooms",
    "propertyBathrooms",
    "propertySquareFeet",
    "propertyYearBuilt",
    "propertyDescription",
    "createdAt",
    "updatedAt",
}
PROHIBITED_TERMS = (
    "mls",
    "realtor.com",
    "zillow",
    "redfin",
    "homes.com",
    "trulia",
    "windermere",
    "moxi.onl",
    "listing id",
)


def validate(data_dir: Path) -> list[str]:
    errors: list[str] = []
    expected_files = {f"{city}.json" for city in CITY_CONFIG}
    actual_files = {path.name for path in data_dir.glob("*.json")}
    if actual_files != expected_files:
        errors.append(f"expected files {sorted(expected_files)}, found {sorted(actual_files)}")

    seen_ids: set[str] = set()
    total = 0
    for city, (prefix, expected_count, center_lat, center_lon) in CITY_CONFIG.items():
        path = data_dir / f"{city}.json"
        if not path.exists():
            continue
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"{path.name}: cannot parse: {error}")
            continue
        if not isinstance(records, list):
            errors.append(f"{path.name}: top-level value must be an array")
            continue
        if len(records) != expected_count:
            errors.append(f"{path.name}: expected {expected_count} records, found {len(records)}")
        total += len(records)

        for index, record in enumerate(records, start=1):
            label = f"{path.name}[{index - 1}]"
            if not isinstance(record, dict):
                errors.append(f"{label}: record must be an object")
                continue
            if set(record) != EXPECTED_FIELDS:
                errors.append(f"{label}: field set does not match schema")
                continue
            expected_id = f"{prefix}{index:03d}"
            if record["propertyId"] != expected_id:
                errors.append(f"{label}: expected ID {expected_id}")
            if record["propertyId"] in seen_ids:
                errors.append(f"{label}: duplicate ID {record['propertyId']}")
            seen_ids.add(record["propertyId"])

            string_fields = EXPECTED_FIELDS - {
                "propertyPrice",
                "propertyBedrooms",
                "propertyBathrooms",
                "propertySquareFeet",
                "propertyYearBuilt",
            }
            if any(not isinstance(record[field], str) for field in string_fields):
                errors.append(f"{label}: string field has an invalid type")
            if not isinstance(record["propertyPrice"], int) or isinstance(record["propertyPrice"], bool):
                errors.append(f"{label}: propertyPrice must be an integer")
            for field in ("propertyBedrooms", "propertySquareFeet", "propertyYearBuilt"):
                if not isinstance(record[field], int) or isinstance(record[field], bool):
                    errors.append(f"{label}: {field} must be an integer")
            if not isinstance(record["propertyBathrooms"], (int, float)) or isinstance(record["propertyBathrooms"], bool):
                errors.append(f"{label}: propertyBathrooms must be numeric")
            elif not math.isclose(record["propertyBathrooms"] * 2, round(record["propertyBathrooms"] * 2)):
                errors.append(f"{label}: propertyBathrooms must use half-step values")

            if record["propertyType"] not in PROPERTY_TYPES:
                errors.append(f"{label}: unrecognized property type")
            if not (100_000 <= record["propertyPrice"] <= 2_000_000):
                errors.append(f"{label}: price outside demonstration range")
            if not (1 <= record["propertyBedrooms"] <= 6):
                errors.append(f"{label}: bedroom count outside range")
            if not (1 <= record["propertyBathrooms"] <= 5):
                errors.append(f"{label}: bathroom count outside range")
            if not (500 <= record["propertySquareFeet"] <= 4_000):
                errors.append(f"{label}: square footage outside range")
            if not (1900 <= record["propertyYearBuilt"] <= 2026):
                errors.append(f"{label}: year outside range")

            address = record["propertyAddress"]
            if not any(marker in address for marker in MARKERS):
                errors.append(f"{label}: address lacks an explicit fictional marker")
            combined = " ".join(str(value) for value in record.values()).lower()
            if "fictional" not in combined or "synthetic" not in combined:
                errors.append(f"{label}: synthetic and fictional wording is required")
            if any(term in combined for term in PROHIBITED_TERMS):
                errors.append(f"{label}: contains prohibited listing or portal terminology")
            if "property_embedding" in record or "description_embedding" in record:
                errors.append(f"{label}: embedding fields must not be stored")
            if any(isinstance(value, list) and len(value) == 1024 for value in record.values()):
                errors.append(f"{label}: embedded 1024-value array is prohibited")

            try:
                lat_text, lon_text = record["propertyCoordinates"].split(",", 1)
                latitude, longitude = float(lat_text), float(lon_text)
            except (AttributeError, TypeError, ValueError):
                errors.append(f"{label}: coordinates must be 'lat,lon'")
                continue
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                errors.append(f"{label}: coordinates outside global ranges")
            if abs(latitude - center_lat) > 0.04001 or abs(longitude - center_lon) > 0.04001:
                errors.append(f"{label}: coordinates exceed city centroid jitter bounds")

    if total != 80:
        errors.append(f"expected 80 total records, found {total}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data",
    )
    args = parser.parse_args()
    errors = validate(args.data_dir)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("Validated 80 fictional records across seven city files")


if __name__ == "__main__":
    main()
