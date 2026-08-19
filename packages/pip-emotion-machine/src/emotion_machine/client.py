"""Main client for the Emotion Machine SDK."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx

from .behaviors import BehaviorAPI
from .exceptions import APIError, KnowledgeJobFailed
from .relationship import Relationship

DEFAULT_BASE_URL = "https://api.emotionmachine.ai"
DEFAULT_TIMEOUT = 30.0

# Final states for knowledge jobs
KNOWLEDGE_FINAL_STATES = {"succeeded", "failed"}


class CompanionAPI:
    """API for managing companions."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
    ) -> None:
        self._http_client = http_client
        self._base_url = base_url
        self._headers = headers

    async def list(self) -> list[dict[str, Any]]:
        """List all companions."""
        response = await self._http_client.get(
            f"{self._base_url}/v1/companions",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()

    async def create(
        self,
        name: str,
        *,
        description: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new companion.

        Args:
            name: Companion name
            description: Optional description
            config: Configuration dict (system_prompt, memory, knowledge, etc.)

        Returns:
            Created companion dict
        """
        payload: dict[str, Any] = {"name": name}
        if description is not None:
            payload["description"] = description
        if config is not None:
            payload["config"] = config

        response = await self._http_client.post(
            f"{self._base_url}/v1/companions",
            headers=self._headers,
            json=payload,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()

    async def get(self, companion_id: str) -> dict[str, Any]:
        """Get a companion by ID."""
        response = await self._http_client.get(
            f"{self._base_url}/v1/companions/{companion_id}",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()

    async def update(
        self,
        companion_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update a companion.

        Args:
            companion_id: Companion ID
            name: New name (if updating)
            description: New description (if updating)
            config: New config (if updating)

        Returns:
            Updated companion dict
        """
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if config is not None:
            payload["config"] = config

        response = await self._http_client.patch(
            f"{self._base_url}/v1/companions/{companion_id}",
            headers=self._headers,
            json=payload,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()

    async def delete(self, companion_id: str) -> None:
        """Delete a companion."""
        response = await self._http_client.delete(
            f"{self._base_url}/v1/companions/{companion_id}",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )


class KnowledgeAPI:
    """API for managing companion knowledge."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
    ) -> None:
        self._http_client = http_client
        self._base_url = base_url
        self._headers = headers

    async def ingest(
        self,
        companion_id: str,
        *,
        file_path: str | Path | None = None,
        content: str | None = None,
        payload_type: str = "markdown",
        key: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        """Ingest knowledge into a companion.

        Args:
            companion_id: Companion ID
            file_path: Path to file to ingest
            content: Raw content to ingest (if not using file_path)
            payload_type: Type of content (markdown, json, text)
            key: Optional key for the content
            mime_type: MIME type for file uploads

        Returns:
            Job info dict with 'id' key
        """
        url = f"{self._base_url}/v1/companions/{companion_id}/knowledge"

        if file_path:
            path = Path(file_path)
            if not path.is_file():
                raise APIError(f"File not found: {file_path}")

            with path.open("rb") as f:
                file_content = f.read()

            if mime_type is None:
                import mimetypes

                mime_type = (
                    mimetypes.guess_type(str(path))[0] or "application/octet-stream"
                )

            files = {"file": (path.name, file_content, mime_type)}
            data = {"type": payload_type}

            # Use multipart headers (no Content-Type - httpx handles it)
            headers = {k: v for k, v in self._headers.items() if k != "Content-Type"}

            response = await self._http_client.post(
                url,
                files=files,
                data=data,
                headers=headers,
                timeout=180.0,
            )
        else:
            payload: dict[str, Any] = {"payload_type": payload_type}
            if content is not None:
                payload["content"] = content
            if key is not None:
                payload["key"] = key

            response = await self._http_client.post(
                url,
                headers=self._headers,
                json=payload,
            )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """Get knowledge job status."""
        response = await self._http_client.get(
            f"{self._base_url}/v1/knowledge-jobs/{job_id}",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()

    async def wait(
        self,
        job_id: str,
        *,
        timeout: float = 120.0,
        interval: float = 0.5,
        raise_on_failure: bool = True,
    ) -> dict[str, Any]:
        """Wait for a knowledge job to complete.

        Args:
            job_id: Job ID to wait for
            timeout: Maximum time to wait in seconds
            interval: Polling interval in seconds
            raise_on_failure: If True, raise KnowledgeJobFailed on failure

        Returns:
            Final job status dict

        Raises:
            KnowledgeJobFailed: If job fails and raise_on_failure is True
            asyncio.TimeoutError: If job doesn't complete within timeout
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while loop.time() < deadline:
            job = await self.get_job(job_id)
            status = job.get("status", "").lower()

            if status in KNOWLEDGE_FINAL_STATES:
                if status == "failed" and raise_on_failure:
                    raise KnowledgeJobFailed(job_id, job.get("error"))
                return job

            await asyncio.sleep(interval)

        raise asyncio.TimeoutError(f"Knowledge job {job_id} timed out after {timeout}s")

    async def search(
        self,
        companion_id: str,
        query: str,
        *,
        max_results: int = 5,
        filters: dict[str, Any] | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """Search companion knowledge.

        Args:
            companion_id: Companion ID
            query: Search query
            max_results: Maximum results to return
            filters: Optional search filters
            mode: Search mode (hybrid, semantic, keyword)

        Returns:
            Search results dict
        """
        payload: dict[str, Any] = {"query": query, "max_results": max_results}
        if filters is not None:
            payload["filters"] = filters
        if mode is not None:
            payload["mode"] = mode

        response = await self._http_client.post(
            f"{self._base_url}/v1/companions/{companion_id}/knowledge/search",
            headers=self._headers,
            json=payload,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()

    async def list_assets(self, companion_id: str) -> list[dict[str, Any]]:
        """List knowledge assets for a companion."""
        response = await self._http_client.get(
            f"{self._base_url}/v1/companions/{companion_id}/knowledge-assets",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()


class EmotionMachine:
    """Main client for the Emotion Machine API.

    Usage:
        # Basic usage
        em = EmotionMachine(api_key="...")
        rel = em.relationship(companion_id, user_id)
        response = await rel.send("Hello!")
        await em.close()

        # Context manager (recommended)
        async with EmotionMachine(api_key="...") as em:
            rel = em.relationship(companion_id, user_id)
            response = await rel.send("Hello!")
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the Emotion Machine client.

        Args:
            api_key: API key (defaults to EM_API_KEY env var)
            base_url: API base URL (defaults to localhost:8100)
            timeout: Request timeout in seconds
        """
        self._api_key = api_key or os.getenv("EM_API_KEY")
        if not self._api_key:
            raise ValueError("API key required: pass api_key or set EM_API_KEY env var")

        self._base_url = base_url or os.getenv("EM_BASE_URL", DEFAULT_BASE_URL)
        self._timeout = timeout

        self._http_client = httpx.AsyncClient(timeout=timeout)
        self._headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        # Resource APIs
        self._companions: CompanionAPI | None = None
        self._knowledge: KnowledgeAPI | None = None
        self._behaviors: BehaviorAPI | None = None

    @property
    def companions(self) -> CompanionAPI:
        """Companion management API."""
        if self._companions is None:
            self._companions = CompanionAPI(
                http_client=self._http_client,
                base_url=self._base_url,
                headers=self._headers,
            )
        return self._companions

    @property
    def knowledge(self) -> KnowledgeAPI:
        """Knowledge management API."""
        if self._knowledge is None:
            self._knowledge = KnowledgeAPI(
                http_client=self._http_client,
                base_url=self._base_url,
                headers=self._headers,
            )
        return self._knowledge

    @property
    def behaviors(self) -> BehaviorAPI:
        """Behavior management API."""
        if self._behaviors is None:
            self._behaviors = BehaviorAPI(
                http_client=self._http_client,
                base_url=self._base_url,
                headers=self._headers,
            )
        return self._behaviors

    def relationship(self, companion_id: str, user_id: str) -> Relationship:
        """Get a relationship handle for a user-companion pair.

        This creates a lightweight handle without making any network calls.
        All operations on the relationship (send, stream, etc.) are async
        and will make API calls when invoked.

        Args:
            companion_id: The companion ID
            user_id: The user ID (must match [a-zA-Z0-9_-]+, max 128 chars)

        Returns:
            Relationship handle
        """
        return Relationship(
            http_client=self._http_client,
            base_url=self._base_url,
            headers=self._headers,
            companion_id=companion_id,
            user_id=user_id,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._http_client.aclose()

    async def __aenter__(self) -> EmotionMachine:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
