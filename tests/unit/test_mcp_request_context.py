"""Unit tests for the MCP request context (plan §C2.1).

Covers header parsing, identifier length / control-character / whitespace
rejection, ContextVar set/reset semantics, and uuid fallback for the
stdio transport.
"""

from __future__ import annotations

import asyncio

import pytest

from src.mcp_server.request_context import (
    MAX_IDENTIFIER_CHARS,
    InvalidRequestHeaderError,
    RequestContext,
    bind_request_context,
    current_request_context,
    generate_request_id,
    get_request_context,
    request_context_from_headers,
    reset_request_context,
)

# --- Header parsing --------------------------------------------------------

def test_request_context_from_headers_accepts_full_set() -> None:
    headers = {
        "X-Request-ID": "req-42",
        "X-RAG-Actor-Key": "actor-9",
        "X-RAG-Session-ID": "session-3",
    }

    context = request_context_from_headers(headers)

    assert context == RequestContext(
        request_id="req-42", actor_key="actor-9", session_id="session-3"
    )


def test_request_context_from_headers_uses_case_insensitive_lookup() -> None:
    headers = {
        "x-request-id": "req-7",
        "X-RAG-actor-KEY": "actor-1",
        "x-rag-session-id": "session-1",
    }

    context = request_context_from_headers(headers)

    assert context.request_id == "req-7"
    assert context.actor_key == "actor-1"
    assert context.session_id == "session-1"


def test_request_context_from_headers_trims_surrounding_whitespace() -> None:
    headers = {
        "X-Request-ID": "   req-1   ",
        "X-RAG-Actor-Key": "  actor-1 ",
    }

    context = request_context_from_headers(headers)

    assert context.request_id == "req-1"
    assert context.actor_key == "actor-1"


@pytest.mark.parametrize(
    "header,value",
    [
        ("X-Request-ID", ""),
        ("X-Request-ID", "   "),
        ("X-Request-ID", "\t\n "),
        ("X-RAG-Actor-Key", ""),
        ("X-RAG-Actor-Key", "    "),
        ("X-RAG-Session-ID", ""),
    ],
)
def test_request_context_rejects_blank_values(
    header: str, value: str
) -> None:
    with pytest.raises(InvalidRequestHeaderError) as info:
        request_context_from_headers({header: value})

    assert info.value.header == header
    assert "blank" in info.value.reason


@pytest.mark.parametrize(
    "header,value",
    [
        ("X-Request-ID", "req-\x01"),
        ("X-Request-ID", "req-\x7f"),
        ("X-Request-ID", "req-\x00"),
        ("X-RAG-Actor-Key", "act\x02or"),
        ("X-RAG-Session-ID", "ses\x1fsion"),
    ],
)
def test_request_context_rejects_control_characters(
    header: str, value: str
) -> None:
    with pytest.raises(InvalidRequestHeaderError) as info:
        request_context_from_headers({header: value})

    assert info.value.header == header


@pytest.mark.parametrize(
    "header",
    ["X-Request-ID", "X-RAG-Actor-Key", "X-RAG-Session-ID"],
)
def test_request_context_rejects_oversize_values(header: str) -> None:
    with pytest.raises(InvalidRequestHeaderError) as info:
        request_context_from_headers({header: "a" * (MAX_IDENTIFIER_CHARS + 1)})

    assert info.value.header == header
    assert "exceed" in info.value.reason


@pytest.mark.parametrize(
    "header",
    ["X-Request-ID", "X-RAG-Actor-Key", "X-RAG-Session-ID"],
)
def test_request_context_accepts_max_length_values(header: str) -> None:
    context = request_context_from_headers({header: "a" * MAX_IDENTIFIER_CHARS})

    assert (header == "X-Request-ID" and len(context.request_id) == MAX_IDENTIFIER_CHARS) or (
        header == "X-RAG-Actor-Key" and len(context.actor_key or "") == MAX_IDENTIFIER_CHARS
    ) or (
        header == "X-RAG-Session-ID" and len(context.session_id or "") == MAX_IDENTIFIER_CHARS
    )


def test_request_context_generates_uuid_when_request_id_missing() -> None:
    context = request_context_from_headers({"X-RAG-Actor-Key": "actor-1"})

    assert len(context.request_id) == 32
    assert all(character in "0123456789abcdef" for character in context.request_id)
    assert context.actor_key == "actor-1"
    assert context.session_id is None


def test_request_context_generates_uuid_when_no_headers() -> None:
    context = request_context_from_headers(None)

    assert isinstance(context.request_id, str) and len(context.request_id) >= 16
    assert context.actor_key is None
    assert context.session_id is None


def test_request_context_generates_uuid_when_headers_empty() -> None:
    context = request_context_from_headers({})

    assert isinstance(context.request_id, str) and len(context.request_id) >= 16


def test_generate_request_id_returns_unique_opaque_string() -> None:
    ids = {generate_request_id() for _ in range(50)}

    assert len(ids) == 50
    for value in ids:
        assert len(value) >= 16


# --- ContextVar set / reset ------------------------------------------------

def test_current_request_context_is_none_outside_bind() -> None:
    assert current_request_context() is None


