# Responsibility fix independent peer review — rounds 1 and 2

**Final round-2 conclusion (2026-10-28): both P1/P2 findings are closed on the final hashes below. No remaining blocking issue was found within this review scope; the change may proceed to the main session's final full-Python/native-MCP verification and normal delivery checks.** The first-round findings and hashes are retained below as historical evidence, not current failures.

## Round 2 — repair verification

The original `peer-probe.py` and `peer-ui-probe.cjs` were rerun against the owners' fixes. The backend probe was extended only to read the post-rejection snapshot and compare session bytes/revision, which does not alter product behavior.

- **P1 closed:** the original JSON-RPC call now returns `isError=true`; the worker stays `pending`, without an attempt/submission, and the consumer remains blocked (`ready_tasks=["worker"]`). Session bytes and state revision remain unchanged. Output: `peer-round2-backend.json`.
- **P2 closed:** both original known-main-role legacy records now yield `reviewLabel="验收未记录"`, `responsibilityCurrent=false`, and `reviewNotRequired=false`. The gate is shared by the app and offline report's empty acceptance history. Actual recorded historical synthesis remains a synthesis execution record with its original summary/evidence, without inventing an accept/reject decision. Output: `peer-round2-ui.txt`.
- Independently reran `python3 -m unittest discover -s tests -p test_responsibility_contract.py -v`: **24/24 passed in 0.186 s**. The four added tests exercise both main/subagent unstarted leaves and both outcomes (with/without assignments), preserve failed/cancelled scheduling cleanup, preserve started main `open`, and follow the public MCP path through bound subagent `open` submission/review and both dependency policies.
- Reviewed the final source-bound owner evidence in `ui/source-binding.json`, `ui/results.json`, `ui/validation.json`, and the added fixtures in `tests/test_responsibility_app.cjs`. The **7 final-source browser groups** cover app DOM, HTML, and XLSX, including known-role legacy leaf/group/attempt and actual historical synthesis, and assert no disk backfill. Start/end app/server/test hashes match the final files. Those browser groups were not redundantly rerun by this reviewer. Earlier 38 general UI groups predate the last P2 fix and are not described as final-source validation.

The repaired backend gate is limited to unstarted leaf `finished` and preserves the separate group synthesis path. The UI repair limits current-policy exemption/pending labels to new responsibility records while retaining explicit historical synthesis evidence. No new confirmed issue was found in these changes. Review remains limited to the documented public API contract and isolated test host; no actual Codex runtime identity authentication, true worker spawn, installation, or real-user-session write is claimed. The main session retains responsibility for its announced full Python run, native MCP discovery, and final delivery.

Final source identity (same baseline HEAD `52f7b19abc452ce1bf2aa7cd23485df7db2028f6` plus uncommitted changes):

| File | SHA-256 |
| --- | --- |
| `plugins/task-progress/server.py` | `f5efaf677f67ef88e013c5e66dcaf12ac351c2b05441a3f198fa3717cd98da22` |
| `plugins/task-progress/app.html` | `502d230623f2a1ef7af7348bcf254b9b6de4dd7229062daa81e184d5630be395` |
| `tests/test_responsibility_contract.py` | `16db5ada81604c0d0fbc8a5ef36c7bf2c1aacd48850cf54cad183c062675f9c8` |
| `tests/test_responsibility_app.cjs` | `07cd338f1b460f5f4df2aa180a7f4b7ffe2eee55b951bf651d278f7773eaff8b` |

## Round 1 — historical findings (both resolved above)

Date: 2026-10-28 (Asia/Shanghai). Version: 0.2.0. Baseline/HEAD: `52f7b19abc452ce1bf2aa7cd23485df7db2028f6` plus the frozen uncommitted responsibility changes. **Not ready to commit until the two findings below are resolved and rechecked.**

Only product source was read. The review wrote its own `peer-*` evidence files; all business operations used temporary home/session directories. No real progress session, installation, commit, push, or public service mutation occurred. The main session owns the real `peer_review` progress node.

## Findings

### P1 — unstarted delegated leaf can bypass submission/review with `finished/open`

Location: `plugins/task-progress/server.py:1189-1191` (also the submission branch at 1225). The leaf-before-start gate rejects only `outcome=resolved`. A new ready leaf planned for a subagent can instead call `update_task(execution_status="finished", outcome="open")` before any `running` attempt. Its current `assignment` remains `{}`, so the subagent submission branch is skipped. The persisted node becomes `finished/open`, with `attempt_id=null`, `submission=null`, and `review_status=null`; a consumer with `dependency_policy="finished"` immediately becomes ready.

