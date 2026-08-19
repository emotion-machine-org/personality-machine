import copy
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import asyncpg
import httpx
import modal
import numpy as np
import pandas as pd
import requests
from fastapi import HTTPException
from openai import OpenAI

# Small specs with this many tools or fewer skip embedding-based search
SMALL_SPEC_TOOL_THRESHOLD = 3

# Internal API hosts - requests to these hosts use X-Internal-Key auth
INTERNAL_API_HOSTS = {
    "localhost:8100",  # local dev
    "localhost:8000",  # local dev alt port
    "api.emotionmachine.ai",  # production
    "api-dev.emotionmachine.ai",  # staging
}

app = modal.App("em-tools")
image = modal.Image.debian_slim().pip_install(
    "requests",
    "openai",
    "tiktoken",
    "numpy",
    "pandas",
    "fastparquet",
    "fastapi",
    "asyncpg",
    "cryptography",
)


def resolve_ref(ref: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolve a JSON Reference like '#/components/schemas/Foo'
    inside the OpenAPI spec.
    """
    if not ref.startswith("#/"):
        raise ValueError(f"Only local refs are supported, got: {ref}")
    parts = ref.lstrip("#/").split("/")
    node = spec
    for part in parts:
        node = node[part]
    # Return a deep copy so we can mutate safely
    return copy.deepcopy(node)


def expand_schema(
    schema: Dict[str, Any], spec: Dict[str, Any], seen: set | None = None
) -> Dict[str, Any]:
    """
    Recursively expand all $ref within a schema. Handles objects, arrays,
    allOf/oneOf/anyOf (shallowly).
    """
    if seen is None:
        seen = set()

    if not isinstance(schema, dict):
        return schema

    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen:
            # Avoid infinite recursion on circular refs
            return {"$ref_cycle": ref}
        seen.add(ref)
        resolved = resolve_ref(ref, spec)
        return expand_schema(resolved, spec, seen)

    result = {}
    for key, value in schema.items():
        if key in ("allOf", "oneOf", "anyOf") and isinstance(value, list):
            result[key] = [expand_schema(v, spec, seen) for v in value]
        elif key == "items" and isinstance(value, dict):
            result[key] = expand_schema(value, spec, seen)
        elif key == "properties" and isinstance(value, dict):
            result[key] = {k: expand_schema(v, spec, seen) for k, v in value.items()}
        else:
            result[key] = value

    return result


def expand_parameter(param: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expand a parameter, resolving $ref and its schema.$ref.
    """
    if "$ref" in param:
        # Reference to #/components/parameters/...
        param = resolve_ref(param["$ref"], spec)

    param = copy.deepcopy(param)

    if "schema" in param and isinstance(param["schema"], dict):
        param["schema"] = expand_schema(param["schema"], spec)

    return param


def get_input_parameters(
    path_item: Dict[str, Any], method_obj: Dict[str, Any], spec: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Collect and expand parameters from both path-level and method-level,
    plus requestBody schema.
    """
    inputs: List[Dict[str, Any]] = []

    # Path-level parameters
    for param in path_item.get("parameters", []):
        inputs.append(expand_parameter(param, spec))

    # Method-level parameters
    for param in method_obj.get("parameters", []):
        inputs.append(expand_parameter(param, spec))

    # Request body (if any)
    request_body = method_obj.get("requestBody")
    if request_body:
        body_entry: Dict[str, Any] = {
            "in": "body",
            "required": request_body.get("required", False),
            "content": {},
        }
        content = request_body.get("content", {})
        for mime, media_obj in content.items():
            schema = media_obj.get("schema")
            if schema:
                body_entry["content"][mime] = expand_schema(schema, spec)
        inputs.append(body_entry)

    return inputs


def get_output_schema(method_obj: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Collect and expand response schemas for each status code and content type.
    """
    outputs: Dict[str, Any] = {}
    for status_code, resp in (method_obj.get("responses") or {}).items():
        content = resp.get("content", {})
        if not content:
            continue
        outputs[status_code] = {}
        for mime, media_obj in content.items():
            schema = media_obj.get("schema")
            if schema:
                outputs[status_code][mime] = expand_schema(schema, spec)
    return outputs


def extract_base_url(openapi_spec: Dict[str, Any]) -> str | None:
    """Extract servers[0].url (string or object) from an OpenAPI spec."""
    if not isinstance(openapi_spec, dict):
        return None
    servers = openapi_spec.get("servers")
    if not isinstance(servers, list) or not servers:
        return None
    first = servers[0]
    candidate = None
    if isinstance(first, dict):
        candidate = first.get("url")
    elif isinstance(first, str):
        candidate = first
    if isinstance(candidate, str):
        candidate = candidate.strip()
        candidate = candidate.strip("/")
        return candidate or None
    return None


def openapi_to_dataframe(openapi_spec: Dict[str, Any]) -> pd.DataFrame:
    """
    Convert an OpenAPI spec (as dict) into a DataFrame with columns:
      - name
      - description
      - input_parameters (fully expanded)
      - output_schema (fully expanded)
    """
    rows = []

    paths = openapi_spec.get("paths", {})
    for path, path_item in paths.items():
        for method, method_obj in path_item.items():
            # Skip non-HTTP keys
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                continue

            op_id = method_obj.get("operationId") or path
            summary = f"[{method.upper()}] " + (
                method_obj.get("summary") or method_obj.get("description") or ""
            )

            inputs = get_input_parameters(path_item, method_obj, openapi_spec)
            outputs = get_output_schema(method_obj, openapi_spec)

            rows.append(
                {
                    "name": op_id,
                    "description": summary,
                    "path": path,  # ← NEW COLUMN
                    "method": method.upper(),  # ← useful for API calls
                    "input_parameters": inputs,
                    "output_schema": outputs,
                }
            )

    df = pd.DataFrame(
        rows, columns=["name", "description", "path", "method", "input_parameters", "output_schema"]
    )
    df["full_text"] = df.apply(
        lambda row: (
            f"{row['name']}\n{row['description']}\n{json.dumps(row['input_parameters'], indent=2)}"
        ),
        axis=1,
    )
    print(f"Found {len(df)} API endpoints.")
    return df


def _vec_text(values: List[float]) -> str | None:
    if not values:
        return None
    try:
        return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"
    except Exception:
        return None


def _clean_val(val: Any) -> Any:
    if val is None:
        return None
    try:
        if isinstance(val, float) and np.isnan(val):
            return None
    except Exception:
        pass
    return val


def _to_float_list(val: Any) -> List[float]:
    """Normalize embeddings coming back from Postgres/vector into a float list."""
    if val is None:
        return []
    if isinstance(val, (list | tuple)):
        try:
            return [float(x) for x in val]
        except Exception:
            return []
    if isinstance(val, str):
        try:
            stripped = val.strip().lstrip("[").rstrip("]")
            parts = [p for p in stripped.split(",") if p.strip()]
            return [float(p) for p in parts]
        except Exception:
            return []
    return []


def cosine_similarity(a, b) -> float:
    a = np.array(a, dtype=float)
    b = np.array(b, dtype=float)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def create_embedding_oai_small(query: str) -> List[float]:
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        client = OpenAI(api_key=api_key)

        return (
            client.embeddings.create(
                model="text-embedding-3-small",
                input=query,
                encoding_format="float",
            )
            .data[0]
            .embedding
        )
    except Exception as e:
        print(f"Error creating embedding: {e}")
        return []


def get_most_relevant_tools(query: str, tool_df: pd.DataFrame, n: int) -> List[str]:
    query_emb = create_embedding_oai_small(query)

    df = tool_df.copy()
    df["similarity"] = df["oai_small_embedding"].apply(
        lambda emb: cosine_similarity(emb, query_emb)
    )

    return df.sort_values("similarity", ascending=False)[["name", "similarity"]]["name"][
        :n
    ].to_list()


def call_openrouter(prompt: str, model: str, openrouter_key: str) -> str:
    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {openrouter_key}",
            },
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Error calling OpenRouter: {e}")
        return "error"


def generate_spec_summary(operation_names: List[str], openrouter_key: str) -> str:
    """Generate a one-sentence summary of the API based on operation names."""
    if not operation_names:
        return ""
    prompt = (
        "Based on these API operation names, write ONE sentence describing what this API can do. "
        "Be concise and focus on capabilities.\n\n"
        f"Operations: {', '.join(operation_names)}"
    )
    return call_openrouter(prompt, "openai/gpt-oss-20b:nitro", openrouter_key)


def _strip_json_block(payload: str) -> Dict[str, Any] | None:
    """Remove Markdown fences and parse a JSON object, returning None on error."""
    if not payload:
        return None

    cleaned = payload.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        parts = cleaned.split("\n", 1)
        if len(parts) == 2:
            cleaned = parts[1]
        cleaned = cleaned.strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def validate_parameters_against_schema(
    input_parameters: List[Dict[str, Any]],
    provided_params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Validate AI-generated parameters against the input schema.

    Returns:
        {
            "valid": bool,
            "missing_required": [{"name": str, "in": str, "description": str}, ...],
            "message": str  # Human-readable message for the conversational AI
        }
    """
    missing_required: List[Dict[str, Any]] = []

    for param in input_parameters:
        if not isinstance(param, dict):
            continue

        param_location = param.get("in", "")

        if param_location == "body":
            is_required = param.get("required", False)
            if not is_required:
                continue

            content = param.get("content", {})
            json_schema = content.get("application/json", {})
            if json_schema:
                required_fields = json_schema.get("required", [])
                properties = json_schema.get("properties", {})

                for field_name in required_fields:
                    if field_name not in provided_params or provided_params.get(field_name) is None:
                        field_schema = properties.get(field_name, {})
                        missing_required.append(
                            {
                                "name": field_name,
                                "in": "body",
                                "type": field_schema.get("type", "unknown"),
                                "description": field_schema.get("description", ""),
                            }
                        )
        else:
            param_name = param.get("name")
            is_required = param.get("required", False)

            if not is_required:
                continue

            if param_name not in provided_params or provided_params.get(param_name) is None:
                schema = param.get("schema", {})
                missing_required.append(
                    {
                        "name": param_name,
                        "in": param_location,
                        "type": schema.get("type", "unknown"),
                        "description": param.get("description", ""),
                    }
                )

    if missing_required:
        param_descriptions = []
        for p in missing_required:
            desc = f"'{p['name']}' ({p['type']})"
            if p.get("description"):
                desc += f": {p['description']}"
            param_descriptions.append(desc)

        message = (
            f"Missing {len(missing_required)} required parameter(s): "
            + "; ".join(param_descriptions)
            + ". Please provide these values to proceed with the API call."
        )

        return {
            "valid": False,
            "missing_required": missing_required,
            "message": message,
        }

    return {"valid": True, "missing_required": [], "message": ""}


def _format_available_tools(ops: List[Dict[str, Any]]) -> str:
    """Render tools into a concise, LLM-friendly string list."""
    blocks: List[str] = []
    for op in ops:
        try:
            inputs_text = json.dumps(op.get("input_parameters") or [], indent=2)
        except Exception:
            inputs_text = str(op.get("input_parameters"))
        blocks.append(
            "Name: {name}\nDescription: {description}\nMethod: {method}\nInput Parameters: {inputs}".format(
                name=op.get("name"),
                description=op.get("description"),
                method=op.get("method"),
                inputs=inputs_text,
            )
        )
    return "\n\n".join(blocks)


def llm_choose_and_parametrize_tool(
    query: str,
    available_tools: str,
    openrouter_key: str,
    conversation_history: List[Dict[str, str]] | None = None,
) -> str:
    # Format conversation history if provided
    history_section = ""
    if conversation_history:
        history_lines = []
        for msg in conversation_history:
            role = msg.get("role", "unknown").capitalize()
            content = msg.get("content", "")
            history_lines.append(f"{role}: {content}")
        history_section = (
            "#Conversation History\n"
            "Use this context to understand what the user is asking for.\n"
            f"{chr(10).join(history_lines)}\n\n"
        )

    prompt = (
        "#Instructions\n"
        "Select the single best tool from the list to satisfy the user query.\n"
        "Extract parameters from the conversation. Pay attention to 'required' fields in the schema.\n"
        "- For required fields: extract values from the conversation history if present.\n"
        '- If no tool is applicable, return: {"tool": null, "parameters": {}}\n\n'
        f"{history_section}"
        "#Current User Message\n"
        f"{query}\n\n"
        "#Available Tools\n"
        f"{available_tools}\n\n"
        "#Response Format\n"
        "Return exactly one JSON object:\n"
        '{"tool": "<tool_name>", "parameters": {...}}\n'
        "Return only valid JSON, no markdown."
    )
    response = call_openrouter(prompt, "openai/gpt-oss-20b:nitro", openrouter_key)
    return response


async def call_from_row(
    row,
    base_url: str,
    auth_headers: Dict[str, str] | None = None,
    payload: dict | None = None,
):
    """
    row: a single DataFrame row representing the API endpoint schema
    auth_headers: dict of header_name -> value for authentication
    payload: dict containing path, query, and body fields generated by an LLM
    """

    payload = payload or {}
    auth_headers = auth_headers or {}

    # Normalize row to dict
    if hasattr(row, "items"):
        row_dict = dict(row)
    else:
        row_dict = row or {}

    method = str(row_dict.get("method", "")).upper()
    path = row_dict.get("path", "")
    inputs = row_dict.get("input_parameters") or []
    if isinstance(inputs, str):
        try:
            inputs = json.loads(inputs)
        except Exception:
            inputs = []

    url = base_url + path
    for param in inputs:
        if not isinstance(param, dict):
            continue
        if param.get("in") == "path":
            name = param.get("name")
            if name not in payload:
                raise HTTPException(400, f"Missing required path param: {name}")
            url = url.replace(f"{{{name}}}", str(payload.get(name)))

    query = {}
    for param in inputs:
        if not isinstance(param, dict):
            continue
        if param.get("in") == "query":
            name = param.get("name")
            if name in payload:
                query[name] = payload[name]

    json_body = None
    multipart_body = None

    for param in inputs:
        if param.get("in") == "body":
            content = param.get("content", {})
            if "multipart/form-data" in content:
                multipart_body = payload  # raw dict is fine
            elif "application/json" in content:
                json_body = payload

    # Use provided auth headers
    headers = dict(auth_headers)

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            params=query or None,
            json=json_body,
            files=multipart_body,
        )

    if response.is_error:
        raise HTTPException(response.status_code, response.text)

    try:
        return response.json()
    except Exception:
        return response.text


def _is_internal_api_call(base_url: str) -> bool:
    """Check if the API call is to an internal EM host."""
    if not base_url:
        return False
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    return parsed.netloc in INTERNAL_API_HOSTS


@app.cls(
    image=image,
    secrets=[modal.Secret.from_name("em-service-secrets")],
    timeout=60 * 5,
    scaledown_window=60 * 2,
)
class ToolsWorker:
    @modal.enter()
    async def initialize_worker(self):
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY")
        self.database_url = os.getenv("DATABASE_DSN")
        self._internal_api_key = os.getenv("INTERNAL_API_KEY", "internal-dev-key")
        self._encryption_key: bytes | None = None
        self._pool: asyncpg.Pool = await asyncpg.create_pool(
            self.database_url,
            min_size=2,
            max_size=4,
            statement_cache_size=0,  # Required for Supabase PgBouncer
        )

    @modal.exit()
    async def cleanup_worker(self):
        if self._pool:
            await self._pool.close()

    def _get_encryption_key(self) -> bytes:
        """Get or derive the encryption key from environment."""
        if self._encryption_key is not None:
            return self._encryption_key

        import base64
        import hashlib

        key_str = os.environ.get("ENCRYPTION_KEY")
        if not key_str:
            raise ValueError("ENCRYPTION_KEY environment variable not set")

        # If key is base64 encoded, decode it
        try:
            key_bytes = base64.b64decode(key_str)
            if len(key_bytes) == 32:
                self._encryption_key = key_bytes
                return self._encryption_key
        except Exception:
            pass

        # Otherwise, derive 32 bytes from the string using SHA-256
        self._encryption_key = hashlib.sha256(key_str.encode()).digest()
        return self._encryption_key

    def _decrypt_secret(self, encrypted: bytes) -> str:
        """Decrypt a secret using AES-256-GCM."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = self._get_encryption_key()
        nonce = encrypted[:12]
        ciphertext = encrypted[12:]

        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")

    async def _get_project_secrets(
        self, project_id: str, secret_names: List[str]
    ) -> Dict[str, str]:
        """Fetch and decrypt project secrets by name."""
        if not secret_names:
            return {}

        async with self._pool.acquire() as conn:
            project_uuid = uuid.UUID(project_id)
            rows = await conn.fetch(
                """
                SELECT secret_name, encrypted_value
                FROM project_secrets
                WHERE project_id = $1 AND secret_name = ANY($2)
                """,
                project_uuid,
                secret_names,
            )
            return {
                row["secret_name"]: self._decrypt_secret(row["encrypted_value"]) for row in rows
            }

    async def _get_spec_secrets_config(self, spec_id: str) -> Dict[str, str]:
        """Get secrets_config from tool_specs (maps header names to secret names)."""
        async with self._pool.acquire() as conn:
            spec_uuid = uuid.UUID(spec_id)
            row = await conn.fetchrow(
                "SELECT secrets_config FROM tool_specs WHERE id = $1",
                spec_uuid,
            )
            if row and row["secrets_config"]:
                config = row["secrets_config"]
                # Handle case where JSONB is returned as string
                if isinstance(config, str):
                    return json.loads(config)
                return config
            return {}

    async def _load_spec_json_content(self, spec_id: str) -> Dict[str, Any] | None:
        """Load the raw OpenAPI spec from tool_specs.json_content."""
        async with self._pool.acquire() as conn:
            spec_uuid = uuid.UUID(spec_id)
            row = await conn.fetchrow(
                "SELECT json_content FROM tool_specs WHERE id = $1",
                spec_uuid,
            )
            if row and row["json_content"]:
                content = row["json_content"]
                if isinstance(content, str):
                    return json.loads(content)
                return content
            return None

    async def _load_spec_base_url(self, spec_id: str) -> str | None:
        """Load stored base_url from tool_specs."""
        async with self._pool.acquire() as conn:
            spec_uuid = uuid.UUID(spec_id)
            row = await conn.fetchrow("SELECT base_url FROM tool_specs WHERE id = $1", spec_uuid)
            return row["base_url"] if row else None

    async def _update_spec_base_url(self, spec_id: str, base_url: str | None) -> None:
        """Persist extracted base_url into tool_specs."""
        async with self._pool.acquire() as conn:
            spec_uuid = uuid.UUID(spec_id)
            await conn.execute(
                "UPDATE tool_specs SET base_url = $1, updated_at = now() WHERE id = $2",
                base_url,
                spec_uuid,
            )

    def _load_operation_from_spec(
        self, spec_json: Dict[str, Any], tool_name: str
    ) -> Dict[str, Any] | None:
        """Load a single operation by name from an OpenAPI spec dict."""
        df = openapi_to_dataframe(spec_json)
        for row in df.to_dict("records"):
            if row.get("name") == tool_name:
                return {
                    "name": row["name"],
                    "description": row["description"],
                    "path": row["path"],
                    "method": row["method"],
                    "input_parameters": row["input_parameters"],
                    "output_schema": row["output_schema"],
                }
        return None

    async def _resolve_auth_headers(self, project_id: str, spec_id: str) -> Dict[str, str]:
        """
        Resolve secrets_config to actual auth headers.
        secrets_config: {"Authorization": "my_api_key", "X-Custom": "other_key"}
        Returns: {"Authorization": "Bearer sk-xxx", "X-Custom": "abc123"}
        """
        secrets_config = await self._get_spec_secrets_config(spec_id)
        if not secrets_config:
            return {}

        # Get all referenced secret names
        secret_names = list(secrets_config.values())
        secrets = await self._get_project_secrets(project_id, secret_names)

        # Build headers dict
        headers = {}
        for header_name, secret_name in secrets_config.items():
            if secret_name in secrets:
                value = secrets[secret_name]
                # Add Bearer prefix for Authorization header if not already present
                if header_name.lower() == "authorization" and not value.lower().startswith(
                    "bearer "
                ):
                    value = f"Bearer {value}"
                headers[header_name] = value

        return headers

    async def _load_operations(self, project_id: str, spec_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            project_uuid = uuid.UUID(project_id)
            spec_uuid = uuid.UUID(spec_id)
            rows = await conn.fetch(
                """
                SELECT name, description, path, method, input_parameters, output_schema, embedding
                FROM tool_operations
                WHERE project_id = $1 AND spec_id = $2
                """,
                project_uuid,
                spec_uuid,
            )
            ops: List[Dict[str, Any]] = []
            for r in rows:
                emb = _to_float_list(r.get("embedding"))
                ops.append(
                    {
                        "name": r["name"],
                        "description": r["description"],
                        "path": r["path"],
                        "method": r["method"],
                        "input_parameters": r["input_parameters"],
                        "output_schema": r["output_schema"],
                        "embedding": emb,
                    }
                )
            return ops

    async def _load_operation_by_name(
        self, project_id: str, spec_id: str, tool_name: str
    ) -> Dict[str, Any] | None:
        """Load a single operation by name, without embedding (faster for API calls)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT name, description, path, method, input_parameters, output_schema
                FROM tool_operations
                WHERE project_id = $1 AND spec_id = $2 AND name = $3
                """,
                uuid.UUID(project_id),
                uuid.UUID(spec_id),
                tool_name,
            )
            if row:
                return {
                    "name": row["name"],
                    "description": row["description"],
                    "path": row["path"],
                    "method": row["method"],
                    "input_parameters": row["input_parameters"],
                    "output_schema": row["output_schema"],
                }
            return None

    async def _search_similar_operations(
        self,
        project_id: str,
        spec_id: str,
        query_embedding: List[float],
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Search for similar operations using pgvector's cosine distance operator.
        Returns operations ordered by similarity (highest first).
        """
        async with self._pool.acquire() as conn:
            project_uuid = uuid.UUID(project_id)
            spec_uuid = uuid.UUID(spec_id)
            embedding_text = _vec_text(query_embedding)

            rows = await conn.fetch(
                """
                SELECT
                    name,
                    description,
                    path,
                    method,
                    input_parameters,
                    output_schema,
                    1 - (embedding <=> $3::vector) as similarity
                FROM tool_operations
                WHERE project_id = $1
                  AND spec_id = $2
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> $3::vector
                LIMIT $4
                """,
                project_uuid,
                spec_uuid,
                embedding_text,
                limit,
            )

            return [
                {
                    "name": r["name"],
                    "description": r["description"],
                    "path": r["path"],
                    "method": r["method"],
                    "input_parameters": r["input_parameters"],
                    "output_schema": r["output_schema"],
                    "similarity": float(r["similarity"]) if r["similarity"] else 0.0,
                }
                for r in rows
            ]

    async def _fetch_conversation_history(
        self, relationship_id: str, limit: int = 10
    ) -> List[Dict[str, str]]:
        """Fetch recent conversation history for tool selection context."""
        async with self._pool.acquire() as conn:
            relationship_uuid = uuid.UUID(relationship_id)
            rows = await conn.fetch(
                """
                SELECT role, content FROM messages
                WHERE relationship_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                relationship_uuid,
                limit,
            )
            # Reverse to get chronological order
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def _write_operations(
        self,
        *,
        project_id: str,
        companion_id: str | None,
        spec_id: str,
        df: pd.DataFrame,
    ) -> int:
        async with self._pool.acquire() as conn:
            project_uuid = uuid.UUID(project_id)
            spec_uuid = uuid.UUID(spec_id)
            companion_uuid = uuid.UUID(companion_id) if companion_id else None

            await conn.execute("DELETE FROM tool_operations WHERE spec_id = $1", spec_uuid)

            inserted = 0
            for row in df.to_dict("records"):
                emb_text = _vec_text(row.get("oai_small_embedding") or [])
                await conn.execute(
                    """
                    INSERT INTO tool_operations (
                        project_id,
                        spec_id,
                        name,
                        description,
                        path,
                        method,
                        input_parameters,
                        output_schema,
                        embedding,
                        embedding_model
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb,
                        CASE WHEN $9::text IS NULL THEN NULL::vector ELSE $9::text::vector END,
                        $10
                    )
                    """,
                    project_uuid,
                    spec_uuid,
                    _clean_val(row.get("name")),
                    _clean_val(row.get("description")),
                    _clean_val(row.get("path")),
                    _clean_val(row.get("method")),
                    json.dumps(
                        row.get("input_parameters")
                        if row.get("input_parameters") is not None
                        else []
                    ),
                    json.dumps(
                        row.get("output_schema") if row.get("output_schema") is not None else {}
                    ),
                    emb_text,
                    "text-embedding-3-small",
                )
                inserted += 1
            await conn.execute("UPDATE tool_specs SET updated_at = now() WHERE id = $1", spec_uuid)
            if companion_uuid:
                await conn.execute(
                    "UPDATE tool_specs SET companion_id = $2 WHERE id = $1",
                    spec_uuid,
                    companion_uuid,
                )
            return inserted

    async def _update_classifier_summary(self, spec_id: str, summary: str) -> None:
        """Update the classifier_summary field for a tool spec."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE tool_specs SET classifier_summary = $1, updated_at = now() WHERE id = $2",
                summary,
                uuid.UUID(spec_id),
            )

    async def _index_tools(
        self,
        request_id: str,
        project_id: str,
        spec_id: str,
        openapi_spec: Dict[str, Any],
        companion_id: str | None = None,
    ):
        base_url = extract_base_url(openapi_spec)
        if base_url:
            await self._update_spec_base_url(spec_id, base_url)

        df = openapi_to_dataframe(openapi_spec)
        df["oai_small_embedding"] = df["full_text"].apply(create_embedding_oai_small)

        # Generate classifier summary from operation names
        operation_names = df["name"].tolist()
        classifier_summary = generate_spec_summary(operation_names, self.openrouter_key)

        inserted = await self._write_operations(
            project_id=project_id,
            companion_id=companion_id,
            spec_id=spec_id,
            df=df,
        )

        # Update spec with classifier summary
        if classifier_summary:
            await self._update_classifier_summary(spec_id, classifier_summary)

        return {"request_id": request_id, "status": "success", "operations_indexed": inserted}

    @modal.method()
    async def index_tools(self, body: Dict[str, Any]):
        try:
            request_id = body["request_id"]
            project_id = body["project_id"]
            spec_id = body["spec_id"]
            openapi_spec = body["openapi_spec"]
            companion_id = body.get("companion_id")
            print(request_id, project_id, spec_id)
            return await self._index_tools(
                request_id, project_id, spec_id, openapi_spec, companion_id
            )
        except Exception as e:
            req = body.get("request_id") if isinstance(body, dict) else None
            print(f"Error indexing tools: {e}")
            return {"request_id": req, "status": "error", "message": str(e)}

    async def _choose_and_parametrize_tool(
        self,
        request_id: str,
        project_id: str,
        spec_id: str,
        query: str,
        relationship_id: str | None = None,
        spec_json: Dict[str, Any] | None = None,
    ):
        try:
            # Fetch conversation history if relationship_id provided
            conversation_history: List[Dict[str, str]] = []
            if relationship_id:
                tik = time.time()
                conversation_history = await self._fetch_conversation_history(
                    relationship_id, limit=10
                )
                tok = time.time()
                print(
                    f"Fetching conversation history took {tok - tik:.2f} seconds, got {len(conversation_history)} messages"
                )

            # Prefer spec payload from API runtime; fall back to worker DB load.
            if isinstance(spec_json, str):
                try:
                    spec_json = json.loads(spec_json)
                except Exception:
                    spec_json = None
            if spec_json:
                print("Using inline spec JSON from request payload")
            else:
                tik = time.time()
                spec_json = await self._load_spec_json_content(spec_id)
                tok = time.time()
                print(f"Loading spec JSON took {tok - tik:.2f} seconds")

            if not spec_json:
                return {
                    "request_id": request_id,
                    "status": "error",
                    "message": "Tool spec not found",
                }

            # Parse spec to get operations
            tik = time.time()
            df = openapi_to_dataframe(spec_json)
            operations = df.to_dict("records")
            tok = time.time()
            print(
                f"Parsing OpenAPI spec took {tok - tik:.2f} seconds, found {len(operations)} operations"
            )

            if not operations:
                return {
                    "request_id": request_id,
                    "status": "error",
                    "message": "No operations found in tool spec",
                }

            # For small specs, skip embeddings - just include all tools
            if len(operations) <= SMALL_SPEC_TOOL_THRESHOLD:
                print(
                    f"Small spec ({len(operations)} tools) - skipping embeddings, including all tools"
                )
                top = operations
            else:
                # Large spec - use embeddings + pgvector search
                tik = time.time()
                query_emb = create_embedding_oai_small(query)
                tok = time.time()
                print(f"Creating query embedding took {tok - tik:.2f} seconds")

                tik = time.time()
                top = await self._search_similar_operations(
                    project_id, spec_id, query_emb, limit=10
                )
                tok = time.time()
                print(f"pgvector similarity search took {tok - tik:.2f} seconds")

                if not top:
                    return {
                        "request_id": request_id,
                        "status": "error",
                        "message": "No tool operations indexed. Run index_tools first for specs with >3 tools.",
                    }

            available_tools = _format_available_tools(top)
            tik = time.time()
            llm_response = llm_choose_and_parametrize_tool(
                query,
                available_tools,
                self.openrouter_key,
                conversation_history=conversation_history if conversation_history else None,
            )
            parsed = _strip_json_block(llm_response) or {}
            tok = time.time()
            print(f"LLM call took {tok - tik:.2f} seconds")
            print(llm_response)

            tik = time.time()
            tool_name = parsed.get("tool") or parsed.get("name")

            if not tool_name:
                return {
                    "request_id": request_id,
                    "status": "error",
                    "message": "LLM did not return a tool name",
                }

            if not any(op.get("name") == tool_name for op in top):
                return {
                    "request_id": request_id,
                    "status": "error",
                    "message": f"Selected tool '{tool_name}' not found in top matches",
                }

            params = parsed.get("parameters") if isinstance(parsed, dict) else {}
            if isinstance(params, str):
                params = _strip_json_block(params) or {}
            if not isinstance(params, dict):
                params = {}
            tok = time.time()
            print(f"Parameter parsing took {tok - tik:.2f} seconds")

            return {
                "request_id": request_id,
                "status": "success",
                "best_tool": tool_name,
                "parameters": params,
            }
        except Exception as e:
            return {"request_id": request_id, "status": "error", "message": str(e)}

    @modal.method()
    async def retrieve_best_tool(self, body: Dict[str, Any]):
        try:
            request_id = body["request_id"]
            project_id = body["project_id"]
            spec_id = body["spec_id"]
            query = body["query"]
            print(request_id, project_id, spec_id, query)
            best_tool = await self._choose_and_parametrize_tool(
                request_id, project_id, spec_id, query
            )
            print(request_id, best_tool)
            if best_tool.get("status") == "success":
                return {
                    "request_id": request_id,
                    "status": "success",
                    "best_tool": best_tool.get("best_tool"),
                }
            return best_tool
        except Exception as e:
            print(f"Error retrieving tools: {e}")
            return {"request_id": request_id, "status": "error", "message": str(e)}

    @modal.method()
    async def choose_and_parametrize_tool(self, body: Dict[str, Any]):
        try:
            request_id = body["request_id"]
            project_id = body["project_id"]
            spec_id = body["spec_id"]
            query = body["query"]
            relationship_id = body.get("relationship_id")  # Optional, for conversation history
            spec_json = body.get("spec_json")
            print(
                request_id,
                project_id,
                spec_id,
                query,
                f"relationship_id={relationship_id}",
                f"has_spec_json={bool(spec_json)}",
            )
            result = await self._choose_and_parametrize_tool(
                request_id, project_id, spec_id, query, relationship_id, spec_json
            )
            print(request_id, result)
            return result
        except Exception as e:
            print(f"Error choosing and parametrizing tools: {e}")
            return {"request_id": body.get("request_id"), "status": "error", "message": str(e)}

    async def _use_api_tool(
        self,
        project_id: str,
        spec_id: str,
        base_url: str | None,
        tool_name: str,
        parameters: Dict[str, Any],
        relationship_id: str | None = None,
        spec_json: Dict[str, Any] | None = None,
    ):
        resolved_base_url = base_url
        if isinstance(spec_json, str):
            try:
                spec_json = json.loads(spec_json)
            except Exception:
                spec_json = None
        if not resolved_base_url and spec_json:
            resolved_base_url = extract_base_url(spec_json)
        if not resolved_base_url:
            resolved_base_url = await self._load_spec_base_url(spec_id)
        if not resolved_base_url:
            resolved_base_url = os.getenv("EM_API_BASE_URL", "https://api.emotionmachine.ai")

        # Try indexed operations first
        op = await self._load_operation_by_name(project_id, spec_id, tool_name)

        # Fall back to loading from inline spec JSON first, then worker DB.
        if not op:
            effective_spec_json = spec_json or await self._load_spec_json_content(spec_id)
            if effective_spec_json:
                op = self._load_operation_from_spec(effective_spec_json, tool_name)

        if not op:
            raise HTTPException(404, "Tool not found")

        # For internal API calls, inject relationship_id into parameters
        if _is_internal_api_call(resolved_base_url) and relationship_id:
            parameters = dict(parameters) if parameters else {}
            parameters["relationship_id"] = relationship_id
            print(f"Internal API call detected, injected relationship_id: {relationship_id}")

        # Validate parameters against the input schema before calling the API
        input_params = op.get("input_parameters") or []
        if isinstance(input_params, str):
            try:
                input_params = json.loads(input_params)
            except Exception:
                input_params = []

        validation_result = validate_parameters_against_schema(input_params, parameters or {})
        if not validation_result["valid"]:
            return {
                "status": "validation_error",
                "missing_required": validation_result["missing_required"],
                "message": validation_result["message"],
            }

        # Resolve auth headers from project secrets
        auth_headers = await self._resolve_auth_headers(project_id, spec_id)

        # For internal API calls, add X-Internal-Key header
        if _is_internal_api_call(resolved_base_url):
            auth_headers = dict(auth_headers) if auth_headers else {}
            auth_headers["X-Internal-Key"] = self._internal_api_key
            print(
                f"Internal API call detected, added X-Internal-Key header: '{self._internal_api_key[:8] if self._internal_api_key else 'None'}...'"
            )

        return await call_from_row(op, resolved_base_url, auth_headers, parameters)

    @modal.method()
    async def use_api_tool(self, body: Dict[str, Any]):
        try:
            request_id = body["request_id"]
            project_id = body["project_id"]
            spec_id = body["spec_id"]
            base_url = body.get("base_url")
            tool_name = body["tool_name"]
            parameters = body["parameters"]
            relationship_id = body.get("relationship_id")  # Optional, for internal API calls
            spec_json = body.get("spec_json")
            print(
                request_id,
                project_id,
                spec_id,
                base_url,
                tool_name,
                parameters,
                f"relationship_id={relationship_id}",
                f"has_spec_json={bool(spec_json)}",
            )
            result = await self._use_api_tool(
                project_id,
                spec_id,
                base_url,
                tool_name,
                parameters,
                relationship_id,
                spec_json,
            )

            # Check if validation failed before API call
            if isinstance(result, dict) and result.get("status") == "validation_error":
                print(f"{request_id} validation_error: {result.get('message')}")
                return {
                    "request_id": request_id,
                    "status": "error",
                    "missing_required": result.get("missing_required", []),
                    "message": result.get("message", ""),
                }

            print(request_id, result)
            return {"request_id": request_id, "status": "success", "api_response": result}

        except Exception as e:
            print(f"Error using tool: {e}")
            return {"request_id": request_id, "status": "error", "message": str(e)}