def test_bind_request_context_makes_current_visible() -> None:
    ctx = RequestContext(request_id="rid")

    token = bind_request_context(ctx)
    try:
        assert current_request_context() is ctx
    finally:
        reset_request_context(token)

    assert current_request_context() is None


def test_reset_restores_previous_value_after_failed_reset_chain() -> None:
    outer = RequestContext(request_id="outer")
    inner = RequestContext(request_id="inner")

    token_outer = bind_request_context(outer)
    token_inner = bind_request_context(inner)
    assert current_request_context() is inner
    reset_request_context(token_inner)
    assert current_request_context() is outer
    reset_request_context(token_outer)
    assert current_request_context() is None


def test_get_request_context_returns_active_or_generates_fresh() -> None:
    fallback_first = get_request_context()
    fallback_second = get_request_context()

    # Without an active binding, each call returns a fresh synthetic value.
    assert fallback_first.request_id != fallback_second.request_id

    bound = RequestContext(request_id="bound")
    token = bind_request_context(bound)
    try:
        active = get_request_context()
        assert active is bound
    finally:
        reset_request_context(token)

    post_reset = get_request_context()
    assert post_reset.request_id != bound.request_id


def test_context_var_propagates_through_asyncio_to_thread() -> None:
    ctx = RequestContext(request_id="rid-thread", actor_key="actor-thread")
    captured: dict[str, RequestContext | None] = {}

    def _thread_probe() -> None:
        # ``asyncio.to_thread`` round-trips here. The ContextVar should
        # carry the parent value because Python copies the context into
        # the worker thread.
        captured["inside"] = current_request_context()

    async def _scenario() -> None:
        token = bind_request_context(ctx)
        try:
            await asyncio.to_thread(_thread_probe)
        finally:
            reset_request_context(token)
        captured["after"] = current_request_context()

    asyncio.run(_scenario())

    assert captured["inside"] == ctx
    assert captured["after"] is None


def test_concurrent_threads_keep_distinct_contexts() -> None:
    """Plan §C2.1 / FR-04: two concurrent reads inside ``asyncio.to_thread``
    must never see each other's ``actor_key`` or ``session_id``.

    We deliberately spawn the readers as raw ``threading.Thread`` instances
    (bypassing the default executor, whose worker pool is too small to host
    the whole test concurrently under a ``Barrier``). The two threads are
    started *after* their owning task has bound a context, and they each
    run inside an ``asyncio.to_thread`` exactly like the real Tool dispatch.
    """

    pairs: list[tuple[str, RequestContext, RequestContext | None]] = []

    async def _read(label: str, ctx: RequestContext) -> None:
        def _probe() -> None:
            observed = current_request_context()
            pairs.append((label, ctx, observed))

        token = bind_request_context(ctx)
        try:
            await asyncio.to_thread(_probe)
        finally:
            reset_request_context(token)

    async def _scenario() -> None:
        ctx_a = RequestContext(request_id="rid-A", actor_key="actor-A")
        ctx_b = RequestContext(request_id="rid-B", actor_key="actor-B")
        await asyncio.gather(_read("A", ctx_a), _read("B", ctx_b))

    asyncio.run(_scenario())

    by_label = {label: (expected, observed) for label, expected, observed in pairs}
    assert set(by_label) == {"A", "B"}
    for label, (expected, observed) in by_label.items():
        assert observed is not None
        assert observed.request_id == expected.request_id
        assert observed.actor_key == expected.actor_key


def test_concurrent_task_threads_have_isolated_contexts() -> None:
    """N=30 sequential ``bind``/``reset`` cycles interleaved across two
    ``asyncio.to_thread`` workers must never leak. The plan cares about
    cross-task safety under realistic pool sizing; this exercises the
    default executor that the MCP SDK actually uses.
    """

    iterations = 30
    mismatches: list[str] = []

    async def _cycle(label: str, index: int) -> None:
        ctx = RequestContext(
            request_id=f"rid-{label}-{index}",
            actor_key=f"actor-{label}-{index}",
        )

        def _probe() -> None:
            observed = current_request_context()
            if (
                observed is None
                or observed.request_id != ctx.request_id
                or observed.actor_key != ctx.actor_key
            ):
                mismatches.append(label)

        token = bind_request_context(ctx)
        try:
            await asyncio.to_thread(_probe)
        finally:
            reset_request_context(token)

    async def _main() -> None:
        await asyncio.gather(
            *[
                _cycle(label, index)
                for label, index in (
                    (label, index)
                    for label in ("left", "right")
                    for index in range(iterations)
                )
            ]
        )

    asyncio.run(_main())

    assert not mismatches, f"context leak in: {mismatches[:5]}"
    assert current_request_context() is None


def test_request_context_is_immutable() -> None:
    ctx = RequestContext(request_id="rid")

    with pytest.raises((AttributeError, TypeError)):
        ctx.request_id = "mutated"  # type: ignore[misc]


def test_request_context_equality_is_value_based() -> None:
    a = RequestContext(request_id="rid", actor_key="a", session_id="s")
    b = RequestContext(request_id="rid", actor_key="a", session_id="s")

    assert a == b
    assert hash(a) == hash(b)
