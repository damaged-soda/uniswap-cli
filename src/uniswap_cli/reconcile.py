from __future__ import annotations

from typing import Any


def _identity(row: dict[str, Any]) -> tuple[str, int] | None:
    tx_hash = row.get("transaction_hash")
    log_index = row.get("log_index")
    if not isinstance(tx_hash, str) or not isinstance(log_index, int):
        return None
    return tx_hash.lower(), log_index


def _identity_model(key: tuple[str, int], row: dict[str, Any]) -> dict[str, Any]:
    return {
        "transaction_hash": key[0],
        "log_index": key[1],
        "block_number": row.get("block_number"),
    }


def reconcile_swap_rows(
    subgraph_rows: list[dict[str, Any]],
    rpc_rows: list[dict[str, Any]],
    *,
    sample_limit: int = 100,
) -> dict[str, Any]:
    subgraph_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    rpc_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    unkeyed_subgraph: list[str] = []
    unkeyed_rpc: list[str] = []

    for row in subgraph_rows:
        key = _identity(row)
        if key is None:
            unkeyed_subgraph.append(str(row.get("id")))
        else:
            subgraph_by_key[key] = row
    for row in rpc_rows:
        key = _identity(row)
        if key is None:
            unkeyed_rpc.append(str(row.get("id")))
        else:
            rpc_by_key[key] = row

    subgraph_keys = set(subgraph_by_key)
    rpc_keys = set(rpc_by_key)
    matched_keys = sorted(subgraph_keys & rpc_keys)
    subgraph_only_keys = sorted(subgraph_keys - rpc_keys)
    rpc_only_keys = sorted(rpc_keys - subgraph_keys)
    amount_mismatches: list[dict[str, Any]] = []
    for key in matched_keys:
        subgraph = subgraph_by_key[key]
        rpc = rpc_by_key[key]
        mismatched_fields = [
            field
            for field in ("amount0_raw", "amount1_raw")
            if subgraph.get(field) != rpc.get(field)
        ]
        if mismatched_fields:
            amount_mismatches.append(
                {
                    **_identity_model(key, rpc),
                    "fields": mismatched_fields,
                    "subgraph": {field: subgraph.get(field) for field in mismatched_fields},
                    "rpc": {field: rpc.get(field) for field in mismatched_fields},
                }
            )

    complete_match = not (
        subgraph_only_keys or rpc_only_keys or unkeyed_subgraph or unkeyed_rpc or amount_mismatches
    )
    return {
        "complete_match": complete_match,
        "subgraph_count": len(subgraph_rows),
        "rpc_count": len(rpc_rows),
        "matched_count": len(matched_keys),
        "subgraph_only_count": len(subgraph_only_keys),
        "rpc_only_count": len(rpc_only_keys),
        "amount_mismatch_count": len(amount_mismatches),
        "unkeyed_subgraph_count": len(unkeyed_subgraph),
        "unkeyed_rpc_count": len(unkeyed_rpc),
        "subgraph_only": [
            _identity_model(key, subgraph_by_key[key]) for key in subgraph_only_keys[:sample_limit]
        ],
        "rpc_only": [_identity_model(key, rpc_by_key[key]) for key in rpc_only_keys[:sample_limit]],
        "amount_mismatches": amount_mismatches[:sample_limit],
        "unkeyed_subgraph": unkeyed_subgraph[:sample_limit],
        "unkeyed_rpc": unkeyed_rpc[:sample_limit],
        "samples_truncated": any(
            len(items) > sample_limit
            for items in (
                subgraph_only_keys,
                rpc_only_keys,
                amount_mismatches,
                unkeyed_subgraph,
                unkeyed_rpc,
            )
        ),
    }
