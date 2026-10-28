# 历史任务与 9 轮计划审计

审查对象：Codex `01a16cc6-d9c6-7c60-8600-19166106f7a5` 对应插件 session `e30c494a-948a-4dc5-be03-4f14246bcadc`。仅分析主会话提供的只读快照，没有修改目标进度、联系目标会话、验证业务代码或执行其测试。

源：`/tmp/task-progress-thread-audit-20261028/progress-snapshot.json`，SHA-256 `2ad80c68658800689263bd352d45bf49580ca986526f8a96fde7b86a28f2ce21`，state_revision=666，当前 revision=260，updated_at=`2026-10-28T10:06:40+08:00`。读取生成的 split_review_due 使用的时点约为 10:09:15（由 elapsed_seconds=1376 推得），不等同于 updated_at。

规范：工作区 `plugins/task-progress/skills/track-progress/SKILL.md` 与 `docs/responsibility-protocol.md`。补充证据：`history-inventory.txt`（全节点概览）、`history-evidence.json`（实例清单、当前九轮完整状态摘要、相关历史原节点和当前原节点）。下述 JSON 定位以源快照为准；`nodes[id=…]` 表示按 id 选择，历史轮次 nodes 是字典。

## 结论与分级

当前没有“总目标已经完成”的假终态：root-e8 为 pending，派生 root_status/status 为 awaiting_review，7 个新协议子智能体提交仍保持 outcome=null/review_status=pending，2 个任务 running，1 个叶任务 pending。completed=18/total=21 是执行结束数，不是已验收解决数；resolved_leaf_count=11。当前九轮与其余历史轮次均未发现同实例重用 task ID、无效依赖、当前 attempt_id 与最后 attempt 不一致、或 resolved 分组下仍有 unresolved 直接子任务的静态矛盾。

最确定的现存协议缺口是当前轮 11 个 main_session 叶任务的 planned_assignment 都没有自执行理由；启动记录补了理由，但四项镜像构建只以共享缓存/串行为由自执行，直接落入 skill 明确不接受的通用例外理由。历史还保留一个归档时仍 running 的 root、一个子任务重试后未失效/重验的已结束旧 root，以及多个同一延续目标被重新初始化为新实例的强信号。后两类是历史账本风险，不能据此声称当前新后端仍存在同样漏洞。

## A. 当前协议问题与待处理事项

### A1 — 当前轮计划缺少主会话自执行理由（明确 skill 不一致；启动后端字段齐全）

实例 `bb94ed82-aee5-44cf-8836-a22ee77cc239`，epoch=8。节点 `p6-amd64-image`、`p6-arm64-image`、`p6-final-validation`、`nginx-proxy-timeout-fix`、`rebuild-amd64-after-fix`、`rebuild-arm64-after-fix`、`smoke-rebuilt-images`、`write-local-delivery-inventory`、`p6-restore-drill`、`p6-evidence-rollup`、`remaining-runtime-ai` 均标 responsibility_version=1/responsibility_legacy=false，planned_assignment.execution_mode=main_session，而 planned_assignment.self_execution_reason 缺失。执行 assignment 均有理由，故不是“启动时未记录理由”的后端绕过。

时间覆盖：第一项 started_at=2026-10-28 09:16:22；最后一项=09:46:19。证据：当前 `/nodes[id=上述节点]/planned_assignment`、`assignment`。skill 明确要求“在计划和启动的 assignment 中填写非空 self_execution_reason”。风险是分工评估无法从计划记录独立恢复；不是旧存储兼容情形。

### A2 — 用串行/共享缓存作为主会话包办镜像构建的理由（明确 workflow 理由不足）

同实例 epoch=8：

| task_id | attempt_id | 时间（+08:00） | 启动理由事实 |
|---|---|---|---|
| p6-amd64-image | 9f9b6008-6932-4279-9a9c-b4c03b91f363 | 10-28 09:16:22–09:18:31 | Docker/build-cache 必须串行，使用用户本地 BuildKit caches |
| p6-arm64-image | 5ad64307-03ac-4212-ba1f-116961a943e3 | 09:18:43–09:21:38 | 共享缓存，必须在 amd64 之后，由 parent 串行执行 |
| rebuild-amd64-after-fix | f8fb1030-3917-4aae-a605-26f1f194c7a5 | 09:30:56–09:33:17 | 共享 BuildKit 缓存必须串行；先构建 amd64 |
| rebuild-arm64-after-fix | 4b8aab5f-843d-426b-8e11-01be4efde04b | 09:33:29–09:36:07 | 共享缓存需要串行，当前轮到 arm64 |

