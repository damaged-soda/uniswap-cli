# 数据源策略

状态：初始调研结论
核对日期：2026-07-19

## 结论

采用“subgraph 优先、RPC 校验与补漏、商业 API 可选、自建索引最后考虑”的组合方案。
不存在一个既官方、免费、覆盖所有版本和链、又保证完整历史与统一口径的单一 API。

## 选择矩阵

| 数据源 | 适合 | 主要限制 | 首版角色 |
|---|---|---|---|
| Uniswap subgraph | pools、swaps、tokens、positions、日/小时聚合和历史区块查询 | 按链和版本拆分；依赖索引进度与 schema；The Graph gateway 需要 key/计费配置 | 主数据源 |
| Uniswap Trading API | 实时报价、路由和交易构建 | 面向交易工作流，不是完整历史分析接口 | 不进入只读 MVP |
| 链上 RPC 日志 | 原始事件、精确区块范围、独立校验 | 大范围扫描慢且昂贵；历史查询依赖 archive 能力和 provider 限制 | fallback 与审计源 |
| 商业数据 API | 统一 OHLCV、跨链聚合、SLA 和长历史 | 成本、供应商口径和锁定风险 | 可选 adapter |
| 自部署 subgraph / 索引器 | 可控 schema、同步状态和保留策略 | 运维、存储、回填和监控成本 | 满足明确需求后再做 |

## 官方 subgraph 口径

Uniswap 开发者文档提供 v2、v3、v4 的开源 subgraph 代码和 The Graph 查询示例。
每个协议版本、每条链通常对应独立部署。文档同时明确：页面列出的公开 deployment 只是
示例，不一定由 Uniswap Labs 维护，也不保证持续可用。

接入前必须验证：

- deployment 是否仍在同步，索引最高区块距离链头多远
- schema commit/version 是否符合预期
- chain、协议版本和合约地址是否匹配
- 查询窗口、分页上限、限流和计费行为
- USD、TVL、volume 等派生值的口径

生产可靠性不足时，可以固定并自部署官方 subgraph 代码，但这不是 MVP 的默认选择。

官方资料：

- [Subgraphs Overview](https://developers.uniswap.org/docs/ecosystem/subgraphs/overview)
- [v4 Queries](https://developers.uniswap.org/docs/ecosystem/subgraphs/concepts/v4/queries)
- [v2 Queries](https://developers.uniswap.org/docs/ecosystem/subgraphs/concepts/v2/queries)
- [Uniswap v2 subgraph](https://github.com/Uniswap/v2-subgraph)
- [Uniswap v3 subgraph](https://github.com/Uniswap/v3-subgraph)
- [Uniswap v4 subgraph](https://github.com/Uniswap/v4-subgraph)

## Trading API 的边界

官方 Trading API 当前文档围绕 API key、quote、approval、swap/order 和交易状态展开。
它可以在未来支持交易型命令，但不能替代 pools、swaps 和长周期指标的历史数据层。

官方资料：

- [Uniswap API Quick Start](https://developers.uniswap.org/docs/get-started/quickstart)
- [Swapping via the Uniswap API](https://developers.uniswap.org/docs/trading/swapping-api/getting-started)

## RPC fallback

RPC provider 用于读取 factory/pool/PoolManager 等合约事件，形成可复核的链上来源。第一版
不会从创世区块扫描全链，而是要求调用方提供合理区块或时间窗口，并在必要时通过日志的
`transactionHash + logIndex` 去重。

RPC 结果和 subgraph 结果对账时，应区分：链重组、索引延迟、token metadata 异常、
USD 定价差异和协议版本语义差异。链上日志是真值来源，但 TVL/USD 等派生指标仍需要明确定义。

## provider 选择与降级

默认策略：

1. 请求聚合历史、实体发现或常规 swap 列表时，先使用匹配链与版本的 subgraph。
2. 请求原始事件、特定日志字段或校验时，使用 RPC。
3. subgraph 不健康时，仅在语义等价且查询窗口可控的情况下自动降级到 RPC。
4. 商业 provider 只有在用户显式配置或查询能力确实要求时才使用。
5. 无法保持同一语义时返回明确错误，不拼接看似完整但口径不一致的结果。

每次响应至少报告 provider、endpoint 的非敏感标识、查询时间、索引区块、实际覆盖范围和
是否发生降级。

## 何时购买 API

满足任一条件时再评估商业服务：

- 需要多链统一 OHLCV，且自行统一价格和 token metadata 成本更高
- 查询频率或历史跨度超过公共 gateway/RPC 的经济范围
- 需要明确 SLA、低延迟链头数据或已清洗的跨协议指标
- 研究结果需要供应商提供的标准化实体、标签或价格源

采购决策必须用代表性查询做准确性、延迟、覆盖、限流和成本对比，并保留 RPC/subgraph
抽样对账，不能只比较营销功能表。
