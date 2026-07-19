from __future__ import annotations

import json

import httpx
import pytest

from uniswap_cli.config import Settings
from uniswap_cli.cursor import encode_cursor
from uniswap_cli.errors import UniswapError
from uniswap_cli.providers.rpc import V3_SWAP_TOPIC, RpcProvider, _hex_int, _human_amount


def _settings(**extra: str) -> Settings:
    return Settings.from_env({"RPC_URL_1": "https://rpc.test/key", **extra})


def test_large_human_amount_is_exact_and_empty_hex_is_rejected() -> None:
    assert _human_amount(500000000000123456789012345678, 18) == "500000000000.123456789012345678"
    with pytest.raises(UniswapError):
        _hex_int("0x", field="test")


@pytest.mark.asyncio
async def test_rpc_source_id_reports_the_endpoint_that_succeeded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if request.url.host == "first.test":
            return httpx.Response(500, json={"message": "unavailable"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": hex(123)})

    settings = Settings.from_env(
        {
            "RPC_URL_1": "https://first.test,https://second.test",
            "UNISWAP_HTTP_MAX_RETRIES": "0",
        }
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = RpcProvider(settings, 1, http_client=client)
    assert await provider.block_number() == 123
    await client.aclose()
    assert provider._source_id() == "rpc:1:2"


@pytest.mark.asyncio
async def test_eth_get_logs_adapts_to_provider_block_limit() -> None:
    requests: list[tuple[int, int]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": payload["id"], "result": hex(119)}
            )
        assert payload["method"] == "eth_getLogs"
        params = payload["params"][0]
        start, end = int(params["fromBlock"], 16), int(params["toBlock"], 16)
        requests.append((start, end))
        if end - start + 1 > 10:
            return httpx.Response(
                400,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "error": {
                        "code": -32600,
                        "message": (
                            "Under the Free tier plan, you can make eth_getLogs requests "
                            "with up to a 10 block range."
                        ),
                    },
                },
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = RpcProvider(_settings(), 1, http_client=client)
    result = await provider.raw_events(
        address="0x" + "11" * 20,
        topics=[V3_SWAP_TOPIC],
        start_block=100,
        end_block=119,
        limit=100,
        cursor=None,
        direction="asc",
    )
    await client.aclose()

    assert result.data == []
    assert requests == [(100, 119), (100, 109), (110, 119)]
    assert result.extra_meta["rpc_log_requests"] == 3
    assert result.indexed_block == 119
    assert result.extra_meta["range_complete"] is True


@pytest.mark.asyncio
async def test_log_cursor_is_bound_to_address_topics_and_range() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": payload["id"], "result": hex(119)}
            )
        pytest.fail("mismatched cursor must fail before eth_getLogs")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = RpcProvider(_settings(), 1, http_client=client)
    cursor = encode_cursor(
        "raw-events",
        1,
        "raw",
        block=100,
        log_index=0,
        query={
            "address": "0x" + "22" * 20,
            "topics": [V3_SWAP_TOPIC],
            "start_block": 100,
            "end_block": 119,
            "direction": "asc",
        },
    )
    with pytest.raises(UniswapError, match="cursor filters"):
        await provider.raw_events(
            address="0x" + "11" * 20,
            topics=[V3_SWAP_TOPIC],
            start_block=100,
            end_block=119,
            limit=100,
            cursor=cursor,
            direction="asc",
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_v3_swap_decoder_matches_live_fixture(load_fixture) -> None:
    provider = RpcProvider(_settings(), 1, http_client=httpx.AsyncClient())
    raw = load_fixture("rpc_v3_swap_log.json")
    block_number = int(raw["blockNumber"], 16)
    provider._block_cache[block_number] = {
        "number": block_number,
        "timestamp": 1784461139,
        "hash": raw["blockHash"],
    }
    token0 = {"address": "0x" + "aa" * 20, "symbol": "USDC", "name": None, "decimals": 6}
    token1 = {"address": "0x" + "bb" * 20, "symbol": "WETH", "name": None, "decimals": 18}
    swap = provider._decode_swap(
        raw,
        "v3",
        "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        token0,
        token1,
    )
    await provider.http._client.aclose()

    assert swap["amount0_raw"] == "-44135808412"
    assert swap["amount0"] == "-44135.808412"
    assert swap["amount1_raw"] == "23548972398988312576"
    assert swap["amount1"] == "23.548972398988312576"
    assert swap["tick"] == "200957"
    assert swap["sender"] == "0xbdb3ba9ffe392549e1f8658dd2630c141fdf47b6"
