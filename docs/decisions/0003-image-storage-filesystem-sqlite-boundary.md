# ADR-0003：ImageStorage 的文件系统与 SQLite 一致性边界

- **状态**：当前采用（ADR-0005 已增加分代引用）
- **日期**：2026-07-30
- **决策范围**：PDF 图片文件、`ImageRef`、SQLite 图片索引以及文档级图片生命周期

## 背景

PDF 中的图片既包含体积较大的二进制内容，也包含需要查询的结构化关系：图片属于
哪个 collection、来自哪个 PDF、位于第几页，以及正文中的占位符和 PDF 坐标。

只保存 PNG 文件无法高效回答这些关系查询；把图片二进制直接塞进 SQLite 又会重复
文件系统已经擅长的职责。当前实现因此把二进制文件和关系索引分开保存。

## 职责边界

`PdfLoader` 与 `ImageStorage` 的职责是串联关系，不是重复关系：

```text
PdfLoader
├── 从 PDF 解码图片
├── 写入 data/images/{collection}/{image_id}.png
└── 生成 ImageRef
        │
        ▼
ImageStorage.save_refs(ImageRef[], doc_key, generation)
├── 校验图片已经存在且路径受管
├── 把 ImageRef 写入 SQLite
├── 提供图片和文档关系查询
└── 协调删除 SQLite 映射与 PNG 文件
```

`ImageStorage` 不负责：

- 解析 PDF 或解码图片；
- 调用多模态模型生成 caption；
- 再次复制 `PdfLoader` 已经写出的 PNG；
- 保证 Chroma、BM25、图片索引和文件完整性记录之间的联合事务。

## 物理存储

当前使用两个本地存储位置：

```text
data/
├── images/
│   └── {collection}/
│       └── {image_id}.png       # 图片二进制
└── db/
    └── image_index.db           # 图片关系和定位元数据
```

SQLite 的 `image_versions` 表保存：

| 字段 | 含义 |
|------|------|
| `storage_id` | `doc_key + generation + image_id` 的哈希主键 |
| `image_id` | Loader 生成的业务图片身份 |
| `doc_key` / `generation` | 逻辑文档身份与不可变分代 |
| `file_path` | PNG 的规范化绝对路径 |
| `collection` | 图片所属知识集合 |
| `source_path` | 来源 PDF 路径 |
| `doc_hash` | 能从规范 image ID 提取时保存的文件 SHA256 |
| `page_num` | 从 1 开始的 PDF 页码 |
| `mime_type` | 图片 MIME 类型 |
| `text_offset` / `text_length` | 图片占位符在规范正文中的位置 |
| `position_json` | 图片在 PDF 页面中的坐标 |
| `created_at` | 首次创建索引行的时间 |

`doc_hash` 只在 `image_id` 以 64 位 SHA256 开头时提取；它是辅助索引，不替代
`source_path + collection` 的文档生命周期边界。

## 保存流程

`save_refs()` 接收已经落盘的 `ImageRef`，按以下顺序处理：

```text
ImageRef[]
    │
    ▼
检查批次内 image_id 不重复
    │
    ▼
校验 collection / source_path / page / offset / mime_type
    │
    ▼
校验图片文件真实存在
    │
    ▼
resolve 路径并确认它位于 image_root/{collection}/ 内
    │
    ▼
把 position 序列化为 JSON
    │
    ▼
整批通过后开启 SQLite 短事务
    │
    ▼
INSERT ... ON CONFLICT(storage_id) DO UPDATE
```

先校验完整批次，再开始数据库写入，保证某一张图片不合法时不会把同批次前面的合法
映射提前写入。同一 generation 的相同 `image_id` 再次保存会更新自己的行；不同
generation 即使共用同一个业务 image ID，也不会互相覆盖。

路径包含检查是安全边界。即使调用方提供了存在的文件，只有文件解析后的真实路径位于
对应 collection 目录内才允许入库；这也阻止指向目录外部的符号链接被登记后删除。

## 查询流程

```text
get(image_id)
    -> 读取候选行并匹配 active_generation
    -> 解码 position_json
    -> 完整 ImageRef 或 None

list_by_document(source_path, collection)
    -> 同一文档 active generation 的 ImageRef[]
    -> 按页码和 image_id 稳定排序

list_by_collection(collection)
    -> collection 中所有 active generation 的 ImageRef[]
    -> 按页码和 image_id 稳定排序
```

