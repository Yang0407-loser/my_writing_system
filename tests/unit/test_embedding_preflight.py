"""The embedding backend must be probed before any LLM token is spent.

Incident (2026-07-26): Ollama was unreachable, the failure surfaced inside
_phase_writing — after character_arcs and world_state had already called the
LLM — and autoretry_for replayed the whole task five times. 26,058 tokens
burned, ~40% of that task's total spend, all of it on planning that was thrown
away.
"""

import sys
import types

import pytest

from app.config import settings
from app.embedding.factory import preflight_embedding_backend


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def install_fake_requests(monkeypatch, *, response=None, raises=None):
    """Inject a stand-in requests module; the factory imports it lazily."""
    calls = []

    def fake_get(url, timeout=None):
        calls.append({"url": url, "timeout": timeout})
        if raises is not None:
            raise raises
        return response

    module = types.ModuleType("requests")
    module.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", module)
    return calls


# ---------------------------------------------------------------------------
# providers that need no probe
# ---------------------------------------------------------------------------


def test_sentence_transformers_is_always_ok(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "sentence_transformers")
    assert preflight_embedding_backend() == (True, "")


def test_openai_is_not_probed(monkeypatch):
    """A probe would cost a billable call, so it must not fire."""
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "openai")
    calls = install_fake_requests(monkeypatch, raises=AssertionError("must not call"))
    assert preflight_embedding_backend() == (True, "")
    assert calls == []


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "nonsense")
    ok, reason = preflight_embedding_backend()
    assert ok is False
    assert "nonsense" in reason


def test_provider_name_is_case_and_space_insensitive(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "  Sentence_Transformers  ")
    assert preflight_embedding_backend() == (True, "")


# ---------------------------------------------------------------------------
# ollama
# ---------------------------------------------------------------------------


def test_ollama_unreachable_is_caught(monkeypatch):
    """The exact 2026-07-26 failure, now surfaced at zero token cost."""
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    install_fake_requests(monkeypatch, raises=ConnectionRefusedError("10061"))

    ok, reason = preflight_embedding_backend()

    assert ok is False
    assert "不可达" in reason
    assert "ConnectionRefusedError" in reason
    assert "127.0.0.1" in reason  # the fix hint travels with the error


def test_ollama_probe_hits_tags_and_is_bounded(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://127.0.0.1:11434/")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "bge-m3:latest")
    calls = install_fake_requests(
        monkeypatch, response=FakeResponse({"models": [{"name": "bge-m3:latest"}]})
    )

    assert preflight_embedding_backend() == (True, "")
    assert calls[0]["url"] == "http://127.0.0.1:11434/api/tags"  # trailing / handled
    assert calls[0]["timeout"] == 5  # must not hang the worker


def test_ollama_up_but_model_not_pulled(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "bge-m3:latest")
    install_fake_requests(
        monkeypatch,
        response=FakeResponse({"models": [{"name": "qwen3:8b"}, {"name": "llama3:8b"}]}),
    )

    ok, reason = preflight_embedding_backend()

    assert ok is False
    assert "未拉取模型" in reason
    assert "qwen3:8b" in reason  # tells you what IS there


def test_model_match_ignores_the_tag(monkeypatch):
    """`bge-m3` and `bge-m3:latest` are the same pull."""
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "bge-m3")
    install_fake_requests(
        monkeypatch, response=FakeResponse({"models": [{"name": "bge-m3:latest"}]})
    )
    assert preflight_embedding_backend() == (True, "")


def test_http_error_is_caught(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "ollama")
    install_fake_requests(
        monkeypatch, response=FakeResponse(error=RuntimeError("500 Server Error"))
    )
    ok, reason = preflight_embedding_backend()
    assert ok is False
    assert "不可达" in reason


def test_unparsable_tag_payload_does_not_block_the_task(monkeypatch):
    """Reachable is the thing that matters; don't fail on a schema change."""
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "ollama")
    install_fake_requests(
        monkeypatch, response=FakeResponse(ValueError("not json"))
    )
    assert preflight_embedding_backend() == (True, "")


def test_empty_model_list_does_not_block_the_task(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "ollama")
    install_fake_requests(monkeypatch, response=FakeResponse({"models": []}))
    assert preflight_embedding_backend() == (True, "")


def test_preflight_never_raises(monkeypatch):
    """It runs inside the task's try block; a probe bug must not mask the task."""
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "ollama")
    install_fake_requests(monkeypatch, raises=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        # BaseException still propagates by design; only Exception is absorbed
        preflight_embedding_backend()


def test_arbitrary_exception_is_absorbed(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "ollama")
    install_fake_requests(monkeypatch, raises=OSError("adapter gone"))
    ok, reason = preflight_embedding_backend()
    assert ok is False
    assert "OSError" in reason