证据：对应 `/assignment/self_execution_reason`。skill 明确“复杂工作默认委派，与能否并行无关”，共享文件应单一 owner 串行交接，不能以依赖/冲突作为主会话包办通用理由。四项理由没有说明不可交接能力；快照也没有提供“只是短小低风险操作”的 complexity 判断。可认定记录理由不满足当前 workflow 要求；不能仅由快照证明这些构建实际复杂程度或环境是否另有不可交接限制。

`remaining-runtime-ai` 的理由“必须与其他改动集成后统一判定”也偏向集成通用理由，而任务实际包含专项真实 AI/Docker 验收；应区分主会话验收决策和执行验证。此项为需要澄清的职责风险，不与上述四项同强度定罪。

### A3 — 7 项待复核为正确保护，但后续成果已有语义依赖未入图（中风险，不能单凭快照断言冒用验收）

待复核节点及 submitted_at：p6-recovery-audit 09:18:31；openspec-remaining-audit 09:24:05；transport-acceptance-script 09:34:36；sdk-uploadable-samples 09:36:55；remaining-app-modes 09:51:52；sdk-optional-tools-audit 09:52:59；remaining-neutral-image 10:01:30。全部 2026-10-28 +08:00。当前 review=null、review_status=pending，子结果没有自动成为 resolved；这是协议正确行为，不能把未复核本身判为非法。

但 `p6-restore-drill`、`p6-evidence-rollup`、`p5-open-local-closures` 都 depends_on=[]，后续 `p6-evidence-rollup` 09:44:17 已 resolved 并引用 transport 脚本 exit 0 与 OpenSpec 勾选；`p6-final-validation` 09:37:47 启动理由写“两个并行文件分支已提交”。图中没有指向相应 subagent 提交/复核的依赖，后端无法据此阻止先消费后复核。若主会话独立检查/复跑了这些证据，可以成立，不能要求所有文本相关性都建依赖；快照未保存相应独立 review，故这里是“验收来源与语义依赖欠清晰”，而非已证明服务端放行 pending dependency。

`p6-final-validation` 的 evidence=[]，summary 中只有 Sumi run ID。叶任务此字段并非当前 protocol 强制，因此不判后端违规，但证据路径应在正式综合验收时补足。

### A4 — 两项 22 分钟拆分复核已到期，未见处理记录（当前待办，不能证明此前已收到提醒而故意忽略）

`remaining-vd-ownership` attempt `2bdde88e-5443-4059-8d1f-2730a159f5bd` 与 `remaining-runtime-ai` attempt `3e169eab-b6a3-4eaa-b44f-dcfab9c0d714` 都在 2026-10-28 09:46:19 启动；split_review_due 显示 1376 秒/22 分钟。前者摘要仍为“开始执行；先核对…”无阶段进度/不拆理由，后者摘要已有 Docker smoke 与真实 AI 未验边界，但未写拆分复核理由。按当前 skill，应在下一次常规工作或等待前处理拆分或记录明确不拆理由。

这两项是真实后台工作还是遗留 running，快照不能认证。前者有 caller-reported agent_name/dispatch.bound；后者 Docker 临时容器已清理、无活跃运行 ID，仅写“保持此任务运行以继续处理 authStatus=unknown”。若主会话仍直接推进真实 AI，是合法 running；若已经转去兄弟任务且没有独立进程继续，则应 finished/open 后新 attempt 恢复。不得仅凭缺进程 ID就断言正在虚报运行。

### A5 — 当前轮重复 SDK 4.2 目标（明确计划冗余，尚无重复执行证据）

`remaining-sdk-optional-tools` 仍 pending、depends_on=[remaining-neutral-image,remaining-app-modes]，planned dispatch 明确“只负责 OpenSpec…4.2”。`sdk-optional-tools-audit` 同轮 09:51:35–09:52:59 已完成同一 OpenSpec 4.2 审查/必要实现，提交说明当前代码已覆盖、无需改动，仍 awaiting_review。两节点均挂 root-e8，前者未被替代/取消/解释保留的新增范围。

