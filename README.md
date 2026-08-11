# Modular RAG MCP Server

面向本地资料库的检索服务。服务通过 MCP 返回候选 Chunk、来源、页码、各阶段分数和关联原图；不生成回答，也不将检索结果包装成问答结论。

## 运行边界

- 一个本地实例可被多个 MCP 客户端并发调用。
- 每个请求独立处理；`request_id`、`actor_key`、`session_id` 只用于请求上下文和 Trace 关联，不保存应用会话。
- 检索索引、BM25 数据、图片、摄取历史、Trace 和配置保存在本地磁盘，由连接到同一实例的客户端共享。
- 项目不提供认证、授权、租户隔离或分布式存储。对不受信任网络或按调用方隔离数据的场景，应由上层网关和存储边界负责。

## 能力

| 模块 | 当前实现 |
| --- | --- |
| 摄取 | PDF、DOCX、CSV、PNG/JPEG/WebP -> `Document` -> Chunk -> 索引；PDF 支持 MinerU 和 MarkItDown |
| 检索 | BM25、Dense 与 RRF Hybrid；可选独立 SQLite FTS5 字面量查找；默认配置为 BM25-only、Grep 关闭 |
| 多模态 | 摄取时可为图片生成描述；检索命中图片 Chunk 时返回原始图片内容 |
| MCP | stdio 与 Streamable HTTP；提供查询、Collection、文档摘要、资料上传和摄取任务查询工具 |
| 可观测性 | Query 与 Ingestion Trace 持久化到 SQLite，Dashboard 展示阶段输入、耗时和分数 |
| 评测 | PostgreSQL 回归集与 HotpotQA 检索基准；指标为 Hit@5、MRR@5 和图片召回检查 |
| 运维控制台 | React + FastAPI 私有维护界面，用于配置、摄取、检索调试、Trace 和 Benchmark |

## 接口

| Tool | 输入 | 输出 |
| --- | --- | --- |
| `query_knowledge_hub` | `query`、`top_k`、`collection` | 候选文本、来源、页码、分数、阶段分数和图片内容 |
| `grep_knowledge_hub`（按配置启用） | `pattern`、`top_k`、`collection`、`case_sensitive` | 完整匹配 Chunk、公开来源、页码、非重叠命中次数和截断状态 |
| `list_collections` | 无 | 可查询的 Collection |
| `get_document_summary` | `doc_id` | 文档标题、摘要、标签和来源信息 |
| `upload_document` | `filename`、`content_base64`、`collection`、`force`、`ai_enrichment` | 已排队的摄取任务 |
| `get_ingestion_job` | `job_id` | 任务状态、阶段进度和摄取结果 |

Streamable HTTP 默认监听 `127.0.0.1:8766`，MCP 路径为 `/mcp`。`/health/live` 不加载索引或模型，`/health/ready` 检查配置、知识存储和 Trace 存储。

## 快速开始

以下命令在项目根目录执行，要求 Python 3.10+ 和 Node.js 20+。

### 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
npm ci --prefix web
```

### 配置

运行时读取 [`config/settings.yaml`](config/settings.yaml)。`RAG_SETTINGS_PATH` 可以指定另一份配置文件；MCP 桌面客户端应传入绝对路径。

默认配置使用 MinerU 解析 PDF，并在需要图片描述或 Dense 评测时使用 MiniMax：

```bash
export MINERU_API_TOKEN='...'
export MINIMAX_API_KEY='...'
export OPENAI_BASE_URL='https://api.minimax.io/v1'
export SGPT_MODEL='MiniMax-M2.5'
```

不要将密钥写入 Git。Dashboard 保存的本地覆盖写入 Git 忽略的 `config/settings.local.yaml`，本地密钥写入 `config/secrets.local.yaml`。

字面量查找使用独立 SQLite trigram 索引，默认关闭。首次开启前保持 `enabled: false`，停止 MCP、
Dashboard 摄取和其他写入，执行离线重建，再开启并重启服务：

```bash
python scripts/rebuild_grep_index.py --settings config/settings.yaml
# 将 grep.enabled 改为 true 后重启 MCP 和 Dashboard
```

Grep 搜索最终 `ChunkRecord.text`，不调用 BM25、Chroma、Embedding 或 LLM。pattern 至少 3 个
Unicode 字符，默认不区分大小写，单次最多返回 20 个 Chunk。数据库缺失或自检失败时只禁用
Grep；原语义查询保持可用。回滚只需把 `grep.enabled` 改回 `false`。

### 摄取与本地查询

```bash
python scripts/ingest.py \
  --path /absolute/path/to/document.pdf \
  --collection default

python scripts/query.py \
  --query '要检索的问题' \
  --collection default \
  --verbose
