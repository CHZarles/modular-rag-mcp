# ADR-0009：query_knowledge_hub 的 MCP 边界与返回格式

- **状态**：当前采用
- **日期**：2026-07-31
- **决策范围**：MCP 客户端如何调用本地知识查询，以及领域响应如何转换为 MCP 结果
- **说明**：本文记录当前实现知识、示例和顾虑，不修改 `DEV_SPEC.md`

## 先用一句话理解

`query_knowledge_hub` 是 MCP 协议和 `KnowledgeService` 之间的适配器。

它不负责实现 Dense、BM25、RRF 或 Rerank 算法，只负责：

1. 向 MCP 客户端声明可用参数；
2. 校验客户端传入的数据；
3. 把参数转换成领域对象 `QueryRequest`；
4. 调用统一的 `KnowledgeService.query()`；
5. 把 `QueryResponse` 转换成人和程序都能使用的 MCP 返回值。

```text
MCP 客户端
    │ tools/call
    ▼
QueryKnowledgeHubTool
    │ QueryRequest
    ▼
KnowledgeService.query()
    │ QueryResponse
    ▼
ResponseBuilder.build_mcp_result()
    │
    ├─ content：给人或模型阅读的 Markdown
    └─ structuredContent：给程序读取的结构化字段
```

## 它和前面模块的关系

| 模块 | 职责 |
|------|------|
| 官方 MCP SDK | 处理 stdio、JSON-RPC、协议握手和 wire 格式 |
| `ProtocolHandler` | 注册 Tool、路由调用、执行线程切换和错误映射 |
| `QueryKnowledgeHubTool` | 校验 MCP 参数并适配 `KnowledgeService` |
| `KnowledgeService` | 为 MCP、CLI 和 Dashboard 提供统一的应用层查询入口 |
| `HybridQueryEngine` | 编排 Dense、Sparse、RRF、过滤和 Rerank |
| `ResponseBuilder` | 先构建领域响应，再把它转换为 MCP 双重返回格式 |

因此，E1 让 Server 能握手，E2 让请求能被正确路由，E3 才让客户端可以通过
`query_knowledge_hub` 执行实际的知识查询。

Tool 只依赖 `KnowledgeService`，不会直接访问 Chroma、BM25 或具体 Retriever。以后替换
检索后端时，只要 `KnowledgeService` 契约不变，MCP Tool 就不需要跟着修改。

## 输入参数

当前 Tool 接受三个参数：

| 参数 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `query` | 非空字符串 | 无，必填 | 用户要查询的问题 |
| `top_k` | 正整数 | `5` | 最多返回多少个相关片段 |
| `collection` | 非空字符串 | `default` | 限定查询的知识集合 |

除了向客户端发布 JSON Schema，Tool 还会在运行时再次校验数据。这是因为调用边界不能
假设每个客户端都正确执行 Schema 校验。

以下请求都会被拒绝：

```json
{}
{"query": "   "}
{"query": "lease", "top_k": 0}
{"query": "lease", "top_k": true}
{"query": "lease", "collection": " "}
{"query": "lease", "unknown": 1}
```

其中布尔值不能作为 `top_k`。虽然 Python 中 `bool` 是 `int` 的子类，但协议语义上
`true` 显然不是“返回一条结果”。未知参数也会被拒绝，避免客户端拼错字段后系统静默
忽略，产生看似成功但行为不符合预期的查询。

## 完整调用例子

客户端发出 MCP `tools/call`：

```json
{
  "name": "query_knowledge_hub",
  "arguments": {
    "query": "租约过期后，旧 worker 为什么不能发布结果？",
    "top_k": 2,
    "collection": "docs"
  }
}
```

Tool 会先去掉字符串首尾空白，再构造：

```python
QueryRequest(
    query="租约过期后，旧 worker 为什么不能发布结果？",
    top_k=2,
    collection="docs",
)
```

`KnowledgeService` 执行查询后，可能返回一个包含答案和引用的 `QueryResponse`。MCP
边界最终返回两种表达：

