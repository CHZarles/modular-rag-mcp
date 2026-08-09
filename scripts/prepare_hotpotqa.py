"""Create a deterministic HotpotQA retrieval benchmark from the raw dev set."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "hotpotqa" / "hotpot_dev_distractor_v1.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "hotpotqa" / "benchmark"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a deterministic HotpotQA distractor retrieval benchmark."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="raw HotpotQA JSON path")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="output directory")
    parser.add_argument("--limit", type=int, default=120, help="number of examples to select")
    args = parser.parse_args(argv)

    try:
        report = prepare_dataset(Path(args.input), Path(args.output), limit=args.limit)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"HotpotQA preparation failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


def prepare_dataset(input_path: Path, output_dir: Path, *, limit: int = 120) -> dict[str, int]:
    """Write deduplicated context paragraphs and title-level relevance labels."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    examples = _load_examples(input_path)
    selected = sorted(examples, key=_selection_key)[:limit]
    if len(selected) < limit:
        raise ValueError(f"requested {limit} examples but input contains {len(selected)}")

    corpus: dict[tuple[str, str], dict[str, str]] = {}
    queries: list[dict[str, Any]] = []
    example_ids: set[str] = set()
    for example in selected:
        example_id = _required_text(example, "_id")
        if example_id in example_ids:
            raise ValueError(f"duplicate HotpotQA example id: {example_id}")
        example_ids.add(example_id)

        contexts = _contexts(example)
        context_titles = {title for title, _ in contexts}
        expected_titles = _supporting_titles(example)
        missing = set(expected_titles) - context_titles
        if missing:
            raise ValueError(
                f"HotpotQA example {example_id} has supporting titles outside context: {sorted(missing)}"
            )

        for title, text in contexts:
            key = (title, text)
            corpus.setdefault(
                key,
                {
                    "section_id": _paragraph_id(title, text),
                    "title": title,
                    "text": text,
                },
            )
        queries.append(
            {
                "case_id": f"hotpotqa:{example_id}",
                "query": _required_text(example, "question"),
                "expected_titles": expected_titles,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        output_dir / "corpus.jsonl", sorted(corpus.values(), key=lambda row: row["section_id"])
    )
    _write_jsonl(output_dir / "queries.jsonl", queries)
    return {"example_count": len(selected), "paragraph_count": len(corpus)}


def _load_examples(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError("HotpotQA input must be a JSON array of objects")
    return data


def _selection_key(example: dict[str, Any]) -> str:
    return hashlib.sha256(_required_text(example, "_id").encode("utf-8")).hexdigest()


def _contexts(example: dict[str, Any]) -> list[tuple[str, str]]:
    raw_contexts = example.get("context")
    if not isinstance(raw_contexts, list):
        raise ValueError("HotpotQA context must be a list")

    contexts: list[tuple[str, str]] = []
    for raw_context in raw_contexts:
        if not isinstance(raw_context, list) or len(raw_context) != 2:
            raise ValueError("HotpotQA context entries must contain a title and sentence list")
        title, sentences = raw_context
        if not isinstance(title, str) or not title.strip():
            raise ValueError("HotpotQA context title must be a non-empty string")
        if not isinstance(sentences, list) or not all(
            isinstance(sentence, str) for sentence in sentences
        ):
            raise ValueError("HotpotQA context sentences must be strings")
        text = " ".join(" ".join(sentences).split())
        if not text:
            raise ValueError("HotpotQA context paragraph must not be empty")
        contexts.append((title.strip(), text))
    return contexts


def _supporting_titles(example: dict[str, Any]) -> list[str]:
    facts = example.get("supporting_facts")
    if not isinstance(facts, list):
        raise ValueError("HotpotQA supporting_facts must be a list")
    titles: set[str] = set()
    for fact in facts:
        if not isinstance(fact, list) or len(fact) != 2:
            raise ValueError(
                "HotpotQA supporting_facts entries must contain a title and sentence index"
            )
        title = fact[0]
        if not isinstance(title, str) or not title.strip():
            raise ValueError("HotpotQA supporting_facts titles must be non-empty strings")
        titles.add(title.strip())
    if not titles:
        raise ValueError("HotpotQA supporting_facts must contain at least one title")
    return sorted(titles)


def _paragraph_id(title: str, text: str) -> str:
    digest = hashlib.sha256(f"{title}\n{text}".encode()).hexdigest()
    return f"hotpotqa-{digest}"


def _required_text(example: dict[str, Any], key: str) -> str:
    value = example.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"HotpotQA {key} must be a non-empty string")
    return value.strip()


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
