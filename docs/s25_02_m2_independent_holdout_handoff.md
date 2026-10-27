# M2 independent holdout intake (reviewer handoff)

This is the S25-02 M2 shadow-only semantic/evidence holdout. It is **not** a
production authorization or M3 cutover.

## Roles and integrity

- Freeze the code candidate before the reviewer receives the classification
  contracts. The reviewer must not reuse or paraphrase the known AI84,
  predeclared or targeted A/B development cases.
- A reviewer who has not participated in M2 prompt tuning independently writes
  and labels >=60 cases, seals a canonical SHA-256 manifest, and records how and
  when it was produced. An unverified `source_kind` property is **not proof**
  of independence; verify reviewer authorship and no leakage out-of-band.
- Each case has: `case_id`, `prompt`, `expected_object`,
  `expected_operation`, `expected_disposition`, `critical`, and `facts`
  (containing at least `has_plan`). Keep prompts and labels private until the
  candidate is frozen.
- Include realistic Chinese/English rephrasings and negative/boundary examples
  spanning ordinary conversation, clarifications, task create, plan create,
  first-step execute, plan resume (including missing checkpoint), plan-step
  revision, independent tool invocations, Pending argument supply/cancel, and
  unrelated requests while Pending. Include safety-critical denial cases.
  Coverage and expected results must be reviewed independently.
- Do not change labels, thresholds, or failed cases after seeing results.
  A failed holdout becomes development evidence; a future independent gate
  needs another unseen cohort.

## File format

The root JSON object has `source_kind: "independent_reviewer_sealed"`,
`sha256_canonical_cases`, and `cases` (array, >=60). The hash is the SHA-256
hex digest of UTF-8 serialized `cases`, using
`json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`.
Do not put patient information, secrets, or real-world side-effect actions
inside the fixtures.

Example shape only (not an actual holdout case):

```json
{
  "source_kind": "independent_reviewer_sealed",
  "sha256_canonical_cases": "<64-digit digest of cases array>",
  "cases": [
    {
      "case_id": "REVIEWER_ASSIGNED_ID",
      "prompt": "REVIEWER_AUTHORED_TEXT",
      "expected_object": "none",
      "expected_operation": "answer",
      "expected_disposition": "safe_non_effect",
      "critical": false,
      "facts": {"has_plan": false}
    }
  ]
}
```

## Run (reviewer-sealed fixture supplied separately)

```powershell
cd D:\HealthAgent
git fetch origin
git switch eval/s25-02-m2-independent-validation-20261027
git pull --ff-only
git rev-parse HEAD
python -m pytest -q
python -m evals.s25_02_m2_independent_holdout `
  --cases ".\reviewer_m2_sealed.json" `
  --output ".\s25_02_m2_independent_result.json" `
  --timeout 90
```

The model is the configured local strong Qwen on the Ollama proxy. The runner
checks 2 full repetitions, >=95% each, 100% critical and no shadow Host authority.
`holdout_gate: PASS` still requires human source verification and separate
M2 decision. `m2_gate` remains HOLD and `m3_cutover` remains NOT_AUTHORIZED.
