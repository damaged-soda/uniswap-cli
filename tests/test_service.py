from __future__ import annotations

import pytest

import uniswap_cli.service as service_module
from uniswap_cli.config import Settings
from uniswap_cli.errors import UniswapError
from uniswap_cli.providers.base import ProviderResult
from uniswap_cli.service import UniswapService, parse_timestamp


def _settings(**extra: str) -> Settings:
    return Settings.from_env({"RPC_URL_1": "https://rpc.test", **extra})


def test_parse_timestamp_requires_timezone_and_preserves_unix_seconds() -> None:
    assert parse_timestamp("2026-07-19T00:00:00Z", field="from") == 1784419200
    assert parse_timestamp("1784419200", field="from") == 1784419200
    with pytest.raises(UniswapError):
        parse_timestamp("2026-07-19T00:00:00", field="from")


@pytest.mark.asyncio
async def test_auto_provider_falls_back_to_rpc_for_bounded_time_range(monkeypatch) -> None:
    class FakeRpcProvider:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            pass

        async def block_at_or_after(self, timestamp: int) -> int:
            assert timestamp == 100
            return 10

        async def block_at_or_before(self, timestamp: int) -> int:
            assert timestamp == 200
            return 20

        async def list_swaps(self, **kwargs) -> ProviderResult:
            assert kwargs["start_block"] == 10
            assert kwargs["end_block"] == 20
            return ProviderResult(
                data=[],
                provider="rpc",
                source_id="rpc:1:1",
                indexed_block=25,
                covered_range={"from_block": 10, "to_block": 20},
            )

    monkeypatch.setattr(service_module, "RpcProvider", FakeRpcProvider)
    service = UniswapService(_settings(), chain=1, protocol="v3")
    result = await service.swaps(
        pool_id="0x" + "11" * 20,
        provider="auto",
        start_timestamp=100,
        end_timestamp=200,
        start_block=None,
        end_block=None,
        limit=100,
        cursor=None,
        direction="asc",
    )

    assert result["meta"]["provider"] == "rpc"
    assert "auto-selected RPC" in result["meta"]["warnings"][-1]


@pytest.mark.asyncio
async def test_swap_range_validation_and_unavailable_auto_provider() -> None:
    service = UniswapService(Settings.from_env({}), chain=1, protocol="v3")
    with pytest.raises(UniswapError, match="cannot be combined"):
        await service.swaps(
            pool_id="0x" + "11" * 20,
            provider="auto",
            start_timestamp=100,
            end_timestamp=200,
            start_block=10,
            end_block=20,
            limit=100,
            cursor=None,
            direction="asc",
        )
    with pytest.raises(UniswapError) as caught:
        await service.swaps(
            pool_id="0x" + "11" * 20,
            provider="auto",
            start_timestamp=None,
            end_timestamp=None,
            start_block=None,
            end_block=None,
            limit=100,
            cursor=None,
            direction="asc",
        )
    assert caught.value.code == "NO_USABLE_PROVIDER"


@pytest.mark.asyncio
async def test_explicit_subgraph_rejects_block_range_before_connecting() -> None:
    service = UniswapService(Settings.from_env({}), chain=1, protocol="v3")
    with pytest.raises(UniswapError) as caught:
        await service.swaps(
            pool_id="0x" + "11" * 20,
            provider="subgraph",
            start_timestamp=None,
            end_timestamp=None,
            start_block=10,
            end_block=20,
            limit=100,
            cursor=None,
            direction="asc",
        )
    assert caught.value.code == "UNSUPPORTED"


@pytest.mark.asyncio
async def test_doctor_marks_all_unconfigured_providers_unusable() -> None:
    service = UniswapService(Settings.from_env({}), chain=1, protocol="v3")
    result = await service.doctor(provider="auto", check_archive=False)
    assert result["data"]["ok"] is False
    assert result["data"]["degraded"] is True
    assert {check["provider"] for check in result["data"]["checks"]} == {
        "subgraph",
        "rpc",
    }


