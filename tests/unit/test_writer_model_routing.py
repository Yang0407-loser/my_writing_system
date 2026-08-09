from app.agents.writer import Writer
from app.config import settings


def test_writer_defaults_to_flash_without_changing_global_model():
    assert settings.LLM_MODEL == "deepseek-v4-pro"
    assert settings.WRITER_LLM_MODEL == "deepseek-v4-flash"


def test_writer_requests_its_dedicated_model(monkeypatch):
    sentinel = object()
    requested = []

    def fake_get_llm_client(*, model=None):
        requested.append(model)
        return sentinel

    monkeypatch.setattr("app.agents.base.get_llm_client", fake_get_llm_client)

    writer = Writer()

    assert writer.llm is sentinel
    assert requested == ["deepseek-v4-flash"]
