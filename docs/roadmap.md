# 实施路线

## Phase 0：仓库与契约

- [x] 建立公开仓库
- [x] 记录 CLI + skill 优先架构
- [x] 记录数据源策略和 daemon 引入门槛
- [x] 起草 CLI 命令与输出 envelope

退出条件：顶层边界明确，后续实现可以通过 PR 逐项收敛。

## Phase 1：数据源 spike

- 验证 Ethereum mainnet 上 v2、v3、v4 subgraph 的 schema、同步状态和分页行为
- 对 pools、swaps、日/小时聚合和历史区块查询制作代表性样本
- 用 RPC 日志抽样对账 swap 数量、顺序、金额和区块范围
- 测量限流、延迟、历史深度和典型查询成本
- 根据结果选定实现语言、第一条 vertical slice 和凭据变量名

预期第一条 vertical slice 是 Ethereum mainnet v3；若 spike 发现 deployment 可靠性或
数据口径不满足，再调整而不是强行固化。

退出条件：形成支持矩阵、fixture 和一份可复现的对账报告。

## Phase 2：只读 MVP

- 实现配置、HTTP/RPC、重试、分页和结构化错误基础层
- 实现 `chains`、`pools`、`swaps`、`series` 和 `doctor`
- 固化 JSON schema 与 provenance metadata
- 提供 fixture 单测和受控真实网络集成测试
- 编写 `SKILL.md`，在目标仓库通过现有 skill 同步机制发现

退出条件：agent 能从 pool 标识稳定取得指定窗口的 swap 与日级指标，并能判断结果来源和
完整性。

## Phase 3：覆盖与可靠性

- 扩展 v2/v4 与更多链
- 增加 RPC fallback 和自动对账工具
- 增加 token metadata、价格源和 USD 口径说明
- 建立 schema 兼容策略、版本 tag、发布产物和 CI
- 评估一个商业 provider adapter，但保持公共模型独立

退出条件：支持矩阵中的能力有明确测试与降级行为。

## Phase 4：基于证据升级

根据真实使用指标决定是否：

- 自部署官方 subgraph
- 购买有 SLA 的数据 API
- 增加本地索引器
- 为共享缓存、限流或后台回填增加可选 daemon
- 增加独立的交易型命令面

这些都不是既定交付物，每一项需要单独 ADR、成本测算和安全评审。
