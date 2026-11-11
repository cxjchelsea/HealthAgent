# S25-06 Slice E3 — real-model dependency-plan HTTP acceptance

Baseline: merged `main@4dcb57ceaa8f16e4a094eb351012527616dc7f71`. This is an **opt-in evaluation**, not an activated production feature.

> **Historical document / superseded operational instructions (Step 6):** The E3 branch and checklist below describe the original November 11 acceptance stage; PR #43 and PR #44 have since merged. For current re-validation switch to the **latest reviewed `main`** instead of the historical E3 feature branch. See `docs/S2.5/S25-06-STEP6-DEVELOPMENT-CLOSEOUT-20261111.md` for the immutable evidence record and `docs/S2.5/S25-06-INTEGRATION-HANDOFF-20261111.md` for the current test/activation boundary. The unchecked historical checklist is **not** a statement that E3 remains unaccepted.

## Scope

A fresh synthetic-owner conversation calls the **actual configured Primary real model** (no mocked proposal, no patched provider or response). The model must create a `dependency-plan-v1` graph from the request: a trusted local `mcp_reference_lookup` for the explicit literal `RAG`, followed by `text_stats` consuming the first step's checkpointed `definition` via a typed `observation_ref`. The original Host performs all admission. The HTTP calls are CREATE (no dispatch), FIRST (only source), and RESUME (only dependent target). Each step is independently verified against the durable Plan, PrimaryRouteEvidence, PlanToolDispatch, AgentAttempt, and Claim/Fence state. A model narrative claiming success is not accepted as evidence.

The script requires a real provider and a deliberate opt-in, generates an ephemeral development bearer token, rejects demo/anonymous auth and creates a temporary SQLite database. It neither mutates an existing database nor writes raw prompts, model answers, receipts, tool arguments, bearer tokens or MCP source text to the report.

## Windows PowerShell commands

From `D:\HealthAgent`, with the usual local virtualenv active and the authorized Ollama Proxy configured in your existing `.env` or session:

```powershell
git fetch origin
git switch feat/s25-06-slice-e3-real-qwen-acceptance-20261111
git pull --ff-only origin feat/s25-06-slice-e3-real-qwen-acceptance-20261111
$env:RUN_S25_06_E3_LIVE = "1"
$env:MODEL_PROVIDER = "ollama_proxy"
$env:MODEL_NAME = "qwen3:14b"
python -m evals.s25_06_e3_live --repeats 2 --output artifacts/s25-06-e3-live.json
```

Configure `MODEL_BASE_URL` locally for the authorized endpoint; never commit it or its secrets. The result is an isolated local artifact not required for CI. Repeat count is 1–5; recommended acceptance threshold is **6/6 stages (three stages × two independent conversations)**, all independently grounded in durable rows. The script exits nonzero on any failure and blocks dependent stages rather than generating false PASS results.

## Explicit gates and exclusions

- [ ] Full offline CI and PostgreSQL checks at exact E3 HEAD.
- [ ] Real `ollama_proxy/qwen3:14b` CREATE / FIRST / RESUME **6/6** (no fake, no proposed-route patch).
- [ ] Review source binding, signed/anchored evidence, task/receipt completeness and privacy of the exported report.
- [ ] Independently validate invalid graph, source spoof, default OFF and unknown tools against E1/E2 regressions.
- [ ] Separate PostgreSQL crash/restart and uncertain dispatch/commit disposition checks before marking the whole S25-06 complete.
- [ ] Explicit user approval for merge.

This harness uses trusted local MCP catalog, synthetic data and development bearer identity; it does not certify arbitrary external MCP, operator IAM, production clinical permissions, or external exactly-once delivery. CI tests for the harness are purely offline safeguards and are **not** a substitute for live-model acceptance.