风险：验收该 audit 后前者仍可能 ready 再派发，重复同一目标；图中前者依赖镜像和 app-modes，而已实际执行的 audit 不依赖它们。如果确为拆出独立审查，应明确前者剩余实现/外部消费验收边界；目前 dispatch 文本高度重合，不能把 planned agent ID 当真实派发，前者 attempt=0。

## B. 历史账本问题（保留事实，不追认新协议）

### B1 — 归档保留一个 running root（明确遗留状态；旧模式/身份字段不追责）

实例 `53b327cb-64e7-4387-a000-62eec9e6c059`，epoch=0，root attempt `3916acd9-ebce-4459-a836-1c857ef78b01`，started_at=2026-10-26 12:29:34，archived_at=13:49:38。root.execution_status=running、finished_at=null；其 7 个子节点中 acceptance=finished/open、docs=pending。root 摘要仍是开工说明，没有外部运行对象。

证据：`/task_history/4/nodes[id=root]`；该历史摘要 root_status=executing。归档保存了当时执行状态，不代表现在真的仍有后台进程；但无法判断旧尝试何时停止、由谁接管。同实例 root running 又有 children 在当前协议不合法；此处早于新 responsibility 记录，作为旧流程遗留，不指控当前后端允许分组 running。

### B2 — 旧叶重试后祖先验收未失效（明确陈旧历史验收；结果仍 open，未错误冒充 resolved）

实例 `fc1b85b1-4d21-4af0-b494-8e7ab2dd11c9` epoch=0：root 在 2026-10-26 10:56:32 finished/open；子节点 verify 的第 3 次 attempt `3cecef84-3ee8-41de-a110-ea8fabbb4960` 于 11:04:32 启动、11:05:52 finished/open。root.finished_at 和根 summary 未重新综合验收、没有 invalidated_by，晚于根验收的子结果仍挂在已结束根下。证据：`/task_history/3/nodes[id=root|verify]` 与 verify.attempts。

当前 skill 要求重试失效消费者及祖先验收。这里说明历史快照存在该缺口；因为根始终 open，不影响“总目标未完成”的诚实性，但根结果的证据时序不完整。其字段没有 responsibility_version/review，不能倒推出当前 retry API 仍接受相同操作。

全量扫描没有发现“当前轮重试上游后仍保留已结束直接消费者”的静态例子，也未发现 current invalidated_by；absence 不能证明实时失效逻辑正确，只说明该记录不能提供触发验证。

### B3 — 多次初始化疑似代替同一目标重规划，且部分旧根未综合验收（强信号，需主会话结合原始用户边界确认）

2026-10-26 的下列切换保存为 task_history 新实例，而非同实例 history 新 epoch：

- `53b327cb…` → `17a1ec2e…`，13:49:38；标题从“实现应用上传部署…”到“继续落实应用自治架构…”，旧根仍 running。
- `17a1ec2e…` epoch=4 → `3f32b26c…`，17:58:20；后者标题自称“第6轮”，前者 root-e4 仍 pending/waiting_synthesis，所有 5 个直接子任务已 resolved。
- `3f32b26c…` → `5e9838c9…`，18:01:21；新标题“第7轮”，旧 root 仍 pending/waiting_synthesis、3 个直接子任务 resolved。
- `5e9838c9…` → `ee4e27dc…`，18:17:42；新标题“第8轮”。
- `ee4e27dc…` → `321b3726…`，18:56:50；旧根 pending/blocked，新增“继续实现…剩余能力”。
- `321b3726…` epoch=1 → `17588f9e…`，19:17:50；旧根 pending/waiting_synthesis、4 个直接子任务 resolved。
- `17588f9e…` → `44ba5065…`，19:20:41；两者均承认整体应用自治目标尚 open，后者是恢复验收的继续切片。

skill 区分“新目标初始化归档”和“原目标继续/阶段切换先恢复并重规划”；这些标题、轮次和连续时间强烈表明同一工作被拆成了多个任务实例，削弱目标完整性和根验收连续性。但初始化也可以合法开始用户明确的新目标，本快照不含逐条用户指令，故不逐个武断判错。尤其独立的“修复 Visual Diff 离线报告包…”有清晰新交付物，不能只因同项目而判重复。

