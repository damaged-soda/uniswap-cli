# 支持矩阵

状态：`0.1` 实现口径

## Chain

CLI 内置常用 Uniswap EVM 网络的名称和 chain ID，包括 Ethereum、Optimism、BNB Chain、
Unichain、Polygon、zkSync、World Chain、Soneium、Robinhood Chain、Base、Arbitrum、Celo、
Avalanche、Ink、Linea、Blast 和 Zora。Robinhood Chain 主网使用 `robinhood`（chain ID
`4663`）；别名为 `robinhood-chain` 和 `robinhood-mainnet`。未登记的正整数 chain ID 仍可用
`--chain <id>` 访问自定义 RPC 或 subgraph。

只有 Ethereum mainnet 的 v2/v3/v4 The Graph deployment ID 随 CLI 内置；Robinhood Chain
以及其他链必须显式配置 `UNISWAP_SUBGRAPH_URL_<chain-id>_<VERSION>`。这样不会把可能失效
或非官方维护的公开 deployment 静默固化进发布物。RPC 则配置
`UNISWAP_RPC_URL_4663`（或兼容的 `RPC_URL_4663`）。

Robinhood Chain 的官方网络参数见 [Robinhood Chain 文档](https://docs.robinhood.com/chain/connecting/)，
其 Uniswap v2/v3/v4 支持状态见 [Uniswap 支持矩阵](https://support.uniswap.org/hc/en-us/articles/14569415293325-Networks-on-Uniswap)。

## 能力

| 能力 | v2 subgraph | v3 subgraph | v4 subgraph | RPC |
|---|---:|---:|---:|---:|
| token 详情与聚合 | ✓ | ✓ | ✓ | — |
| pool 列表/详情 | ✓ | ✓ | ✓ | — |
| swap 历史 | ✓ | ✓ | ✓ | v2/v3 规范化；v4 用 raw events |
| volume/TVL/tx count 1h/1d | ✓ | ✓ | ✓ | — |
| fees USD 1h/1d | 从 volume × 0.003 推导 | ✓ | ✓ | — |
| pool OHLCV 1h/1d | — | ✓ | ✓ | — |
| raw GraphQL | ✓ | ✓ | ✓ | — |
| raw EVM logs | — | — | — | 任意 EVM chain/address/topic |
| archive block 健康检查 | — | — | — | Ethereum 已实现样本检查 |
| subgraph/RPC swap 对账 | ✓ | ✓ | — | v2/v3 身份与原始金额逐条比较 |

`—` 表示当前没有语义等价实现，不会静默返回近似结果。

## Provider 选择

- `auto` 对 token/pool/series 使用 subgraph。
- `auto` 对带 block 范围的 swap 使用 RPC；对时间范围优先 subgraph。
- subgraph 未配置时，只有同时给出 `--from` 和 `--to` 才允许自动改用 RPC，并在
  `meta.warnings` 标明降级。
- 显式 `--provider subgraph|rpc` 不再自动切换。

## 稳定 schema

版本化 schema 位于：

- [`schemas/response-0.1.schema.json`](../schemas/response-0.1.schema.json)
- [`schemas/pool-0.1.schema.json`](../schemas/pool-0.1.schema.json)
- [`schemas/swap-0.1.schema.json`](../schemas/swap-0.1.schema.json)
- [`schemas/series-point-0.1.schema.json`](../schemas/series-point-0.1.schema.json)

`raw graphql` 和 `raw events` 的 `data` 保持上游/链上形态，不受实体 schema 兼容承诺约束；
response envelope 仍受版本约束。
