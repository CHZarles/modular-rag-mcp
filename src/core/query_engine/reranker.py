"""重排序器实现。"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import replace

from src.core.types import RetrievalCandidate
from src.ports.query import BaseReranker


class _RerankTimeoutError(RuntimeError):
    """区分编排器等待超时与后端自己抛出的 TimeoutError。"""


class FallbackReranker:
    """为可插拔重排后端统一提供候选限制、超时和 Fusion 顺序回退。"""

    def __init__(
        self,
        backend: BaseReranker,
        *,
        backend_name: str,
        top_m: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        if top_m is not None and top_m <= 0:
            raise ValueError("reranker top_m must be positive")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("reranker timeout_seconds must be positive")
        if not backend_name.strip():
            raise ValueError("reranker backend_name must not be empty")

        self.backend = backend
        self.backend_name = backend_name.strip().lower()
        self.top_m = top_m
        self.timeout_seconds = timeout_seconds

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        """调用后端精排；异常或超时时保留输入的 Fusion 顺序。"""
        if top_k <= 0:
            raise ValueError("reranker top_k must be positive")
        started = time.monotonic()
        if not candidates:
            _record_rerank_stage(
                trace,
                method=self.backend_name,
                provider=type(self.backend).__name__,
                details={
                    "status": "skipped",
                    "input_count": 0,
                    "output_count": 0,
                    "top_k": top_k,
                    "fallback": False,
                },
                started=started,
            )
            return []

        # top_m 限制昂贵后端的输入；当调用方要求更多结果时，至少覆盖 top_k。
        candidate_limit = max(top_k, self.top_m or 0)
        backend_candidates = candidates[:candidate_limit] if self.top_m else candidates

        try:
            reranked = self._call_backend(
                query,
                backend_candidates,
                top_k,
                trace,
            )
            results = list(reranked[:top_k])
            _record_rerank_stage(
                trace,
                method=self.backend_name,
                provider=type(self.backend).__name__,
                details={
                    "status": "success",
                    "input_count": len(candidates),
                    "backend_input_count": len(backend_candidates),
                    "output_count": len(results),
                    "top_k": top_k,
                    "fallback": False,
                },
                started=started,
            )
            return results
        except _RerankTimeoutError as exc:
            return self._fallback_with_trace(candidates, top_k, str(exc), trace, started)
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            return self._fallback_with_trace(candidates, top_k, reason, trace, started)

    def _call_backend(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: object | None,
    ) -> list[RetrievalCandidate]:
        if self.timeout_seconds is None:
            return self.backend.rerank(query, candidates, top_k, trace=trace)

        # Python 线程不能强制终止正在运行的后端调用；这里让查询及时回退，并请求取消尚未
        # 开始的任务。真正的网络超时仍应由具体 provider 同时配置。
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reranker")
        try:
            future = executor.submit(
                self.backend.rerank,
                query,
                candidates,
                top_k,
                trace=trace,
            )
            try:
                return future.result(timeout=self.timeout_seconds)
            except FutureTimeoutError as exc:
                # 后端可能主动抛 TimeoutError；只有 Future 尚未完成才表示本层等待超时。
                if future.done():
                    raise
                future.cancel()
                raise _RerankTimeoutError(
                    f"timeout after {self.timeout_seconds:g} seconds"
                ) from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _fallback(
        self,
        candidates: list[RetrievalCandidate],
        top_k: int,
        reason: str,
    ) -> list[RetrievalCandidate]:
        """保留 Fusion 分数、来源和顺序，只附加明确的回退原因。"""
        return [
            replace(
                candidate,
                debug={
                    **candidate.debug,
                    "rerank": {
                        "backend": self.backend_name,
                        "fallback": True,
                        "reason": reason,
                    },
                },
            )
            for candidate in candidates[:top_k]
        ]

    def _fallback_with_trace(
        self,
        candidates: list[RetrievalCandidate],
        top_k: int,
        reason: str,
        trace: object | None,
        started: float,
    ) -> list[RetrievalCandidate]:
        results = self._fallback(candidates, top_k, reason)
        _record_rerank_stage(
            trace,
            method=self.backend_name,
            provider=type(self.backend).__name__,
            details={
                "status": "fallback",
                "input_count": len(candidates),
                "output_count": len(results),
                "top_k": top_k,
                "fallback": True,
                "reason": reason,
            },
            started=started,
        )
        return results


class NoneReranker:
    """保持融合结果原顺序的空操作重排序器。"""

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        if top_k <= 0:
            raise ValueError("reranker top_k must be positive")
        started = time.monotonic()
        results = list(candidates[:top_k])
        _record_rerank_stage(
            trace,
            method="none",
            provider=type(self).__name__,
            details={
                "status": "skipped",
                "input_count": len(candidates),
                "output_count": len(results),
                "top_k": top_k,
                "fallback": False,
            },
            started=started,
        )
        return results


def _record_rerank_stage(
    trace: object | None,
    *,
    method: str,
    provider: str,
    details: dict[str, object],
    started: float,
) -> None:
    if trace is None or not hasattr(trace, "record_stage"):
        return
    trace.record_stage(
        "rerank",
        {"method": method, "provider": provider, "details": details},
        elapsed_ms=(time.monotonic() - started) * 1000.0,
    )