### B4 — 旧 subagent→main_session 重试接管是兼容历史风险，不能自动判为新协议违规

典型：实例 `ee4e27dc-1dbd-4780-9505-85ab73a3faff` epoch=0 中 sdk-audit、visual-diff-audit、proxy-tests-audit 都先 subagent finished/open（18:18:23–18:22:27），再 main_session retry 后数秒 resolved（18:22:51–18:22:55）。当前实例 epoch=4 runtime-host-acceptance、epoch=7 transparent-proxy/custom-model-consumer 也保存旧角色接管尝试。

旧记录没有 planned_assignment/reassignment_history/review contract，新的责任协议明确“Existing completed/archived results remain readable without inventing an acceptance record”。不能把无 review 直接判成子 agent 自验，不能回填虚构 review，也不能用现在的 reassign/retry角色约束无条件追诉旧 API。应保留“主会话接管及独立验收来源不完整”的不确定性。

## C. 九轮重规划核对

当前实例固定 `bb94ed82-aee5-44cf-8836-a22ee77cc239`，epoch 0–7 存在 history、当前 epoch=8；root id 实际保存为 root/root-e1…root-e8，未推算 ID 写回。所有被关闭轮次都没有 running 节点，符合“重规划前结束运行尝试”的静态要求；快照不能证明外部执行者已实际停止。

| epoch | closed_at（+08:00） | 节点数 | 根结果/变化 |
|---|---|---:|---|
| 0 | 10-27 09:09:40 | 4 | root finished/open；Sumi ask-human 阻塞，真实环境仍未验 |
| 1 | 10-27 09:17:10 | 8 | root pending；用户明确暂停 Sumi，已完成格式/build证据保留，余下lint/gate未执行 |
| 2 | 10-27 09:33:06 | 7 | root-e2 finished/open；分组旧执行取消后 synthesis，真实集成留后续 |
| 3 | 10-27 09:42:18 | 4 | root-e3 finished/open；本机/mock消费者与真实环境边界明确 |
| 4 | 10-27 09:58:33 | 4 | root-e4 finished/resolved，仅本机集成小目标，summary 明确整体自治仍开放；非总目标完成证明 |
| 5 | 10-27 11:19:56 | 4 | root pending；visual-diff-agent-loop cancelled，按用户要求拆大任务；不冒充完成 |
| 6 | 10-27 14:24:13 | 5 | 4叶resolved、root pending；结束叶任务后直接重规划，缺本轮根synthesis，但未假resolved |
| 7 | 10-28 09:15:59 | 32 | root pending；VD真AI/旧历史对账open，recovery-release-delivery pending；重规划说明外部阻塞并切发行边界 |
| 8 | 未关闭 | 23 | 21叶：11 resolved、7 awaiting_review、2 running、1 pending；root pending/派生awaiting_review |

epoch 6 的“叶全结束但无根验收”与多个归档 waiting_synthesis 一样，是真实未验收状态；不能从百分比或叶计数推断已交付。epoch 7→8 重规划保留了真实 AI/旧任务对账 open 的说明，未抹掉历史 open，但当前根标题缩窄为本机发行交付，正式最终回复仍应提醒原大目标未完成，不能把发行完成误写成整体应用自治完成。

## D. 覆盖、依赖与证据局限

- 覆盖全部 15 个 task_history 实例、其中全部历史轮次，以及当前实例全部 9 轮；完整清单见下表/附件。总共 32 个计划快照。
- 所有有效依赖在各轮 nodes 内可定位，未发现同轮不存在的前驱，依赖/祖先依赖检查未发现当前保留的开始时间早于前驱最新 finished_at；结构静态无跨轮复用 task_id。未调用后端试验拒绝路径。
- 旧审查结果常无 review/dispatch/planned_assignment，按 legacy 未记录处理；model/agent/execution_mode 全是调用方声明，不把其存在作为真实派发、宿主身份或实际模型证明。
- 未读取业务源码、Docker、真实数据、密钥或外部运行状态。报告只判断进度账本与规范一致性，不证明业务实现/测试结论真实。
- 快照只保存当前节点/attempt摘要，不能证明每次 get_ready_tasks、render_progress、start→spawn→bind 调用顺序，也不能证明用户是否明确启动了新目标；这些需原始会话工具调用补充。本报告不因缺调用日志判其未执行。
- 不应“修复”旧历史补造验收；后续当前会话可复核 pending 提交、消除重复SDK节点、处理拆分复核、补实质依赖和主会话例外理由，并在最终综合验收中引用历史来源/范围。

