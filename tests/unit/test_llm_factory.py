from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.settings import Settings
from libs.llm import ChatResponse, LLMFactory, Message, create_llm


class FakeLLM:
    def __init__(self, model: str) -> None:
        self.model = model

    def chat(
        self,
        messages: list[Message],
        trace: object | None = None,
        **kwargs: object,
    ) -> ChatResponse:
        return ChatResponse(content=messages[-1].content, model=self.model)


@pytest.fixture(autouse=True)
def cleanup_fake_provider() -> Iterator[None]:
    LLMFactory.unregister("fake")
    yield
    LLMFactory.unregister("fake")


def test_llm_factory_routes_by_provider() -> None:
    LLMFactory.register("fake", lambda config: FakeLLM(str(config["model"])))
    settings = Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "fake", "model": "fake-chat"},
        embedding={"provider": "openai"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "chroma"},
        retrieval={"sparse_backend": "bm25"},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"]},
        observability={"enabled": True},
    )

    llm = LLMFactory.create(settings)

    assert llm.chat([Message(role="user", content="hello")]).content == "hello"
    assert llm.chat([Message(role="user", content="hello")]).model == "fake-chat"


def test_create_llm_accepts_llm_config_mapping() -> None:
    LLMFactory.register("fake", lambda config: FakeLLM(str(config["model"])))

    llm = create_llm({"provider": "fake", "model": "inline"})

    assert llm.chat([Message(role="user", content="ok")]).model == "inline"


def test_llm_factory_names_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported LLM provider: missing"):
        LLMFactory.create({"llm": {"provider": "missing"}})


def test_llm_factory_requires_provider() -> None:
    with pytest.raises(ValueError, match=r"llm\.provider"):
        LLMFactory.create({"llm": {}})
