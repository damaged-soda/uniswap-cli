# 架构

状态：`0.1` 已实现
日期：2026-07-19

## 目标

提供一个稳定、可脚本化、对 agent 友好的 Uniswap 数据入口。调用方只学习一套命令和
输出模型，不需要理解每个链、协议版本、subgraph 或商业供应商的差异。

当前实现聚焦只读研究：池发现、池状态、swap 历史、TVL、交易量、费用和时间序列。

## 非目标

- 首版不负责报价、签名、提交交易或管理钱包。
- 不承诺用一个上游覆盖所有链、协议版本和历史深度。
- 不预先建设常驻 daemon、自有数据库、全量索引器或远程服务。
- 不把 provider 的原始 schema 直接暴露为稳定公共接口。

## 组件

```text
人 / agent
    |
    v
uniswap CLI  <----  uniswap skill（发现与操作指导）
    |
    v
统一查询与输出模型
    |
    +---- subgraph provider（历史和聚合数据优先）
    +---- RPC provider（原始日志、校验和补漏）
    +---- commercial provider（可选增强）
```

### CLI

CLI 是唯一稳定入口，承担参数校验、分页、单次调用内的并发与重试、provider 选择、
规范化和结构化输出。它无跨调用状态，所有域相关配置均来自当前进程环境。

### skill

skill 负责让 agent 发现能力，说明典型任务应该调用哪些命令、如何限定查询范围、如何
解释 provenance 和错误。skill 不复制 provider 逻辑，也不持有凭据。

### provider

每类上游实现同一组内部能力接口。查询规划器根据请求、支持矩阵和显式配置选择 provider；
降级必须出现在结果 metadata 中，不得静默改变口径。

## 稳定数据模型

所有公共实体至少包含以下维度：

- `chain_id` 与规范化 chain 名称
- `protocol_version`，例如 `v2`、`v3`、`v4`
- pool 标识；v4 pool ID 与旧版本 pool address 不混为一谈
- token address、decimals 和原始整数金额
- UTC 时间戳；适用时包含 block number、transaction hash 和 log index
- `source`、索引到的最高区块、查询时间和 schema version

金额同时保留原始整数和可读十进制值。USD 指标必须标明其定价来源，不能伪装成链上原始值。

## daemon 决策门槛

以下需求经真实使用证明后，才考虑增加 daemon：

- 多次 CLI 调用需要共享缓存、连接池或全局限流状态
- 需要持续监听新区块、执行长时间回填或维护本地索引
- 需要向其他机器提供独立远程服务
- 单次进程无法可靠完成的后台任务

单次调用内部的分页、并发、重试和连接复用不是 daemon 的理由。增加 daemon 后，CLI 仍是
公共入口，daemon 只是可选后端，且不负责 namespace 或凭据归属判断。

## 配置与安全

- API key 和 RPC 凭据从环境变量或受管 secret 引用读取。
- 凭据不得出现在命令参数、URL、日志、fixture 或 git 历史中。
- 默认日志写 stderr，数据写 stdout，便于 shell 管道稳定消费。
- MVP 不接触私钥；未来若增加交易能力，必须另做安全设计与独立决策。

## 分发与运行

当前不需要服务部署、容器、rig、launchd 或 systemd。每台需要使用的机器安装 Python 包，
并把 skill 发现链接铺到目标仓库即可。GitHub Actions 在 `v*` tag 与包版本一致时构建 wheel
和 sdist，并发布 GitHub Release。
