#!/usr/bin/env python3
"""Generate deterministic fictional property records for the sample."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

CITY_CONFIG = {
    "atlanta": ("ATL", 15, 33.7490, -84.3880),
    "chicago": ("CHI", 15, 41.8781, -87.6298),
    "cupertino": ("CUP", 2, 37.3230, -122.0322),
    "frisco": ("FRI", 3, 33.1507, -96.8236),
    "miami": ("MIA", 15, 25.7617, -80.1918),
    "oakland": ("OAK", 15, 37.8044, -122.2712),
    "seattle": ("SEA", 15, 47.6062, -122.3321),
}

MARKERS = ("X1", "X2", "X3", "X4", "X5", "X6")
PROPERTY_TYPES = ("Single Family Residence", "Townhouse", "Multi Family", "Condos")
FEATURES = (
    "garden, garage, balcony",
    "patio, fireplace, storage",
    "terrace, parking, shed",
    "porch, attic, driveway",
)
TIMESTAMP = "2026-01-01T00:00:00Z"


def _fraction(seed: str) -> float:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)


def _jitter(seed: str) -> float:
    return round((_fraction(seed) * 0.08) - 0.04, 5)


def build_record(city: str, index: int) -> dict[str, object]:
    prefix, _, latitude, longitude = CITY_CONFIG[city]
    marker = MARKERS[(index - 1) % len(MARKERS)]
    property_type = PROPERTY_TYPES[(index - 1) % len(PROPERTY_TYPES)]
    bedrooms = 1 + ((index * 3 + len(city)) % 5)
    bathrooms = 1.0 + (((index * 2 + len(city)) % 7) * 0.5)
    square_feet = 700 + ((index * 173 + len(city) * 41) % 2600)
    year_built = 1980 + ((index * 7 + len(city)) % 46)
    price = 180_000 + ((index * 47_000 + len(city) * 13_000) % 1_020_000)
    lat = round(latitude + _jitter(f"{city}:{index}:lat"), 5)
    lon = round(longitude + _jitter(f"{city}:{index}:lon"), 5)
    property_id = f"{prefix}{index:03d}"

    return {
        "propertyId": property_id,
        "propertyName": f"Fictional {marker} {property_type} {index}",
        "propertyType": property_type,
        "propertyAddress": f"{index} Fictional {marker} Street, {city.title()}",
        "propertyFeatures": FEATURES[(index - 1) % len(FEATURES)],
        "propertyCoordinates": f"{lat:.5f},{lon:.5f}",
        "propertyPrice": price,
        "propertyBedrooms": bedrooms,
        "propertyBathrooms": bathrooms,
        "propertySquareFeet": square_feet,
        "propertyYearBuilt": year_built,
        "propertyDescription": (
            f"This is a synthetic listing for property {property_id}, which is a {property_type} "
            f"marked as {marker}."
        ),
        "createdAt": TIMESTAMP,
        "updatedAt": TIMESTAMP,
    }


def generate(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for city, (_, count, _, _) in CITY_CONFIG.items():
        records = [build_record(city, index) for index in range(1, count + 1)]
        content = json.dumps(records, indent=2, ensure_ascii=True) + "\n"
        (output_dir / f"{city}.json").write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data",
        help="Directory for the seven generated JSON files",
    )
    args = parser.parse_args()
    generate(args.output_dir)
    print(f"Generated 80 deterministic fictional records in {args.output_dir}")


if __name__ == "__main__":
    main()
