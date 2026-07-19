# 实施路线

## Phase 0：仓库与契约

- [x] 建立公开仓库
- [x] 记录 CLI + skill 优先架构
- [x] 记录数据源策略和 daemon 引入门槛
- [x] 起草 CLI 命令与输出 envelope

退出条件：顶层边界明确，后续实现可以通过 PR 逐项收敛。

## Phase 1：数据源 spike

- [x] 验证 Ethereum mainnet 上 v2、v3、v4 官方 schema 与 The Graph 鉴权边界
- [x] 对 pools、swaps、日/小时聚合制作固定 fixture
- [x] 用 RPC 日志抽样验证 swap 数量、顺序、金额和区块范围
- [x] 测量 RPC 限流、延迟、archive 深度和典型查询成本
- [x] 选择 Python 3.11+、第一条 Ethereum v3 vertical slice 和凭据变量名
- [ ] 注入 The Graph key 后补一轮 live subgraph smoke（当前缺 key，未冒充通过）

第一条 vertical slice 已以 Ethereum mainnet v3 完成；完整记录见
[`spike-2026-07-19.md`](spike-2026-07-19.md)。

退出条件：形成支持矩阵、fixture 和一份可复现的对账报告。

## Phase 2：只读 MVP

- [x] 实现配置、HTTP/RPC、重试、分页和结构化错误基础层
- [x] 实现 `chains`、`protocols`、`tokens`、`pools`、`swaps`、`series`、`raw` 和 `doctor`
- [x] 固化 JSON schema 与 provenance metadata
- [x] 提供 fixture 单测和受控 RPC live test
- [x] 编写仓内 `SKILL.md`
- [ ] 在 personal manifest 登记 skill，并铺设/验证发现链接

退出条件：agent 能从 pool 标识稳定取得指定窗口的 swap 与日级指标，并能判断结果来源和
完整性。

## Phase 3：覆盖与可靠性

- [x] 扩展 v2/v4 subgraph；常见 chain 名称和任意数字 chain ID 可配置自定义 endpoint
- [x] 增加 v2/v3 RPC fallback、raw v4 PoolManager event 能力和显式降级告警
- [x] 增加 token metadata、原始整数金额和 USD/subgraph 口径说明
- [x] 建立 schema 兼容策略、版本 tag、GitHub Release 产物和 CI
- [x] 增加 `swaps reconcile` subgraph/RPC 自动对账命令
- [x] 评估 CoinGecko Onchain API；`0.1` 不接入，保留 provider 独立边界

退出条件：支持矩阵中的能力有明确测试与降级行为。

## Phase 4：基于证据升级

根据真实使用指标决定是否：

- 自部署官方 subgraph
- 购买有 SLA 的数据 API
- 增加本地索引器
- 为共享缓存、限流或后台回填增加可选 daemon
- 增加独立的交易型命令面

这些都不是既定交付物，每一项需要单独 ADR、成本测算和安全评审。
