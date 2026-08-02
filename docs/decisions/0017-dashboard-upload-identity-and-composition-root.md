# ADR-0017：Dashboard 上传身份与共享摄取装配

- **状态**：当前采用
- **日期**：2026-08-02
- **决策范围**：Dashboard 上传文件如何获得稳定文档身份，以及 CLI 与 Dashboard 如何复用同一套 Pipeline 装配
- **决策关系**：建立在 ADR-0004 的摄取编排、ADR-0005 的 generation 状态机和 ADR-0016 的文档生命周期之上
- **说明**：本文记录当前实现和面试相关原理，不修改 `DEV_SPEC.md`

## 一句话结论

上传的 PDF 先以原始文件名原子写入配置化的持久目录，再把这个稳定路径交给 IngestionPipeline；
CLI 和 Dashboard 都调用 `src.ingestion.build_ingestion_pipeline()`，避免两个入口装配出行为不同的摄取系统。

## 为什么不能直接摄取临时上传文件

Streamlit 的上传对象只有文件名和字节内容。最直接的实现是创建随机临时目录，把字节写入后调用
Pipeline，执行结束再删除临时文件。这个方案看似干净，但会破坏当前系统的文档身份模型。

当前逻辑文档身份为：

```text
doc_key = SHA256(normalized_source_path + "\0" + collection)
```

若每次上传都使用不同的随机临时路径，即使用户重复上传同一个文件，也会得到不同 `doc_key`：

```text
/tmp/upload-a7f1/guide.pdf + docs -> doc_key A
/tmp/upload-c923/guide.pdf + docs -> doc_key B
```

后果是内容去重、force 重摄取、generation 递增和文档删除都无法围绕同一个逻辑文档工作，Dashboard
会不断创建重复文档。临时路径不能承担领域身份。

## 当前上传流程

配置中声明持久上传目录：

```yaml
ingestion:
  storage:
    upload_root: ./data/uploads
```

页面执行以下流程：

```text
UploadedFile
    │  校验 .pdf、非空、剥离目录部分
    ▼
upload_root/.guide.pdf.<random>.uploading
    │  同目录原子 replace
    ▼
upload_root/guide.pdf
    │  IngestionRequest(source_path=稳定路径, collection=...)
    ▼
IngestionPipeline
```

同一文件名和 Collection 再次上传时路径不变，因此 `doc_key` 不变；内容哈希相同时正常跳过，内容变化
时创建下一 generation，启用 force 时即使内容相同也创建新代。

## 为什么先写临时文件再 replace

直接向 `upload_root/guide.pdf` 写入可能留下半个文件：进程中断、磁盘错误或并发读取都可能让 Pipeline
看到不完整内容。当前先在同一目录写入随机 `.uploading` 文件，再用 `Path.replace()` 切换目标文件。

同一文件系统内的 rename/replace 对观察者是原子的，因此稳定路径只会指向旧的完整文件或新的完整
文件。Pipeline 随后还会创建自己的不可变快照，保证哈希计算和 Loader 读取的是同一份字节。

这形成两层保护：

1. Dashboard 的原子落盘避免发布半写文件；
2. Pipeline 的摄取快照避免源文件在哈希与解析之间变化。

## 文件名在这里代表什么

Dashboard 把 `文件名 + Collection` 视为用户可理解的逻辑身份。上传名会先经过 `Path(name).name`，
避免 `../` 等路径片段逃逸上传目录。

这项选择有明确语义：

- 同名文件上传到同一 Collection：更新同一逻辑文档；
- 同名文件上传到不同 Collection：是两个逻辑文档；
- 不同文件名但内容相同：仍是两个逻辑文档；
- 删除索引不会删除上传源文件，便于失败后重试或重新摄取。

如果未来需要同一 Collection 内保留多个同名来源，应让用户选择相对目录或显式 document key，而不是
重新使用随机临时路径。

## 为什么把 Pipeline Builder 移到 src

原先完整装配只存在于 `scripts/ingest.py`。Dashboard 若直接导入脚本层，会形成“一个入口适配器依赖
另一个入口适配器”的反向依赖；若复制代码，又容易让两个入口使用不同的 Loader、Transform 顺序、
generation store 或批大小。

当前把 composition root 收敛为：

```python
build_ingestion_pipeline(settings) -> IngestionPipeline
```

它位于 `src/ingestion/factory.py`，统一装配：

- SQLiteIntegrityStore；
- PdfLoader 与共享 image_root；
- DocumentChunker 和三个 Transform；
- Dense/Sparse Encoder 与 BatchProcessor；
- Chroma、BM25、ImageStorage；
- generation 控制面和 claim lease。

CLI 与 Dashboard 仍负责各自的交互逻辑，但摄取能力的依赖图只有一个定义。这就是 composition root：
在系统边界集中把抽象端口连接到具体实现，业务 Pipeline 本身仍只接收依赖，不读取全局配置。

## 进度为什么使用回调而不是让 Pipeline 依赖 Streamlit

Pipeline 只暴露 `on_progress(stage, step, total)`，页面把它适配成 `st.progress()`。这样：

- Pipeline 不依赖 UI 框架；
- CLI 可以不传回调；
- 测试可以收集阶段序列；
- 未来可接 WebSocket、任务队列或日志，而不用修改摄取核心。

回调是典型的依赖倒置：核心层声明通知契约，外层决定如何展示。

## 当前边界

- Dashboard 当前按文件名覆盖上传源，不提供同 Collection 下同名多文档能力；
- 页面同步执行 Pipeline，适合本地维护场景，长任务还没有后台队列与取消能力；
- 原子 replace 解决单文件落盘完整性，不等于跨上传目录和四个索引存储的事务；
- 跨存储删除仍遵循 ADR-0016 的幂等补偿策略；
- Pipeline 在首次进入页面时按配置装配并缓存，配置文件修改时间变化后会重新装配。

## 面试表达建议

1. “我们的 doc_key 包含规范化路径，所以随机临时上传路径会把重复上传错误地变成新文档。”
2. “我先把上传文件原子落到配置化稳定路径，再交给 Pipeline 快照，身份稳定且不会读取半写文件。”
3. “文件名加 Collection 是当前产品语义，同名重传走 generation 更新而不是制造重复文档。”
4. “CLI 和 Dashboard 共享 src 层的 composition root，入口只做交互适配，核心 Pipeline 不依赖 Streamlit。”
5. “进度通过回调做依赖倒置，未来替换成异步任务或 WebSocket 时不用改摄取核心。”
