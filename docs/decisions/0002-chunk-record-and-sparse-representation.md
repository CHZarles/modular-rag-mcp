# ADR-0002：ChunkRecord 逻辑聚合与稀疏表示边界

- **状态**：当前采用（C12、C14、ADR-0005 分代身份已落地）
- **日期**：2026-07-30
- **决策范围**：`Chunk`、`ChunkRecord`、Dense/Sparse 表示以及 Vector/BM25 跨索引身份

## 背景

文档完成切分、正文清洗和图片描述补充后，需要同时建立稠密向量索引和 BM25
关键词索引。两条检索路线描述的是同一个最终文本块，但使用不同的数据表示和物理
存储，因此需要一个领域对象把正文、来源信息和检索特征关联起来。

`ChunkRecord` 承担这个逻辑聚合职责。它不是新的文档切分结果，也不表示所有字段
必须以同一种格式写入同一个数据库，更不提供 Chroma 与 BM25 之间的原子事务。

## 概念边界

可以把当前模型理解为：

```text
ChunkRecord
= 最终正文 text
+ 来源与过滤信息 metadata
+ 最终存储 ID id
+ 完整正文指纹 content_hash
+ 可选稠密向量 dense_vector
+ 可选稀疏表示 sparse_vector
```

`Chunk` 和 `ChunkRecord` 的职责不同：

| 对象 | 所处阶段 | 主要内容 | ID 含义 |
|------|----------|----------|---------|
| `Chunk` | 切分与转换阶段 | 正文、metadata、来源引用、切分位置 | C4 切分阶段身份 |
| `ChunkRecord` | 编码与存储边界 | 最终正文、metadata、Dense/Sparse 特征、正文哈希 | C12 最终存储身份 |

C12 `VectorUpserter` 根据最终正文生成存储 ID，并返回与输入顺序一致的
`ChunkRecord`。这个返回值应成为后续 Vector 和 BM25 两条索引路线对齐 Chunk
身份的依据。

## 稀疏向量是什么

“向量”可以理解为一组有固定坐标含义的数。假设整个词表只有下面六个词：

```text
[agent, bm25, dense, rag, retrieval, vector]
```

对于正文：

```text
RAG rag BM25 retrieval retrieval
```

当前 `SparseEncoder` 会先做大小写和 Unicode 规范化，再得到：

```text
tokens = [rag, rag, bm25, retrieval, retrieval]
```

按照上面的词表顺序，它在概念上可以写成一个普通向量：

```text
[0, 1, 0, 2, 2, 0]
```

很多坐标都是 `0`。真实词表可能有几万甚至更多词，为每个 Chunk 保存全部零值既
浪费空间，也没有计算价值，因此只保存出现过的词及其词频：

```python
{
    "terms": {
        "rag": 2,
        "bm25": 1,
        "retrieval": 2,
    },
    "doc_length": 5,
}
```

这就是当前项目中的“稀疏向量”：它在数学上对应一个大部分坐标为零的词表向量，
在代码里则用 `terms` 字典只记录非零坐标。`doc_length` 是规范化分词后的总词数，
用于 BM25 的文档长度归一化。

当前表示只是单个 Chunk 的局部统计：

- `SparseEncoder` 负责分词、词频 `TF` 和 `doc_length`；
- `BM25Indexer` 汇总全语料的文档频率 `DF`、逆文档频率 `IDF` 和平均文档长度；
- 查询时，BM25 再把 TF、IDF 和长度归一化组合成最终相关性分数。

所以这里的 `sparse_vector` 不是 AI 模型生成的 Embedding，也不是已经算好的 BM25
分数。它由本地确定性代码生成；同一正文和同一分词规则会得到相同结果。

中文当前采用重叠二元词组。例如：

```text
混合检索 -> [混合, 合检, 检索]
```

对应的 `terms` 中三个词频都是 `1`，`doc_length` 为 `3`。

## 与稠密向量的区别

| 维度 | 稀疏表示 | 稠密向量 |
|------|----------|----------|
| 坐标含义 | 通常直接对应词或 token | 由 Embedding 模型学习，单个维度难以直接解释 |
| 零值数量 | 大多数坐标为零，只保存非零项 | 通常几乎每个维度都有浮点值 |
| 主要能力 | 专有名词、编号和原词精确匹配 | 同义表达和语义相近内容匹配 |
| 当前生成方式 | 本地分词与词频统计 | OpenAI-compatible Embedding API |
| 当前检索器 | BM25 | Chroma 向量距离查询 |

两者不是互相替代，而是从不同角度描述同一段正文，后续通过 Hybrid Search 融合结果。

## 逻辑记录与物理存储

`ChunkRecord` 在领域层把信息组织在一起，物理存储仍然分开：

