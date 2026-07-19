from __future__ import annotations

import os

import pytest

from uniswap_cli.config import Settings
from uniswap_cli.providers.rpc import V3_SWAP_TOPIC, RpcProvider

pytestmark = pytest.mark.live


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_UNISWAP_LIVE_TESTS") != "1" or not os.getenv("RPC_URL_1"),
    reason="set RUN_UNISWAP_LIVE_TESTS=1 and RPC_URL_1",
)
async def test_ethereum_rpc_health_and_logs() -> None:
    async with RpcProvider(Settings.from_env(), 1) as provider:
        health = await provider.health(check_archive=True)
        head = health.indexed_block
        assert head is not None
        result = await provider.raw_events(
            address="0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
            topics=[V3_SWAP_TOPIC],
            start_block=head - 9,
            end_block=head,
            limit=10,
            cursor=None,
            direction="asc",
        )
    assert health.data["archive"]["ok"] is True
    assert result.covered_range == {"from_block": head - 9, "to_block": head}
