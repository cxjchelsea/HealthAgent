"""Run existing LOCAL AI84 development suite and attach verified Git provenance.
Not an independent acceptance run; does not change labels or scoring.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

EXPECTED_OBJECT_BASE = "39fa7a4ec3221a4b23b6aa9c920735603304edf2"
EXPECTED_MANIFEST = "418408790481ff9c1e77542b9da7dd8cd740d2d7921a62016d7e5071b9129ed6"


def git(*args):
    return subprocess.check_output(["git", *args]).decode("utf-8", errors="replace").strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", default="s25_02_m2_ai_eval_runner.py")
    parser.add_argument("--cases", default="s25_02_m2_ai_eval_84_sealed.json")
    parser.add_argument("--output", default="s25_02_m2_original_object_84_regression.json")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--expected-branch", default="experiment/s25-02-m2-original-object-evidence-20261027",
                        help="Explicit branch under evaluation; default preserves frozen original.")
    parser.add_argument("--expected-sha", default=None,
                        help="Optional exact candidate HEAD; abort on mismatch.")
    a = parser.parse_args()
    sha = git("rev-parse", "HEAD")
    branch = git("branch", "--show-current")
    if branch != a.expected_branch:
        parser.error(f"wrong branch: {branch}")
    if a.expected_sha and sha != a.expected_sha:\n        parser.error(f"wrong HEAD: {sha}")\n    dirty = git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        parser.error("tracked working tree has uncommitted modifications")
    runner = Path(a.runner)
    cases = Path(a.cases)
    if not runner.is_file() or not cases.is_file():
        parser.error("local original runner and sealed cases must both exist")
    # Run the same sealed harness; do not edit case expectations.
    subprocess.run([sys.executable, str(runner), "--cases", str(cases),
                    "--output", a.output, "--timeout", str(a.timeout)], check=True)
    dest = Path(a.output)
    report = json.loads(dest.read_text(encoding="utf-8"))
    if not report.get("complete") or report.get("manifest_sha256") != EXPECTED_MANIFEST:
        raise ValueError("incomplete or mismatched known development report")
    report["legacy_reported_candidate_sha"] = report.get("candidate_sha")\n    report["evaluated_expected_sha"] = a.expected_sha or sha
    report["candidate_sha"] = sha
    report["git_branch"] = branch
    report["provenance_method"] = "git_rev_parse_HEAD_verified_clean_tracked_tree"
    report["evaluation_type"] = "KNOWN_DEVELOPMENT_REGRESSION_NOT_INDEPENDENT_ACCEPTANCE"
    report["m2_gate"] = "HOLD"
    report["m3_cutover"] = "NOT_AUTHORIZED"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"candidate_sha": sha, "branch": branch,
                      "scores": report["per_repetition"],
                      "failed_case_ids": report["failed_case_ids"],
                      "m2_gate": report["m2_gate"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
