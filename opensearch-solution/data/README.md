# Synthetic property data

All 80 records in this directory are fictional and generated for demonstration only across seven cities. They are not sourced from an MLS, listing portal, broker, property owner, or other real-estate dataset. Addresses, property IDs, descriptions, prices, specifications, and coordinates must not be used for property identification or valuation decisions.

The seven city files use coarse, deterministically jittered coordinates near city centroids. Generate and validate them from the solution root:

```bash
python3 scripts/generate_synthetic_data.py
python3 scripts/validate_synthetic_data.py
```

The generator uses fixed formulas and SHA-256-derived coordinate jitter. Repeated runs produce byte-identical JSON. Embeddings are generated only during loading and are not stored in these source files.

## How this dataset was created

`scripts/generate_synthetic_data.py` builds every record deterministically:

This static record text was generated using Amazon Nova and reviewed by the maintainer. The deterministic generator makes no model calls at runtime, and all property records and addresses are fictional. Seven real city names and approximate city-center coordinates are retained as factual inputs, though the original source of the approximate centroid constants was not recorded.

- **Numbers:** price, bedrooms, bathrooms, square footage, and year built come from fixed arithmetic on the record number and city name.
- **Coordinates:** each record is a city-center latitude and longitude, hard-coded in the generator for the seven city names, plus SHA-256-derived jitter of up to 0.04 degrees.
- **Timestamps:** every record uses the same fixed timestamp.

All records are fictional. No record is derived from a real property, listing, or person.

## Bring your own authorized data

Use a separate data directory and a separate OpenSearch index name. Do not replace the bundled fictional dataset when evaluating a new source. Each input file is a JSON array. Every record uses this schema:

| Field | Type |
|---|---|
| `propertyId` | string, unique |
| `propertyName` | string |
| `propertyType` | string |
| `propertyAddress` | string |
| `propertyFeatures` | string |
| `propertyCoordinates` | string in `lat,lon` order |
| `propertyPrice` | integer |
| `propertyBedrooms` | integer |
| `propertyBathrooms` | number |
| `propertySquareFeet` | integer |
| `propertyYearBuilt` | integer |
| `propertyDescription` | string |
| `createdAt` | ISO 8601 date-time string |
| `updatedAt` | ISO 8601 date-time string |

The loader accepts `--data-dir` and `--index-name`. It creates Titan Text Embeddings V2 vectors during loading. Keep the new index separate until schema, coordinate order and ranges, record counts, search quality, and authorization controls have been validated.

Authenticated users may POST to `_search` on any index whose name starts with `real-estate-properties`. An index named with that prefix, for example `real-estate-properties-byo`, is therefore searchable by every authenticated user of the deployment, even though the app itself queries only the sample index. To keep bring-your-own records away from those users, choose an index name outside that prefix, or narrow the authenticated role's resource in `backend/infrastructure/real_estate_search_stack.py`.

Record text is also included in the model prompt for AI summaries. The app displays summaries as plain text, but records can still influence what the model writes for other users.

You are responsible for proving the source, provenance, completeness, quality, licensing, consent, privacy, and rights to use any replacement data. Review who can load and search the separate index before use. The bundled validator proves only the deterministic fictional dataset's structure and markers. This sample cannot verify the factual authenticity of customer-provided records.