```text
                         ┌─ Chroma
                         │  id / text / dense vector / metadata
最终 ChunkRecord ────────┤
                         └─ BM25 本地索引
                            id / text / terms / doc_length / postings / IDF
```

当前 `ChromaStore` 还会把 `sparse_vector` 编码成 metadata 内部 JSON，以便
`get_by_ids()` 可以还原完整的 `ChunkRecord`；它不参与 Chroma 的稠密距离计算。
真正的稀疏检索由独立的 BM25 索引完成。

两边能否代表同一个 Chunk，关键不在于是否位于同一个文件，而在于是否使用相同的
最终 `ChunkRecord.id`。

## 当前决策

1. 保留 `ChunkRecord` 作为领域层的统一存储载体，不把 Chroma 或 BM25 的原生结构
   泄漏到上层 Pipeline。
2. Dense 和 Sparse 字段继续保持可选，因为同一个领域对象还要支持端口传输和按 ID
   读取；具体适配器在自己的写入边界校验必需字段。
3. C12 生成的最终存储 ID 作为跨索引身份来源；Pipeline 使用返回的
   `ChunkRecord.id` 替换 BM25 输入 Chunk 的 C4 ID。ADR-0005 进一步把 `doc_key` 和
   `generation` 写入该 ID，隔离并发 worker 的物理记录。
4. 保持 Vector 和 BM25 物理存储分离。当前规模下不为了跨存储事务提前引入新的
   数据库或分布式事务组件。

## 已知顾虑与残余风险

| 顾虑 | 可能后果 | 当前控制 | 后续处理 |
|------|----------|----------|----------|
| Vector 与 BM25 分两次写入 | 一路成功、另一路失败时出现部分分代数据 | 未完成 generation 不会发布，查询继续读取旧 active；失败代可精确回收 | 详见 ADR-0005；不把分代发布描述成跨存储联合事务 |
| 正文变化会生成新 ID | 磁盘短期同时存在新旧记录 | ID 含 `doc_key + generation`，查询只接受 active generation | 发布后尽力清理，后续补周期性 GC |
| Sparse 数据同时存在于 Chroma metadata 和 BM25 快照 | 占用额外空间，并存在副本不一致的可能 | Chroma 副本只用于领域对象还原，BM25 快照才负责稀疏检索 | 先保留便于调试；实测存储压力明显时再评估不保存 Chroma Sparse 副本 |
| 最终 ID 的内容派生后缀截取 32 个十六进制字符 | 理论上仍存在极低概率前缀碰撞 | `doc_key + generation + chunk_index + 完整正文哈希` 共同参与身份，完整 `content_hash` 随记录保存 | 只有实测规模或合规要求需要时再扩大后缀，不提前增加 ID 长度 |
| 中文仅使用重叠二元词组 | 可能把词切得过碎，无法达到专业分词器的召回和精度 | 无额外依赖、规则确定且可复现 | 用真实中文语料评估；指标不足时再替换可插拔 tokenizer |
| `sparse_vector` 的静态类型是宽松的 `JsonDict` | 非法 `terms` 或 `doc_length` 可能流到较晚阶段才失败 | `BM25Indexer` 写入时校验映射、正整数词频和长度总和 | 若多种 Sparse 实现增加，再考虑专用强类型数据类；当前不提前增加抽象 |

这些顾虑说明 `ChunkRecord` 解决的是“统一描述和身份对齐”，不是“跨存储一致性已经
自动解决”。ADR-0005 用分代写入和 active 指针保证可见性，但仍不提供跨存储联合事务。

## 验证证据

- `tests/unit/test_sparse_encoder.py` 覆盖英文词频、Unicode/大小写规范化、中文二元词组、
  空正文和顺序保持。
- `tests/unit/test_bm25_indexer_roundtrip.py` 覆盖 Sparse 结构校验、BM25 统计、幂等更新、
  查询和快照恢复。
- `tests/unit/test_vector_upserter_idempotency.py` 覆盖最终 ID、Dense/Sparse 顺序对齐、
  向量校验和幂等写入。
- `tests/integration/test_ingestion_pipeline.py` 覆盖最终 ID 跨 Chroma/BM25 对齐、分代
  发布以及过期 worker 的旧代结果不可见。

## 关联实现与决策

- `src/core/types.py` 中的 `Chunk`、`ChunkRecord`
- `src/ingestion/embedding/sparse_encoder.py`
- `src/ingestion/storage/vector_upserter.py`
- `src/ingestion/storage/bm25_indexer.py`
- `src/libs/vector_store/chroma_store.py`
- `docs/decisions/0001-bm25-index-persistence.md`
- `docs/decisions/0004-ingestion-pipeline-orchestration-and-lease.md`
- `docs/decisions/0005-ingestion-generation-state-machine.md`
