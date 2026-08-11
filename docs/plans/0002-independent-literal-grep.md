# 独立字面量 Grep 检索增量开发规格

> 文档类型：Incremental DEV SPEC
> 版本：1.0
> 状态：已完成
> 日期：2026-08-10
> 基线：当前 `DEV_SPEC.md`、现有 generation 发布机制、MCP Server 和 React Dashboard
> 实施范围：独立 Chunk 正文索引、MCP Tool、Dashboard 精确查找、迁移与生命周期接入

## 0. 文档关系

本规格把已确认的 Grep 方案写成可直接实施和验收的增量需求。它只定义“按字面量查找最终
Chunk 正文”的能力，不改变现有语义检索架构。

发生冲突时，本规格仅在 Grep 范围内优先；BM25、Chroma、Hybrid Query、RRF、reranker、图片
召回和现有 MCP Tool 的行为仍以当前实现及 `DEV_SPEC.md` 为准。

实施完成后再把稳定的配置、目录和运行说明合并回 `DEV_SPEC.md`。本文件保留为设计和验收依据。

---

## 1. 一句话说明

新增一份独立的 SQLite FTS5 trigram 索引，保存摄取流程最终生成的 `ChunkRecord.text`。Grep
查询只访问这份索引和 FileIntegrity 的已发布版本清单，不调用 BM25、向量库、Embedding、LLM
或 Hybrid Query。

```text
现有语义检索：问题 -> Dense/BM25 -> RRF/Rerank -> 结果

新增精确查找：字面量 -> 独立 SQLite trigram 索引 -> Python 精确校验 -> 结果
```

这两条链路只共享最终 Chunk 数据和 FileIntegrity 的版本可见性，不共享检索索引。

---

## 2. 目标、边界与术语

### 2.1 目标

1. 在单个最终 Chunk 正文中查找至少 3 个 Unicode 字符的字面量。
2. 支持中文、英文及其他语言；默认不区分大小写，可显式切换为区分大小写。
3. 返回完整 Chunk、非重叠命中次数和公开引用信息。
4. 以独立索引承接后续数据增长，不把 Grep 查询压力或数据结构塞进 BM25。
5. 默认关闭；关闭或故障时，不影响现有 MCP、BM25、Chroma 和 Dashboard 语义检索。

### 2.2 术语

| 术语 | 本规格中的含义 |
|---|---|
| 字面量 | 输入是什么就查什么，不把 `.`, `*`, `[]` 等字符解释成正则或查询操作符 |
| 最终 Chunk | 完成 ChunkRefiner、MetadataEnricher、ImageCaptioner 等 transform 后，由 `VectorUpserter` 生成的 `ChunkRecord` |
| generation | 同一文档的一次摄取版本编号，例如版本 1、版本 2；不是 AI 文本生成 |
| active generation | FileIntegrity 当前已发布、允许查询看见的文档版本 |
| 旧版本清理 | 新版本发布后删除旧 generation 的物理记录；本期不新增定时任务 |
| 有效检索串 | 区分大小写时为原 pattern；不区分大小写时为 NFKC + casefold 后的 pattern |
| Grep available | 配置已开启，数据库格式正确，索引自检通过，active Chunk 数量与 FileIntegrity 一致 |

### 2.3 明确非目标

- 不支持正则表达式、通配符、模糊匹配、跨 Chunk 匹配或多 pattern 查询。
- 不计算 BM25 分数、相似度分数，不参与 RRF、reranker 或 Hybrid Query。
- 不返回图片、分数、阶段分数、命中位置或高亮片段。
- 不提供公共分页或游标；只返回有限的 Top K 和 `truncated`。
- 不新增 OpenSearch、Elasticsearch、PostgreSQL、Redis 或其他运行时服务。
- 不新增索引接口、单实现工厂、后台 worker、定时清理脚本或在线 schema 迁移框架。
- 不承诺本期容量超过 10,000 个 active Chunk；超出后以实际基准决定是否升级后端。

---

## 3. 需求规格

### 3.1 功能需求

