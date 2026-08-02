# ADR-0018：Trace 持久化所有权与弹性 JSONL 读取

- **状态**：当前采用
- **日期**：2026-08-02
- **决策范围**：Ingestion Trace 由谁创建和持久化，Dashboard 如何安全读取仍在追加的 JSON Lines 文件
- **决策关系**：建立在 ADR-0015 的 Trace 阶段归属和 ADR-0017 的 Dashboard/CLI 入口装配之上
- **说明**：本文记录当前实现和面试相关原理，不修改 `DEV_SPEC.md`

## 一句话结论

Pipeline 只负责向调用方提供的 `TraceContext` 写阶段事件，CLI/Dashboard 入口负责创建上下文并在请求结束后
持久化；Trace 写入失败不得改变摄取结果，Dashboard 则按完整 JSONL 行读取并跳过损坏或半写记录。

## 为什么“已经打点”不等于“已经可观测”

F4 在 Pipeline 内加入了 load、split、transform、embed、upsert 等阶段打点，但如果入口调用时没有传入
`TraceContext`，或请求结束后没有执行 Collector，所有事件只存在于内存，Dashboard 永远读不到。

完整链路必须同时包含：

```text
入口创建 TraceContext
        │
        ▼
Pipeline/组件记录阶段
        │
        ▼
入口收集并 finish
        │
        ▼
traces.jsonl
        │
        ▼
TraceService 解析
        │
        ▼
Dashboard 动态渲染
```

Instrumentation 解决“记录什么”，Collector 解决“记录放到哪里”，TraceService 解决“如何读取”。三者缺一不可。

## 为什么持久化所有权在入口而不在 Pipeline

Pipeline 可以被 CLI、Dashboard、测试或未来的后台 Worker 调用。若 Pipeline 自己创建并写死 Collector：

- 核心编排会依赖具体文件路径和 JSONL 后端；
- 上层无法把多个子调用归入同一条 Trace；
- 测试难以注入内存上下文；
- 更换 OpenTelemetry、SQLite 或远程 Collector 时需要修改业务核心。

当前 `run_traced_ingestion()` 是入口侧适配器：

1. 根据请求创建 `TraceContext(trace_type="ingestion")`；
2. 把 source、collection、force、request_id 写入顶层 metadata；
3. 把同一个 Trace 传给 Pipeline；
4. 用结果补充 status、chunk_count、image_count、error；
5. 调用 `TraceCollector.collect()` 完成并追加 JSONL。

Pipeline 仍只依赖可选 TraceContext，不知道文件路径，也不决定是否开启可观测性。

## 为什么 Trace 写入失败不能覆盖业务成功

摄取可能已经完成三类索引写入并发布 active generation。若随后磁盘写 Trace 失败，却把接口结果改成 failed，
调用方会认为摄取失败并重试，实际数据却已经可见。这与“发布后回调不能改写业务结果”的原则相同。

当前 Collector 采用 best-effort：

```text
Ingestion success
      │
      ├── Trace append success -> 返回 success
      └── Trace append failed  -> 记录 warning，仍返回 success
```

可观测性系统应观察业务，不应反过来决定业务是否成功。代价是极端情况下会缺失一条 Trace，需要通过日志、
磁盘监控或更可靠的异步 Collector 发现。

## 为什么使用 JSON Lines

每条 Trace 独占一行，相比一个不断扩大的 JSON 数组：

- 追加不需要读取和重写整个文件；
- 单条记录损坏不会让整个历史不可解析；
- 可以流式读取，内存成本与单行大小相关；
- 便于 `tail`、日志采集器和后续批量导入。

它不是事务数据库。进程终止、并发 Writer 或磁盘写满都可能留下最后一条不完整记录，因此 Reader 必须
假设输入可能部分损坏。

## TraceService 如何做弹性读取

`TraceService` 每次建立一个文件快照视图，逐行执行：

1. 忽略空行；
2. `json.loads()` 解析单行；
3. 校验 trace_id、trace_type、时间戳、耗时、stages 和 metadata；
4. 无效行只增加 `malformed_line_count`，不阻断其他记录；
5. 按 `started_at` 倒序返回；
6. 按 trace_type 在服务层过滤。

这种策略特别适合 Writer 正在追加时 Reader 读到半行的情况。下一次 Streamlit rerun 会重新读取文件，若
该行随后写完整就会自然出现；页面不会因为一次竞争窗口崩溃。

## 为什么解析成独立只读模型

Dashboard 不直接恢复运行时 `TraceContext`，而是解析成 `TraceRecord` 和 `TraceStage`：

- 读取历史不需要伪造单调时钟内部状态；
- 数据经过类型和范围校验后再进入 UI；
- `elapsed_ms` 拒绝负数、NaN 和 Infinity；
- 页面不依赖 TraceContext 未来新增的运行时方法；
- Query 和 Ingestion 页面可复用同一个读取契约。

这是典型的写模型与读模型分离：写侧对象优化阶段记录，读侧对象优化查询和展示。

## method/provider 为什么必须随 Trace 存储

系统组件可插拔，只根据固定阶段名显示“Embedding”无法说明当前使用哪个实现。每个阶段同时记录：

```json
{
  "stage": "embed",
  "data": {
    "method": "batch",
    "provider": "DenseEncoder",
    "details": {"dense_vector_count": 12}
  }
}
```

Dashboard 用这些字段动态生成图例和详情标题，因此替换 Loader、Embedding 或 Store 后页面无需增加后端
名称判断。这也是可观测性 schema 应记录“语义角色 + 具体实现”的原因。

## 当前边界

- JSONL 追加适合本地单进程维护场景，不提供多 Writer 强一致性；
- Reader 当前每次扫描完整文件，历史很大时应增加分页、偏移索引或迁移 SQLite；
- 损坏行只计数不自动修复，避免 Reader 修改证据文件；
- Trace 持久化是 best-effort，尚无落盘重试队列；
- 当前页面展示主阶段耗时，子阶段仍保留在详情数据中供后续扩展；
- Query 入口需要复用同样的 Trace 所有权原则，不能只依赖 Query Pipeline 内部打点。

## 面试表达建议

1. “Pipeline 内打点只是 instrumentation，入口还必须拥有 Trace 的创建、结束和持久化生命周期。”
2. “Trace 写失败不能把已经发布的摄取改成失败，可观测性采用 best-effort 非干扰原则。”
3. “JSONL 适合追加和流式读取，但 Reader 必须容忍半写尾行和局部损坏。”
4. “我把运行时 TraceContext 与 Dashboard 只读模型分开，解析时校验时间戳和耗时范围。”
5. “method/provider 写进事件 schema，页面才能在替换可插拔组件后继续动态展示。”
