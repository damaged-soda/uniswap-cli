from __future__ import annotations

import httpx
import pytest

from uniswap_cli.config import Settings
from uniswap_cli.errors import UniswapError
from uniswap_cli.providers.subgraph import SubgraphProvider


def _settings(protocol: str) -> Settings:
    return Settings.from_env(
        {f"UNISWAP_SUBGRAPH_URL_1_{protocol.upper()}": "https://subgraph.test/graphql"}
    )


@pytest.mark.asyncio
async def test_v3_pool_list_normalizes_and_paginates(load_fixture) -> None:
    fixture = load_fixture("subgraph_v3_pools.json")

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode()
        assert "orderBy: totalValueLockedUSD" in payload
        return httpx.Response(200, json=fixture)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = SubgraphProvider(_settings("v3"), 1, "v3", http_client=client)
    result = await provider.list_pools(
        limit=1,
        cursor=None,
        order_by="tvl-usd",
        direction="desc",
    )
    await client.aclose()

    assert result.indexed_block == 25566569
    assert result.next_cursor is not None
    pool = result.data[0]
    assert pool["id"] == "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
    assert pool["fee_tier"] == "500"
    assert pool["token0"]["symbol"] == "USDC"
    assert pool["tvl_usd"] == "200000.1"


@pytest.mark.asyncio
async def test_v2_swaps_use_signed_pool_deltas_and_raw_units(load_fixture) -> None:
    fixture = load_fixture("subgraph_v2_swaps.json")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=fixture))
    )
    provider = SubgraphProvider(_settings("v2"), 1, "v2", http_client=client)
    result = await provider.list_swaps(
        pool_id="0x4444444444444444444444444444444444444444",
        start_timestamp=1784461100,
        end_timestamp=1784461200,
        limit=10,
        cursor=None,
        direction="asc",
    )
    await client.aclose()

    swap = result.data[0]
    assert swap["amount0"] == "10.5"
    assert swap["amount1"] == "-0.005"
    assert swap["amount0_raw"] == "10500000"
    assert swap["amount1_raw"] == "-5000000000000000"
    assert swap["block_number"] == 25566509


@pytest.mark.asyncio
async def test_v4_ohlcv_series(load_fixture) -> None:
    fixture = load_fixture("subgraph_v4_series.json")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=fixture))
    )
    provider = SubgraphProvider(_settings("v4"), 1, "v4", http_client=client)
    result = await provider.series(
        pool_id="0x" + "77" * 32,
        metric="ohlcv",
        interval="1d",
        start_timestamp=None,
        end_timestamp=None,
        limit=10,
        cursor=None,
    )
    await client.aclose()

    point = result.data[0]
    assert point["value"] == {"open": "1990", "high": "2025", "low": "1980", "close": "2000"}
    assert point["price_unit"] == "token1-per-token0"
    assert point["fees_usd"] == "10.25"


@pytest.mark.asyncio
async def test_graphql_errors_are_not_treated_as_empty_data() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"errors": [{"message": "auth error: missing authorization header"}]}
            )
        )
    )
    provider = SubgraphProvider(_settings("v3"), 1, "v3", http_client=client)
    with pytest.raises(UniswapError) as caught:
        await provider.health()
    await client.aclose()
    assert caught.value.code == "SUBGRAPH_AUTH_FAILED"