| ID | 优先级 | 需求 |
|---|---|---|
| FR-01 | P0 | `grep.enabled` 默认必须为 `false`；关闭时不得创建或打开 Grep DB、注册 MCP Tool、写入 Grep 数据或显示 Web 精确查找模式 |
| FR-02 | P0 | Grep 必须使用独立 SQLite 数据库；运行时查询不得读取 BM25 或 Chroma |
| FR-03 | P0 | 索引内容必须来自 `VectorUpserter` 返回的最终 `ChunkRecord`，搜索范围仅为每条记录的完整 `text` |
| FR-04 | P0 | `pattern` 必须保留原值、不执行 trim；原值和有效检索串均至少 3 个 Unicode code point，原值最多 4,000 个，且不得包含 NUL |
| FR-05 | P0 | 查询只执行字面量子串匹配；FTS 只负责召回候选，最终是否命中及命中次数必须由 Python 校验 |
| FR-06 | P0 | 默认使用 `NFKC + casefold` 进行不区分大小写检索；`case_sensitive=true` 时按原始 Python 字符串区分大小写 |
| FR-07 | P0 | 每次查询只返回查询开始时 FileIntegrity active generation 快照中的 Chunk，不读取未发布或旧版本 |
| FR-08 | P0 | 结果必须按 `source_path`、页码、`chunk_index`、`chunk_id` 稳定升序排列；`top_k` 范围为 1..20，默认 20 |
| FR-09 | P0 | 查询达到 deadline 时返回已确认的部分结果，并设置 `timed_out=true`、`truncated=true`；超时不是 5xx 错误 |
| FR-10 | P0 | 开启 Grep 后，新 generation 必须在发布前完成 Grep 整批写入；写入失败必须阻止本 generation 发布，旧 active generation 继续服务 |
| FR-11 | P0 | 发布成功后立即顺手清理该文档的旧 Grep generation；失败 generation 做 best-effort 清理，残留在该文档下次成功发布时再次清理 |
| FR-12 | P0 | 删除文档时必须删除 Grep 数据；删除失败必须保留 FileIntegrity 记录作为重试依据，不得出现控制记录已删而 Grep 正文仍残留 |
| FR-13 | P0 | 现有数据必须通过一次性离线脚本从 BM25 中保存的最终 Chunk 迁入 Grep；服务运行时不得依赖该迁移读取接口 |
| FR-14 | P0 | 数据库缺失、版本不符、完整性失败或 active Chunk 计数不符时，Grep 必须标记为 unavailable，但原查询和管理能力继续可用 |
| FR-15 | P0 | available 时注册 MCP Tool `grep_knowledge_hub`；disabled 或启动自检失败时不注册该 Tool |
| FR-16 | P0 | MCP 输出必须复用现有 wire 脱敏函数；Trace 不得保存 raw pattern、Chunk 正文或绝对路径 |
| FR-17 | P0 | Dashboard 必须增加 `POST /api/grep`，并由 `/api/overview` 的 `capabilities.grep` 明确告知前端可用性 |
| FR-18 | P0 | 现有 `/query` 页面必须增加“语义检索/精确查找”模式；共用输入框，分别保留 Top K、响应和错误状态；Grep 模式显示大小写 Toggle 和无分数结果 |

### 3.2 非功能需求

| ID | 类别 | 可验证约束 |
|---|---|---|
| NFR-01 | 容量 | 首期验收数据集为 10,000 个 active Chunk，正文覆盖 CJK、Latin 和其他 Unicode 样本 |
| NFR-02 | 时限 | 默认 `timeout_ms=1000`；受控基准中所有请求必须在 deadline 后 250 ms 内返回或给出稳定失败，不得无限扫描 |
| NFR-03 | 原子性 | 单 generation 的 Grep 写入必须是一个事务；校验或 SQL 失败后该批次零写入 |
| NFR-04 | 依赖 | 不新增第三方运行时依赖，使用 Python 标准库 `sqlite3`、`unicodedata`、`hashlib`、`json` 和现有框架 |
| NFR-05 | 隐私 | wire 不暴露绝对路径和任意 metadata；Trace 只记录 pattern 长度、collection、大小写模式、计数、耗时和状态 |
| NFR-06 | 故障隔离 | Grep 故障只能禁用 Grep；不得阻止现有 MCP Server、Dashboard、BM25/向量查询启动或执行 |
| NFR-07 | 版本一致性 | 查询以一次 active-generation 快照为可见性边界；一次响应不得混入同一文档的两个版本 |
| NFR-08 | 正确性 | FTS 候选与 Python 全扫描的差分测试必须覆盖多语言、组合字符、引号、空格、FTS 特殊字符和大小写 |
| NFR-09 | 可维护性 | 只注入具体 `SQLiteGrepIndex | None`，不为单个 SQLite 实现新增 Protocol、通用分页层或状态机 |
| NFR-10 | 兼容性 | 关闭 Grep 时，现有配置文件仍可加载，现有 MCP Tool 列表、`/api/query` 请求响应和语义检索页面行为保持不变 |

---

## 4. 配置与启用规则

### 4.1 配置

```yaml
grep:
  enabled: false
  db_path: ./data/db/chunk_text.db
  timeout_ms: 1000
```

校验规则：

| 字段 | 规则 |
|---|---|
| `grep` | 可选 mapping；旧配置缺失时等同于 `{enabled: false}` |
| `enabled` | bool，默认 `false`；不得接受 `0/1` 或字符串 |
| `db_path` | 开启时必须为非空字符串 |
| `timeout_ms` | 开启时必须为非 bool 正整数，默认 1000 |

### 4.2 三种状态

