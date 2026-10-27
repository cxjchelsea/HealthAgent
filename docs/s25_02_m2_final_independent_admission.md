# S25-02 M2 — independent holdout final admission record

## Frozen classifier and scope

The **classifier/evidence candidate** is frozen at
`bbf748c97f457c49237d6f541bac4e7c20ea5c85` on
`fix/s25-02-m2-plan-boundaries-20261027`.
The separate `audit/s25-02-m2-independent-intake-20261027` branch
modifies only holdout intake/reporting and its tests/documentation.
Before accepting scores, audit `git diff bbf748c97f457c49237d6f541bac4e7c20ea5c85 HEAD -- agent/`
and `git diff ... -- evals/s25_02_m2_evidence_structured_live.py evals/s25_02_m2_plan_step_quotes.py`
to verify that the classifier and scoring implementation are unchanged.
Record *both* frozen candidate SHA and actual evaluation-runner SHA; do not
mislabel them as identical.

## Admission requirements: independent HUMAN reviewer

An external evaluator who did not author M2 classifiers, repair prompts, or
author the known AI84 and 72-case cohorts must:

1. Author and label >=60 **unseen** requests. Include ordinary dialogue,
   clarifications, task create, ordered plan create, execute-first, resume,
   revise, missing checkpoint, tools, bound Pending, unrelated Pending requests,
   cancel and prohibited/missing-proof effects. Include explicit negative
   and adversarial cases; cover each high-risk route, not just one token
   `critical=true`. Cases cannot be relabeled after unblinding.
2. Record their name/role (or independently auditable reviewer identifier),
   authorship date, collection method, conflict-of-interest/development-access
   attestation, and list of development datasets checked for overlap. Store
   this attestation **outside** the classifier repository if confidential.
3. Seal JSON `source_kind=independent_reviewer_sealed`, exactly a
   `sha256_canonical_cases` hash of canonical UTF-8 JSON `cases` and
   explicit Boolean `critical` on **every** case. Verify semantic diversity
   and lack of overlap by human review; hash inequality alone is insufficient.
4. Confirm `facts` represents a synthetic Host state and labels have
   independent review. Do not include real personal health information or
   actual side-effect actions.

## Execute

Use the separate audit branch with identical classifier code, on a clean
working tree. Seal cases outside git and avoid exposing holdout prompts to
developers before running.

```powershell
cd D:\HealthAgent
git fetch origin
git switch audit/s25-02-m2-independent-intake-20261027
git pull --ff-only
git status --short
git diff bbf748c97f457c49237d6f541bac4e7c20ea5c85 HEAD -- agent/
git diff bbf748c97f457c49237d6f541bac4e7c20ea5c85 HEAD -- evals/s25_02_m2_evidence_structured_live.py evals/s25_02_m2_plan_step_quotes.py
python -m pytest -q tests/test_s25_02_m2_independent_holdout.py
python -m evals.s25_02_m2_independent_holdout `
  --cases ".\reviewer_m2_sealed.json" `
  --output ".\s25_02_m2_independent_result.json" `
  --timeout 90
```

The runner reports `technical_holdout_gate` for the numerical and synthetic
boundary verdict. `holdout_gate=PENDING_EXTERNAL_PROVENANCE_REVIEW`
**cannot** mean M2 closed. A human gate decision requires the written reviewer
attestation, labeled-case coverage inspection, manifest integrity, comparison
against prior case corpora, CI, both repetition scores >=95%, all genuinely
critical cases correct, no Host effects (and *separate* existing physical Host
integration test evidence). No independent set has yet been supplied or run.

## Closure

Only after the evidence above is collected, independently reviewed, and
the frozen core version matches may M2 be assessed PASS/CLOSED and Step 6
documentation proceed. Neither a synthetic `host_authorization=false` value
nor two 100% development runs establish production Host authority safety.
M3 is NOT_AUTHORIZED and main merge still requires explicit permission.
