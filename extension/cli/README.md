# CLI Extension

状态：规划中。该目录当前不包含可执行 CLI，也不改变现有 MCP 服务行为。

## 目标

后续 CLI 的主要调用方是 Agent，不是人工导航界面。它作为本地 MCP 服务的运维入口，负责群晖 PDF 导入和导入后检索。这里的“专属桶”对应现有的 `collection`：它限定检索范围，但不构成用户认证、权限控制或租户隔离。

每个命令应使用稳定的子命令和参数，在 stdout 输出单个 JSON 结果，在 stderr 输出诊断信息，并用退出码区分参数错误、同步失败和摄取失败。对数据有副作用的命令必须提供 `--dry-run`。

## 群晖导入

- 复用 [`scripts/import_synology.py`](../../scripts/import_synology.py) 的 SSH/rsync 同步和摄取链路，不重复实现同步或 PDF 摄取。
- Agent 负责向用户收集群晖地址、SSH 端口、远程目录和用户名。密码必须经附着终端的无回显输入传给 SSH；不得出现在命令行、环境变量、配置、stdout、日志或 Trace。
- 用户选择一个合法的 `collection` 名称。导入成功后，该 Collection 中的文档由现有 MCP `list_collections` 和 `query_knowledge_hub` 读取。
- 需要在导入前显式显示目标 Collection、远程目录和同步范围；失败时保留现有索引，不把部分同步结果标为成功。

## 实现边界

- 不保存群晖密码；支持 SSH 私钥时优先使用系统 SSH 认证。
- 不新增“bucket”数据结构。若需要空 Collection 在导入前即可被 MCP 发现，应先在 Core 层补充 Collection 创建能力，而不是依赖 Dashboard 配置服务。
- 不在该扩展中实现认证、授权或多租户；`collection` 仅是共享本地实例中的检索命名空间。