| 状态 | 查询 | 摄取 | 删除 | Dashboard |
|---|---|---|---|---|
| disabled | 不注册 Tool | 完全跳过 Grep | 完全跳过 Grep | 不显示精确查找 |
| enabled + available | 可用 | 必须写入后才能发布 | 必须同步删除 | 显示精确查找 |
| enabled + unavailable | Grep 返回稳定不可用状态；其他 Tool 正常 | 新摄取失败，防止发布缺 Grep 数据的新版本 | 删除失败并保留 integrity record | 语义检索和其他页面正常，隐藏精确查找 |

开启步骤固定为：先保持 `enabled=false` 完成离线重建和校验，再改为 `true` 并重启。回滚只需把
`enabled` 改回 `false`，不需要删除数据库，也不影响原检索链路。

---

## 5. 数据模型

### 5.1 数据库位置与版本

- 默认文件：`data/db/chunk_text.db`
- SQLite application schema：`PRAGMA user_version = 1`
- 服务使用 WAL；离线重建文件使用普通 journal，完成后原子替换。
- 最终数据库不保存 `building/ready` 状态；`.building` 文件尚未替换就天然不可见。
- `user_version` 不匹配时不做在线迁移，Grep 直接 unavailable，要求重新执行离线脚本。

### 5.2 DDL

```sql
CREATE TABLE chunks (
    rowid         INTEGER PRIMARY KEY,
    chunk_id      TEXT UNIQUE NOT NULL,
    collection    TEXT NOT NULL,
    doc_key       TEXT NOT NULL,
    generation    INTEGER NOT NULL,
    source_path   TEXT NOT NULL,
    page_order    INTEGER NOT NULL,
    chunk_index   INTEGER NOT NULL,
    text          TEXT NOT NULL,
    search_text   TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    content_hash  TEXT NOT NULL
);

CREATE INDEX idx_chunks_doc_generation
    ON chunks(doc_key, generation);

CREATE INDEX idx_chunks_stable_order
    ON chunks(collection, source_path, page_order, chunk_index, chunk_id);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
    text,
    search_text,
    content='chunks',
    content_rowid='rowid',
    tokenize='trigram'
);

CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text, search_text)
    VALUES (new.rowid, new.text, new.search_text);
END;

CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, search_text)
    VALUES ('delete', old.rowid, old.text, old.search_text);
END;

CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, search_text)
    VALUES ('delete', old.rowid, old.text, old.search_text);
    INSERT INTO chunks_fts(rowid, text, search_text)
    VALUES (new.rowid, new.text, new.search_text);
END;

PRAGMA user_version = 1;
```

### 5.3 字段来源

| 字段 | 来源与规则 |
|---|---|
| `chunk_id` | `ChunkRecord.id`，必须唯一 |
| `collection` | `record.metadata["collection"]` |
| `doc_key` | `record.metadata["doc_key"]` |
| `generation` | `record.metadata["generation"]`，非 bool 正整数 |
| `source_path` | `record.metadata["source_path"]`，只用于内部过滤和稳定排序 |
| `page_order` | page 为正整数时取 page，否则取 `-1` |
| `chunk_index` | `record.metadata["chunk_index"]`，非 bool 非负整数 |
| `text` | 最终 `ChunkRecord.text`，不 trim、不重新切分 |
| `search_text` | `unicodedata.normalize("NFKC", text).casefold()` |
| `metadata_json` | record metadata 的确定性 JSON；写入前必须验证可序列化 |
| `content_hash` | `sha256(text.encode("utf-8")).hexdigest()`，必须等于 record 值 |

### 5.4 批次写入约束

`SQLiteGrepIndex.upsert(records)` 接收 Pipeline 已生成的完整 generation 批次：

1. SQL 事务开始前验证所有 record；任一条非法时整批拒绝。
2. 同一批 record 必须具有相同的 `collection`、`doc_key`、`generation` 和 `source_path`。
3. 不允许重复 `chunk_id` 或 `chunk_index`。
4. 在一个写事务中删除该 generation 不在本批 ID 集合中的旧行，再执行幂等 upsert。
5. 内容表和 FTS trigger 任一步失败都回滚，不能留下半个 generation。
6. 空批次为无写入的成功结果；FileIntegrity 的 `chunk_count=0` 仍是权威记录。

不新增公共索引接口。Pipeline、DocumentManager 和 GrepService 直接接收
`SQLiteGrepIndex | None`，因为本期只有一个实现。

---

## 6. 查询语义

### 6.1 输入

MCP Tool：

```text
grep_knowledge_hub(
  pattern: string,
  collection: string = "default",
  top_k: integer = 20,
  case_sensitive: boolean = false
)
```

JSON Schema 关键约束：

```json
{
  "type": "object",
  "properties": {
    "pattern": {"type": "string", "minLength": 3, "maxLength": 4000},
    "collection": {"type": "string", "minLength": 1, "maxLength": 128, "default": "default"},
    "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 20},
    "case_sensitive": {"type": "boolean", "default": false}
  },
  "required": ["pattern"],
  "additionalProperties": false
}
```

运行时再次执行同样校验，并补充：