This is a public-API workflow bypass independent of caller authentication: no false agent identity is needed. An open execution result still needs an execution attempt and, when delegated, main-session review. This does not concern legitimate pre-execution `failed/cancelled` scheduling cleanup.

Reproduce from repository root:

```sh
python3 docs/reviews/2026-10-28-responsibility-fix-evidence/peer-probe.py
```

Observed: `is_error=false`; worker `finished/open` with no attempt/review; `ready_tasks=["consumer"]`. Fix expectation: reject unstarted leaf `finished` for both outcomes, leave bytes/revision unchanged, and retain ordinary subagent `running→bound→finished/open→review_task(accept)` behavior.

### P2 — legacy main-session records acquire current-policy acceptance labels

Location: `plugins/task-progress/app.html:427,432,1158` and offline export consumers of `responsibilityFields`. For a completed legacy record with the already-recorded `assignment.execution_mode="main_session"`, but no `responsibility_version`, submission, review, or synthesis attempt, the display helper reports `主会话直接执行（无需独立提交复核）` and `reviewNotRequired=true`. A legacy completed group is labelled `待综合验收（须先完成子任务复核）`, while its empty review history claims main-session direct execution. The offline report uses the same `reviewNotRequired` flag for its empty acceptance history. Thus legacy execution role is being used to infer a current acceptance policy, contrary to the contract's explicit unrecorded legacy acceptance boundary.

Reproduce the actual pure helper extracted from current `app.html`:

```sh
node docs/reviews/2026-10-28-responsibility-fix-evidence/peer-ui-probe.cjs
```

Expected: legacy records without recorded acceptance consistently state `验收未记录` / `旧执行记录不代表主会话已接受`, regardless of whether the old execution role was known. The role itself can remain visible. This probe verifies display/export derivation; it does not claim a full browser capture. The existing browser legacy fixture clears `assignment` to `{}`, so it misses this known-role variant.

## Verified coverage and limits

Independent local command `python3 -m unittest discover -s tests -p test_responsibility_contract.py -v` passed all **20** tests in 0.142 s. The command is outside the Dagu whitelist and short, so local execution follows `dagu-test-runner` and the task authorization. Source review checked plan/attempt role separation, reassignment and immutable execution metadata; dispatch binding and stale-ID rejection; pending-review dependency gating; open acceptance; reject/retry and dependency invalidation history; group/root main-session synthesis, unresolved-child rejection; legacy read behavior; and app/HTML/XLSX independent review history derivation. No other confirmed product defects were found in this round.

Existing test coverage confirms pending submissions cannot be overwritten, retried, decomposed, reinitialized, cleared, or replanned; a pending consumer prevents producer retry; accepted-open remains open; root cannot resolve failed/open/pending-review children; prior submission/review/dispatch evidence survives retries; completed legacy reads do not rewrite session bytes. Passing these tests does not cover the two additional variants above.

Caller role, model/reasoning metadata, and agent IDs are explicitly caller-reported, without host authentication. Legacy linear compatibility is an accepted product boundary. Neither is reported as a new defect. This review did not spawn an actual runtime worker, install a plugin, modify a real session, or independently rerun the broader server/HTTP/state, workflow, and 43-group UI suites reported by the main session. Browser behavior beyond the reviewed existing evidence needs a fresh source-bound recheck after owner fixes.

## Source binding

Frozen inputs at round-1 review:

| File | SHA-256 |
| --- | --- |
| `plugins/task-progress/server.py` | `fc7cebd25f71090209784804b1ea504fe0ff80080087283e39a3e944fca08f6d` |
| `plugins/task-progress/app.html` | `ff1e8d830634148ff513952e043bf446c13178bb4cfdd14189948211d31413f8` |
| `tests/test_responsibility_contract.py` | `445dc52227ca03980232b356a166104d970fe17367c33cf19c43644a1b9e3461` |
| `tests/test_responsibility_app.cjs` | `0cd56d7a74d3fd3bc9bda61e73f79c980b3887cd281f0f94b5855369dec623ec` |

Reproducers intentionally print current behavior so they remain useful for owner fixes and follow-up review; the observations above belong to these frozen hashes. Product and tests were not edited by this reviewer.
