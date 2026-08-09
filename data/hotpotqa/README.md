# HotpotQA Retrieval Benchmark

`benchmark/` is a deterministic 120-question retrieval benchmark prepared from
HotpotQA's distractor development set. It contains every context paragraph from
each selected example, including the supplied hard negatives. Paragraphs are
deduplicated by title and normalized text; relevance is a title listed in that
example's `supporting_facts`.

## Files

- `benchmark/corpus.jsonl`: 1,194 deduplicated context paragraphs with stable IDs.
- `benchmark/queries.jsonl`: 120 questions with supporting-paragraph title labels.
- `hotpot_dev_distractor_v1.json`: local raw download, deliberately ignored by Git.

## Source and License

Source: [HotpotQA distractor development set](http://curtis.ml.cmu.edu/datasets/hotpot/hotpot_dev_distractor_v1.json).
The official [HotpotQA repository](https://github.com/hotpotqa/hotpot) states
that the dataset is licensed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/legalcode).
This derived benchmark is distributed under the same license.

Attribution: Zhilin Yang, Peng Qi, Saizheng Zhang, Yoshua Bengio, William W.
Cohen, Ruslan Salakhutdinov, and Christopher D. Manning, *HotpotQA: A Dataset
for Diverse, Explainable Multi-hop Question Answering*, EMNLP 2018.

## Rebuild

Place the official raw JSON at `data/hotpotqa/hotpot_dev_distractor_v1.json`,
then run:

```bash
.venv/bin/python scripts/prepare_hotpotqa.py
.venv/bin/python scripts/evaluate_retrieval.py --dataset hotpotqa
```

Selection sorts examples by the SHA-256 digest of `_id`, then takes the first
120, so rebuilding from the same source reproduces the committed JSONL files.

## Untuned MiniMax Baseline

Run on 2026-08-09 with `minimax/embo-01`, the production splitter
(`chunk_size=1000`, `chunk_overlap=200`), and the formal `HybridQueryEngine`:

| Strategy | Hit@5 | MRR@5 |
| --- | ---: | ---: |
| BM25 | 0.9083 | 0.7543 |
| Dense | 0.2750 | 0.1800 |
| Hybrid | 0.7250 | 0.4508 |

BM25 remains the selected production route. Its Hit@5 clears the 0.90 gate,
but its MRR@5 does not clear the unchanged 0.80 gate. The independent image
check returned 2/3 original image payloads, meeting its 2/3 gate.
