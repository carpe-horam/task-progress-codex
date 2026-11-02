# External prerequisite review

`ready` remains the existing graph predicate: a pending leaf with satisfied task dependencies. Its explicit alias is `graph_ready`; UI wording is “依赖就绪 / 任务依赖满足”. It does not establish real-world executability.

Each HTN node independently projects `external_prerequisite_status`: `unknown` (including old nodes without records), `blocked`, or `satisfied`. Only an effective satisfied declaration is labelled “外部前置已确认”. `actionable.status=requires_on_site_judgment` remains separate: these records do not automatically verify authorization, hosts, devices, or other capabilities. External status never changes dependency satisfaction, auto-ready, execution outcomes, or root acceptance.

`set_external_prerequisite` requires `session_id`, current `task_instance_id`, `task_id`, `expected_epoch`, `expected_revision`, `status`, a nonempty `reason`, nonempty string-array `evidence`, and explicit `reviewer={execution_mode:"main_session",agent_name:"..."}`. Reviewer identity and evidence are caller-reported, not authenticated by the host. Any node execution state is permitted. A successful call appends an immutable `external_prerequisite_reviews` entry and advances revision. Stale identity/epoch/revision, invalid status, or missing reason/evidence/reviewer is rejected before writing.

Optional `source_attempt_id` must equal the node’s current attempt. Omit it only for a node-scoped review; it must not be used to imply current-attempt proof. When the node's attempt changes, an attempt-bound latest review projects `unknown` plus `external_prerequisite_stale=true`; its original status and evidence remain in the append-only ledger. A new review supersedes the projection without replacing history. Snapshots evaluate their own attempt and records.

Tree and task queries expose the separate state and latest reason/evidence/source. Full ledger reads use `get_progress(view="records",task_id=...,record_kind="external_prerequisite_reviews")`; UI history is loaded on demand. HTML and XLSX exports include graph readiness, external status and basis, reviewer, attempt source, and review history. Caller strings are inserted as text or HTML/XML escaped.
