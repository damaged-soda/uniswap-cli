from __future__ import annotations

import copy
import json

import httpx
import pytest

from uniswap_cli.config import Settings
from uniswap_cli.cursor import encode_cursor
from uniswap_cli.errors import UniswapError
from uniswap_cli.providers.subgraph import SubgraphProvider


def _settings(protocol: str) -> Settings:
    return Settings.from_env(
        {f"UNISWAP_SUBGRAPH_URL_1_{protocol.upper()}": "https://subgraph.test/graphql"}
    )


@pytest.mark.asyncio
async def test_v3_pool_list_normalizes_and_paginates(load_fixture) -> None:
    fixture = load_fixture("subgraph_v3_pools.json")
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        assert "orderBy: totalValueLockedUSD" in payload["query"]
        if len(requests) == 1:
            return httpx.Response(200, json=fixture)
        exhausted = copy.deepcopy(fixture)
        exhausted["data"]["pools"] = []
        return httpx.Response(200, json=exhausted)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = SubgraphProvider(_settings("v3"), 1, "v3", http_client=client)
    result = await provider.list_pools(
        limit=1,
        cursor=None,
        order_by="tvl-usd",
        direction="desc",
    )

    assert result.indexed_block == 25566569
    assert result.next_cursor is not None
    pool = result.data[0]
    assert pool["id"] == "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
    assert pool["fee_tier"] == "500"
    assert pool["token0"]["symbol"] == "USDC"
    assert pool["tvl_usd"] == "200000.1"

    with pytest.raises(UniswapError, match="cursor filters"):
        await provider.list_pools(
            limit=1,
            cursor=result.next_cursor,
            order_by="tvl-usd",
            direction="asc",
        )
    second = await provider.list_pools(
        limit=1,
        cursor=result.next_cursor,
        order_by="tvl-usd",
        direction="desc",
    )
    await client.aclose()

    assert second.data == []
    assert requests[1]["variables"]["skip"] == 1
    assert requests[1]["variables"]["where"]["totalValueLockedUSD_lte"] == "200000.1"


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
async def test_swap_rejects_missing_decimals_instead_of_breaking_schema(load_fixture) -> None:
    fixture = load_fixture("subgraph_v2_swaps.json")
    fixture["data"]["swaps"][0]["pair"]["token0"]["decimals"] = None
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=fixture))
    )
    provider = SubgraphProvider(_settings("v2"), 1, "v2", http_client=client)
    with pytest.raises(UniswapError, match="exact raw units"):
        await provider.list_swaps(
            pool_id="0x4444444444444444444444444444444444444444",
            start_timestamp=None,
            end_timestamp=None,
            limit=10,
            cursor=None,
            direction="asc",
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_single_v3_pool_query_uses_validated_literal_id(load_fixture) -> None:
    fixture = load_fixture("subgraph_v3_pools.json")
    response = copy.deepcopy(fixture)
    response["data"]["pool"] = response["data"].pop("pools")[0]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "query Pool($id" not in payload["query"]
        assert 'pool(id: "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640")' in payload["query"]
        return httpx.Response(200, json=response)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = SubgraphProvider(_settings("v3"), 1, "v3", http_client=client)
    result = await provider.get_pool("0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640")
    await client.aclose()
    assert result.data["fee_tier"] == "500"


@pytest.mark.asyncio
@pytest.mark.parametrize("query_kind", ["list", "get"])
async def test_v4_pool_queries_match_current_subgraph_schema(load_fixture, query_kind) -> None:
    fixture = load_fixture("subgraph_v4_pools.json")
    pool_id = fixture["data"]["pools"][0]["id"]

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "isExternalLiquidity" not in payload["query"]
        assert "tickSpacing" in payload["query"]
        response = copy.deepcopy(fixture)
        if query_kind == "get":
            response["data"]["pool"] = response["data"].pop("pools")[0]
        return httpx.Response(200, json=response)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = SubgraphProvider(_settings("v4"), 1, "v4", http_client=client)
    if query_kind == "list":
        result = await provider.list_pools(
            limit=1,
            cursor=None,
            order_by="tvl-usd",
            direction="desc",
        )
    else:
        result = await provider.get_pool(pool_id)
    await client.aclose()

    normalized = result.data[0] if query_kind == "list" else result.data
    assert normalized["id"] == pool_id
    assert normalized["hooks"] == "0x3333333333333333333333333333333333333333"
    assert "external_liquidity" not in normalized


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


@pytest.mark.asyncio
async def test_swap_cursor_rejects_non_numeric_boundary_before_query() -> None:
    pool = "0x4444444444444444444444444444444444444444"
    cursor = encode_cursor(
        "swaps-list",
        1,
        "v2",
        boundary="not-a-number",
        tie_offset=1,
        query={"direction": "asc", "where": {"pair": pool}},
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: pytest.fail("must fail before query"))
    )
    provider = SubgraphProvider(_settings("v2"), 1, "v2", http_client=client)
    with pytest.raises(UniswapError, match="timestamp boundary"):
        await provider.list_swaps(
            pool_id=pool,
            start_timestamp=None,
            end_timestamp=None,
            limit=10,
            cursor=cursor,
            direction="asc",
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_non_object_subgraph_row_fails_loudly(load_fixture) -> None:
    fixture = load_fixture("subgraph_v2_swaps.json")
    fixture["data"]["swaps"].append("not-an-object")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=fixture))
    )
    provider = SubgraphProvider(_settings("v2"), 1, "v2", http_client=client)
    with pytest.raises(UniswapError, match="non-object row"):
        await provider.list_swaps(
            pool_id="0x4444444444444444444444444444444444444444",
            start_timestamp=None,
            end_timestamp=None,
            limit=10,
            cursor=None,
            direction="asc",
        )
    await client.aclose()
