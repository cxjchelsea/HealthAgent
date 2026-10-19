import json
from pathlib import Path
from evals.runner import run

def test_smoke_runner(tmp_path):
    assert run(Path('evals/datasets/smoke-v1.jsonl'),tmp_path)
    assert json.loads((tmp_path/'metrics.json').read_text())['passed']==5
