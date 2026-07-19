# uniswap-cli

面向人和 agent 的 Uniswap 数据查询 CLI。项目当前处于设计阶段，第一目标是用统一、
可审计的命令行接口快速获取池、交易和历史指标，不包含交易执行。

## 核心决策

- 默认形态是无状态 CLI + skill，不注册 MCP。
- CLI 每次调用直接访问上游；没有跨调用共享资源时不运行 daemon。
- 历史数据优先来自 Uniswap subgraph，链上 RPC 用于校验、补漏和原始事件查询。
- 第三方商业 API 是可选 provider，不成为调用方依赖。
- 所有结果都携带来源、链、协议版本、区块或时间范围和 schema 版本。
- API key、RPC URL 等凭据只从调用环境读取，永不进入 git。

完整决策见 [架构](docs/architecture.md) 和
[CLI + skill 优先的 ADR](docs/decisions/0001-cli-skill-first.md)。

## 计划中的命令面

以下只是待实现契约，不代表当前已有可执行程序：

```text
uniswap chains list
uniswap pools list --chain ethereum --protocol v3
uniswap pools get --chain ethereum --protocol v3 --pool 0x...
uniswap swaps list --chain ethereum --protocol v3 --pool 0x... --from ... --to ...
uniswap series get --metric volume-usd --interval 1d --pool 0x... --from ... --to ...
uniswap raw events --chain ethereum --address 0x... --from-block ... --to-block ...
uniswap doctor
```

输出默认使用 JSON。详细草案见 [CLI 契约](docs/cli-contract.md)。

## 数据来源结论

Uniswap 的 Trading API 适合报价和交易执行，不是完整的历史分析 API。历史研究的第一
选择是按协议版本和链拆分的 subgraph；需要链上真值或 subgraph 无法覆盖的字段时，再
读取 RPC 日志。公开 subgraph deployment 的维护状态和 schema 必须在使用前校验，生产
场景可以进一步考虑自部署或购买商业数据服务。

依据、限制和降级顺序见 [数据源策略](docs/data-sources.md)。

## 当前状态

仓库目前只包含设计文档。接下来先完成数据源验证和统一数据模型，再选择实现语言并提交
第一个只读 MVP。路线图见 [实施路线](docs/roadmap.md)。

## 文档

- [架构](docs/architecture.md)
- [数据源策略](docs/data-sources.md)
- [CLI 契约](docs/cli-contract.md)
- [实施路线](docs/roadmap.md)
- [ADR 0001：CLI + skill 优先](docs/decisions/0001-cli-skill-first.md)

## License

许可证尚未选择；当前保留所有权利。公开可见不等于已授予再分发或修改许可。
