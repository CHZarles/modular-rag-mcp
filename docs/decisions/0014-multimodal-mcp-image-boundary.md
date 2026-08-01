# ADR-0014：MCP 多模态图片组装与本地/远端文件边界

- **状态**：当前采用
- **日期**：2026-08-01
- **决策范围**：检索命中的图片如何进入 `QueryResponse` 和 MCP `content`，以及本地模式与 HTTP 模式分别允许读取哪些数据
- **决策关系**：沿用 ADR-0003 的图片文件与索引边界、ADR-0009 的 MCP 响应格式，并补充 ADR-0011 所在查询链路的最终响应阶段
- **说明**：本文记录当前实现、原理、失败语义和限制，不修改 `DEV_SPEC.md`

## 先用一句话理解

图片返回分成两个阶段：本地响应组装器可以读取命中 Chunk 明确引用的本地图片并生成
`ImagePayload.data_base64`；MCP 序列化器只消费已经进入 `QueryResponse` 的 Base64，绝不把
`uri` 当成本机路径再次读取。

```text
本地模式
Chunk.metadata.images[].path
    → 读取本地文件
    → Base64
    → QueryResponse.images[]
    → MCP ImageContent

HTTP 模式
远端服务生成 QueryResponse.images[].data_base64
    → MCP Server 只校验并转发 Base64
    → MCP ImageContent
```

## 为什么要拆成两个阶段

图片在系统中有两种不同身份：

1. 检索阶段的图片是一个存储引用，例如本地 `path` 或远端 `uri`；
2. MCP 协议阶段的图片是可以直接传输的内容，即 Base64 数据和 MIME 类型。

如果把二者混在一起，MCP Server 看到一个路径就可能尝试读取它。这在本地单进程中看似可行，
但切换到 HTTP KnowledgeService 后，路径通常属于远端容器或另一台机器：

```text
/remote-host/data/images/docs/diagram-1.png
```

本地 MCP 进程既不拥有该文件，也不应猜测共享挂载、拼接 URL 或主动下载。正确的能力归属是：
拥有图片文件的服务负责读取它，跨服务边界只传递明确的内容或未来定义好的资源协议。

因此当前实现保留两个独立步骤：

| 阶段 | 输入 | 允许的 I/O | 输出 |
|------|------|------------|------|
| 本地领域响应组装 | `RetrievalCandidate.metadata.images` | 可读取 metadata 中明确给出的本地 `path` | `ImagePayload` |
| MCP 协议序列化 | `QueryResponse.images` | 不允许读取任何路径或 URI | MCP `ImageContent` |

这个分层也使 HTTP 适配器尚未实现时，远端边界已经由当前代码结构约束，而不是留给未来调用者
自行约定。

## 本地图片如何进入 QueryResponse

`ResponseBuilder.build()` 调用：

```python
images = multimodal_assembler.resolve_images(
    candidates,
    request.include_images,
)
```

每个候选 Chunk 可携带如下 metadata：

```python
{
    "source_path": "manual.pdf",
    "images": [
        {
            "image_id": "diagram-1",
            "path": "data/images/docs/diagram-1.png",
            "mime_type": "image/png",
            "page": 3,
        }
    ],
    "image_refs": ["diagram-1"],
}
```

`image_refs` 表示 Chunk 引用了哪些图片，`images` 提供返回图片所需的结构化位置和类型。当前
Assembler 从 `images` 读取完整记录，而不是根据 `image_refs` 自行打开 SQLite 图片索引。
上游 Splitter 会把 Chunk 实际引用的图片记录分发到该 Chunk 的 `metadata.images`，因此响应层
不需要了解图片索引表或存储实现。

对于每条有效图片记录，Assembler：

1. 取得 `image_id`；
2. 读取 `path` 指向的本地文件；
3. 使用标准 Base64 编码文件字节；
4. 构造 `ImagePayload`；
5. 将图片数量写入响应 metadata。

```python
ImagePayload(
    image_id="diagram-1",
    mime_type="image/png",
    data_base64="iVBORw0KGgo...",
    uri="data/images/docs/diagram-1.png",
    source_ref="chunk-7",
)
```

`uri` 仍可保留来源和诊断信息，但它不是 MCP 序列化阶段获取图片数据的后备方案。

## 为什么按 image_id 去重

Chunk 通常存在重叠，相邻 Chunk 也可能同时包含同一图片占位符。若不去重，一次查询可能把
同一张大图重复编码和返回多次。

Assembler 使用 `image_id` 作为响应内的稳定身份：

```text
chunk-7 → diagram-1
chunk-8 → diagram-1
chunk-9 → table-2

QueryResponse.images → [diagram-1, table-2]
```

只有成功加载的图片才进入去重集合。这样第一条同 ID 记录的文件缺失时，后续有效记录仍有
机会提供图片。HTTP 服务直接提供的 `QueryResponse.images` 当前不会在 MCP 序列化阶段再次
去重，远端生产者应遵守响应契约并避免重复项。

## MIME 类型如何确定

本地组装按以下优先级确定 MIME：

1. 使用 metadata 中以 `image/` 开头的合法 `mime_type`；
2. 根据路径扩展名调用标准 MIME 推断，例如 `.jpg` → `image/jpeg`；
3. 无法识别时回退为 `image/png`。