```json
{
  "content": [
    {
      "type": "text",
      "text": "旧 worker 发布时必须校验当前 claim_token。\n\n### 引用\n[1] ingestion-design.md，第 3 页"
    }
  ],
  "structuredContent": {
    "answer": "旧 worker 发布时必须校验当前 claim_token。",
    "citations": [
      {
        "id": "c1",
        "source": "ingestion-design.md",
        "page": 3,
        "chunk_id": "chunk-1",
        "score": 0.9,
        "text": "发布操作必须同时匹配 generation 和 claim_token。",
        "metadata": {}
      }
    ],
    "request_id": "req-1",
    "metadata": {
      "collection": "docs",
      "candidate_count": 1,
      "image_count": 0
    }
  }
}
```

## 为什么同时返回 content 和 structuredContent

`content` 和 `structuredContent` 不是两次查询，也不是两套互相独立的答案。它们来自同一
个 `QueryResponse`，只是面向不同使用者：

| 返回部分 | 主要使用者 | 适合做什么 |
|----------|------------|------------|
| `content` | 人、聊天模型、只支持文本的客户端 | 直接阅读答案和 `[1]`、`[2]` 引用列表 |
| `structuredContent` | UI、Agent、自动化程序 | 展示来源、跳转页码、读取 Chunk ID、保留请求 ID |

如果只返回 Markdown，程序就需要解析字符串才能找到页码和来源，容易受排版变化影响。
如果只返回 JSON，普通客户端又难以直接把结果展示给用户。双重表达让可读性和机器可用性
同时保留。

当前结构化引用中的 `score` 是查询链路最终候选项的分数语义，可能是 RRF 分数或未来的
Rerank 分数，不能一概理解为“百分之九十相关”。

## 空结果和错误不是一回事

知识库正常查询但没有命中时，Tool 返回一个成功结果：

```json
{
  "content": [
    {"type": "text", "text": "未找到相关知识库内容。"}
  ],
  "structuredContent": {
    "answer": "未找到相关知识库内容。",
    "citations": [],
    "request_id": null,
    "metadata": {}
  }
}
```

这和系统故障不同：

| 情况 | MCP 行为 |
|------|----------|
| 参数非法 | JSON-RPC `INVALID_PARAMS` |
| Tool 名称不存在 | JSON-RPC `METHOD_NOT_FOUND` |
| 查询内部异常 | 脱敏后的 JSON-RPC `INTERNAL_ERROR`，完整异常只写日志 |
| 查询成功但没有命中 | 正常 Tool 结果，引用列表为空 |

区分二者可以避免把 Embedding 服务故障错误地告诉用户成“知识库没有答案”。

## 为什么延迟创建 KnowledgeService

Server 使用缓存的工厂函数，在第一次调用 Tool 时才读取配置并装配本地查询链路：

```text
initialize / tools/list
        │
        └─ 不连接模型服务，也不创建完整查询链路

第一次 tools/call
        │
        └─ 创建并缓存 KnowledgeService

后续 tools/call
        │
        └─ 复用同一个 KnowledgeService
```

这样即使 Embedding 服务暂时不可用，MCP 客户端仍能完成握手并查看 Tool 列表。真正执行
查询时，依赖故障才会按内部错误返回。同步的知识查询通过 `asyncio.to_thread()` 执行，
避免阻塞官方 SDK 的异步协议循环。

## 如何验证真实 MCP 进程边界

内存流集成测试能验证 Server 路由，但不能证明命令行启动、环境继承、stdio 管道和真实本地
索引可以一起工作。I1 因此使用官方 `stdio_client` 启动独立 Python 子进程，再由官方
`ClientSession` 顺序执行：

```text
父进程：生成 PDF -> 摄取到临时 SQLite / BM25 / Chroma
                         │
                         └─ 持久化文件
                                  │
官方 stdio_client -> 启动 MCP Server 子进程
                                  │ initialize
                                  │ tools/list
                                  │ tools/call(query_knowledge_hub)
                                  ▼
                         structuredContent.citations
```

这条测试刻意不直接调用 `create_mcp_server()`，因为那会绕开几个生产环境中常见的故障点：

- 子进程入口能否通过 `python -m src.mcp_server.server` 正常启动；
- stdout 是否只承载 JSON-RPC，日志是否留在 stderr；
- initialize 之后，Client 是否能发现并调用真实 Tool；
- 服务能否从磁盘恢复父进程刚构建的索引；
- MCP 返回经过 SDK 序列化后是否仍包含结构化引用。

