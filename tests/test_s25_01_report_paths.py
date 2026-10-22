"""S25-01 file location contract: default paths managed, explicit paths respected."""
from pathlib import Path
from evals.s25_01_paths import RESULTS, report_path

def test_bare_filename_goes_to_s25_results():
    assert report_path("s25_01_live.json") == RESULTS / "s25_01_live.json"

def test_explicit_output_directory_is_respected(tmp_path):
    requested = tmp_path / "custom" / "report.json"
    assert report_path(str(requested)) == requested
    assert requested.parent.is_dir()

def test_result_location_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert report_path("s25_01_e2e.json") == RESULTS / "s25_01_e2e.json"
