from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol
from urllib.parse import urlparse
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, HttpUrl

from ..errors import (
    ProviderAuthFailedError,
    ProviderBadResponseError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)


class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=300)
    max_results: int = Field(default=5, ge=1, le=10)
    request_id: str
    routebook_id: UUID
    place_id: UUID


class SearchResult(BaseModel):
    title: str
    url: HttpUrl
    snippet: str
    site_name: str | None = None
    published_at: datetime | None = None
    rank: int = Field(ge=1)


class SearchResponse(BaseModel):
    results: list[SearchResult] = Field(default_factory=list)


class SearchProvider(Protocol):
    def search(self, request: SearchRequest) -> SearchResponse: ...


class ZhipuWebSearchPrimeProvider:
    """Small MCP Streamable-HTTP adapter; credentials never enter request/log payloads."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        timeout_seconds: float = 12,
        client: httpx.Client | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json, text/event-stream",
        }

    def search(self, request: SearchRequest) -> SearchResponse:
        headers = dict(self._headers)
        initialize = {
            "jsonrpc": "2.0",
            "id": f"{request.request_id}:initialize",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "routebook-agent", "version": "1.0"},
            },
        }
        initialized = self._post(initialize, headers=headers)
        session_id = initialized.headers.get("mcp-session-id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
            allow_empty=True,
        )
        payload = {
            "jsonrpc": "2.0",
            "id": request.request_id,
            "method": "tools/call",
            "params": {
                "name": "web_search_prime",
                "arguments": {
                    "search_query": request.query,
                    "content_size": "medium",
                },
            },
        }
        response = self._post(payload, headers=headers)
        data = self._decode(response)
        error = data.get("error")
        if isinstance(error, dict):
            raise ProviderBadResponseError(
                details={"provider": "zhipu_web_search_prime", "reason": "mcp_error"}
            )
        return SearchResponse(results=self._map_results(data, request.max_results))

    def _post(
        self,
        payload: dict[str, object],
        *,
        headers: dict[str, str],
        allow_empty: bool = False,
    ) -> httpx.Response:
        try:
            response = self._client.post(self._endpoint, headers=headers, json=payload)
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(details={"provider": "zhipu_web_search_prime"}) from exc
        if response.status_code in {401, 403}:
            raise ProviderAuthFailedError(details={"provider": "zhipu_web_search_prime"})
        if response.status_code == 429:
            raise ProviderRateLimitedError(details={"provider": "zhipu_web_search_prime"})
        if response.status_code >= 500:
            raise ProviderUnavailableError(details={"provider": "zhipu_web_search_prime"})
        if response.status_code >= 400:
            raise ProviderBadResponseError(details={"provider": "zhipu_web_search_prime"})
        if not allow_empty and not response.content:
            raise ProviderBadResponseError(details={"provider": "zhipu_web_search_prime"})
        return response

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, object]:
        try:
            if "text/event-stream" in response.headers.get("content-type", ""):
                lines = [
                    line[5:].strip()
                    for line in response.text.splitlines()
                    if line.startswith("data:")
                ]
                return json.loads(lines[-1]) if lines else {}
            value = response.json()
            return value if isinstance(value, dict) else {}
        except (ValueError, json.JSONDecodeError) as exc:
            raise ProviderBadResponseError(details={"provider": "zhipu_web_search_prime"}) from exc

    @classmethod
    def _map_results(cls, payload: dict[str, object], limit: int) -> list[SearchResult]:
        result = payload.get("result")
        if isinstance(result, dict):
            content = result.get("content", result.get("results", []))
        else:
            content = []
        if (
            isinstance(content, list)
            and content
            and isinstance(content[0], dict)
            and "text" in content[0]
        ):
            try:
                decoded = json.loads(str(content[0]["text"]))
                if isinstance(decoded, str):
                    decoded = json.loads(decoded)
                content = decoded.get("results", decoded) if isinstance(decoded, dict) else decoded
            except ValueError:
                content = []
        rows: list[SearchResult] = []
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("link")
            title = item.get("title")
            if (
                not isinstance(url, str)
                or not url.startswith(("http://", "https://"))
                or not isinstance(title, str)
            ):
                continue
            rows.append(
                SearchResult(
                    title=title[:300],
                    url=url,
                    snippet=str(item.get("snippet") or item.get("content") or "")[:2000],
                    site_name=str(
                        item.get("site_name") or item.get("media") or urlparse(url).netloc
                    ),
                    rank=len(rows) + 1,
                )
            )
            if len(rows) >= limit:
                break
        return rows
