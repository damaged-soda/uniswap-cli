from __future__ import annotations

import asyncio
import email.utils
import random
from datetime import UTC, datetime
from typing import Any

import httpx

from uniswap_cli.config import Settings
from uniswap_cli.errors import UniswapError, redact_text


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip()
    try:
        return max(float(text), 0.0)
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max((parsed - datetime.now(UTC)).total_seconds(), 0.0)


def _redact_values(value: str, sensitive_values: tuple[str, ...]) -> str:
    redacted = value
    for sensitive in sensitive_values:
        if sensitive:
            redacted = redacted.replace(sensitive, "[redacted]")
            if sensitive.lower().startswith("bearer "):
                redacted = redacted.replace(sensitive[7:], "[redacted]")
    return redact_text(redacted)


def _upstream_message(
    payload: Any, fallback: str, *, sensitive_values: tuple[str, ...] = ()
) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return _redact_values(str(error["message"]), sensitive_values)[:1_000]
        if isinstance(error, str):
            return _redact_values(error, sensitive_values)[:1_000]
        if payload.get("message"):
            return _redact_values(str(payload["message"]), sensitive_values)[:1_000]
    return _redact_values(fallback, sensitive_values)[:1_000]


class JsonHttpClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=settings.timeout_seconds)
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)

    async def __aenter__(self) -> JsonHttpClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        endpoint_label: str,
        operation: str,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> Any:
        total_attempts = self.settings.max_retries + 1
        last_error: UniswapError | None = None
        for attempt in range(1, total_attempts + 1):
            response: httpx.Response | None = None
            try:
                async with self._semaphore:
                    response = await self._client.request(
                        method,
                        url,
                        headers=headers,
                        json=json_body,
                        timeout=self.settings.timeout_seconds,
                    )
            except httpx.HTTPError as exc:
                last_error = UniswapError(
                    "UPSTREAM_NETWORK_ERROR",
                    f"{operation} failed: {type(exc).__name__}",
                    retryable=True,
                    context={"endpoint": endpoint_label, "attempt": attempt},
                )
            else:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None

                retryable_status = response.status_code == 429 or response.status_code >= 500
                if response.status_code < 400:
                    if payload is None:
                        raise UniswapError(
                            "UPSTREAM_INVALID_RESPONSE",
                            f"{operation} returned non-JSON content",
                            context={
                                "endpoint": endpoint_label,
                                "status_code": response.status_code,
                            },
                        )
                    return payload

                if response.status_code in {401, 403}:
                    code = "UPSTREAM_AUTH_FAILED"
                elif response.status_code == 429:
                    code = "UPSTREAM_RATE_LIMITED"
                else:
                    code = "UPSTREAM_HTTP_ERROR"
                last_error = UniswapError(
                    code,
                    f"{operation} failed with HTTP {response.status_code}",
                    retryable=retryable_status,
                    context={
                        "endpoint": endpoint_label,
                        "status_code": response.status_code,
                        "attempt": attempt,
                        "upstream_message": _upstream_message(
                            payload,
                            response.text,
                            sensitive_values=tuple((headers or {}).values()),
                        ),
                    },
                )

            if last_error is None or not last_error.retryable or attempt >= total_attempts:
                assert last_error is not None
                raise last_error
            delay = self.settings.retry_backoff_seconds * (2 ** (attempt - 1))
            if response is not None:
                retry_after = _retry_after_seconds(response.headers.get("retry-after"))
                if retry_after is not None:
                    delay = max(delay, retry_after)
            delay += random.uniform(0, delay * 0.1)
            await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error
