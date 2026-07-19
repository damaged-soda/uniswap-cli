# AGENTS.md — uniswap-cli 工作约定

## 当前阶段

项目目前处于设计和数据源验证阶段。README 与 `docs/` 是当前行为契约；实现不得静默偏离。

## 改动流程

- 除仓库初始化外，改动从 `codex/<主题>`、`feat/<主题>`、`fix/<主题>` 或
  `docs/<主题>` 分支发起，通过 PR 合入 `main`。
- PR 标题和描述默认使用中文。
- commit 使用个人身份 `leavan <damaged.soda@gmail.com>`。
- 不提交 API key、RPC URL 中的凭据、钱包、助记词、`.env` 或运行时缓存。

## 架构护栏

- 默认交付物是无状态 CLI + skill，不注册 MCP。
- 没有跨调用共享状态、后台任务或独立远程服务需求时，不引入 daemon。
- MVP 只读；交易报价、签名和执行不在首版范围。
- 上游实现放在 provider 边界后，公共输出不得泄漏某个 GraphQL 或商业 API 的内部结构。
- JSON 输出变更必须考虑 `schema_version` 和向后兼容性。

## 验证

- 文档中的外部事实优先引用官方来源。
- provider 集成必须有固定 fixture 的契约测试；真实网络测试应单独标记，且不得打印凭据。
- 合并前至少验证格式、单元测试、CLI `--help` 和无凭据泄漏。
