# CLI 契约草案

状态：`schema_version = 0.1` 已实现
日期：2026-07-19

## 设计原则

- 名词优先，命令可以被人直接发现，也容易被 agent 从 `--help` 学会。
- stdout 默认只输出机器可读 JSON；诊断信息写 stderr。
- 时间、区块、chain 和协议版本必须显式或能以可解释方式解析。
- 大结果必须分页或流式输出，不能悄悄截断。
- 相同实体在不同 provider 下保持公共 schema 一致。

## 命令树

```text
uniswap
├── chains list
├── protocols list
├── tokens get
├── pools list
├── pools get
├── swaps list
├── swaps reconcile
├── series get
├── raw graphql
├── raw events
└── doctor
```

`raw` 命令明确暴露上游语义，不属于稳定规范化接口；它用于研究、调试和新能力试验。

## 通用选项

```text
--chain <name|chain-id>
--protocol <v2|v3|v4>
--provider <auto|subgraph|rpc|...>
--from <RFC3339|unix-seconds>
--to <RFC3339|unix-seconds>
--from-block <number>
--to-block <number>
--limit <number>
--cursor <opaque-token>
--format <json|jsonl|table>
--timeout <duration>
```

冲突的时间和区块边界应 fail loud，不做隐式猜测。`table` 仅供人阅读，稳定集成使用 JSON。

## 输出 envelope

```json
{
  "schema_version": "0.1",
  "data": [],
  "meta": {
    "chain_id": 1,
    "protocol_version": "v3",
    "provider": "subgraph",
    "queried_at": "2026-07-19T00:00:00Z",
    "indexed_block": 0,
    "range": {},
    "next_cursor": null,
    "warnings": []
  }
}
```

地址输出统一为小写可比较形式；原始整数使用十进制字符串，不以 IEEE-754 number 表达。
实体 schema 见 [`schemas/`](../schemas/)。

## 错误

错误输出也使用结构化 JSON，并至少包含稳定 code、可读 message、是否可重试和非敏感上下文。
首批错误类别：参数错误、能力不支持、认证失败、限流、上游不可用、索引落后、范围过大和
结果不完整。

## 环境变量

主要变量：

- `UNISWAP_THE_GRAPH_API_KEY`
- `UNISWAP_SUBGRAPH_URL_<chain-id>_<VERSION>`
- `UNISWAP_SUBGRAPH_AUTH_TOKEN_<chain-id>_<VERSION>`
- `UNISWAP_RPC_URL_<chain-id>`，缺失时继承 `RPC_URL_<chain-id>`
- `UNISWAP_DEFAULT_CHAIN` / `UNISWAP_DEFAULT_PROTOCOL`
- `UNISWAP_HTTP_*` 与 `UNISWAP_RPC_MAX_*` 安全阈值

`uniswap doctor` 只报告配置是否存在和连通性，不回显 secret 或带 secret 的完整 endpoint。
