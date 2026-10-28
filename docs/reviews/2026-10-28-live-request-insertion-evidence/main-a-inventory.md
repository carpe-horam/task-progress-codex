# 主任务 A 接口清单

主会话已在并行任务 P 验收完成后返回原实例，重新读取实际运行服务并逐项比较，22 个接口名称与必填参数和插入前一致。

| 接口 | 必填参数 |
|---|---|
| reassign_task | session_id, task_id, assignment, reason, reviewer |
| bind_task_agent | session_id, task_id, attempt_id, agent_name |
| review_task | session_id, task_id, attempt_id, decision, summary, evidence, reviewer |
| register_ui_view | session_id, view_id |
| check_ui_view | session_id, view_id |
| unregister_ui_view | session_id, view_id |
| init_progress | steps |
| init_task_network | task_title |
| decompose_task | session_id, task_id, tasks |
| update_task | session_id, task_id, execution_status |
| get_ready_tasks | session_id |
| rebuild_task_network | session_id, reason |
| update_step | session_id, step_index, status |
| get_progress | session_id |
| render_progress | session_id |
| save_export_files | files |
| clear_progress | session_id |
| route_request | session_id, task_instance_id, request_id, request_text, task_title, mode, reason, acceptance_criteria |
| activate_request | session_id, task_instance_id, request_id |
| update_request | session_id, task_instance_id, request_id, reason |
| cancel_request | session_id, task_instance_id, request_id, reason |
| update_task_context | session_id, task_instance_id, goal, constraints, acceptance_criteria, resume_note, reason |
