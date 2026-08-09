# ADR 0028: MCP PDF Upload and Safe Ingestion Concurrency

> **状态**：当前采用
> **日期**：2026-08-09

## Context

MCP 原有三个 Tool 均为只读接口。Agent 若要添加知识，只能绕过 MCP 调用脚本或 Dashboard。Dashboard 上传又在任务被队列接受前覆盖 `upload_root/filename`；同名并发请求可能使一个任务读取另一个请求的内容。

摄取会更新 Chroma、BM25、图片索引和 generation 控制面。允许多个 Pipeline 无限制并行写入这些存储，不能带来可证明的正确性收益。

## Decision

- MCP 增加 `upload_document` 和 `get_ingestion_job`。上传通过 JSON 参数传递 Base64 PDF，单文件上限为 50 MiB。
- Dashboard 与 MCP 共用 `UploadIngestionCoordinator` 和 `IngestionJobService`。
- 请求内容先写入独立暂存文件。任务被有界队列接受后，worker 才将暂存文件原子发布到稳定目标路径。
- 目标路径使用非阻塞文件锁保护。相同目标正在处理时返回 `document_busy`，拒绝的请求不得覆盖稳定源文件。
- 服务并发接收不同文件，但摄取使用容量为 8 的队列和单 worker。该模型不并行写入多个索引。
- Pipeline 继续负责内容哈希、不可变快照、SQLite claim、lease heartbeat 和 generation fence；上传层不复制这些逻辑。
- MCP 任务结果不返回绝对路径、文件哈希、上传内容或 Provider 原始异常。

## Consequences

- Agent 可以通过同一 MCP 连接提交文件并轮询任务，CLI 只承担协议适配。
- 并发语义是并发接收、安全排队、串行摄取，不是并行执行多个 Pipeline。
- 任务状态保存在当前服务进程内；进程终止后客户端可以重新提交，Pipeline 的内容幂等和 generation 控制避免重复发布。
- 当前文件锁依赖 POSIX `flock`，与项目的本地 macOS/Linux 部署边界一致。若增加 Windows 部署，再替换为跨平台锁实现。
