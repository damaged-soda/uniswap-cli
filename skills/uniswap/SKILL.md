---
name: uniswap
description: Query read-only Uniswap v2/v3/v4 pool and token data, swap history, TVL, volume, fees, OHLCV time series, and raw EVM events through the uniswap CLI. Use when investigating a Uniswap pool, comparing historical metrics, retrieving swaps over a time or block range, validating indexed data against chain logs, or checking Uniswap data provenance and provider health.
---

# Uniswap read-only data

Use the `uniswap` CLI. Keep queries read-only and bounded; never put RPC URLs or API keys in
arguments, files, or output.

## Choose the data path

- Use subgraph-backed `tokens`, `pools`, `swaps`, and `series` for discovery, USD metrics, and
  historical aggregation.
- Use `swaps --provider rpc` for block-bounded v2/v3 chain truth. Expect no USD valuation or token
  symbols in this mode.
- Use `raw events` for exact logs or v4 PoolManager events.
- Use `raw graphql` only when the normalized commands lack a field. Treat its shape as unstable.
- Run `doctor` when credentials, indexing status, archive access, or chain identity are uncertain.
  Treat `data.degraded: true` as a signal to inspect every provider check even when one path remains
  usable.

## Start with discovery

```bash
uniswap doctor --chain ethereum --protocol v3
uniswap chains list
uniswap protocols list --chain ethereum
uniswap pools list --chain ethereum --protocol v3 --limit 10
uniswap pools get --chain ethereum --protocol v3 --pool 0x...
```

The bundled subgraphs require `UNISWAP_THE_GRAPH_API_KEY`. A custom deployment may instead use
`UNISWAP_SUBGRAPH_URL_<chain-id>_<VERSION>`. RPC commands read `UNISWAP_RPC_URL_<chain-id>` and
then the shared `RPC_URL_<chain-id>` fallback.

## Query history

```bash
# Indexed swaps in a UTC time window
uniswap swaps list --chain ethereum --protocol v3 --pool 0x... \
  --from 2026-07-18T00:00:00Z --to 2026-07-19T00:00:00Z --direction asc

# Chain logs normalized as swaps; v2/v3 only
uniswap swaps list --chain ethereum --protocol v3 --provider rpc --pool 0x... \
  --from-block 25566508 --to-block 25566517 --direction asc

# 对账同一区块范围内的 subgraph 与 RPC swap 身份/原始金额
uniswap swaps reconcile --chain ethereum --protocol v3 --pool 0x... \
  --from-block 25566508 --to-block 25566517

# Daily OHLCV or another metric
uniswap series get --chain ethereum --protocol v3 --pool 0x... \
  --metric ohlcv --interval 1d --from 2026-07-01T00:00:00Z --to 2026-07-19T00:00:00Z

# Exact event logs; repeat --topic by topic position and use null as a wildcard
uniswap raw events --chain ethereum --address 0x... --topic 0x... \
  --from-block 25566508 --to-block 25566517
```

Use `meta.next_cursor` until it is null when the answer must be complete. Keep the same command,
filters, provider, direction, chain, and protocol when supplying `--cursor`.

## Interpret results safely

- Read `meta.provider`, `source_id`, `indexed_block`, `range`, and `warnings` before drawing a
  conclusion.
- For RPC pages, require `meta.range_complete: true` or follow `meta.next_cursor`; never infer
  completeness from the requested block range alone.
- Treat subgraph USD/TVL values as indexed and derived metrics, not raw chain state.
- Treat swap `amount0` and `amount1` as pool deltas: positive enters the pool, negative leaves it.
- Preserve `amount*_raw` strings for exact arithmetic; do not coerce them to floating point.
- For v4, distinguish the bytes32 pool ID from a v2/v3 pool contract address.
- Do not silently combine subgraph and RPC rows when their covered ranges or semantics differ.
- If an upstream fails, report the structured error. Change provider only when the alternate path
  preserves the requested semantics.

Use `--format jsonl` for streaming rows and `--format table` only for human inspection. Consult
[`docs/cli-contract.md`](../../docs/cli-contract.md) and
[`docs/data-sources.md`](../../docs/data-sources.md) for the full contract and source boundaries.