MCP 输出字段必须使用协议要求的 `mimeType`：

```json
{
  "type": "image",
  "data": "iVBORw0KGgo...",
  "mimeType": "image/png"
}
```

当前实现不会通过 Pillow 完整解码图片来重新判断格式。也就是说，它验证传输契约和 MIME
形状，但不承担图片内容取证。需要严格防伪或内容安全扫描时，应在图片摄取或专用媒体服务中
完成，而不是增加每次查询的解码成本。

## Base64 为什么还要校验和规范化

HTTP 返回的数据属于跨进程输入，即使领域类型标注为 `str`，运行时仍可能出现空值、损坏内容
或 Data URL。MCP 序列化前会执行：

1. 只接受字符串；
2. 对 `data:image/png;base64,...` 移除 Data URL 头；
3. 使用严格 Base64 校验进行解码；
4. 拒绝空字节内容；
5. 重新编码为规范的标准 Base64 字符串。

以下输入不会生成图片块：

```text
None
""
"not base64!"
只有 uri、没有 data_base64 的 ImagePayload
mime_type 不是 image/* 的 ImagePayload
```

跳过非法图片而不是让整个 Tool 调用失败，是因为图片是查询答案的附属内容。只要文本和引用
仍可用，客户端就应得到可降级的结果。

## HTTP 模式的硬边界

MCP 图片构造函数只读取：

```python
image.data_base64
image.mime_type
```

它不会读取、解析或探测：

```python
image.uri
image.metadata["path"]
任何约定目录
图片索引数据库
```

因此下面的远端响应是安全且可预测的：

```python
ImagePayload(
    image_id="remote-1",
    mime_type="image/jpeg",
    data_base64="/9j/4AAQ...",
    uri="/remote-host/images/remote-1.jpg",
)
```

即使 `uri` 对本机完全不可访问，只要 `data_base64` 合法，MCP 仍会返回图片。反过来，如果
远端只返回 `uri`，当前实现只返回文本，不会偷偷访问路径或发起网络请求。

未来如果图片体积使内嵌 Base64 不合适，应显式设计 MCP Resource、签名 URL 或受控图片代理，
而不是恢复“尝试读取 URI”的隐式行为。

## 最终 MCP 内容顺序

`ResponseBuilder.build_mcp_result()` 始终先生成文本块，再追加有效图片块：

```json
{
  "content": [
    {"type": "text", "text": "答案和引用..."},
    {"type": "image", "data": "...", "mimeType": "image/png"},
    {"type": "image", "data": "...", "mimeType": "image/jpeg"}
  ],
  "structuredContent": {
    "answer": "答案...",
    "citations": [],
    "request_id": "req-1",
    "metadata": {"image_count": 2}
  }
}
```

文本优先保证不支持图片展示的客户端仍能理解主要结果。现有 `structuredContent` 的答案、引用、
请求 ID 和 metadata 结构保持兼容；图片二进制不会再复制进 structured content，避免同一响应
重复放大。

## 失败和降级语义

以下问题只影响对应图片，不影响文本答案：

- metadata 不是列表或图片记录不是对象；
- 缺少 `image_id` 或 `path`；
- 本地文件不存在、不可读或为空；
- HTTP Base64 非法或为空；
- MIME 类型不是图片类型。

`include_images=False` 则在读取任何文件前直接返回空图片列表。这既是功能开关，也是调用方在
带宽敏感场景下避免图片 I/O 的方式。

当前实现没有设置单张图片大小、图片数量或响应总字节数上限。Base64 通常会让数据体积增加约
三分之一，因此在通过网络提供服务前，应补充明确的限额、截断策略和可观测指标。限额必须是
公开配置和稳定语义，不能静默截断到一个无法解释的固定值。

## 验证重点

集成测试覆盖以下边界：

- 本地 PNG 文件能被读取、Base64 编码并生成 MCP `ImageContent`；
- Base64 解码后的字节与原文件完全一致；
- 相邻 Chunk 重复引用同一 `image_id` 时只返回一次；
- 缺失图片不会破坏有效图片和文本结果；
- HTTP 风格响应使用远端提供的 Base64 和 MIME；
- 测试显式禁止 `Path.read_bytes()`，证明 HTTP MCP 序列化不会读取远端路径；
- URI-only 和非法 Base64 图片被跳过，文本仍正常返回。

这里最重要的回归保护不是“响应中有一张图片”，而是同时证明两个方向：本地模式确实能读取
拥有的文件，HTTP 模式确实不会读取不拥有的文件。

## 决策结果

当前采用以下规则：

- 图片存储引用与协议图片内容分离；
- 本地 Assembler 是唯一允许从 Chunk 图片路径读取文件的响应组件；
- MCP 序列化只信任并校验 `QueryResponse.images[].data_base64`；
- `uri` 仅是引用信息，不是隐式读取或下载指令；
- 图片错误按单项降级，不能遮蔽可用的文本答案；
- MCP `content` 保持文本在前、图片在后，既有 structured content 保持兼容；
- 远端资源传输、大小限制和媒体安全属于后续显式设计，不在当前路径读取逻辑中猜测实现。

