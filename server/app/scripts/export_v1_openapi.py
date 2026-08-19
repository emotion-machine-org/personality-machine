#!/usr/bin/env python3
"""
Export the /v1 API subset of the OpenAPI spec to a JSON file.

Usage:
    python scripts/export_v1_openapi.py

Output:
    v1-openapi.json in the current directory
"""

import json
import sys
from pathlib import Path

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.main import app


def extract_v1_openapi() -> dict:
    """Extract only /v1 endpoints from the OpenAPI spec."""
    full_spec = app.openapi()

    v1_spec = {
        "openapi": full_spec.get("openapi", "3.1.0"),
        "info": {
            "title": "Emotion Machine API v1",
            "description": "Public API for Emotion Machine companions, chat, knowledge, and voice sessions.",
            "version": "1.0.0",
        },
        "servers": [
            {"url": "https://api.emotionmachine.ai", "description": "Production"},
            {"url": "http://localhost:8100", "description": "Local development"},
        ],
        "paths": {},
        "components": {
            "schemas": {},
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "API key (e.g., em_live_xxx or emk_prod_xxx)",
                }
            },
        },
        "security": [{"BearerAuth": []}],
    }

    # Filter paths to only /v1 endpoints
    used_schemas = set()
    for path, methods in full_spec.get("paths", {}).items():
        if path.startswith("/v1"):
            v1_spec["paths"][path] = methods
            # Track which schemas are referenced
            _collect_schema_refs(methods, used_schemas)

    # Copy only referenced schemas
    all_schemas = full_spec.get("components", {}).get("schemas", {})
    for schema_name in used_schemas:
        if schema_name in all_schemas:
            v1_spec["components"]["schemas"][schema_name] = all_schemas[schema_name]
            # Also get nested schema references
            _collect_nested_schemas(
                all_schemas[schema_name], all_schemas, v1_spec["components"]["schemas"]
            )

    return v1_spec


def _collect_schema_refs(obj: dict | list, refs: set) -> None:
    """Recursively collect $ref schema names from an object."""
    if isinstance(obj, dict):
        if "$ref" in obj:
            ref = obj["$ref"]
            if ref.startswith("#/components/schemas/"):
                refs.add(ref.split("/")[-1])
        for value in obj.values():
            _collect_schema_refs(value, refs)
    elif isinstance(obj, list):
        for item in obj:
            _collect_schema_refs(item, refs)


def _collect_nested_schemas(schema: dict, all_schemas: dict, output: dict) -> None:
    """Recursively collect nested schema references."""
    refs = set()
    _collect_schema_refs(schema, refs)
    for ref_name in refs:
        if ref_name in all_schemas and ref_name not in output:
            output[ref_name] = all_schemas[ref_name]
            _collect_nested_schemas(all_schemas[ref_name], all_schemas, output)


def main():
    v1_spec = extract_v1_openapi()

    output_path = Path("v1-openapi.json")
    with open(output_path, "w") as f:
        json.dump(v1_spec, f, indent=2)

    endpoint_count = len(v1_spec["paths"])
    schema_count = len(v1_spec["components"]["schemas"])
    print(f"Exported {endpoint_count} endpoints and {schema_count} schemas to {output_path}")


if __name__ == "__main__":
    main()