SQLite 行返回领域层前会重新构造 `ImageRef`，避免让 `sqlite3.Row` 或数据库字段名
泄漏到 Pipeline、Dashboard 或 MCP 响应组装层。

## 删除策略

分代 GC 使用 `doc_key + generation` 作为精确范围：

```text
delete_generation(doc_key, generation)
    -> 删除该代 SQLite 引用
    -> 仅在没有任何其他引用时删除共享 PNG
```

显式删除整个文档时，`delete_by_document()` 仍使用 `source_path + collection` 删除所有
generations：

```text
delete_by_document(source_path, collection)
    │
    ▼
查询该文档的全部 file_path
    │
    ▼
在 SQLite 事务中删除映射
    │
    ▼
事务提交后删除 PNG 文件
    │
    ▼
返回删除的映射数量
```

当前选择“先提交索引删除，再清理无引用文件”。原因是文件清理失败时，留下不再被系统引用
的孤立文件，比留下仍能查询但指向缺失图片的有效索引行更容易恢复，也更少影响在线
查询。这个顺序只是明确的失败偏好，不是跨存储原子性。

## 并发模型

- 每个操作创建并关闭自己的 SQLite 连接，不在线程间共享 Connection。
- 数据库启用 WAL，读操作可以和单个写操作并行。
- `busy_timeout` 让短暂写锁竞争等待，而不是立即失败。
- 单次批量 upsert 和单次文档索引删除各自在一个 SQLite 事务内完成。
- 同一 `image_root` 的保存和物理删除使用进程内路径锁与跨进程 `flock` 串行化，避免
  文件校验通过后、引用 INSERT 前被并发 GC 删除。
- PDF 解析、模型调用和文件生命周期不持有 SQLite 长事务。

WAL 只保护 SQLite 文件，不保护 PNG 文件，也不提供同一文档的业务互斥。

## 当前决策

1. PNG 保存在本地文件系统，SQLite 只保存可查询的图片引用和定位元数据。
2. 复用 `ImageStore` 端口，不向 Pipeline 暴露 SQLite 表或文件操作细节。
3. `PdfLoader` 负责创建图片文件，`ImageStorage` 只索引已经存在的受管文件。
4. 使用标准库 SQLite、WAL、短连接和 upsert，不为当前本地规模引入对象存储或外部数据库。
5. 删除采用“SQLite 映射优先、文件清理随后”的失败偏好。
6. Pipeline 要求 `PdfLoader.image_root` 与 `ImageStorage.image_root` 指向同一受管根目录；
   C15 正式装配入口负责从同一配置创建二者。
7. 图片二进制继续使用 Loader 的内容寻址文件，不为每个 generation 重复复制；
   `image_versions` 引用负责分代隔离，查询只返回 active generation。

## 已知顾虑与残余风险

| 顾虑 | 可能后果 | 当前控制 | 后续处理 |
|------|----------|----------|----------|
| Loader 写出 PNG 后、SQLite 入库前进程崩溃 | 产生没有索引行的孤立图片 | Pipeline 标记失败；图片 ID 和路径确定，重新摄取可覆盖同一文件 | 有实际积累后增加“文件对 SQLite”清理工具 |
| SQLite 删除成功后 PNG 删除失败 | 产生不再被引用的孤立图片 | 删除顺序不会留下有效索引指向缺失文件 | 向调用方传播文件错误；需要时增加孤立文件巡检和重试 |
| PNG 被人工删除但索引行仍存在 | `get()` 仍返回 ImageRef，真正读取图片时失败 | 保存阶段检查文件存在；文档删除和重新摄取可恢复 | DocumentManager 增加索引健康检查前，不声称读取时自动修复 |
| 文件系统与 SQLite 没有联合事务 | 断电或异常可能留下两边不一致 | SQLite 自身事务完整；失败结果偏向可重新摄取或清理的状态 | 不把 WAL 描述为跨存储事务；更强一致性需求出现时重新设计 |
| 同一 generation 的 image ID 被更新到新路径 | 当前引用改指向新路径，旧 PNG 可能成为孤立文件 | 正常 Loader 使用确定性 ID 和路径，不会主动移动 | 允许图片迁移时增加旧路径清理或禁止 ID 改绑 |
| 数据库保存绝对路径 | 项目目录整体移动后，已有映射可能失效 | 当前定位是单机固定工作区 | 需要可搬迁索引时改存相对 `image_root` 的路径并提供迁移 |
| `source_path` 使用调用方传入字符串 | 同一 PDF 的相对、绝对或符号链接路径可能被视为不同文档 | Pipeline 在创建 `doc_key` 和图片引用前统一为规范绝对路径 | 迁移旧 C13 数据时仍需路径映射策略 |
| 多线程 save/delete 同一文档 | WAL 保证数据库不写坏，但业务结果取决于操作顺序 | generation 隔离摄取写入，GC 只按 `doc_key + generation` 精确删除 | 显式整文档删除和摄取仍需 DocumentManager 协调 |
| SQLite 表缺少显式 schema version | 将来字段变化时难以判断是否需要迁移 | 新旧表并存，不把旧 `image_index` 行误当成已分代数据 | 正式迁移旧索引前增加 `schema_version` 和迁移工具 |
| `list_by_collection()` 一次返回全部记录 | 大 collection 可能增加内存和 UI 延迟 | 当前本地小规模语料 | 实测出现压力后再增加分页，不提前扩展接口 |
| 同步 GC 后旧 worker 又写入 fenced generation | 留下查询不可见的图片引用和文件 | active_generation 过滤保证不会返回，且不会覆盖新代 | 增加周期性 generation GC |
| 磁盘没有配额和周期性垃圾回收 | 大量图片或孤立文件可能耗尽空间 | 发布后执行一次尽力清理，图片按 collection 集中存放 | Dashboard 统计或运维需求出现时增加容量监测与周期性清理 |