- 不 trim `pattern`，所以三个空格是合法字面量。
- 使用 Python Unicode code point 数量判断长度；不按 UTF-8 bytes 计数。
- 不区分大小写时，NFKC + casefold 后的有效检索串也必须至少 3 个 code point，否则返回参数错误。
- 拒绝 NUL；collection 复用现有 `validate_identifier` 语义。
- bool 不能作为 integer `top_k`。

### 6.2 候选召回

一次查询执行以下步骤：

1. 使用 `time.monotonic()` 计算唯一 deadline。
2. 从 FileIntegrity 一次读取指定 collection 的 `doc_key -> active generation` 快照。
3. 快照为空时立即返回空结果。
4. 在本次 SQLite 连接的临时表写入该快照。
5. 根据 `case_sensitive` 固定选择 FTS 的 `text` 或 `search_text` 列。
6. 把有效检索串构造成 FTS phrase：外层加双引号，内部 `"` 双写；整个表达式作为 SQL 参数绑定。
7. 用一个 SQL cursor 联结 FTS、`chunks` 和临时 active 表，并按稳定键升序流式读取。

pattern 永远不拼进 SQL，只有固定列名由大小写模式选择。FTS 的作用只是快速缩小候选集，允许
假阳性，不允许直接把 FTS 命中当成最终结果。

### 6.3 最终校验和计数

每个候选用 Python 做最终校验：

```python
needle = pattern if case_sensitive else normalize(pattern)
haystack = text if case_sensitive else normalize(text)
match_count = haystack.count(needle)
```

其中 `normalize(value)` 固定为 `unicodedata.normalize("NFKC", value).casefold()`。
`str.count()` 定义了非重叠命中次数。`match_count == 0` 的 FTS 假阳性必须丢弃。

一个 Chunk 最多返回一次；不检查相邻 Chunk，也不把正文拼接后搜索。

### 6.4 Top K、截断和排序

- 公共接口没有分页。
- 流式收集到 `top_k + 1` 个真实命中即停止；默认配置下即第 21 个。
- 返回前 `top_k` 个；存在第 `top_k + 1` 个时 `truncated=true`。
- 没有额外相关性排序，返回顺序就是内部稳定顺序：

```text
source_path ASC, page_order ASC, chunk_index ASC, chunk_id ASC
```

### 6.5 超时

- SQLite 连接安装 `set_progress_handler`，回调通过 monotonic deadline 中断长 SQL。
- 每处理一个候选也检查 deadline，覆盖 Python normalization 和计数时间。
- 中断后保留已经完成 Python 校验的结果，不返回尚未校验的候选。
- 超时响应固定为 `timed_out=true, truncated=true`，HTTP/MCP 调用本身仍成功。
- 非超时 SQLite 损坏或 I/O 错误返回稳定 `grep_unavailable`/`grep_failed`，原始异常只进私有日志。

本期不做 active map 二次读取和整轮重试。一次查询以开始时取得的 active generation 快照为
一致性边界，这与现有本地检索的查询时快照语义一致。

---

## 7. 响应与隐私契约

### 7.1 结构化响应

```json
{
  "matches": [
    {
      "chunk_id": "...",
      "text": "完整 Chunk 正文",
      "source": "document.pdf",
      "page": 1,
      "metadata": {},
      "match_count": 2
    }
  ],
  "truncated": false,
  "timed_out": false,
  "trace_id": "..."
}
```

字段规则：

| 字段 | 规则 |
|---|---|
| `chunk_id` | 最终 Chunk ID，经 wire string 清理 |
| `text` | 原始完整 Chunk，经 `sanitize_wire_string`；不返回 normalized 副本 |
| `source` | 使用 `public_source_label(source_path)`，不得返回绝对路径 |
| `page` | 正整数或 `null` |
| `metadata` | 使用 `public_citation_metadata` 白名单 |
| `match_count` | 正整数，按第 6.3 节语义计算 |
| `truncated` | 存在更多真实命中或查询超时时为 true |
| `timed_out` | deadline 中断查询时为 true |
| `trace_id` | 本次 query trace ID |

不返回 `score`、`score_kind`、`score_stages`、`images`、命中 offsets、内部 `doc_key`、
`generation` 或原始 `source_path`。

### 7.2 Trace

使用现有 Query Trace 存储和 collector，但 `retrieval_mode="grep"`，metadata 只允许：

```text
pattern_length
collection
top_k
case_sensitive
result_count
truncated
timed_out
elapsed_ms
status
error_code
```

`pattern_length` 是 code point 数量。禁止保存 raw pattern、normalized pattern、完整 Chunk、任意
metadata、绝对路径和原始异常。collector 写失败沿用现有查询策略：记录 warning，不覆盖成功结果。

---

## 8. 摄取、发布与旧版本清理

### 8.1 写入位置

Transforms 仍先执行。`VectorUpserter` 继续负责生成最终 ID、hash 和 `ChunkRecord`；Grep 不从
初始 `Chunk` 重新推导这些值。

开启 Grep 后，存储顺序固定为：

