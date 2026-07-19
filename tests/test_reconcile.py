from __future__ import annotations

from uniswap_cli.reconcile import reconcile_swap_rows


def _row(tx: str, log_index: int, amount0: str, amount1: str) -> dict:
    return {
        "id": f"{tx}#{log_index}",
        "transaction_hash": tx,
        "log_index": log_index,
        "block_number": 100,
        "amount0_raw": amount0,
        "amount1_raw": amount1,
    }


def test_reconcile_complete_match_is_order_independent() -> None:
    first = _row("0x" + "11" * 32, 1, "10", "-20")
    second = _row("0x" + "22" * 32, 2, "30", "-40")
    result = reconcile_swap_rows([first, second], [second, first])
    assert result["complete_match"] is True
    assert result["matched_count"] == 2


def test_reconcile_surfaces_missing_and_amount_mismatches() -> None:
    shared_subgraph = _row("0x" + "11" * 32, 1, "10", "-20")
    shared_rpc = _row("0x" + "11" * 32, 1, "11", "-20")
    subgraph_only = _row("0x" + "22" * 32, 2, "1", "-1")
    rpc_only = _row("0x" + "33" * 32, 3, "2", "-2")
    result = reconcile_swap_rows(
        [shared_subgraph, subgraph_only],
        [shared_rpc, rpc_only],
    )
    assert result["complete_match"] is False
    assert result["amount_mismatch_count"] == 1
    assert result["subgraph_only_count"] == 1
    assert result["rpc_only_count"] == 1