@pytest.mark.asyncio
async def test_reconcile_orchestration_matches_same_swap(monkeypatch) -> None:
    row = {
        "id": "swap",
        "transaction_hash": "0x" + "aa" * 32,
        "log_index": 7,
        "block_number": 10,
        "amount0_raw": "-123",
        "amount1_raw": "456",
    }

    class FakeSubgraphProvider:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def list_swaps(self, **_kwargs) -> ProviderResult:
            return ProviderResult(
                data=[row],
                provider="subgraph",
                source_id="subgraph-id",
                indexed_block=20,
            )

        async def close(self) -> None:
            pass

    class FakeRpcProvider:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def block(self, number: int) -> dict:
            return {"number": number, "timestamp": number * 10, "hash": None}

        async def list_swaps(self, **_kwargs) -> ProviderResult:
            return ProviderResult(data=[row], provider="rpc", source_id="rpc:1:1", indexed_block=20)

        async def close(self) -> None:
            pass

    monkeypatch.setattr(service_module, "SubgraphProvider", FakeSubgraphProvider)
    monkeypatch.setattr(service_module, "RpcProvider", FakeRpcProvider)
    service = UniswapService(
        _settings(UNISWAP_SUBGRAPH_URL_1_V3="https://subgraph.test"),
        chain=1,
        protocol="v3",
    )
    result = await service.reconcile_swaps(
        pool_id="0x" + "11" * 20,
        start_block=10,
        end_block=10,
        max_swaps=100,
        sample_limit=10,
    )

    assert result["data"]["complete_match"] is True
    assert result["data"]["matched_count"] == 1


@pytest.mark.asyncio
async def test_reconcile_rejects_limit_above_rpc_cap() -> None:
    service = UniswapService(_settings(), chain=1, protocol="v3")
    with pytest.raises(UniswapError, match="between 1 and 10000"):
        await service.reconcile_swaps(
            pool_id="0x" + "11" * 20,
            start_block=10,
            end_block=20,
            max_swaps=10_001,
            sample_limit=10,
        )


@pytest.mark.asyncio
async def test_reconcile_allows_exactly_max_swaps(monkeypatch) -> None:
    rows = [
        {
            "id": f"swap-{index}",
            "transaction_hash": "0x" + f"{index + 1:064x}",
            "log_index": index,
            "block_number": 10,
            "amount0_raw": str(index),
            "amount1_raw": str(-index),
        }
        for index in range(10)
    ]

    class FakeSubgraphProvider:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def list_swaps(self, *, cursor, **_kwargs) -> ProviderResult:
            return ProviderResult(
                data=rows if cursor is None else [],
                provider="subgraph",
                source_id="subgraph-id",
                indexed_block=20,
                next_cursor="probe" if cursor is None else None,
            )

        async def close(self) -> None:
            pass

    class FakeRpcProvider:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def block(self, number: int) -> dict:
            return {"number": number, "timestamp": number * 10, "hash": None}

        async def list_swaps(self, **_kwargs) -> ProviderResult:
            return ProviderResult(data=rows, provider="rpc", source_id="rpc:1:1", indexed_block=20)

        async def close(self) -> None:
            pass

    monkeypatch.setattr(service_module, "SubgraphProvider", FakeSubgraphProvider)
    monkeypatch.setattr(service_module, "RpcProvider", FakeRpcProvider)
    service = UniswapService(
        _settings(UNISWAP_SUBGRAPH_URL_1_V3="https://subgraph.test"),
        chain=1,
        protocol="v3",
    )
    result = await service.reconcile_swaps(
        pool_id="0x" + "11" * 20,
        start_block=10,
        end_block=10,
        max_swaps=10,
        sample_limit=10,
    )
    assert result["data"]["subgraph_count"] == 10
    assert result["data"]["complete_match"] is True