```text
VectorUpserter
  -> BM25Indexer
  -> SQLiteGrepIndex.upsert(final_records)
  -> ImageStorage
  -> FileIntegrity.mark_staged
  -> FileIntegrity.publish
```

关闭 Grep 时，该位置只做一次 `None` 判断，不构造连接也不写文件。

### 8.2 失败规则

1. Grep 写失败时退出本次摄取，不调用 `mark_staged()` 或 `publish()`。
2. Pipeline 沿用现有失败路径调用 `mark_failed()`；旧 active generation 不变。
3. `mark_failed()` 成功后，单独 best-effort 调用 `remove_generation(doc_key, generation)`。
4. 清理失败只记录日志；该残留仍不可见，并在同一文档下次成功发布后再次清理。
5. 已开启但 unavailable 时，Grep 写入必须失败，不能静默跳过后发布不完整版本。

### 8.3 发布后清理

新 generation 发布成功后，沿用 `FileIntegrity.list_garbage_generations(doc_key)` 获取可删除旧版本。
Grep 清理使用独立 `try/except`，不能放进当前 vector/BM25/image 共用的异常块，避免其他 store
先失败后跳过 Grep 清理。

本期不新增 cron、独立后台清理进程或保留代数配置。清理时机只有：

1. 本次失败后；
2. 本次成功发布后。

这足以覆盖正常失败、正常升级和绝大多数迟到 worker；进程被强杀留下的不可见行会在该文档
下次成功发布时删除。

---

## 9. 文档删除

`DocumentManager` 增加可选 `SQLiteGrepIndex` 依赖，删除顺序保持现有外部 store 尽量全部尝试的
策略：vector、BM25、Grep、images，最后才删除 FileIntegrity 控制记录。

规则：

1. disabled 时 Grep 不参与删除，保持当前行为。
2. enabled 时调用 `remove_document(normalized_source_path, collection)`，删除该逻辑文档所有 generation。
3. Grep 删除异常追加到现有 `errors`。
4. 任一外部 store 删除失败时，不调用 `file_integrity.remove_record()`。
5. 重试同一删除操作必须幂等。

不另建“删除任务表”。保留下来的 FileIntegrity 记录就是现有重试锚点。

---

## 10. 就绪检查与故障隔离

### 10.1 启动检查

配置开启后，查询装配执行 `SQLiteGrepIndex.check_ready(...)`：

1. DB 文件存在且可只读打开。
2. `PRAGMA user_version = 1`。
3. `PRAGMA integrity_check` 返回 `ok`。
4. FTS5 integrity check 通过。
5. 从 FileIntegrity 获取 active generation 及对应 published `chunk_count`。
6. 按 `(doc_key, generation)` 比较 Grep active 行数；每个 active 文档都必须与 FileIntegrity 数量一致。

旧 generation 残留不影响 ready，因为它们不在 active map 中；查询时也不会被联结出来。

### 10.2 隔离行为

检查失败时：

- 记录不含路径和正文的稳定错误码与私有异常日志。
- 不注册 Grep MCP Tool。
- `/api/overview` 返回 `capabilities.grep=false`。
- `/api/grep` 返回 HTTP 503 `grep_unavailable`。
- 原 MCP Tool、`/api/query`、文档浏览、Trace 和配置页面继续工作。
- 由于配置仍为 enabled，新摄取和文档删除不得绕过 Grep 完整性要求。

SQLiteGrepIndex 构造函数只保存配置，不创建数据库。查询装配失败不能让整个 MCP Server 或
Dashboard AppContext 构造失败。

---

## 11. MCP 接入

### 11.1 Tool 注册

在现有 `ProtocolHandler` 装配中条件注册 `GrepKnowledgeHubTool`：

```text
disabled              -> 不构造 index，不注册
enabled + ready       -> 注册 grep_knowledge_hub
enabled + not ready   -> 不注册，记录 grep_unavailable
```

Stdio 和 Streamable HTTP 必须读取同一份 Settings 并执行同一 ready 检查。ready 检查只访问本地
SQLite/FileIntegrity，不初始化 Embedding 或 LLM。

### 11.2 Tool 执行

`GrepKnowledgeHubTool` 负责：

1. JSON Schema 和 Python 双重输入校验；
2. 调用 `GrepService.search()`；
3. wire 脱敏；
4. 创建不含 pattern 的 Query Trace；
5. 把内部异常映射为稳定 Tool 错误。

`GrepService` 负责 active 快照、deadline、FTS 候选、Python 最终校验、Top K 和响应状态。
它不挂到 `KnowledgeService` 或 `HybridQueryEngine`，避免改变原查询服务接口。

---

## 12. Dashboard API 与 Web 页面

### 12.1 API

`AppContext` 增加独立、可空的 `grep_service`。构建失败时只把它设为 unavailable，不通过
`_safe_build` 让整个 Dashboard 启动失败。

新增：

```http
POST /api/grep
Content-Type: application/json

{
  "pattern": "error code",
  "collection": "default",
  "top_k": 20,
  "case_sensitive": false
}
```