## E. 15 个归档实例完整索引

| # | task_instance_id | 标题 | 最后epoch | 归档时间（+08:00） | 状态 |
|---|---|---|---:|---|---|
| 0 | b776701c-e441-47be-8f47-f63c5a1f175b | 修正远程主机 Agent 认证状态显示 | 0 | 2026-10-26T09:11:47+08:00 | completed/resolved |
| 1 | ddd9ba19-a1a9-4def-aaeb-250a0426e69f | 启动 AIGC Eval 并验证远程主机功能 | 0 | 2026-10-26T09:14:55+08:00 | pending/blocked |
| 2 | 6ac783d4-8a99-4e11-a3a6-bb1a4a07026c | 暂停 Sumi 并提交 AIGC Eval 改动 | 0 | 2026-10-26T09:33:56+08:00 | completed/resolved |
| 3 | fc1b85b1-4d21-4af0-b494-8e7ab2dd11c9 | 完善并验证远程主机全链路功能 | 0 | 2026-10-26T12:29:00+08:00 | open/open |
| 4 | 53b327cb-64e7-4387-a000-62eec9e6c059 | 实现应用上传部署与启动后远程连接 AIGC Eval | 0 | 2026-10-26T13:49:38+08:00 | pending/executing |
| 5 | 17a1ec2e-39ef-4514-babe-20d3e889cc68 | 继续落实应用自治架构：上传部署、连接生命周期与外部应用验收 | 4 | 2026-10-26T17:58:20+08:00 | waiting_synthesis/waiting_synthesis |
| 6 | 3f32b26c-6d48-4e94-90c8-2bda909e1470 | 修复上传应用恢复启动与关闭竞态，并继续应用自治验收（第 6 轮） | 0 | 2026-10-26T18:01:21+08:00 | waiting_synthesis/waiting_synthesis |
| 7 | 5e9838c9-be54-4892-b160-dc443596e5b1 | 补齐远程应用 Connector 身份撤销，并验证连接生命周期（第 7 轮） | 0 | 2026-10-26T18:17:42+08:00 | completed/resolved |
| 8 | ee4e27dc-1dbd-4780-9505-85ab73a3faff | 收敛应用远程透明代理并验证真实入口（第 8 轮） | 0 | 2026-10-26T18:56:50+08:00 | pending/blocked |
| 9 | 321b3726-646a-40df-9cd1-aa1ffdbeb199 | 继续实现应用自治与远程调用剩余能力 | 1 | 2026-10-26T19:17:50+08:00 | waiting_synthesis/waiting_synthesis |
| 10 | 17588f9e-77fb-444d-bef5-2ee221964e23 | 继续补齐应用自治与上传部署的真实服务入口验收 | 0 | 2026-10-26T19:20:41+08:00 | open/open |
| 11 | 44ba5065-b28c-4177-b17f-cb614e6f86fd | 补齐上传应用平台重启后的业务数据恢复验收 | 0 | 2026-10-26T19:22:54+08:00 | open/open |
| 12 | e9c66a44-b789-4ce5-b268-d4103034c063 | 修复 Visual Diff 离线报告包遗漏 AI 结果 | 3 | 2026-10-27T00:46:11+08:00 | completed/resolved |
| 13 | af15f64b-287d-4adc-9916-097aabd69c06 | 继续实现 application-owned-execution 的部署升级与能力调用缺口 | 0 | 2026-10-27T08:27:35+08:00 | completed/resolved |
| 14 | fe31a825-c18b-4c57-b956-3610f44f27d7 | 继续实施应用自治执行方案的 Runtime、部署与 Visual Diff 缺口 | 0 | 2026-10-27T08:58:30+08:00 | completed/resolved |
