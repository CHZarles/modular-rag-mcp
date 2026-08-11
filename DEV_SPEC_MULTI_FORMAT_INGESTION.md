# Developer Specification: 多格式统一入库

> 版本：0.1
>
> 状态：Draft
>
> 类型：现有 Ingestion 能力的增量规范

## 1. 文档关系

本规范补充 [DEV_SPEC.md](DEV_SPEC.md) 和
[DEV_SPEC_INTERFACES.md](DEV_SPEC_INTERFACES.md)，只描述 PDF、DOCX、CSV 和独立图片
如何进入现有摄取体系。现有分代发布、存储一致性和查询契约继续有效。

相关决策：

- [ADR 0003](docs/decisions/0003-image-storage-filesystem-sqlite-boundary.md)：图片文件与引用存储。
- [ADR 0017](docs/decisions/0017-dashboard-upload-identity-and-composition-root.md)：上传文件身份与装配入口。
- [ADR 0028](docs/decisions/0028-mcp-upload-concurrency.md)：上传并发、队列与大小限制。

## 2. 目标与范围

### 2.1 目标

- 现有上传入口支持 PDF、DOCX、CSV、PNG、JPG/JPEG 和 WebP。
- 所有格式统一输出既有 `Document`，复用当前切分、增强、编码、存储、查询和删除链路。
- 独立图片通过视觉描述进入文本检索空间，命中后仍可返回原图。
- Web 只读展示支持格式、上传限制和实际生效的关键组件。
- 保留 PDF Loader 的可配置能力，不为固定格式集合增加插件框架。

### 2.2 第一版范围

| 格式 | 规范化结果 |
|---|---|
| PDF | 沿用 MarkItDown 或 MinerU，输出 Markdown 和内嵌图片引用 |
| DOCX | 使用 MarkItDown 输出 Markdown |
| CSV | 使用标准库 `csv`，每行转为带列名的自包含文本 |
| 独立图片 | 生成一个图片引用和一段必需的视觉描述 |

### 2.3 非目标

- 不支持 `.doc`、PPT/PPTX、XLS/XLSX、HEIC、GIF 或动画图片。
- 不在第一版抽取 DOCX 内嵌图片、批注或修订记录。
- 不实现批量上传、图库、OCR 工作台、CLIP 或独立多模态索引。
- 不新增数据库表、上传路由、任务状态机或公共领域对象。
- 不实现动态 Loader 注册、扩展名到类名的 YAML 映射或格式专用 Pipeline。

## 3. 架构决策

### 3.1 使用现有 Loader seam

`BaseLoader` 是 `src/ports/ingestion.py` 中的 `Protocol`，不是需要继承的基类。
`FormatRouter` 只要提供同样的 `supported_extensions` 和 `load(...) -> Document`，
就通过结构类型满足 `BaseLoader`：

```text
BaseLoader Protocol
        ^
        | structural typing
   FormatRouter
        |
        +-- .pdf  -> configured PDF Adapter
        +-- .docx -> DocxLoader
        +-- .csv  -> CsvLoader
        +-- image -> ImageLoader
```

因此，`FormatRouter` 是组合型 Adapter，不是 `BaseLoader` 的名义子类，也不引入新的继承层级。

### 3.2 不变的数据流

```text
Dashboard / MCP / CLI
          |
          v
UploadIngestionCoordinator
          |
          v
IngestionPipeline
          |
          v
FormatRouter -> format Adapter -> Document
          |
          v
DocumentChunker -> Transforms -> Encode -> Stores -> Publish
```

`IngestionPipeline` 仍只依赖一个 `BaseLoader`。格式差异在 Loader seam 内结束，
下游不得判断 `doc_type` 来选择另一套切分、索引或查询流程。

### 3.3 最小接口

现有接口保持不变：

```python
class BaseLoader(Protocol):
    supported_extensions: tuple[str, ...]

    def load(
        self,
        source_path: str,
        collection: str,
        trace: Any | None = None,
    ) -> Document: ...
```