响应使用第 7.1 节结构。参数错误为 400，unavailable 为 503，意外执行错误为稳定 500；正常
超时返回 200 和部分结果。

现有 overview 响应增加一个向后兼容字段：

```json
{
  "capabilities": {
    "grep": true
  }
}
```

前端必须读取 capability，不得通过先发一个失败请求来探测。

### 12.2 `/query` 页面

不新增页面和侧边栏入口。现有页面在标题下、搜索表单上方增加紧凑分段控件：

```text
[语义检索] [精确查找]
```

页面规则：

1. `capabilities.grep` 不为 true 时不显示分段控件，页面与现在相同。
2. 默认模式始终是“语义检索”。
3. 两种模式共用一个输入框和 Collection 选择器。
4. 语义模式继续 trim 输入、调用 `/api/query`，默认 Top K 保持 5。
5. Grep 模式不 trim 输入、调用 `/api/grep`，默认 Top K 为 20、最大 20。
6. 两种模式分别记住自己的 Top K、response、loading 和 error，切换时不把结果类型互相转换。
7. Grep 模式显示“区分大小写” Toggle；语义模式不显示。
8. 前端 Grep 长度判断使用 Unicode code point 数量，行为与后端一致。
9. 提交期间复用当前 loading 和错误展示，不并发重复提交。

### 12.3 Grep 结果卡

沿用现有卡片视觉和引用 metadata 展示，只改变内容字段：

- 显示公开来源、页码、完整正文、Chunk ID、metadata、`字面量命中 N 次`。
- 不显示 rank 圆标中的相关性含义、score badge、阶段分数和关联图片。
- `truncated=true` 时显示“结果已截断”的非阻塞状态。
- `timed_out=true` 时显示“检索超时，当前为部分结果”的非阻塞状态。
- 无结果时沿用现有 EmptyState，但文案为“未找到精确匹配”。

前端新增独立 `GrepMatch`、`GrepResponse` 类型和 `api.grepKnowledge()`，不得把无 score 的 Grep
命中伪装成 `QueryResult`。

---

## 13. 现有数据迁移

### 13.1 迁移边界

迁移只执行一次，要求停止 MCP、Dashboard 摄取和其他写入。迁移完成后，运行时 Grep 不再读取
BM25。后续数据由 Pipeline 双写进入独立 Grep DB。

### 13.2 BM25 只读导出

为具体 `BM25Indexer` 增加：

```text
list_active_chunk_records(collection: str) -> list[ChunkRecord]
```

该方法：

1. 在现有 BM25 文件锁下取得稳定 snapshot；
2. 用 FileIntegrity active map 过滤未发布 generation；
3. 从已保存的最终 text 和 metadata 重建 `ChunkRecord`；
4. 计算 `sha256(text)`，并验证 chunk ID 与最终 ID 规则一致；
5. 只供离线重建脚本和诊断测试调用，不进入 GrepService。

不为这个一次性读取需求新增 BM25 Protocol 方法。

### 13.3 重建脚本

新增：

```bash
python scripts/rebuild_grep_index.py --settings config/settings.yaml
```

流程：

1. 读取 Settings、FileIntegrity 和 BM25 snapshot。
2. 删除上次遗留的 `chunk_text.db.building`，不碰当前正式 DB。
3. 在 `.building` 中创建 version 1 schema 和 FTS。
4. 按 collection/doc_key/generation 写入 active `ChunkRecord`。
5. 校验每个 active generation 的行数与 FileIntegrity `chunk_count`。
6. 执行 SQLite 与 FTS integrity check。
7. 使用固定随机种子，从真实正文抽取至少 3 code point 的 literal，对索引结果和 Python 全扫描做差分校验；空库时跳过该项。
8. flush 并 fsync 数据库文件。
9. 用 `os.replace()` 原子替换正式 DB，再 fsync 父目录。
10. 输出 collection、document、Chunk 数量和校验结果，不输出正文或绝对路径。

任一步失败都保留旧正式 DB，删除或保留 `.building` 供诊断均可，但不得部分替换。脚本成功后
管理员才把 `grep.enabled` 改为 true。

---

## 14. 文件变更清单

### 14.1 新增

| 文件 | 职责 |
|---|---|
| `src/ingestion/storage/sqlite_grep_index.py` | schema、事务写入、FTS cursor、删除、自检 |
| `src/core/services/grep_service.py` | active 快照、deadline、Python 最终校验、Top K |
| `src/mcp_server/tools/grep_knowledge_hub.py` | Tool schema、wire、Trace、稳定错误 |
| `scripts/rebuild_grep_index.py` | 唯一的离线迁移/重建入口 |
| `tests/unit/test_sqlite_grep_index.py` | 索引写入、Unicode、字面量、事务、删除 |
| `tests/unit/test_grep_service.py` | 可见性、排序、Top K、超时、计数 |
| `tests/unit/test_grep_knowledge_hub.py` | MCP contract、脱敏、Trace |

