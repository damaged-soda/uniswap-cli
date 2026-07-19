from __future__ import annotations

import httpx
import pytest

from uniswap_cli.config import Settings
from uniswap_cli.errors import UniswapError
from uniswap_cli.http import JsonHttpClient


def _settings() -> Settings:
    return Settings.from_env({"UNISWAP_HTTP_MAX_RETRIES": "0"})


@pytest.mark.asyncio
async def test_remote_protocol_error_is_structured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("server disconnected", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http = JsonHttpClient(_settings(), client=client)
    with pytest.raises(UniswapError) as caught:
        await http.request(
            "POST",
            "https://example.test/graphql",
            endpoint_label="test",
            operation="query",
        )
    await client.aclose()
    assert caught.value.code == "UPSTREAM_NETWORK_ERROR"


@pytest.mark.asyncio
async def test_invalid_url_is_structured() -> None:
    http = JsonHttpClient(_settings())
    with pytest.raises(UniswapError) as caught:
        await http.request(
            "POST",
            "http://[::1",
            endpoint_label="test",
            operation="query",
        )
    await http.close()
    assert caught.value.code == "UPSTREAM_NETWORK_ERROR"


@pytest.mark.asyncio
async def test_upstream_echo_of_authorization_value_is_redacted() -> None:
    secret = "Bearer very-secret-key"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                403, json={"message": f"invalid authorization {secret}"}
            )
        )
    )
    http = JsonHttpClient(_settings(), client=client)
    with pytest.raises(UniswapError) as caught:
        await http.request(
            "POST",
            "https://example.test/graphql",
            endpoint_label="test",
            operation="query",
            headers={"authorization": secret},
        )
    await client.aclose()
    rendered = str(caught.value.as_dict())
    assert "very-secret-key" not in rendered
    assert "[redacted]" in rendered


@pytest.mark.asyncio
async def test_upstream_echo_of_bare_url_key_segment_is_redacted() -> None:
    key = "alchemy-secret-segment"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(403, json={"message": f"rejected key {key}"})
        )
    )
    http = JsonHttpClient(_settings(), client=client)
    with pytest.raises(UniswapError) as caught:
        await http.request(
            "POST",
            f"https://rpc.test/v2/{key}",
            endpoint_label="rpc:1:1",
            operation="eth_blockNumber",
        )
    await client.aclose()
    rendered = str(caught.value.as_dict())
    assert key not in rendered
    assert "[redacted]" in rendered