新增 Router 只承担静态委派：

```python
class FormatRouter:
    supported_extensions = (".pdf", ".docx", ".csv", ".png", ".jpg", ".jpeg", ".webp")

    def __init__(self, loaders_by_extension: Mapping[str, BaseLoader]) -> None: ...
    def load(self, source_path: str, collection: str, trace: Any | None = None) -> Document: ...
```

Factory 先按现有配置创建 PDF Adapter，再与三个固定 Adapter 装配 Router。
不新增 `BaseDocxLoader`、`BaseCsvLoader`、`BaseImageLoader` 或通用注册表。

## 4. 功能需求

### 4.1 路由与公共输出

- **FR-01**：现有 `POST /api/ingestion/jobs` 接受全部目标格式，不新增上传路由。
- **FR-02**：扩展名匹配忽略大小写；未知扩展名在暂存前拒绝，不得回退到 PDF Loader。
- **FR-03**：Router 版本应包含所装配 Adapter 的 revision，避免解析规则变化后错误跳过重建。
- **FR-04**：所有 Adapter 返回现有 `Document`，metadata 至少包含
  `source_path`、`collection`、`doc_type`、`title` 和 `images`。
- **FR-05**：Adapter 只负责解析和规范化，不切分、不生成 embedding、不写索引。
- **FR-06**：空白 DOCX 和没有有效数据行的 CSV 必须失败，不发布零 Chunk generation。

### 4.2 独立图片

- **FR-07**：独立图片复制到现有 `image_root/<collection>/` 受管目录，文件名由内容哈希确定。
- **FR-08**：图片输出一个 `[IMAGE: image_id]` 占位符和一个现有 `ImageRef` 形状的引用。
- **FR-09**：独立图片必须成功生成视觉描述；模型不可用、调用失败或返回空文本时，
  任务失败且不发布新 generation。
- **FR-10**：PDF 内嵌图片保持现有降级语义，Caption 失败仍可发布 PDF 正文。
- **FR-11**：独立图片 Caption 不受 `ai_enrichment=false` 关闭；该开关仍只控制可选的
  Chunk 精炼和元数据富化。
- **FR-12**：成功发布的独立图片必须进入现有 `ImageStorage`，查询命中时由现有响应链路返回原图。

### 4.3 生命周期兼容

- **FR-13**：后台队列、同名文件锁、不可变快照、claim、lease 和 generation fence 保持不变。
- **FR-14**：Vector、BM25、Grep 和 ImageStorage 的写入、发布与回收顺序保持不变。
- **FR-15**：删除继续按现有 `source_path + collection` 跨存储执行，不增加格式分支。

## 5. 格式规范

### 5.1 公共 metadata

| 字段 | 规则 |
|---|---|
| `source_path` | Pipeline 最终恢复为上传目录中的规范绝对路径 |
| `collection` | 使用请求中的 collection |
| `doc_type` | `pdf`、`docx`、`csv` 或 `image` |
| `title` | 默认使用不含扩展名的文件名 |
| `images` | 无图片时为空列表 |

### 5.2 PDF

- 行为保持不变。
- `ingestion.loader.provider` 继续在 `markitdown` 和 `mineru` 之间选择。
- 正文输出 Markdown；内嵌图片继续使用占位符和现有降级策略。

### 5.3 DOCX

- 使用 MarkItDown DOCX Adapter 输出 Markdown。
- `doc_type=docx`，`images=[]`。
- 上传层确认文件是有效 DOCX 容器；Adapter 对空转换结果报错。
- 第一版不抽取内嵌图片。

### 5.4 CSV

- 使用 UTF-8 或 UTF-8 BOM 解码，其他编码第一版拒绝。
- 使用标准库 `csv`，支持引号、字段内换行以及逗号、分号、Tab 分隔符。
- 第一行必须是非空且不重复的表头；全空行跳过，至少保留一行数据。
- 每行输出一个 Markdown 小节，所有值只按文本处理：

