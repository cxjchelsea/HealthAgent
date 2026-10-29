"""M5 physical verifier gate tests. No synthetic report counts as live acceptance."""
import pytest
from evals.s25_02_m5_unified_physical import verify


def test_rejects_fake_provider_before_opening_database(tmp_path):
    report = {"schema": "s25-m5-unified-live-v1",
              "model": {"provider": "fake", "m5_unified_route_enabled": True},
              "verdict": "PASS", "records": []}
    with pytest.raises(ValueError, match="non_real_or_non_unified_report"):
        verify(tmp_path / "missing.db", report)


def test_rejects_incomplete_or_unpassed_real_report(tmp_path):
    report = {"schema": "s25-m5-unified-live-v1",
              "model": {"provider": "ollama_proxy", "m5_unified_route_enabled": True},
              "verdict": "PASS", "records": []}
    with pytest.raises(ValueError, match="unpassed_or_incomplete_live_report"):
        verify(tmp_path / "missing.db", report)


def test_rejects_wrong_server_flag(tmp_path):
    report = {"schema": "s25-m5-unified-live-v1",
              "model": {"provider": "ollama_proxy",
                        "m5_unified_route_enabled": False},
              "verdict": "PASS", "records": []}
    with pytest.raises(ValueError, match="non_real_or_non_unified_report"):
        verify(tmp_path / "missing.db", report)
