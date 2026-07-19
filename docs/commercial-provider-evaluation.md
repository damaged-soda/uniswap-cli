# 商业 provider 评估：CoinGecko Onchain API

状态：`0.1` 不接入，保留 adapter 位
核对日期：2026-07-19

## 为什么选它做代表

CoinGecko Onchain API（原 GeckoTerminal API）直接提供多链 pool 详情、交易和 OHLCV，正好
覆盖 subgraph 最容易产生运维成本的“跨链统一行情”需求。官方称覆盖 200+ networks，并提供
`/onchain/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}`。

官方资料：

- [DeFi & Onchain Analytics](https://docs.coingecko.com/docs/defi-onchain-analytics)
- [Pool OHLCV by Pool Address](https://docs.coingecko.com/reference/pool-ohlcv-contract-address)
- [Authentication](https://docs.coingecko.com/reference/authentication)
- [Pricing](https://www.coingecko.com/en/api/pricing)

## 能力与成本快照

截至核对日：

- Demo plan 为 10,000 credits/月、100 calls/分钟；付费 Basic 页面价为 35 美元/月，
  100,000 credits/月、300 calls/分钟。
- pool OHLCV 支持 day/hour/minute/second 和最多 1,000 行；单次最多 6 个月，旧数据用
  `before_timestamp` 翻页。
- 文档说明 Analyst 及以上可访问自 2021-09 起的 pool OHLCV，实际深度取决于 pool 被开始
  跟踪的时间。
- Pro key 推荐放 `x-cg-pro-api-key` header；4xx/5xx 虽不扣月度 credit，仍计入分钟限流。
- 商业许可要求 attribution，并限制再分发；具体采购前必须重新核对当期条款和价格。

价格和套餐会变化，这些数字只作为 2026-07-19 的决策输入，不写进 CLI 行为。

## 优点

- 跨链 network/pool 发现、token metadata、liquidity、trade 和 OHLCV 是统一 REST shape。
- OHLCV 提供更细粒度和 empty-interval 选项，省去自行从 swaps 造 candle。
- 可作为 subgraph USD 派生指标之外的第二个定价口径。
- Demo key 足以做 adapter spike，无需先购买。

## 不直接接入 `0.1` 的原因

1. **身份不完全等价**：接口按 pool contract address 查询；Uniswap v4 的多个 pool 共用
   PoolManager、协议身份是 bytes32 pool ID，必须先实测 vendor 如何映射，不能假设兼容。
2. **价格语义不同**：返回 base/quote OHLCV 与当前 subgraph 的 token0/token1 price 方向不同，
   需要显式 currency/token/inversion metadata 才能进入公共 schema。
3. **历史深度受套餐和 tracking start 影响**：不能把“供应商有端点”当成“指定 pool 全历史完整”。
4. **许可证与 attribution**：公开 CLI 的原始数据再分发边界需要单独确认。
5. **现有需求已被覆盖**：Ethereum v2/v3/v4 subgraph + v2/v3 RPC 对账已经满足当前只读目标，
   暂无数据证明购买服务比保持 provider 独立更划算。

## 接入门槛

出现以下任一真实需求后再实现 `coingecko` adapter：

- 需要多个没有可靠 subgraph deployment 的链统一 OHLCV；
- 需要 minute/second candle，自己从 swaps 聚合的成本不可接受；
- 需要明确 SLA 或历史跨度，而当前 The Graph/RPC 无法满足；
- 代表性 pool 对账证明 CoinGecko 的覆盖、时间边界和价格方向可稳定规范化。

届时先用 Demo key 做 v2/v3/v4 各一组测试，比较 completeness、延迟、价格方向、volume、
分页、限流和成本。adapter 仍只能生成当前公共 `ProviderResult`，不得把 CoinGecko JSON
直接暴露为默认稳定接口。
