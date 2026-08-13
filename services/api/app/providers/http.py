from __future__ import annotations

import logging
import time
from collections.abc import Callable, Generator, Mapping
from typing import Any

import httpx

from ..errors import (
    ProviderAuthFailedError,
    ProviderBadResponseError,
    ProviderError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)

log = logging.getLogger("routebook.providers")


class QueryKeyAuth(httpx.Auth):
    """Inject a query credential without placing it in adapter call arguments."""

    def __init__(self, key: str, *, parameter: str = "key") -> None:
        self._key = key
        self._parameter = parameter

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.url = request.url.copy_merge_params({self._parameter: self._key})
        yield request


class HeaderKeyAuth(httpx.Auth):
    """Inject a header credential without placing it in adapter call arguments."""

    def __init__(self, key: str, *, header: str) -> None:
        self._key = key
        self._header = header

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers[self._header] = self._key
        yield request


class ProviderHttpClient:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        client: httpx.Client | None = None,
        auth: httpx.Auth | None = None,
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 8.0,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.2,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleeper = sleeper
        self._auth = auth
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(read_timeout_seconds, connect=connect_timeout_seconds),
        )

    def get_json(
        self,
        path: str,
        *,
        operation: str,
        params: Mapping[str, str],
        validate: Callable[[dict[str, Any]], None] | None = None,
        accepted_http_statuses: frozenset[int] = frozenset(),
    ) -> dict[str, Any]:
        started_at = time.monotonic()
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.get(
                    path,
                    params=params,
                    auth=self._auth,
                )
                if response.status_code not in accepted_http_statuses:
                    self._raise_for_http_status(response, operation=operation)
                payload: object
                if response.status_code == 204 and not response.content:
                    payload = {"code": "204"}
                else:
                    payload = response.json()
                if not isinstance(payload, dict):
                    raise ProviderBadResponseError(
                        details={"provider": self.provider, "operation": operation}
                    )
                if validate is not None:
                    validate(payload)
                log.info(
                    "provider call completed provider=%s operation=%s "
                    "attempt=%d status=%d duration_ms=%d",
                    self.provider,
                    operation,
                    attempt,
                    response.status_code,
                    int((time.monotonic() - started_at) * 1000),
                )
                return payload
            except httpx.TransportError as exc:
                if attempt == self._max_attempts:
                    self._log_failure(operation, "PROVIDER_UNAVAILABLE", attempt, started_at)
                    raise ProviderUnavailableError(
                        details={"provider": self.provider, "operation": operation}
                    ) from exc
                self._backoff(attempt, operation)
            except (ProviderRateLimitedError, ProviderUnavailableError) as exc:
                if attempt == self._max_attempts:
                    self._log_failure(operation, exc.code, attempt, started_at)
                    raise
                self._backoff(attempt, operation)
            except ProviderError as exc:
                self._log_failure(operation, exc.code, attempt, started_at)
                raise
            except ValueError as exc:
                self._log_failure(operation, "PROVIDER_BAD_RESPONSE", attempt, started_at)
                raise ProviderBadResponseError(
                    details={"provider": self.provider, "operation": operation}
                ) from exc
        raise AssertionError("provider retry loop exhausted unexpectedly")

    def _raise_for_http_status(self, response: httpx.Response, *, operation: str) -> None:
        status = response.status_code
        details = {"provider": self.provider, "operation": operation, "http_status": status}
        if 200 <= status < 300:
            return
        if status in {401, 403}:
            raise ProviderAuthFailedError(details=details)
        if status == 429:
            raise ProviderRateLimitedError(details=details)
        if status >= 500:
            raise ProviderUnavailableError(details=details)
        raise ProviderError(details=details)

    def _backoff(self, attempt: int, operation: str) -> None:
        delay = self._retry_backoff_seconds * (2 ** (attempt - 1))
        log.warning(
            "provider call retry provider=%s operation=%s attempt=%d delay_seconds=%.2f",
            self.provider,
            operation,
            attempt,
            delay,
        )
        self._sleeper(delay)

    def _log_failure(
        self,
        operation: str,
        error_code: str,
        attempt: int,
        started_at: float,
    ) -> None:
        log.warning(
            "provider call failed provider=%s operation=%s attempt=%d error_code=%s duration_ms=%d",
            self.provider,
            operation,
            attempt,
            error_code,
            int((time.monotonic() - started_at) * 1000),
        )