```

摄取对未变化文档跳过重复处理；使用 `--force` 生成新版本。查询只读取当前 active generation。

### 启动 MCP

stdio 入口供 Claude Desktop、VS Code 等客户端启动：

```bash
RAG_SETTINGS_PATH="$PWD/config/settings.yaml" modular-rag-mcp
```

HTTP 入口：

```bash
RAG_SETTINGS_PATH="$PWD/config/settings.yaml" modular-rag-mcp-http
curl http://127.0.0.1:8766/health/live
curl http://127.0.0.1:8766/health/ready
```

MCP 客户端应将 `modular-rag-mcp` 的绝对路径设为 `command`，并在子进程环境中传入 `RAG_SETTINGS_PATH` 和所需凭据。stdio 的 stdout 只承载 JSON-RPC 消息。

### Agent CLI

`modular-rag-cli` 是对 HTTP MCP Tool 的命令行适配，不重复实现检索或摄取：

```bash
modular-rag-cli collections
modular-rag-cli query '要检索的问题' --collection default --top-k 5
modular-rag-cli document <doc_id>
modular-rag-cli upload ./document.pdf --collection default
modular-rag-cli upload ./architecture.png --collection default
modular-rag-cli job <job_id>
```

它默认连接 `http://127.0.0.1:8766/mcp`；`RAG_MCP_URL` 或 `--url` 可指定其他端点。stdout 只输出单个 `structuredContent` JSON，适合 Agent 直接消费；请求诊断写入 stderr。完整命令契约见 [extension/cli/README.md](extension/cli/README.md)。

单文件上传限制为 50 MiB，支持 PDF、DOCX、CSV、PNG、JPG/JPEG 和 WebP。独立图片必须成功生成视觉描述才会发布；PDF 内嵌图片描述失败时仍保留正文。服务可以并发接收不同文件，但通过容量为 8 的有界队列和单 worker 串行执行摄取，避免并行写入多个索引。同一稳定文件正在处理时返回 `document_busy`；独立暂存、原子发布、文件锁以及 Pipeline 的 claim、lease 和 generation fence 共同防止同名上传串写或过期 worker 发布结果。

### 启动 Dashboard

```bash
npm --prefix web run build
python scripts/start_dashboard_api.py
```

默认地址由 `dashboard.address` 和 `dashboard.port` 决定，当前为 <http://127.0.0.1:8501>。该界面默认绑定 loopback，不应直接暴露到公网。

## 检索与评测

`retrieval.enable_dense` 与 `retrieval.enable_sparse` 决定生产检索路径：

| Dense | Sparse | 路径 |
| --- | --- | --- |
| `false` | `true` | BM25 |
| `true` | `false` | Dense |
| `true` | `true` | Hybrid（RRF） |

HotpotQA Benchmark 使用与生产相同的 `HybridQueryEngine`，但在隔离索引上同时执行三种路径。质量门槛只按当前生产路径判定，不会因对照组得分更高而自动修改配置。

2026-08-09 在固定的 120-query HotpotQA 派生集上，使用 MiniMax `embo-01`、`chunk_size=1000`、`chunk_overlap=200` 记录的结果如下：

| 路径 | Hit@5 | MRR@5 |
| --- | ---: | ---: |
| BM25 | 0.9417 | 0.8326 |
| Dense | 0.2750 | 0.1800 |
| Hybrid | 0.8083 | 0.4878 |

图片检查为 `3/3` 命中并返回原图。基于该记录，默认生产路径保持 BM25。完整实验、基线和迁移要求见 [评测记录](docs/records/2026-08-09-title-aware-bm25.md)。该数据是当前配置与公开数据集上的回归证据，不代表业务领域准确率。

运行评测：

```bash
python scripts/evaluate_retrieval.py --dataset postgres
python scripts/evaluate_retrieval.py --dataset hotpotqa
```

HotpotQA 的 120 个确定性查询和 1,194 个去重段落已提交；原始下载文件保持 Git 忽略。来源、许可证和重建步骤见 [data/hotpotqa/README.md](data/hotpotqa/README.md)。PostgreSQL 回归集的来源和许可证见 [data/README.md](data/README.md)。

## 测试与构建

```bash
# Python
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/mypy src scripts

# Dashboard
npm --prefix web test
npm --prefix web run lint
npm --prefix web run build

# Wheel
uv build --wheel
```

默认测试使用临时目录和确定性 Provider，不读取个人索引，也不调用外部模型。`evaluate_retrieval.py` 是联网质量门禁，会使用当前配置的真实 Provider。

## 目录

```text
src/                 Core、摄取、Provider 适配器、MCP Server 与 Dashboard API
web/                 React Dashboard
scripts/             摄取、查询、评测、数据准备与本地启动入口
extension/cli/       Agent 调用的 HTTP MCP CLI
config/settings.yaml 默认运行配置
data/                公开回归集和本地运行数据目录
tests/               Unit、Integration 与 E2E 测试
docs/                架构决策、评测记录和实施计划
```

## 相关文档

- [配置文件](config/settings.yaml)
- [HotpotQA 数据与归因](data/hotpotqa/README.md)
- [检索评测记录](docs/records/2026-08-09-title-aware-bm25.md)
- [Grep 增量开发规格](docs/plans/0002-independent-literal-grep.md)
- [Grep 10,000 Chunk 基准](docs/benchmarks/grep-10000-2026-08-10.md)
- [运行边界 ADR](docs/decisions/0027-readonly-component-and-preference-trace.md)
- [接口与数据边界 ADR](docs/decisions/0009-query-knowledge-hub-mcp-boundary.md)
- [MCP 上传与并发边界 ADR](docs/decisions/0028-mcp-upload-concurrency.md)

## 许可证

代码采用 MIT License。随仓库分发的公开数据集遵循各自的许可证与归因要求，详见 `data/` 下的说明文件。