### 为什么使用 RAG_SETTINGS_PATH

MCP Server 是独立进程，不能直接接收 pytest 中的 Python 对象。启动参数通过
`RAG_SETTINGS_PATH` 指向临时配置，让子进程和父进程读取同一组持久化路径。没有这个配置
注入点，测试只能污染仓库默认数据目录，多个测试或多个工作区也容易互相干扰。

默认服务仍采用延迟初始化，环境变量在第一次 Tool 调用时读取；若未设置，则回退到
`config/settings.yaml`。服务创建后继续按进程缓存，因此运行期间切换环境变量不会热加载。

### 桌面客户端为什么要使用绝对可执行路径

VS Code 和 Claude Desktop 启动 MCP Server 时，不保证当前目录是项目根目录，也不保证
继承用户交互式 Shell 的 `PATH`、虚拟环境和环境变量。若配置只写：

```json
{"command": "python", "args": ["-m", "src.mcp_server.server"]}
```

同一份配置可能在终端可用，在桌面客户端中却导入失败，甚至误用系统 Python。当前运行说明
因此直接指向虚拟环境安装出的 `modular-rag-mcp` 绝对路径，并用绝对
`RAG_SETTINGS_PATH` 选择配置：

```text
MCP Client
  └─ /absolute/project/.venv/bin/modular-rag-mcp
       ├─ stdin/stdout：MCP JSON-RPC
       ├─ stderr：应用日志
       └─ RAG_SETTINGS_PATH：运行时配置与本地索引路径
```

这样客户端启动不依赖 `cwd`，Python 依赖也固定在项目虚拟环境中。API Key 通过客户端的
`env` 注入子进程，而不是提交到仓库；VS Code 可以用 password input 延迟询问，Claude
Desktop 则需要保护本地配置文件权限。

客户端配置需要同时解决三类隔离问题：

| 隔离 | 显式配置 | 避免的问题 |
|------|----------|------------|
| Python 环境 | 虚拟环境可执行文件的绝对路径 | 系统 Python 缺包或版本错误 |
| 应用配置 | `RAG_SETTINGS_PATH` 绝对路径 | 读取错误知识库或默认配置 |
| Provider 凭据 | 子进程 `env` | GUI 未继承 Shell 环境、密钥误入仓库 |

默认 `KnowledgeService` 会被进程内缓存，所以修改 settings 或环境变量后必须重启该 MCP
Server。MCP 的 stdout 仍必须保持协议纯净：不能用会输出欢迎语的 Shell wrapper，也不能
把应用日志改到 stdout，否则客户端会把普通文本当作损坏的 JSON-RPC 消息。

### 为什么 E2E 查询只启用 BM25

摄取阶段需要生成 Dense Vector，所以父进程注册一个确定性测试 Embedding，并把向量和
BM25 索引都写入磁盘。但 Python Provider 注册表是进程内状态，不会自动复制到 Server
子进程。让子进程再次使用该测试 Provider，反而会把测试绑到隐式跨进程状态上。

查询工厂现在支持两个独立开关：

```yaml
retrieval:
  enable_dense: false
  enable_sparse: true
```

关闭 Dense 后，工厂不会创建 Embedding Provider 或 Vector Store；Server 只从共享的
BM25 快照查询。这样测试不依赖网络、API Key 或父进程注册表，同时仍然验证真实 PDF、
真实摄取结果、generation 可见性、查询编排和 Citation 生成。生产默认配置仍同时启用
Dense 与 Sparse，不改变正常的混合检索行为。

两路都关闭会在装配阶段失败，而不是让运行时把“没有检索通道”伪装成“没有搜索结果”。
配置值也必须是真正的布尔值，避免字符串 `"false"` 在 Python 中因 truthy 语义被误判。

## 当前实际能力边界

### 1. 当前 answer 不是 LLM 综合生成的最终回答

当前 `LocalKnowledgeService` 执行检索后，`ResponseBuilder` 默认把召回到的上下文整理成
`answer`。它还没有额外调用 MiniMax M3，把多个 Chunk 综合成自然语言答案。

所以当前闭环更准确地描述为：

```text
问题 -> 混合检索 -> 相关 Chunk -> 引用化返回
```

而不是：

