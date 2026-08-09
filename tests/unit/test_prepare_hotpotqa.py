import json
from pathlib import Path

from scripts.prepare_hotpotqa import prepare_dataset


def test_prepare_dataset_keeps_all_contexts_and_deduplicates_stably(tmp_path: Path) -> None:
    raw_path = tmp_path / "hotpot.json"
    raw_path.write_text(
        json.dumps(
            [
                {
                    "_id": "example-b",
                    "question": "Question B?",
                    "supporting_facts": [["Evidence B", 0], ["Evidence C", 0]],
                    "context": [
                        ["Evidence B", ["Beta paragraph."]],
                        ["Evidence C", ["Gamma paragraph."]],
                        ["Hard negative B", ["Distractor B."]],
                    ],
                },
                {
                    "_id": "example-a",
                    "question": "Question A?",
                    "supporting_facts": [["Evidence A", 0], ["Evidence B", 0]],
                    "context": [
                        ["Evidence A", ["Alpha", " paragraph."]],
                        ["Evidence B", ["Beta paragraph."]],
                        ["Hard negative A", ["Distractor A."]],
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )

    first = tmp_path / "first"
    second = tmp_path / "second"
    report = prepare_dataset(raw_path, first, limit=2)
    prepare_dataset(raw_path, second, limit=2)

    corpus = [json.loads(line) for line in (first / "corpus.jsonl").read_text().splitlines()]
    queries = [json.loads(line) for line in (first / "queries.jsonl").read_text().splitlines()]

    assert report == {"example_count": 2, "paragraph_count": 5}
    assert {row["title"] for row in corpus} == {
        "Evidence A",
        "Evidence B",
        "Evidence C",
        "Hard negative A",
        "Hard negative B",
    }
    assert len({row["section_id"] for row in corpus}) == 5
    assert {tuple(row["expected_titles"]) for row in queries} == {
        ("Evidence A", "Evidence B"),
        ("Evidence B", "Evidence C"),
    }
    assert (first / "corpus.jsonl").read_bytes() == (second / "corpus.jsonl").read_bytes()
    assert (first / "queries.jsonl").read_bytes() == (second / "queries.jsonl").read_bytes()
