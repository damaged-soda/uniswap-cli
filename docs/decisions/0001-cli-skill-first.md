# ADR 0001：CLI + skill 优先，daemon 按需

- 状态：Accepted
- 日期：2026-07-19

## 背景

目标是让人和 agent 快速查询 Uniswap 数据。MCP、常驻 daemon 和远程 HTTP 服务都能
暴露能力，但也会引入客户端注册、schema 常驻上下文、环境绑定、服务运维和多机部署成本。
当前核心需求只是按请求读取外部 API 或链上数据，没有跨调用共享状态。

## 决策

实现一个无状态 `uniswap` CLI，并用 skill 提供 agent 侧发现和操作指导。CLI 直接调用
subgraph、RPC 或可选商业 provider。首版不注册 MCP，不运行 daemon，也不部署到 rig。

## 后果

优点：

- 人和不同 agent 客户端共享同一入口
- namespace、凭据和默认配置在调用时由环境决定
- CLI 可独立测试、组合和审计
- provider 或传输方式变化不会改变调用方契约

代价：

- 每次进程需要独立初始化连接
- 暂无跨调用缓存和后台任务
- skill 的质量决定 agent 能否正确发现和组合命令

只有跨调用缓存/限流/连接池、持续索引、后台回填或远程服务需求被实际证明时，才新增
可选 daemon。CLI 仍保持为公共入口。
