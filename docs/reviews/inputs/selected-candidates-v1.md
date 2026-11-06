# Selected calibration candidates v1

This is the worker-input manifest for the three selected calibration candidates. It does not authorize a run and does not freeze A/B/C host configurations or numeric budgets. Never provide the evaluator-only oracle files or public PR/diff references to workers.

## FIX-1 — coupled defect repair

- Repository: `carpe-horam/task-progress-codex`
- Base commit: `8fb28a063d3fa9f5e14e217f8312c88ce9196d70`
- Request: [`fix-1-user-request.md`](fix-1-user-request.md), SHA-256 `5c60469aa151147d1513bfbb9538fed7ebfef63fd12928eefd659e9684225bf0`
- Initial state: [`fix-1-initial-state.json`](fix-1-initial-state.json), SHA-256 `ec52ee606fb27993a243fb2f370f5a833ce6024ee697c1f4948d83679a39a527`; archive-derived reconstruction, not an original service response
- Evaluator-only oracle: `../evaluator-only/fix-1-oracle.md`, SHA-256 `0aa97188d336964f6b516d7fca0a6422aacfad476d5ec1b9eae0eace2f297226`
- Worker deliverable: a focused patch, production-path regression evidence, and a short change summary; no release or remote change
- Evaluation scope: same-instance main-session serialization, queue/independent assignment, atomic concurrent starts, independent subagent parallelism, truthful attempt lifecycle, actionable waiting display, and production-path tests
- Historical limitation: the original task instance ID is absent from the archive; the frozen state is reconstructed from attempt timestamps

## INV-2 — evidence-bound investigation

- Repository/source baseline: `carpe-horam/task-progress-codex` at `a5202646591d725ce0e41d4b729f40397eed5be2` (v0.2.15)
- Request: [`inv-2-user-request.md`](inv-2-user-request.md), SHA-256 `d00cad84341ead977dcde2686433de1d345c11abbdecbb7e6bb92673133e35f3`
- Evidence packet: [`inv-2-evidence.md`](inv-2-evidence.md), SHA-256 `cc98abf2fe72986b9b66de79b8547e6e340edbfa594f34ccfb9c0979a56f5538`
- Evaluator-only oracle: `../evaluator-only/inv-2-oracle.md`, SHA-256 `afc98812e88608bc14ef8de400c199d009c8e2d40003750871ec65e08101540c`
- Worker deliverable: a concise explanation of the exact-set contract, source-level validation order, safe recovery steps, and what can/cannot be attributed from the evidence
- Evaluation scope: rule semantics and attribution discipline only; no code change is required
- Historical limitation: the original failed call payload, exact tree, binding/fingerprint and raw response are missing; this task cannot score identification of that incident's exact cause

## MOD-1 — multi-module compatibility change

- Repository: `carpe-horam/task-progress-codex`
- Base commit: `050dd3175e0e4dde9b6cb09da9bcd1f51c173b87`
- Request: [`mod-1-user-request.md`](mod-1-user-request.md), SHA-256 `55d9b4d840109aef4dafbae2055502232895c2bea0f744aafdc3f5da71fb32b8`
- Compatibility input: [`mod-1-legacy-fixture.json`](mod-1-legacy-fixture.json), SHA-256 `1c24c2e23e86ba1bfabda0e0f967eaa32f94646855cf1de99e9b51bbdac65c24`; synthetic fixture derived from the old node schema, not recovered historical storage
- Evaluator-only oracle: `../evaluator-only/mod-1-oracle.md`, SHA-256 `9ccc79031854b21169dc434cc3b774d931d211a982bdfe6ce690be7fba578113`
- Worker deliverable: a focused patch and regression evidence across server, UI, and HTML/XLSX export; no release or remote change
- Evaluation scope: retire the manual unknown/status mechanism, preserve ordinary graph dependency behavior and historical records, and keep current UI/export presentation accurate
- Historical limitation: the original stored session is not available; compatibility is judged only against the frozen synthetic fixture and source contract

## Shared isolation and blind-input procedure

1. For each run, materialize a new isolated workspace at the exact candidate base commit. Capture commit ID, repository status, input hashes, and the starting file manifest before providing the request.
2. Run A, B, and C serially. Preserve each run's complete workspace/artifact manifest before resetting; never reuse an agent context or patch across groups. If a reset cannot be proven clean, mark that run contaminated and do not overwrite its evidence.
3. Give each worker only its task section's request, source baseline, and listed fixture/evidence packet. Do not provide oracle files, public PRs, implementation diffs, prior assistant conclusions, or other groups' results.
4. At completion, capture the final source/artifact hash before independent evaluation. Bind every oracle verdict to that exact hash.

## Freeze gaps

- **Host readback (2026-11-07):** `codex plugin list --json` reports `task-progress@task-progress-codex` v0.2.20 installed/enabled from the local marketplace. The Task Progress service reports installed/loaded/healthy v0.2.20. Loaded release `server.py` SHA-256: `ce904cc9d01894f9be04bd40aeac19f24ac5c488c4956e03ee6bc97cee98da01`; installed plugin manifest SHA-256: `188a5bce9fcbeda926ba9c5f6a927934ee78efbb3d41122e9d9f3e8f5d889896`; current `track-progress/SKILL.md` SHA-256: `1d359d3801d57c5adad0fc814da26d7adaa5c3b94f86b5e38d3cb7afe12ec22a`; `delegation.md` SHA-256: `9bd5f0fec22f01b46dc01fc415b3da3e406c859533b09dd29978c175687120c6`; `htn-dispatch.md` SHA-256: `f63689adfa0c912384964a78ab531fb5893fee92b71714eae26d72833de0c211`. Local service release cache contains v0.2.19 (`server.py` SHA-256 `d8787370aa1409467cd5fa79637b894a89e4f714742bd4e8d99a19115b2934d1`) and v0.2.20, but v0.2.19 is not enabled in the current Codex plugin catalog.
- **A/B/C feasibility:** C can target the currently loaded v0.2.20 host, subject to an independent run reset. A single-executor policy can be requested in this host, but Task Progress tools remain visible, so strict tool-isolated A is unverified. B can target cached v0.2.19 source, but no evidence shows per-thread/plugin version switching or a second isolated Codex host. Do not treat an instruction to ignore tools as equivalent to an isolated host configuration. A read-only CLI probe confirmed `CODEX_HOME` can select a separate empty plugin catalog (`codex plugin list --json` returned no installed/available plugins); the isolated home reports `Not logged in`, while the current home is authenticated. No credentials were copied. This proves catalog-directory isolation only, not an executable independent experiment host. The service manager source also uses a fixed port `53421` and LaunchAgent label `io.task-progress.mcp`; concurrent per-home service isolation has not been verified.
- A/B/C actual context policy, model/effort, actor settings, tool visibility, plugin/skill hashes, and proof of isolated configurations: not frozen. Any unsupported group must be recorded as `unsupported` and excluded from controlled A/B/C comparison, not silently approximated.
- Per-run and total wall-clock limits, retry policy and acceptance of missing token/cost as `unknown`: not confirmed.
- Independent reset snapshots and verification receipts for all nine runs: not yet produced.
- No calibration run has started. This manifest is a reproducibility aid, not a claim that S0 is execution-ready.