```markdown
## Row 1

name: Alice
role: Architect
team: Platform
```

- 第一版不推断日期、数值或业务 schema。

### 5.5 独立图片

- Pillow 实际识别结果必须与 PNG、JPEG 或 WebP 后缀相容。
- 保留原始字节，不二次编码；`image_id` 使用文件 SHA256。
- 初始 `Document.text` 为标题和图片占位符：

```markdown
# architecture

[IMAGE: <image_id>]
```

- `images` 只含一个引用，`page=null`、`position={}`、`mime_type` 使用实际类型。
- `ImageCaptioner` 成功后替换占位符文本，同时保留 Chunk 中的 `image_refs` 和 `images`。

## 6. 上传准入与失败

### 6.1 准入规则

- 单文件上限继续为 50 MiB，服务端检查为准。
- PDF 校验 `%PDF-` 文件头。
- DOCX 校验 ZIP 和必需成员，并限制声明的解压总量，避免压缩炸弹。
- CSV 校验 UTF-8、无 NUL，并能解析有效表头和数据行。
- 图片使用 Pillow 验证内容、实际格式和单帧状态。
- 浏览器 MIME 只能用于提示，不能替代服务端校验。

### 6.2 稳定错误

| 错误码 | HTTP/任务状态 | 含义 |
|---|---:|---|
| `unsupported_file_type` | 400 | 后缀不在白名单 |
| `invalid_file` | 400 | Base64 或通用内容无效 |
| `invalid_pdf` | 400 | 保留现有 PDF 错误 |
| `invalid_docx` | 400 | DOCX 无效 |
| `invalid_csv` | 400 | CSV 无效 |
| `invalid_image` | 400 | 图片无效或不支持 |
| `file_too_large` | 400 | 超过 50 MiB |
| `document_busy` | 409 | 同一目标已有任务 |
| `ingestion_queue_full` | 429 | 队列已满 |
| `image_caption_required` | failed | 独立图片没有有效描述，不发布 |

`UploadIngestionCoordinator` 继续负责文件名清洗、准入、暂存、锁和任务提交。
Dashboard、MCP 和 CLI 只做入口适配，不建立各自的格式解析逻辑。

## 7. 配置与 Web 展示

### 7.1 配置

第一版不新增 YAML 配置：

- `ingestion.loader.provider` 只决定 PDF Loader。
- DOCX、CSV 和图片 Adapter 固定装配。
- 独立图片 Caption 是固定质量门，不新增开关。
- Caption 复用现有 LLM/Vision 配置。
- 格式集合、50 MiB 上限和 DOCX 解压上限使用代码常量。

依赖只做一项调整：

```text
markitdown[pdf] -> markitdown[pdf,docx]
```

CSV 使用标准库，图片继续复用已安装的 Pillow。

### 7.2 Web options

扩展现有 `GET /api/ingestion/options`，不新增接口：

```json
{
  "collections": ["default"],
  "ai_enrichment_default": false,
  "accepted_extensions": [".pdf", ".docx", ".csv", ".png", ".jpg", ".jpeg", ".webp"],
  "max_upload_bytes": 52428800,
  "pdf_loader_provider": "mineru",
  "image_caption_provider": "openai",
  "image_caption_model": "configured-model",
  "splitter_provider": "recursive"
}
```

约束：

- Provider 或 model 未配置时返回 `null`。
- 不返回 API Key、Token、Base URL 或完整 Prompt。
- Web 文件选择器和大小提示从响应派生；服务端校验仍是权威结果。
- 页面只读展示支持格式、上限、PDF Loader、图片描述模型和 Splitter，不提供底层参数编辑。

### 7.3 Web 交互

- 上传标题改为“添加资料”，拖拽区接受全部目标格式。
- `AI 富化` 继续控制可选文本增强；选择图片时说明 Caption 是必需步骤。
- 保留单文件、collection、强制重建、任务进度和文档删除交互。
- 不增加高级参数面板。

