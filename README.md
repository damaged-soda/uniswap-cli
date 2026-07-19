# uniswap-cli

面向人和 agent 的 Uniswap 只读数据 CLI：查询 v2/v3/v4 pool、token、swap 历史、TVL、
volume、fees、OHLCV，以及用于对账的原始 EVM 日志。默认形态是 **CLI + skill**，不注册
MCP、不运行 daemon、不部署服务器。

## 已实现能力

- 统一 JSON envelope、版本化 pool/swap/series schema 和结构化错误
- Ethereum mainnet v2/v3/v4 官方 subgraph deployment
- 自定义 chain ID、subgraph URL 与 RPC fallback
- pool 列表/详情、token 聚合、swap 历史、1h/1d 时间序列
- v2/v3 block-bounded RPC swap 解码
- raw GraphQL 和带 topic 的 raw EVM log 查询
- RPC 范围自适应、重试、限流安全阈值、cursor 分页和 secret redaction
- subgraph/RPC/chain/archive/indexing `doctor`
- `json`、`jsonl` 和 `table` 输出
- 可随仓分发的 [`uniswap` skill](skills/uniswap/SKILL.md)

完整覆盖见 [支持矩阵](docs/support-matrix.md)。

## 安装

需要 Python 3.11+：

```bash
git clone https://github.com/damaged-soda/uniswap-cli.git
cd uniswap-cli
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/uniswap --help
```

也可以用隔离安装工具直接安装公开仓库：

```bash
pipx install git+https://github.com/damaged-soda/uniswap-cli.git
```

wheel 会把 JSON Schema 和 skill 一并安装到环境的
`share/uniswap-cli/{schemas,skills}`；仓库开发与 personal namespace 仍以仓内
[`skills/uniswap`](skills/uniswap/) 为正本。

## 配置

凭据只通过环境注入，绝不放进参数或仓库：

```bash
# 官方文档列出的 Ethereum v2/v3/v4 The Graph deployment
export UNISWAP_THE_GRAPH_API_KEY=...

# RPC；如果 namespace 已有 RPC_URL_1，CLI 会直接继承
export UNISWAP_RPC_URL_1=https://...

# 其他链或自部署 subgraph
export UNISWAP_SUBGRAPH_URL_8453_V3=https://...
export UNISWAP_SUBGRAPH_AUTH_TOKEN_8453_V3=...
```

查看所有变量：

```bash
uniswap --help
uniswap doctor --chain ethereum --protocol v3
```

Bundled The Graph endpoint 无 key 时会返回显式 `SUBGRAPH_AUTH_MISSING`，不会把鉴权失败解释成
空数据。官方 [Subgraphs Overview](https://developers.uniswap.org/docs/ecosystem/subgraphs/overview)
也提醒公开 deployment 不一定由 Uniswap Labs 维护，生产使用前应核对同步与 schema。

## 常用命令

```bash
# 发现
uniswap chains list
uniswap protocols list --chain ethereum
uniswap pools list --chain ethereum --protocol v3 --limit 10
uniswap pools get --chain ethereum --protocol v3 --pool 0x...
uniswap tokens get --chain ethereum --protocol v3 --address 0x...

# subgraph 历史 swap
uniswap swaps list --chain ethereum --protocol v3 --pool 0x... \
  --from 2026-07-18T00:00:00Z --to 2026-07-19T00:00:00Z --direction asc

# RPC 链上 swap；v2/v3
uniswap swaps list --chain ethereum --protocol v3 --provider rpc --pool 0x... \
  --from-block 25566508 --to-block 25566517 --direction asc

# 时间序列
uniswap series get --chain ethereum --protocol v3 --pool 0x... \
  --metric ohlcv --interval 1d --from 2026-07-01T00:00:00Z --to 2026-07-19T00:00:00Z

# 精确链上日志
uniswap raw events --chain ethereum --address 0x... --topic 0x... \
  --from-block 25566508 --to-block 25566517
```

大结果通过 `meta.next_cursor` 继续：

```bash
uniswap swaps list ... --cursor '<opaque cursor>'
```

cursor 与 chain、protocol、provider、direction 和过滤条件共同使用；不要把一个查询的 cursor
挪给另一个查询。

RPC 的 `meta.range` 表示请求过滤范围；`meta.range_complete` 只有在无需继续分页时才为 true，
同时应检查 `next_cursor`。显式请求高于 RPC head 的 `to-block` 会失败，不会伪装成已覆盖。
`UNISWAP_RPC_MAX_LOG_REQUESTS` 统计包含 retry 与 fallback 在内的实际 `eth_getLogs` HTTP
尝试数，避免多 endpoint 配置把名义预算成倍放大。

## 输出语义

成功结果：

```json
{
  "schema_version": "0.1",
  "data": [],
  "meta": {
    "chain_id": 1,
    "chain": "ethereum",
    "protocol_version": "v3",
    "provider": "subgraph",
    "source_id": "...",
    "queried_at": "2026-07-19T00:00:00Z",
    "indexed_block": 0,
    "range": {},
    "next_cursor": null,
    "warnings": []
  }
}
```

运行时错误以同版本 JSON 写入 stderr 并返回 1；参数错误返回 2。`doctor` 在所请求 provider
全部不可用时也返回 1，同时仍把检查结果写到 stdout。`amount0/amount1` 是 pool
视角的 delta：正数进入 pool，负数离开。精确计算使用 `amount*_raw` 字符串，不要转成浮点数。

## 开发与验证

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -q
.venv/bin/python -m build

# 需要安全注入 RPC_URL_1
RUN_UNISWAP_LIVE_TESTS=1 .venv/bin/pytest -q -m live
```

CI 覆盖 Python 3.11/3.12/3.13、lint、fixture 契约测试、JSON Schema 和构建。`v*` tag 必须与
包版本一致，成功后自动生成 GitHub Release 产物。

## 文档

- [架构](docs/architecture.md)
- [数据源策略](docs/data-sources.md)
- [数据源 spike](docs/spike-2026-07-19.md)
- [商业 provider 评估](docs/commercial-provider-evaluation.md)
- [支持矩阵](docs/support-matrix.md)
- [CLI 契约](docs/cli-contract.md)
- [实施路线](docs/roadmap.md)
- [ADR 0001：CLI + skill 优先](docs/decisions/0001-cli-skill-first.md)

## 明确边界

- 不报价、不签名、不提交交易、不接触私钥。
- Uniswap Trading API 面向报价和执行，不作为历史数据层。
- v2 pool series 没有 OHLC；v2 fees USD 以 `volume × 0.003` 推导并明确标记。
- v4 RPC 规范化 swap 需要额外 PoolKey 元数据，当前使用 subgraph 或 raw PoolManager events。
- CoinGecko Onchain API 已完成桌面评估，但尚无证据值得在 `0.1` 引入供应商依赖。
- 没有证据需要跨调用状态，因此不提供 daemon。

## License

许可证尚未选择；当前保留所有权利。公开可见不等于已授予再分发或修改许可。
