"""Live-eval opt-in reads the same pydantic Settings as other project settings."""
from pathlib import Path
from agent.config import Settings


def test_live_opt_in_is_false_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("RUN_S25_01_LIVE", raising=False)
    config = tmp_path / "blank.env"
    config.write_text("", encoding="utf-8")
    assert Settings(_env_file=config).run_s25_01_live is False


def test_live_opt_in_reads_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("RUN_S25_01_LIVE", raising=False)
    config = tmp_path / "test.env"
    config.write_text("RUN_S25_01_LIVE=1\n", encoding="utf-8")
    assert Settings(_env_file=config).run_s25_01_live is True


def test_environment_overrides_dotenv(monkeypatch, tmp_path):
    config = tmp_path / "test.env"
    config.write_text("RUN_S25_01_LIVE=0\n", encoding="utf-8")
    monkeypatch.setenv("RUN_S25_01_LIVE", "1")
    assert Settings(_env_file=config).run_s25_01_live is True