### 14.2 修改

| 文件 | 最小改动 |
|---|---|
| `src/core/settings.py` | 增加默认关闭的可选 `grep` section |
| `config/settings.yaml` | 写入默认关闭配置示例 |
| `src/ingestion/storage/__init__.py` | 导出具体 `SQLiteGrepIndex` |
| `src/ingestion/storage/bm25_indexer.py` | 增加离线 active Chunk 导出 |
| `src/ingestion/factory.py` | enabled 时向 Pipeline 注入具体 index |
| `src/ingestion/pipeline.py` | 在 publish 前写 Grep；失败/发布后独立清理 |
| `src/ingestion/document_manager.py` | enabled 时把 Grep 纳入删除完整性 |
| `src/observability/dashboard/services/data_service.py` | 向 DocumentManager 注入 Grep |
| `src/core/services/__init__.py` | 导出 GrepService |
| `src/mcp_server/server.py` | 按 enabled + ready 条件注册 Tool |
| `src/observability/dashboard/api.py` | AppContext、capability、`POST /api/grep`、payload/response model |
| `web/src/lib/types.ts` | `capabilities`、`GrepMatch`、`GrepResponse` |
| `web/src/lib/api.ts` | `api.grepKnowledge()` |
| `web/src/pages/QueryPage.tsx` | 两种检索模式和 Grep 结果 |
| `web/src/styles.css` | 分段控件、Toggle、部分结果状态和响应式样式 |
| 对应现有测试文件 | 配置兼容、Pipeline、DocumentManager、MCP list、Dashboard API 回归 |

### 14.3 明确不改

- `HybridQueryEngine`、DenseRetriever、SparseRetriever、Fusion、reranker。
- `query_knowledge_hub` 的输入、输出和默认 Top K。
- BM25 的分词、倒排、评分和在线 query 路径。
- Chroma schema、Embedding/LLM Provider 和图片索引。
- FileIntegrity 的 generation 发布模型和数据库 schema。

---

## 15. 实施顺序

### Phase 1：独立索引内核

1. 增加默认关闭配置及兼容测试。
2. 实现 `SQLiteGrepIndex` schema、批次写入、search cursor、删除和 ready 检查。
3. 实现多语言字面量差分、事务回滚和超时测试。

完成条件：不接 MCP/UI，也能对最终 `ChunkRecord` 正确写、查、删；关闭配置零副作用。

### Phase 2：离线迁移

1. 增加 BM25 active Chunk 只读导出。
2. 实现 `.building` 重建、计数/完整性/差分校验和原子替换。
3. 在真实现有数据副本上演练一次 disabled -> rebuild -> enabled。

完成条件：重启后 ready 检查通过，运行时断开 BM25 仍可执行 Grep 查询。

### Phase 3：摄取与删除生命周期

1. Pipeline 注入 Grep 并在发布前写入。
2. 接入失败清理和发布后旧版本清理。
3. DocumentManager 接入删除完整性。

完成条件：写失败不发布、旧版本继续可用、删除失败保留 integrity record。

### Phase 4：MCP 与 Trace

1. 实现 GrepService 和 Tool。
2. 条件注册 Stdio/HTTP Tool。
3. 接入 wire 安全和无 pattern Trace。

完成条件：enabled/disabled/unavailable Tool list 与调用行为满足契约。

### Phase 5：Dashboard

1. 增加 capability 和 `/api/grep`。
2. 修改 QueryPage 模式、Toggle、独立结果状态和卡片。
3. 完成 API 测试、Web Node 纯函数测试、typecheck、lint 和 build。

完成条件：Grep 不可用时页面完全退回当前语义检索；可用时能完成精确查找全流程。

### Phase 6：容量验收与文档合并

1. 在 10,000 active Chunk 数据集执行 correctness/performance benchmark。
2. 记录 DB/WAL 大小、查询 P50/P95、超时数和写入耗时。
3. 把最终配置和运维步骤同步到 `DEV_SPEC.md`/README。

---

## 16. 验收场景

### AC-01：默认关闭不影响原体系

给定旧配置没有 `grep` section，启动 Stdio、HTTP MCP 和 Dashboard：

- 不创建 `chunk_text.db`；
- Tool list 与当前版本一致；
- overview capability 为 false；
- QueryPage 不显示“精确查找”；
- 原摄取、删除和 `/api/query` 测试全部通过。

### AC-02：字面量和大小写

正文同时包含中文、英文、兼容字符、组合字符、引号、空格及 `. * [ ] AND OR`：

- 所有字符均按字面量处理；
- 默认模式按 NFKC + casefold 命中；
- case-sensitive 模式只命中原始大小写；
- Python 全扫描与 Grep 结果集合及 `match_count` 完全一致。

### AC-03：三字符边界

- 1、2 code point pattern 返回参数错误，不扫描数据库；
- 3 code point pattern 进入 FTS；
- 三个空格可搜索且不得被 trim；
- raw 长度达标但 normalization 后不足 3 的 pattern 返回参数错误；
- 4,001 code point 和含 NUL 的 pattern 被拒绝。

