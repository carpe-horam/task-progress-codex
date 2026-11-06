# INV-2 evidence packet

This packet contains source facts for the investigation prompt in [`inv-2-user-request.md`](inv-2-user-request.md). It is intentionally bounded: the original failed call payload and exact source binding are not preserved, so this packet cannot establish the specific cause of that failure.

## Source baseline

- Repository: `carpe-horam/task-progress-codex`
- Ref: `v0.2.15` (`a5202646591d725ce0e41d4b729f40397eed5be2`)
- File: `plugins/task-progress/server.py`
- Git blob SHA-1: `9c7069785221adae85c30f2e07333f8e2b287b12`
- Relevant function: `review_stage_inventory`
- Canonical exact-set error text at this ref: `盘点必须恰好覆盖范围内当前全部未解决叶任务；请刷新任务树后重试。`

## Observed history

- The user reported that a `review_stage_inventory` operation in the referenced Issue #223 conversation encountered an error.
- The historical session archive later records an accepted inventory for task `e3_find_issue_only_p14_gate_path`, attempt `e9f5f11b-836b-45f2-967f-5e7d4e1ad0ab`, at `2026-11-04T15:09:16+08:00`, with 10 decisions and 10 target bindings. Inventory ID: `4e16efc7-dcc2-46a2-be70-e21dda0f8732`; source fingerprint: `e306061002545c519956450cbbea02287dd784ad0e92767cc77df1b293b2a239`.
- The archive does not retain the failed submission's task tree, submitted decision IDs, source fingerprint, or raw tool response. The accepted 10-item record is a later state and is not evidence of which item was missing, extra, duplicated, or stale in the earlier call.

