"""Common S25-01 report location.

Bare output names are saved under evals/results/s25_01/, independently of
the shell working directory. Explicit paths remain respected.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results" / "s25_01"

def report_path(output: str) -> Path:
    requested = Path(output).expanduser()
    destination = RESULTS / requested if requested.parent == Path(".") else requested
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination
