# Task Progress 状态与职责合同审计

对象：`/Users/machengling/.codex/worktrees/3e36/task-process` 当前源码及 `progress-snapshot.json`。目标 session `e30c494a-948a-4dc5-be03-4f14246bcadc`、instance `bb94ed82-aee5-44cf-8836-a22ee77cc239`、state_revision=666、epoch=8。源码身份见 `contract-source-identity.json`。

本审计只读目标快照与源码。复现使用临时目录导入 backend 与精确抽取的 app 纯函数；没有连接共享服务、启动目标真实任务、修改插件/安装/提交或联系目标会话。所有写入限 `contract-*` 文件及已删除的隔离临时目录。先读取 dagu-test-runner；本次短小隔离检查未命中白名单，按本地路由执行。

## 已确认缺陷

### P2：HTML 导出将“全部执行已结束但待逐项复核”误写成“待汇总验收”

位置：`plugins/task-progress/app.html:984-990`，尤其 990；相关计数 `app.html:781-786`。

`delivery` 在 root 未 resolved/open 时，仅以 `currentStats.ended === total && open === 0` 判定“待汇总验收”。subagent submission 的 executionStatus 已为 finished、outcome=null，因而两个甚至全部叶节点还处于 `reviewStatus=pending` 也满足此条件。这把**逐项独立 review 尚未完成**误写成**已具备父/root 综合验收条件**。

隔离复现（`contract-probe.py` 创建 2 个实际 backend 子任务，running→bind→finished resolved，只写临时 business state）：后台 status/root_status 均 awaiting_review；2/2 执行结束、0 resolved、2 pending_review。用原 app 纯函数生成 HTML 后，计划状态仍“待主会话验收”，但“最终交付”显示“待汇总验收”（`contract-export-evidence.json`、`contract-all_pending-export.html`）。

实际影响：离线报告头部与明细/后台操作门禁冲突，会将下一步误导到 synthesis。后端仍正确拒绝父/root 提交，**没有绕过验收或误放行依赖**。当前目标有 2 running +1 blocked，HTML 为“尚未完成”，尚未触发此缺陷；当余下工作都提交后可能触发。建议以 pending-review 优先，再判断综合验收资格；保持 ended 统计继续包含 submission。

兼容风险：修复应只影响标签推导，不将旧结果补写 accepted、不改变旧记录 outcome、不把执行结束计数改成已验收计数。当前测试 `tests/test_responsibility_app.cjs` 的 worker pending 场景仍有 consumer 未执行，断言“尚未完成”，未覆盖所有叶节点均待验收的边界；无需改后端合同。

## 当前状态判断：未发现汇总/依赖错误

- 21 是叶任务数；root 与有孩子的 `p6-delivery-inventory` 不计入分母。
- 18 执行结束 =11 main_session resolved +7 subagent submission pending；18/21 向下取整 85%。`completed` 的字段名有历史包袱，但 UI 明示“项执行结束”，合同也明确允许计入 pending submission（`server.py:717-742`；`docs/responsibility-protocol.md` Result submission and review）。不能将 18 解读成 18 已验收。
- 11 resolved 叶节点均声明 main_session，review_status=None 是直接执行合同，不是漏 review。7 个 subagent submission 的 node outcome 均为 null，原提交 outcome=resolved 保存在 submission；没有混入 resolved 数。
- root 与 overall awaiting_review 优先于 2 running 是当前明确的聚合顺序（`server.py:688-706,745-756`），不表示正在运行的任务已经停止。executing_count=2 与 split_review_due 同时保留。
- `remaining-sdk-optional-tools` 的两个前驱 `remaining-neutral-image`、`remaining-app-modes` 均 pending-review；`server.py:633-649` 明确阻止 resolved 与 finished 两种依赖策略在 review 前放行。ready=0、blocked=1 正确。
- 对**仅内存克隆**接受这 7 个提交后，resolved=18、pending_review=0、ready=1、blocked=0、root_status=executing、overall=in_progress；唯一 ready 正是 `remaining-sdk-optional-tools`。这是预期门禁，不能报告为依赖死锁。该计算不代表任何目标任务已被实际验收。
- 同快照用当前后台重算，completed/total/percent/resolved/pending-review/executing/blocked/ready/status/root_status 全部与捕获数据完全一致，见 `contract-backend-evidence.json`。
- 目标历史共有 8 个计划快照、15 个已归档任务；历史均无 pending-review。旧验收信息未记录不等于 rejected，也不应追溯伪造 accepted。当前 backend 对旧 pending 明确要求 reassign、旧 in-flight 允许恢复后提交 review；测试覆盖在 `tests/test_responsibility_contract.py:338-374`。

## 验证与限制

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_responsibility_contract.py -q`：24 tests passed（精确运行日志见 `contract-responsibility-tests.log`）。覆盖计划角色、绑定、submission/review、两种依赖政策、不可跳过 review、重试失效、父/root 门禁和 legacy 读取/恢复。
- `PYTHONDONTWRITEBYTECODE=1 python3 /tmp/task-progress-thread-audit-20261028/contract-probe.py`：通过。
- `node /tmp/task-progress-thread-audit-20261028/contract-export-probe.cjs`：通过，使用 exact-source 纯函数生成实际/边界离线 HTML；不运行 DOM、浏览器、共享 HTTP/MCP 或保存 handler。此验证可证明导出标签和计数，不能证明截图布局或宿主交互。
- 快照 SHA256：`2ad80c68658800689263bd352d45bf49580ca986526f8a96fde7b86a28f2ce21`；复现前后目标快照 bytes 不变。
- 没有核验目标会话中 evidence 指向的业务代码/产物是否正确，不对用户任务实际交付作接受判断；这由主会话独立验收。
