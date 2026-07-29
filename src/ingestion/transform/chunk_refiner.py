"""通过确定性规则和可选 LLM 对文档块做保守清洗。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.core.types import Chunk
from src.libs.llm import BaseLLM, Message, create_llm

_DEFAULT_PROMPT = "请清理下面的文档块，只返回清理后的正文：\n\n{text}"
_PROMPT_PATH = Path(__file__).resolve().parents[3] / "config/prompts/chunk_refinement.txt"
_FENCE_PATTERN = re.compile(r"^\s*(```|~~~)")
_HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
_THINK_BLOCK_PATTERN = re.compile(r"<think>[\s\S]*?</think>\s*", re.IGNORECASE)
_NOISE_LINE_PATTERNS = (
    re.compile(r"^\[(?:页眉|页脚|header|footer)\](?:\s.*)?$", re.IGNORECASE),
    re.compile(r"^page\s+\d+\s+(?:of|/)\s+\d+$", re.IGNORECASE),
    re.compile(r"^页码\s*\d+\s*/\s*\d+$"),
    re.compile(r"^(?:company\s+confidential|confidential)$", re.IGNORECASE),
    re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$"),
)


class ChunkRefiner:
    """先执行规则去噪，再按配置选择是否让 LLM 二次精炼。"""

    name = "chunk_refiner"

    def __init__(
        self,
        settings: Any,
        llm: BaseLLM | None = None,
        prompt_path: str | Path | None = None,
    ) -> None:
        self._use_llm = _read_use_llm(settings)
        self._prompt = self._load_prompt(prompt_path)
        self._llm = llm

        # LLM 配置或客户端创建失败不能阻断摄取，transform 会自动保留规则结果。
        if self._use_llm and self._llm is None:
            try:
                self._llm = create_llm(settings)
            except Exception:
                self._llm = None

    def transform(self, chunks: list[Chunk], trace: Any | None = None) -> list[Chunk]:
        """逐块精炼；任一块失败时保留其原文，并继续处理后续块。"""
        refined_chunks: list[Chunk] = []
        for chunk in chunks:
            if chunk.metadata.get("refined_by") in {"rule", "llm"}:
                refined_chunks.append(chunk)
                continue

            try:
                rule_text = self._rule_based_refine(chunk.text)
                text = rule_text
                refined_by = "rule"
                fallback = False

                if self._use_llm and rule_text:
                    llm_text = self._llm_refine(rule_text, trace)
                    if llm_text is not None:
                        text = llm_text
                        refined_by = "llm"
                    else:
                        fallback = True

                metadata = {**chunk.metadata, "refined_by": refined_by}
                if fallback:
                    metadata["refinement_fallback_reason"] = "llm_refinement_failed"
                refined_chunks.append(replace(chunk, text=text, metadata=metadata))
            except Exception as exc:
                # 故障隔离在单个 Chunk 边界，避免一段坏数据终止整个文档摄取。
                metadata = {
                    **chunk.metadata,
                    "refined_by": "none",
                    "refinement_error": str(exc),
                }
                refined_chunks.append(replace(chunk, metadata=metadata))

        return refined_chunks

    def _rule_based_refine(self, text: str) -> str:
        """删除常见导出噪声并规范空白，围栏代码块内部保持原样。"""
        # 先按围栏代码块分段，只从普通文本中删除可能跨行的 HTML 注释。
        parts = re.split(r"((?:```|~~~)[\s\S]*?(?:```|~~~))", text)
        without_comments = "".join(
            part if index % 2 else _HTML_COMMENT_PATTERN.sub("", part)
            for index, part in enumerate(parts)
        )

        lines: list[str] = []
        fence_marker: str | None = None
        for raw_line in without_comments.splitlines():
            fence_match = _FENCE_PATTERN.match(raw_line)
            if fence_match:
                marker = fence_match.group(1)
                if fence_marker is None:
                    fence_marker = marker
                elif fence_marker == marker:
                    fence_marker = None
                lines.append(raw_line)
                continue

            if fence_marker is not None:
                lines.append(raw_line)
                continue

            stripped = raw_line.strip()
            if any(pattern.fullmatch(stripped) for pattern in _NOISE_LINE_PATTERNS):
                continue
            if not stripped:
                if lines and lines[-1] != "":
                    lines.append("")
                continue

            # Markdown 嵌套列表依赖行首缩进，只压缩正文内部的连续空白。
            leading = raw_line[: len(raw_line) - len(raw_line.lstrip(" \t"))]
            lines.append(leading + re.sub(r"[ \t]+", " ", stripped))

        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines)

    def _llm_refine(self, text: str, trace: Any | None = None) -> str | None:
        """调用 LLM 精炼文本；接口异常或空响应统一返回 ``None``。"""
        if self._llm is None:
            return None
        try:
            response = self._llm.chat(
                [Message(role="user", content=self._prompt.format(text=text))],
                trace=trace,
            )
        except Exception:
            return None
        # 部分 OpenAI-compatible 模型会把推理过程混入 content，不能写入检索索引。
        content = _THINK_BLOCK_PATTERN.sub("", response.content).strip()
        return content or None

    def _load_prompt(self, prompt_path: str | Path | None = None) -> str:
        """读取 Prompt 模板；文件缺失、为空或无占位符时使用内置模板。"""
        path = Path(prompt_path) if prompt_path is not None else _PROMPT_PATH
        try:
            prompt = path.read_text(encoding="utf-8").strip()
        except OSError:
            return _DEFAULT_PROMPT
        return prompt if prompt and "{text}" in prompt else _DEFAULT_PROMPT


def _read_use_llm(settings: Any) -> bool:
    """兼容 Settings 对象和原始配置字典，读取 ChunkRefiner 开关。"""
    ingestion = settings.get("ingestion", {}) if isinstance(settings, Mapping) else getattr(
        settings, "ingestion", {}
    )
    if not isinstance(ingestion, Mapping):
        return False
    config = ingestion.get("chunk_refiner", {})
    if not isinstance(config, Mapping):
        return False
    value = config.get("use_llm", False)
    return value if isinstance(value, bool) else str(value).strip().lower() == "true"


__all__ = ["ChunkRefiner"]
