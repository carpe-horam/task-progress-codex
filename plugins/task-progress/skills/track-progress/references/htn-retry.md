### 4. 重试与新一轮规划

- 叶任务以 `failed` 或 `finished/open` 收尾时，若原因明确，在 `update_task` 的 `failure_reason` 填写具体原因；不清楚时不要猜测。连续两次未解决后再次重试，回执的 `retry_review_due` 提供最近两次的原因和摘要，提示 agent 结合上下文判断是否为相同或相关问题，不依赖文字精确匹配。原因或摘要为空或被截断时，按需读取 attempts 记录；信息仍不足就明确不确定。若问题重复或相关，汇总已尝试方法及新增信息，判断是否拆分、调整依赖或验收、变更执行条件、寻求必要信息，或保留现状并说明理由。记录判断和下一步；提醒不会自动改任务或阻止重试。成功或取消的尝试中断连续计数。
- 重试已结束叶任务：先处理待复核提交，再用 `update_task` 传 `execution_status="running", retry=true` 及新的完整 assignment，执行角色须与保留的计划一致，保存新 `attempt_id` 并完成新的派发绑定；旧 submission/review 留在历史，当前 review 清空。旧尝试的迟到上报不能覆盖当前尝试。
- 每次重试都重新检查自身及祖先的依赖。如果有运行中的直接或传递下游仍依赖本次重试将使其失效的结果，先协调结束这些尝试，不能让旧下游与新上游并行。成功重试会使已结束的消费者（含依赖分组的后代）及其祖先验收失效，保留历史结果和 attempts，并恢复为 pending、标记 invalidated_by。再次读取 ready 列表，按依赖顺序重新执行或验收；旧 attempt_id 不再能更新这些节点。这里的状态失效不表示已实际派发重跑。
- 需要调整任务网络：先核实并结束所有 running 尝试，再调用 `rebuild_task_network({"session_id":"<本会话 ID>","task_instance_id":"<当前任务实例 ID>","reason":"<重规划原因>"})`。
- 重建会将当前计划轮次保存到当前任务的 `history`，递增 `epoch` 并返回新的 `root_id`；`task_instance_id` 不变，这仍是同一个任务。必须使用实际返回的 `root_id`，不要推算其名称。用当前任务实例尚未使用过的任务 ID 重新分解；同一 `task_instance_id` 的重规划轮次不能复用节点 ID。不要继续使用旧轮次的 root/task ID。
- 派发及协调时将 `session_id`、`task_instance_id`、`epoch`、`task_id`、`attempt_id` 与实际智能体对应保存；收到旧任务实例、旧 epoch 或旧 attempt 的结果时拒绝更新当前任务。历史结果可作为附有来源的参考证据，不能直接冒充新一轮验收结果。