### AC-04：版本可见性

同一文档 V1 已发布、V2 已写 Grep 但尚未发布：查询只能看到 V1。发布 V2 后的新查询只能看到
V2；同一响应不混合两个 generation。

### AC-05：写失败

注入 Grep SQL 错误：整批零写入，本 generation 标为 failed，不切换 active generation，V1
查询继续成功；失败清理异常也不覆盖主要失败。

### AC-06：稳定 Top K 和超时

- 相同数据库重复查询顺序一致；
- 第 `top_k + 1` 个真实命中令 `truncated=true`；
- progress handler 中断后返回已验证结果，`timed_out=true`、`truncated=true`；
- 不出现公共 cursor 或第二页。

### AC-07：删除完整性

正常删除移除全部 Grep generations 后才删 integrity record。注入 Grep 删除错误时，其他 store
仍尽量清理，但 integrity record 保留，响应 errors 可见并可重试。

### AC-08：迁移独立性

从现有 BM25 完成离线重建，ready 计数一致。随后令 BM25 query/load 不可用，Grep 仍能返回正确
结果，证明运行时没有依赖 BM25。

### AC-09：Web 双模式

- capability false 时没有 Grep UI，也不调用 `/api/grep`；
- capability true 时可切换，共用输入，分别保持 Top K 和结果；
- Grep 提交保留首尾空格，Toggle 正确传参；
- 卡片没有 score/stages/images，正确显示命中次数；
- 超时和截断提示不会把部分结果伪装成完整结果。

---

## 17. 需求到测试矩阵

| 需求 | 自动化或验收证据 |
|---|---|
| FR-01、NFR-10 | Settings 缺省测试；disabled 文件零创建；MCP list、Pipeline、Dashboard/Web 回归 |
| FR-02、FR-03 | Pipeline final-record 集成测试；BM25 故障下 Grep 查询测试 |
| FR-04、FR-05、FR-06、NFR-08 | SQLiteGrepIndex 参数化 Unicode/特殊字符/大小写/空格差分测试 |
| FR-07、NFR-07 | publish 前后与并发快照 generation 可见性测试 |
| FR-08 | 稳定排序、1/20 Top K、`top_k + 1` 截断测试 |
| FR-09、NFR-02 | progress handler 强制中断与 1,000 ms deadline 受控返回测试 |
| FR-10、NFR-03 | Pipeline SQL/trigger 故障注入、整批回滚、旧 active 保留测试 |
| FR-11 | failed best-effort、publish 后旧 generation、其他 store 清理失败隔离测试 |
| FR-12 | DocumentManager 正常/失败/重复删除测试 |
| FR-13 | 重建脚本 active-only、hash/ID、原子替换、BM25 断开测试 |
| FR-14、NFR-06 | DB 缺失、version、integrity、count mismatch 的 capability 与原查询回归 |
| FR-15 | Stdio 与 HTTP 的 enabled/disabled/unavailable Tool list contract tests |
| FR-16、NFR-05 | wire 白名单、绝对路径、metadata、raw pattern、异常文本泄露测试 |
| FR-17 | Dashboard overview capability、grep 200/400/503/500 API 测试 |
| FR-18 | Web 纯函数 Node tests、`npm run typecheck`、`npm run lint`、`npm run build` 和浏览器 smoke |
| NFR-01 | 10,000 active Chunk 多语言 correctness/performance benchmark 报告 |
| NFR-04 | `pyproject.toml`/lockfile 依赖 diff 审查，确认无新增运行时包 |
| NFR-09 | 架构审查：无 Grep Protocol、公共分页类型、后台清理进程或单实现工厂 |

发布门禁：

```bash
pytest -q
ruff check .
mypy src
npm --prefix web test
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run build
```

另外必须保存一次第 10,000 Chunk 基准结果；性能不达标时保持 `grep.enabled=false`，不以放宽
deadline、降低正确性或改回扫描作为发布手段。

---

## 18. 完成定义

只有同时满足以下条件，本增量才算完成：

1. FR-01 至 FR-18、NFR-01 至 NFR-10 均有通过的测试或验收证据。
2. 现有全量测试在 disabled 配置下无回归。
3. 离线迁移在现有数据副本上成功，active count、SQLite/FTS integrity 和差分校验全部通过。
4. 10,000 active Chunk 基准满足 1 秒 deadline 与 250 ms 受控退出余量。
5. Web 的 disabled、available、unavailable 三种状态均完成 smoke 验收。
6. 运行时 Grep 在 BM25 不可用的测试中仍能独立查询。
7. 文档已说明启用、回滚、重建和故障隔离方式。

本期到此为止。出现超过 10,000 active Chunk 后的真实性能证据，或 SQLite 单机文件成为明确
瓶颈时，再单独评估分片或外部搜索服务；现在不预建该复杂度。