这些风险意味着当前实现适合本地、单机、小规模图片资产管理，但不能据此声称拥有
跨 SQLite、文件系统、Chroma 和 BM25 的事务一致性。

## 恢复策略

- SQLite 丢失或损坏：保留源 PDF，重新摄取以重建图片文件和索引映射。
- PNG 缺失但索引存在：按 `source_path + collection` 删除旧映射并重新摄取文档。
- PNG 存在但索引缺失：重新摄取会写回确定性路径和 ID；清理工具落地前可人工核对。
- 文档删除部分失败：若 SQLite 尚未提交，可以重试；若映射已删除但文件清理失败，
  再次调用 `delete_by_document()` 已无法找到孤立文件，需要人工清理或未来的 reconcile 工具。
- 路径安全校验失败：不删除目录外文件，也不静默忽略异常；先修复受污染索引或配置。

## 升级触发条件

出现以下任一情况时重新评估当前方案：

- 需要跨机器共享或远程访问图片；
- 工程目录需要频繁搬迁或索引需要打包分发；
- 图片数量使全量 collection 查询、磁盘扫描或本地备份明显变慢；
- 多个进程频繁并发摄取、删除同一文档；
- 业务要求数据库行和图片二进制具备强一致提交或版本化恢复；
- 实际出现不可接受的孤立文件、缺失文件或磁盘容量问题。

推荐按测量结果演进：

```text
本地小规模
文件系统 + SQLite 映射
          │
          ▼
需要可搬迁、巡检或更强本地恢复
相对路径 + schema migration + reconcile/cleanup
          │
          ▼
需要共享或大规模图片资产
对象存储 + 事务数据库中的对象 key / 状态机
```

## 验证证据

- `tests/unit/test_image_storage.py` 覆盖默认路径、WAL、完整字段恢复和 `doc_hash`。
- 测试覆盖分代 upsert、active generation 查询和跨 collection 删除隔离。
- 测试覆盖缺失文件、目录越界、重复 ID 和非法页码的批次拒绝。
- 测试覆盖文档级文件删除和多线程写入后映射完整性。
- C13 全量回归结果：`158 passed, 5 skipped`；相关 Ruff、Mypy、compileall 和
  `git diff --check` 均通过。

## 关联实现与决策

- `src/ingestion/storage/image_storage.py`
- `src/libs/loader/pdf_loader.py`
- `src/core/types.py` 中的 `ImageRef`
- `src/ports/ingestion.py` 中的 `ImageStore`
- `tests/unit/test_image_storage.py`
- `docs/decisions/0001-bm25-index-persistence.md`
- `docs/decisions/0002-chunk-record-and-sparse-representation.md`
- `docs/decisions/0004-ingestion-pipeline-orchestration-and-lease.md`
- `docs/decisions/0005-ingestion-generation-state-machine.md`
