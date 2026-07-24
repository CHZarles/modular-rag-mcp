"""Batch processor — chunk items into fixed-size batches."""

from __future__ import annotations

from typing import Callable, Iterable, TypeVar

T = TypeVar("T")


class BatchProcessor:
    """Pure batcher. Defers the work to a user-supplied ``process_fn``."""

    def __init__(self, batch_size: int = 16) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        self.batch_size = batch_size

    def process(
        self,
        items: Iterable[T],
        process_fn: Callable[[list[T]], list],
    ) -> list:
        out: list = []
        batch: list[T] = []
        for item in items:
            batch.append(item)
            if len(batch) >= self.batch_size:
                out.extend(process_fn(batch))
                batch = []
        if batch:
            out.extend(process_fn(batch))
        return out