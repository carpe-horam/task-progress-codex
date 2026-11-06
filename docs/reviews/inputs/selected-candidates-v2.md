# Selected calibration candidates v2

Date: 2026-11-07

Status: execution snapshot frozen for the approved 9-round calibration. This version preserves the three selected task inputs from v1 and fixes a common Codex CLI host route for A/B/C. A/B/C are run serially in nine separate clean worktrees. No run has started at the time this manifest was written.

## Scope and immutable input source

- User-confirmed scope: FIX-1, INV-2, MOD-1; one run for each A/B/C combination, nine runs total. This does not authorize the later 36-round matrix.
- Input source: [`selected-candidates-v1.md`](selected-candidates-v1.md), SHA-256 `34280ae9f704a51b317c0c656df0383a4ef1a4a34c44191bd242decd07f64463`. Its task requests, base refs, fixture/evidence hashes, deliverables, and evaluator-only oracle hashes remain unchanged.
- Workers receive only the candidate's user request, the candidate's listed input fixture/evidence, the repository at its base commit, and the assigned A/B/C operating instructions. They do not receive evaluator-only oracle files, PR/diff references, previous assistant conclusions, or results from another run.
- The parent evaluator uses the oracle hashes recorded in v1 and binds each verdict to that run's final source hash.

## Common host and execution limits

| Item | Frozen value |
| --- | --- |
| Host | Codex CLI `0.166.0`, macOS `Darwin 27.0.0`, `arm64` |
| Requested model/effort | `gpt-6-luna` / `high`, explicitly read from current Codex configuration and passed to every run |
| Host configuration | `codex exec --ignore-user-config`; authentication remains in the existing CODEX_HOME, no credentials are copied |
| Context | A new non-resumed CLI execution for each run; no prior run transcript, workspace, or agent context is supplied |
| Sequence | Serial across all nine runs; each run starts in its own detached clean worktree |
| Per-run wall clock | 10 minutes from the candidate prompt becoming available to the executor |
| Total wall clock | 90 minutes for the nine runs |
| Retry | At most one setup/host retry before the candidate prompt is exposed. Once exposed, preserve that run's outcome; do not retry the task under the same run ID |
| Token/actual cost | `unknown` because the selected host does not expose auditable per-run token and cost receipts; do not estimate or substitute zero |
| Budget exhaustion | Preserve partial output and trace, mark `budget_exhausted`, and score unfinished required oracle items as not run |

The wall-clock and retry limits use the conservative default proposed during calibration planning after the 9-run count was confirmed. If the user supplies a different bound before a run starts, supersede this file with a new version and preserve v2 as the prior snapshot.

## A/B/C controls

All three controls use the same Codex CLI binary, requested model/effort, candidate task, base commit, per-run wall-clock cap, and independent final oracle. The treatment is the execution policy and Task Progress version.

| Group | Execution policy | Task Progress host | Skill/reference source | Isolation evidence |
| --- | --- | --- | --- | --- |
| A | One executor; no worker spawning; do not call Task Progress for coordination | No Task Progress MCP server. `--ignore-user-config --disable multi_agent` removes user plugin/MCP configuration and disables agent spawning for this CLI execution | No Task Progress coordination skill | CLI contract says `--ignore-user-config` does not load CODEX_HOME/config.toml; no plugin/MCP server is added. The current Desktop's tool visibility does not apply to this separate CLI host. |
| B | Current pre-optimization workflow: delegate when useful, manually bind owner and attempt, no S1/S2-specific workflow | Standalone Task Progress MCP stdio server from v0.2.19 release, source file SHA-256 `d8787370aa1409467cd5fa79637b894a89e4f714742bd4e8d99a19115b2934d1` | Track-progress skill, delegation, and HTN-dispatch references at source commit `ef7f37f1a3502f17c1c69aec95b801250a82b23d`; SHA-256 values `ef1ed7f6ec9e11301b26496d076f3f301e4d4cea21279ab18c6e708670ff99f3`, `0564bbaee387e1ba191cd3d10cafecbdbf2eeea540d60d6d10a253071f3c5cf0`, and `cd8d2a77aea21584d3ebd966def9d548060b7065671861ef8682186ed4383a7d` | v0.2.19 stdio MCP handshake and `tools/list` succeeded; 35 tools were returned. The Codex CLI accepts a direct `mcp_servers.task_progress` command pointing at this release server, avoiding global plugin changes and the fixed port 53421 service. |
| C | Optimized workflow: current S1/S2 rules and same-owner continuity; S3 automatic binding is not enabled because causal host evidence is missing | Standalone Task Progress MCP stdio server from v0.2.21 release, source file SHA-256 `ce904cc9d01894f9be04bd40aeac19f24ac5c488c4956e03ee6bc97cee98da01` | Installed v0.2.21 track-progress skill, delegation, and HTN-dispatch references; SHA-256 values `1d359d3801d57c5adad0fc814da26d7adaa5c3b94f86b5e38d3cb7afe12ec22a`, `9bd5f0fec22f01b46dc01fc415b3da3e406c859533b09dd29978c175687120c6`, and `ca3fae3950bcc8fdf83396cb76bfe7b9f70c66874391c4d1bf2689855b74cdf9` | v0.2.21 stdio MCP handshake and `tools/list` succeeded; 35 tools were returned. The Codex CLI direct MCP route will be used instead of the Desktop's globally shared service. |

