#!/usr/bin/env python3
"""Create the sample index and load deterministic fictional records."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import boto3
from botocore.config import Config
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth

EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"


def create_opensearch_client(endpoint: str, region: str) -> OpenSearch:
    session = boto3.Session(region_name=region)
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("AWS credentials are unavailable to the loader task")
    frozen = credentials.get_frozen_credentials()
    auth = AWS4Auth(
        frozen.access_key,
        frozen.secret_key,
        region,
        "es",
        session_token=frozen.token,
    )
    host = endpoint.removeprefix("https://").removeprefix("http://")
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
        max_retries=3,
        retry_on_timeout=True,
    )


def create_index_mapping() -> dict[str, object]:
    vector = {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
            "name": "hnsw",
            "space_type": "cosinesimil",
            "engine": "lucene",
            "parameters": {"ef_construction": 128, "m": 24},
        },
    }
    return {
        "mappings": {
            "properties": {
                "propertyId": {"type": "keyword"},
                "propertyName": {"type": "text", "analyzer": "standard"},
                "propertyType": {"type": "keyword"},
                "propertyAddress": {"type": "text", "analyzer": "standard"},
                "propertyFeatures": {"type": "text", "analyzer": "standard"},
                "propertyCoordinates": {"type": "geo_point"},
                "propertyPrice": {"type": "float"},
                "propertyBedrooms": {"type": "integer"},
                "propertyBathrooms": {"type": "float"},
                "propertySquareFeet": {"type": "integer"},
                "propertyYearBuilt": {"type": "integer"},
                "propertyDescription": {"type": "text", "analyzer": "standard"},
                "createdAt": {"type": "date"},
                "updatedAt": {"type": "date"},
                "property_embedding": vector,
                "description_embedding": vector,
            }
        },
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "index.knn": True,
        },
    }


def generate_embedding(client, text: str) -> list[float]:
    response = client.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps({"inputText": text, "dimensions": 1024, "normalize": True}),
        contentType="application/json",
    )
    return json.loads(response["body"].read())["embedding"]


def load_records(data_dir: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(data_dir.glob("*.json")):
        city_records = json.loads(path.read_text(encoding="utf-8"))
        records.extend(city_records)
        print(f"Loaded {len(city_records)} fictional records from {path.name}")
    if len(records) != 80:
        raise ValueError(f"expected 80 generated records, found {len(records)}")
    return records


def setup_index(client: OpenSearch, bedrock_client, index_name: str, records) -> None:
    if client.indices.exists(index=index_name):
        client.indices.delete(index=index_name)
        print(f"Deleted existing sample index {index_name}")
        time.sleep(2)
    client.indices.create(index=index_name, body=create_index_mapping())
    print(f"Created sample index {index_name}")

    for position, source in enumerate(records, start=1):
        document = source.copy()
        combined = " ".join(
            str(document[field])
            for field in ("propertyType", "propertyFeatures", "propertyDescription")
        )
        document["property_embedding"] = generate_embedding(bedrock_client, combined)
        document["description_embedding"] = generate_embedding(
            bedrock_client, str(document["propertyDescription"])
        )
        client.index(index=index_name, id=document["propertyId"], body=document)
        print(f"Indexed fictional record {position}/80: {document['propertyId']}")

    client.indices.refresh(index=index_name)
    count = client.count(index=index_name)["count"]
    if count != 80:
        raise RuntimeError(f"expected 80 indexed records, found {count}")
    print("Validated 80 indexed fictional records")


def resolve_data_dir(
    requested: Path | None,
    script_path: Path | None = None,
) -> Path:
    """Resolve an explicit, container, or repository data directory."""
    if requested is not None:
        selected = requested.expanduser().resolve()
    else:
        script_dir = (script_path or Path(__file__)).resolve().parent
        container_data_dir = script_dir / "data"
        repository_data_dir = script_dir.parent.parent / "data"
        selected = (
            container_data_dir
            if container_data_dir.is_dir()
            else repository_data_dir
        )
    if not selected.is_dir():
        raise FileNotFoundError(f"data directory does not exist: {selected}")
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("OPENSEARCH_ENDPOINT"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION"))
    parser.add_argument("--index-name", default=os.environ.get("INDEX_NAME", "real-estate-properties"))
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    if not args.endpoint or not args.region:
        parser.error("--endpoint/OPENSEARCH_ENDPOINT and --region/AWS_REGION are required")

    try:
        data_dir = resolve_data_dir(args.data_dir)
    except FileNotFoundError as error:
        parser.error(str(error))

    client = create_opensearch_client(args.endpoint, args.region)
    bedrock_client = boto3.client(
        "bedrock-runtime",
        region_name=args.region,
        config=Config(connect_timeout=30, read_timeout=120, retries={"max_attempts": 3}),
    )
    setup_index(client, bedrock_client, args.index_name, load_records(data_dir))


if __name__ == "__main__":
    main()