```text
问题 -> 检索 -> MiniMax M3 阅读上下文 -> 生成完整答案
```

Tool 已经复用了统一 `QueryResponse` 边界，未来增加生成阶段时不需要改变 MCP 调用方式，
但生成质量、引用对齐和幻觉控制仍需要单独设计与验证。

### 2. 完整 Dense 查询仍需要 Embedding 服务

MiniMax M3 的聊天或多模态能力不等于 Embedding 能力。当前 DenseRetriever 仍需要一个
可用的 OpenAI-compatible Embedding 端点。Embedding 调用失败时，Hybrid Search 可以在
Sparse 通道正常的前提下降级到 BM25；这不等于完整的 Dense + Sparse 混合检索已经运行。

### 3. 当前 Tool 没有暴露全部 QueryRequest 字段

领域对象还支持 `filters`、`include_images` 和 `request_id`，当前 Tool 只开放主线必需的
`query`、`top_k` 和 `collection`。这是有意缩小首版协议面，不代表这些能力不存在。

### 4. 当前 MCP 返回没有携带图片和完整候选调试信息

`QueryResponse` 可以包含 `images` 和 `items`，但当前 MCP 结果只输出答案、引用、请求 ID
和响应 metadata。客户端暂时不能通过这个 Tool 获取图片负载、RRF 通道贡献或完整候选项
debug。只有出现明确客户端需求后，再扩展稳定输出 Schema。

### 5. 延迟初始化缓存以进程为边界

默认 `KnowledgeService` 在进程内只创建一次。运行期间修改配置文件不会自动重建服务，
通常需要重启 MCP Server。当前没有热加载配置和依赖健康检查。

## 已知顾虑与升级触发条件

| 顾虑 | 当前处理 | 何时升级 |
|------|----------|----------|
| 只返回检索上下文，不生成综合答案 | 保持引用可追溯，不伪装成生成式 RAG | 有可用生成模型并建立答案、引用对齐评估后 |
| Dense 依赖单独的 Embedding 端点 | Dense 失败时允许 Sparse 降级 | 需要稳定混合检索质量时配置并监控 Embedding 服务 |
| MCP 输出暂不包含图片 | 保留领域层 `images` 能力 | 客户端确认支持 MCP 图片内容且有真实使用场景时 |
| 未暴露 filters 和 request_id | 首版保持小而明确的协议面 | UI 或 Agent 出现元数据过滤、请求串联需求时 |
| 结构化响应没有完整 candidates | 引用提供最小可追溯信息 | 客户端需要检索解释或调试面板时 |
| 服务实例不热更新配置 | 重启进程后重新装配 | 需要长期运行和在线配置变更时 |
| Tool 返回 Schema 尚未声明 outputSchema | 由 SDK 校验实际 `CallToolResult` | 外部客户端需要正式结构契约和兼容性管理时 |

协议字段一旦被外部客户端依赖，后续删除或改变语义会产生兼容性成本。因此新增字段前应先
确认调用场景；改变字段语义时应增加版本策略，而不是静默复用旧字段名。

## 验证证据

- `tests/unit/test_query_knowledge_hub_tool.py` 覆盖服务边界、参数规范化、引用返回和非法参数；
- `tests/unit/test_response_builder.py` 覆盖 Markdown、结构化引用和空结果；
- `tests/integration/test_mcp_server.py` 使用官方 `ClientSession` 验证
  `initialize -> tools/list -> tools/call` 完整流程；
- `tests/e2e/test_mcp_client.py` 使用官方 stdio Client 启动真实子进程，从临时持久化索引
  查询并断言结构化 citations；
- 当前 E3 完整回归结果：`254 passed, 5 skipped`；
- E3 代码已通过 Ruff、全量源码 Mypy、compileall 和 `git diff --check`。

## 关联实现

- `src/mcp_server/tools/query_knowledge_hub.py`
- `src/mcp_server/protocol_handler.py`
- `src/mcp_server/server.py`
- `src/core/services/knowledge_service.py`
- `src/core/services/local_knowledge_service.py`
- `src/core/response/response_builder.py`
- `tests/unit/test_query_knowledge_hub_tool.py`
- `tests/unit/test_response_builder.py`
- `tests/integration/test_mcp_server.py`