For B and C, the direct stdio MCP command uses `python3` and the exact release `server.py` path shown above. Each run has its own Task Progress session identity; no state, attempt, or workspace is reused between runs. Actual model/effort is recorded from run metadata when available; otherwise it remains `unknown`, not inferred from the requested values.

## Prepared reset set

Nine detached worktrees have been materialized under `/private/tmp/task-process-s0-calibration-v2/worktrees/`. Each is based on the candidate's exact v1 commit, is clean, and has a matching JSON start record under `/private/tmp/task-process-s0-calibration-v2/results/` with the HEAD, porcelain status, and tracked-file manifest hash. A run must recheck its start record immediately before exposing the prompt and capture the final source/artifact manifest before evaluation.

| Run | Base ref | Start record |
| --- | --- | --- |
| FIX-1-A | `8fb28a063d3fa9f5e14e217f8312c88ce9196d70` | `FIX-1-A.start.json` |
| FIX-1-B | `8fb28a063d3fa9f5e14e217f8312c88ce9196d70` | `FIX-1-B.start.json` |
| FIX-1-C | `8fb28a063d3fa9f5e14e217f8312c88ce9196d70` | `FIX-1-C.start.json` |
| INV-2-A | `a5202646591d725ce0e41d4b729f40397eed5be2` | `INV-2-A.start.json` |
| INV-2-B | `a5202646591d725ce0e41d4b729f40397eed5be2` | `INV-2-B.start.json` |
| INV-2-C | `a5202646591d725ce0e41d4b729f40397eed5be2` | `INV-2-C.start.json` |
| MOD-1-A | `050dd3175e0e4dde9b6cb09da9bcd1f51c173b87` | `MOD-1-A.start.json` |
| MOD-1-B | `050dd3175e0e4dde9b6cb09da9bcd1f51c173b87` | `MOD-1-B.start.json` |
| MOD-1-C | `050dd3175e0e4dde9b6cb09da9bcd1f51c173b87` | `MOD-1-C.start.json` |

## Preflight receipts and remaining caveats

- Codex CLI and platform: `codex-cli 0.166.0`; `Darwin 27.0.0 arm64`.
- Current Codex configuration reports requested `gpt-6-luna`, effort `high`, and concurrency limit 6. Every run receives the same explicit model and effort; host-reported actual values must be recorded separately.
- `codex exec --help` exposes `--ignore-user-config`, `--disable`, `--enable`, `--ephemeral`, `--json`, `--model`, and configuration overrides. `codex mcp list` accepted a direct v0.2.19 stdio server override without changing the installed global plugin.
- Direct stdio initialize handshakes and `tools/list` calls succeeded independently for server versions 0.2.19 and 0.2.21, each returning 35 tools and the corresponding server version.
- Prompt files for all nine runs are frozen under `/private/tmp/task-process-s0-calibration-v2/prompts/`; manifest SHA-256 `8584f2ebf3da3eee33297272b765c0a1fb4c768d89dcb03cd015923eefa43f4b`. A content check confirmed none of the prompt files contains the `evaluator-only` path.
- The active Desktop remains on installed/loaded/healthy v0.2.21; none of the nine runs switches or stops that service.
- The three historical cases have known limits preserved from v1: FIX-1 state is archive-derived reconstruction, INV-2 does not contain the original failed payload/tree/binding, and MOD-1 uses a synthetic legacy fixture. These limits reduce attribution or historical fidelity; do not silently replace the chosen candidates.
- Status at freeze: configurations, prompt hashes, and clean reset worktrees prepared; the direct server protocol is verified, while Codex `exec` model-invocation/tool-visibility readback, run traces, final artifacts, and oracle scores remain pending. No calibration task prompt has yet been exposed.