## 8. 改动范围

### 8.1 新增

| 文件 | 职责 |
|---|---|
| `src/libs/loader/format_router.py` | 静态格式路由 |
| `src/libs/loader/docx_loader.py` | DOCX 到 Markdown |
| `src/libs/loader/csv_loader.py` | CSV 到行文本 |
| `src/libs/loader/image_loader.py` | 独立图片到 `Document + ImageRef` |

### 8.2 修改

| 模块 | 改动 |
|---|---|
| Loader exports / Ingestion factory | 装配 Router 和四类 Adapter |
| Upload coordinator | 多格式准入和图片 Caption profile |
| ImageCaptioner | 独立图片严格失败，PDF 继续降级 |
| Dashboard API / Web | 扩展 options、选择器和只读展示 |
| MCP / CLI | 泛化描述和本地文件校验，继续复用 Coordinator |
| `pyproject.toml` | 启用 MarkItDown DOCX extra |
| Tests / README | 补格式契约、入口行为和使用说明 |

### 8.3 明确不改

- `BaseLoader`、`Document`、`Chunk`、`ImageRef` 公共接口。
- `IngestionPipeline` 阶段和发布逻辑。
- Vector、BM25、Grep、ImageStorage schema 与查询接口。
- 文档删除和多模态响应组装。

预计新增 4 个 Loader 模块，修改约 10 个生产/入口模块，并补 5 至 7 个测试文件。
改动量中等，但核心链路侵入低；没有数据库迁移和第二套 Pipeline。

## 9. 测试与验收

### 9.1 最小测试集

- Router：全部扩展名、大小写、未知格式、Adapter revision。
- DOCX：正常、空文档、损坏容器。
- CSV：BOM、引号/换行、错误表头、空数据。
- 图片：三种格式、损坏内容、格式不匹配、Caption 成功与失败。
- Pipeline：四类格式各一个完整入库测试；PDF Caption 失败仍能发布。
- Upload/API：50 MiB、错误码、同名锁、队列满、options 无敏感信息。
- Web/MCP/CLI：使用同一格式集合，不建立重复解析路径。

### 9.2 验收标准

- **AC-01**：现有 PDF 摄取、检索、图片返回和删除测试保持通过。
- **AC-02**：DOCX 中的唯一短语可检索到对应 Chunk。
- **AC-03**：CSV 的列名和值组合可检索到对应行。
- **AC-04**：独立图片可按视觉描述检索，并返回原图。
- **AC-05**：独立图片 Caption 失败时任务失败，旧 active generation 不受影响。
- **AC-06**：PDF 内嵌图片 Caption 失败时正文仍成功发布。
- **AC-07**：Web 可看到格式、限制和关键组件，但不能编辑底层参数。
- **AC-08**：`IngestionPipeline`、领域类型和数据库 schema 无变更。

## 10. 实施与回滚

### 10.1 实施顺序

1. 实现 Router、DOCX/CSV/Image Adapter 和 contract tests。
2. Factory 接入 Router，泛化上传准入，补图片 Caption 质量门。
3. 扩展 Dashboard options 和 Web 上传界面。
4. 对齐 MCP、CLI、README，并运行四类格式的集成测试。

### 10.2 回滚

- Factory 恢复注入原 PDF Loader，即可停止接收新格式，不需要数据库回滚。
- 已入库的新格式仍是普通 `ChunkRecord` 和 `ImageRef`，旧查询与删除链路可继续处理。
- 需要清理时使用现有文档删除能力，不直接删除索引或 SQLite 文件。

## 11. 结论

该方案符合现有体系：`FormatRouter` 复用真实存在的 Loader seam，四种输入在 Loader 后收敛为
同一 `Document`，下游零格式分支。配置仅保留真正可变的 PDF Provider；固定策略不配置化。

这是中等工作量、低下游侵入的增量改动。它比在 Pipeline 中增加多套分支更整洁，
也比建设通用插件框架更符合当前需求。
