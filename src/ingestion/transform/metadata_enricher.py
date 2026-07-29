"""为文档块生成可检索的标题、摘要和主题标签。"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.core.types import Chunk, JsonDict
from src.libs.llm import BaseLLM, Message, create_llm

_PROMPT = """你是 RAG 文档元数据提取器。请根据文档块生成元数据，并遵守以下要求：
1. title 是准确、简短的小标题。
2. summary 保留关键事实，不添加原文没有的信息。
3. tags 包含 2 到 5 个适合检索或过滤的主题标签。
4. 只返回一个 JSON 对象，不输出 Markdown、思考过程或说明。

返回格式：
{{"title":"...","summary":"...","tags":["...","..."]}}

文档块：
{text}
"""
_THINK_BLOCK_PATTERN = re.compile(r"<think>[\s\S]*?</think>\s*", re.IGNORECASE)
_JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$", re.IGNORECASE)
_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_FENCE_LINE_PATTERN = re.compile(r"^\s*(?:```|~~~)")
_IMAGE_PLACEHOLDER_PATTERN = re.compile(r"\[IMAGE:\s*[^\]]+\]")
_MARKDOWN_PREFIX_PATTERN = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)")
_TAG_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_+#.-]*|[\u4e00-\u9fff]{2,}")
_STOP_WORDS = {
    "about",
    "also",
    "and",
    "are",
    "for",
    "from",
    "into",
    "that",
    "the",
    "this",
    "with",
}


class MetadataEnricher:
    """以规则结果兜底，并可通过 LLM 生成更丰富的语义元数据。"""

    name = "metadata_enricher"

    def __init__(self, settings: Any, llm: BaseLLM | None = None) -> None:
        self._use_llm = _read_use_llm(settings)
        self._llm = llm

        # 客户端装配失败与在线调用失败采用同一种降级语义，不阻断摄取。
        if self._use_llm and self._llm is None:
            try:
                self._llm = create_llm(settings)
            except Exception:
                self._llm = None

    def transform(self, chunks: list[Chunk], trace: Any | None = None) -> list[Chunk]:
        """逐块注入元数据；重复执行时跳过已经成功处理的 Chunk。"""
        enriched_chunks: list[Chunk] = []
        for chunk in chunks:
            if chunk.metadata.get("metadata_enriched_by") in {"rule", "llm"}:
                enriched_chunks.append(chunk)
                continue

            try:
                rule_metadata = self._rule_based_metadata(chunk)
                enrichment = rule_metadata
                enriched_by = "rule"
                fallback = False

                if self._use_llm and chunk.text.strip():
                    llm_metadata = self._llm_metadata(chunk.text, trace)
                    if llm_metadata is not None:
                        enrichment = llm_metadata
                        enriched_by = "llm"
                    else:
                        fallback = True

                metadata = {
                    **chunk.metadata,
                    **enrichment,
                    "metadata_enriched_by": enriched_by,
                }
                if fallback:
                    metadata["metadata_enrichment_fallback_reason"] = "llm_enrichment_failed"
                enriched_chunks.append(replace(chunk, metadata=metadata))
            except Exception as exc:
                # 未预期异常也隔离在单块边界，原始 Chunk 仍可进入后续流水线。
                metadata = {
                    **chunk.metadata,
                    "metadata_enriched_by": "none",
                    "metadata_enrichment_error": str(exc),
                }
                enriched_chunks.append(replace(chunk, metadata=metadata))

        return enriched_chunks

    def _rule_based_metadata(self, chunk: Chunk) -> JsonDict:
        """从 Markdown 结构、正文和来源路径生成确定性的兜底元数据。"""
        plain_text = _plain_text(chunk.text)
        heading = _first_heading(chunk.text)
        existing_title = _nonempty_text(chunk.metadata.get("title"))
        source_title = _source_title(chunk.metadata.get("source_path"))

        title = heading or _first_sentence(plain_text) or existing_title or source_title
        if not title:
            title = f"Chunk {chunk.chunk_index + 1}"
        title = _truncate(title, 80)

        existing_summary = _nonempty_text(chunk.metadata.get("summary"))
        summary = _truncate(plain_text or existing_summary or title, 240)
        return {
            "title": title,
            "summary": summary,
            "tags": _rule_tags(title, summary),
        }

    def _llm_metadata(self, text: str, trace: Any | None = None) -> JsonDict | None:
        """调用 LLM 并校验 JSON 契约；任何失败都返回 ``None``。"""
        if self._llm is None:
            return None
        try:
            response = self._llm.chat(
                [Message(role="user", content=_PROMPT.format(text=text))],
                trace=trace,
            )
        except Exception:
            return None
        return _parse_llm_metadata(response.content)


def _parse_llm_metadata(content: str) -> JsonDict | None:
    """解析模型返回的 JSON，并拒绝缺字段或字段为空的结果。"""
    cleaned = _THINK_BLOCK_PATTERN.sub("", content).strip()
    fenced = _JSON_FENCE_PATTERN.fullmatch(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()

    try:
        value = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, Mapping):
        return None

    title = _nonempty_text(value.get("title"))
    summary = _nonempty_text(value.get("summary"))
    raw_tags = value.get("tags")
    if not isinstance(raw_tags, Sequence) or isinstance(raw_tags, str | bytes):
        return None
    tags = [tag for item in raw_tags if (tag := _nonempty_text(item))]
    if not title or not summary or not tags:
        return None
    return {"title": title, "summary": summary, "tags": tags[:5]}


def _plain_text(text: str) -> str:
    """移除影响摘要可读性的 Markdown 标记，但保留实际文本内容。"""
    lines: list[str] = []
    for line in text.splitlines():
        if _FENCE_LINE_PATTERN.match(line):
            continue
        line = _IMAGE_PLACEHOLDER_PATTERN.sub("", line)
        line = _MARKDOWN_PREFIX_PATTERN.sub("", line).strip()
        if line:
            lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _first_heading(text: str) -> str:
    match = _HEADING_PATTERN.search(text)
    return _clean_inline(match.group(1)) if match else ""


def _first_sentence(text: str) -> str:
    if not text:
        return ""
    sentence = re.split(r"(?<=[.!?。！？])\s+", text, maxsplit=1)[0]
    return _clean_inline(sentence)


def _clean_inline(text: str) -> str:
    return re.sub(r"[`*_~]+", "", text).strip()


def _source_title(value: Any) -> str:
    source_path = _nonempty_text(value)
    return Path(source_path).stem if source_path else ""


def _nonempty_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _rule_tags(title: str, summary: str) -> list[str]:
    """按出现频次选择少量关键词，并始终保留标题作为第一个主题标签。"""
    tags = [title]
    tokens = [token for token in _TAG_PATTERN.findall(f"{title} {summary}")]
    counts = Counter(token.casefold() for token in tokens if token.casefold() not in _STOP_WORDS)
    original = {token.casefold(): token for token in tokens}
    for normalized, _ in counts.most_common():
        candidate = original[normalized]
        if candidate.casefold() not in {tag.casefold() for tag in tags}:
            tags.append(candidate)
        if len(tags) == 5:
            break
    return tags


def _read_use_llm(settings: Any) -> bool:
    ingestion = settings.get("ingestion", {}) if isinstance(settings, Mapping) else getattr(
        settings, "ingestion", {}
    )
    if not isinstance(ingestion, Mapping):
        return False
    config = ingestion.get("metadata_enricher", {})
    if not isinstance(config, Mapping):
        return False
    value = config.get("use_llm", False)
    return value if isinstance(value, bool) else str(value).strip().lower() == "true"


__all__ = ["MetadataEnricher"]
