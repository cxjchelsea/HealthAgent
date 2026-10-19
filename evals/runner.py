import json
from datetime import datetime, timezone
from pathlib import Path

def run(input_path:Path, output_path:Path):
    cases=[json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    results=[]
    for case in cases:
        actual=case["input"].strip().lower()
        expected=case["expected_normalized"]
        results.append({"case_id":case["case_id"],"passed":actual==expected})
    output_path.mkdir(parents=True,exist_ok=True)
    (output_path/"metrics.json").write_text(json.dumps({"cases":len(results),"passed":sum(r["passed"] for r in results),"results":results},ensure_ascii=False,indent=2),encoding="utf-8")
    (output_path/"manifest.json").write_text(json.dumps({"runner":"deterministic_normalization_v1","executed_at":datetime.now(timezone.utc).isoformat(),"real_model_calls":0},indent=2),encoding="utf-8")
    return all(x["passed"] for x in results)
if __name__=="__main__":
    import sys
    ok=run(Path(sys.argv[1]) if len(sys.argv)>1 else Path('evals/datasets/smoke-v1.jsonl'),Path(sys.argv[2]) if len(sys.argv)>2 else Path('evals/results/smoke'))
    raise SystemExit(0 if ok else 1)
