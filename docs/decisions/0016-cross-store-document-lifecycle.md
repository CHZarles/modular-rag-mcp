# ADR-0016：跨存储文档生命周期与可重试删除

- **状态**：当前采用
- **日期**：2026-08-02
- **决策范围**：DocumentManager 如何聚合文档视图，并协调 Chroma、BM25、图片存储和摄取控制面完成删除
- **决策关系**：建立在 ADR-0003 的图片引用边界、ADR-0004 的摄取编排，以及 ADR-0005 的 generation 状态机之上
- **说明**：本文记录当前实现和面试相关原理，不修改 `DEV_SPEC.md`

## 先用一句话理解

DocumentManager 把 FileIntegrity 当作“哪些 generation 当前可见”的控制面，把 Chroma、BM25 和
ImageStorage 当作数据面；删除时先执行三个数据面的幂等清理，只有全部成功后才移除控制面记录，
任何部分失败都保留控制记录作为重试锚点。

```text
                    FileIntegrity
                active_generation 指针
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
       Chroma           BM25       ImageStorage
    dense chunks    sparse index    image refs/files
```

## 为什么这是面试重点

这是典型的“一个业务操作跨多个非事务资源”问题。SQLite 事务只能保护 FileIntegrity，不能同时
覆盖 Chroma 文件、BM25 快照和图片数据库/文件系统，因此不能声称删除具有数据库意义上的原子性。
真正需要回答的是：

- 哪个存储决定文档是否对外可见；
- 多个数据面如何保持同一个文档身份；
- 删除执行到一半失败时，系统如何恢复；
- 重试为什么不会误删其他文档或产生重复副作用；
- 历史 generation 仍残留时，列表和详情为什么不会读到旧版本。

当前实现采用控制面/数据面分离与可重试补偿，而不是引入分布式事务。

## FileIntegrity 为什么是权威控制面

文档的稳定身份是：

```text
doc_key = SHA256(normalized_source_path + "\0" + collection)
```

每次摄取产生递增的 `generation`，只有 `document_state.active_generation` 指向的版本可以公开。
Chroma、BM25 和 ImageStorage 都保存 `doc_key + generation`，但它们只是物理数据的持有者，不负责
决定哪一代有效。

DocumentManager 列表时先读取：

```python
active = file_integrity.get_active_generations(collection)
```

再从摄取历史中只保留：

```python
row["attempt_status"] == "published"
and active[row["doc_key"]] == row["generation"]
```

因此旧 generation 即使还没有被 GC，也不会重复出现在文档列表中。这与查询链路的 generation
可见性规则一致，避免 Dashboard 和实际检索看到两套不同的“当前版本”。

## 为什么删除顺序是数据面优先、控制面最后

一次显式删除涉及四步：

```text
1. Chroma.delete_by_metadata(source_path, collection)
2. BM25.remove_document(source_path, collection)
3. ImageStorage.delete_by_document(source_path, collection)
4. FileIntegrity.remove_record(source_path, collection)
```

前三步都成功后才执行第四步。原因是控制面记录既是可见性来源，也是失败后的重试入口。

如果先删控制面，然后 Chroma 删除失败：

- 数据面会留下没有管理入口的孤儿数据；
- `list_documents()` 已经找不到该文档；
- 后续操作很难知道应该继续清理哪个 `doc_key` 和 generation。

如果数据面删除失败而控制面仍在：

- `DeleteResult.errors` 明确指出失败的存储；
- 文档仍能被管理界面发现；
- 再次执行相同删除即可补偿剩余数据；
- 已成功的步骤再次执行只是得到“删除 0 条”，不会破坏结果。

这不是严格原子删除，而是可观察、可重试、最终收敛的 Saga/补偿式思路。

## 幂等性从哪里来

删除能安全重试，依赖每个数据面都使用稳定条件执行幂等操作：

| 存储 | 删除条件 | 重复执行结果 |
|------|----------|--------------|
| Chroma | `source_path + collection` metadata | 已不存在时删除 0 条 |
| BM25 | Chunk metadata 中的 `source_path + collection` | 重建同一剩余快照 |
| ImageStorage | SQLite 中的文档引用 | 无引用时删除 0 条 |
| FileIntegrity | 由路径与 collection 计算的 `doc_key` | 已不存在时返回 `False` |

图片文件还有额外约束：只有 SQLite 中没有任何 generation 再引用同一路径时，才删除物理文件。
这样不同 generation 共享内容寻址图片时，不会因为删除其中一代而破坏另一代。

## 为什么部分失败时仍尝试其他数据面

DocumentManager 会分别捕获 Chroma、BM25 和 ImageStorage 的异常，而不是第一个失败就停止。这样一次
请求可以尽可能完成清理，并返回完整错误列表。

代价是失败窗口内可能出现部分数据面已删除、部分仍存在的状态。当前系统接受这个短暂不一致，
因为：

1. 单机本地 Dashboard 是主要管理入口；
2. 每一步可幂等重试；
3. 控制面记录不会在部分失败时丢失；
4. 调用方能从 `DeleteResult.errors` 判断操作尚未完成。

若未来要求强一致删除，需要增加 `deleting` 墓碑状态和后台补偿任务，而不是简单调整四个函数的
调用顺序。

## 文档详情为什么按 doc_key + generation 查询

`get_document_detail()` 不能只按 `source_path` 读取 Chroma。相同路径可能有多个历史 generation，
只按路径会把新旧 Chunk 混在一起。

当前先从控制面确定活跃记录，再使用：

```python
{"doc_key": active_doc_key, "generation": active_generation}
```

读取 Chroma，并按 `chunk_index` 排序。返回给 Dashboard 的详情只包含 Chunk 正文、metadata、内容哈希
和关联图片，不返回稠密向量，避免把高维 Embedding 作为管理页面负载。

## FileIntegrity 删除为什么同时清理 state 与 attempts

`document_state` 是当前状态，`ingestion_attempt` 是各代历史，两者存在外键关系。删除时在同一个
SQLite `BEGIN IMMEDIATE` 事务内：

1. 先删除该 `doc_key` 的 attempts；
2. 再删除 document_state；
3. 提交后文档可重新摄取。

`remove_record()` 同时兼容两种身份：

- 传入 `source_path + collection`，精确删除一个逻辑文档；
- 只传 `source_revision`，兼容早期按内容哈希清理记录的调用方式。

管理链路始终使用第一种方式，避免相同内容位于不同路径时被一起删除。

## 当前边界

- 当前没有跨四个存储的分布式事务，删除保证的是幂等补偿与最终收敛；
- 不建议在同一文档正在摄取时并发执行删除，未来可用 `deleting` 状态与租约协商加强围栏；
- `removed_bm25=True` 表示 BM25 清理步骤成功完成，不表示删除前一定存在匹配 Chunk；
- 全集合统计只汇总 active 文档，物理存储中的旧 generation 不计入公开资产数量；
- G3/G4 页面应展示 `DeleteResult.errors`，不能把部分失败提示为删除成功。

## 面试表达建议

可以按下面顺序回答：

1. “四个本地存储无法共享一个事务，所以我没有伪造强原子性。”
2. “FileIntegrity 是 generation 可见性的控制面，其他三个是数据面。”
3. “删除先做幂等数据清理，全部成功后才删控制记录，失败时保留重试锚点。”
4. “列表和详情始终先解析 active generation，再读取对应 Chunk 和图片，旧代不会泄漏。”
5. “如果业务升级到强一致要求，我会增加 deleting 墓碑、后台补偿和并发摄取围栏。”
