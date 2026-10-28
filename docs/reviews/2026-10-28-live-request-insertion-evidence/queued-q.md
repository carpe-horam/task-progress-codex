# 排队任务 Q：安装版恢复协议核对

读取时间：2026-10-28T12:29:19.207445+08:00（Asia/Shanghai）

实际读取文件：

- `/Users/machengling/.codex/plugins/cache/task-progress-codex/task-progress/0.2.0/skills/track-progress/references/request-routing.md`
- `/Users/machengling/.codex/plugins/cache/task-progress-codex/task-progress/0.2.0/skills/track-progress/SKILL.md`

| 规则 | 判断 | 真实行号与简短证据 |
|---|---|---|
| 独立请求排队 | 存在 | request-routing.md:9 明确独立目标暂不适合并行时使用 `route_request(mode="queue")` 并继续原任务；13 行规定资源冲突或无子智能体容量时排队；21 行规定原任务收尾后显式激活 FIFO 队首。SKILL.md:16、42 对应独立请求不能覆盖原任务。 |
| 可隔离任务由子智能体并行 | 存在 | request-routing.md:10 明确资源可隔离的独立目标建立 parallel 实例后规划并派发子智能体，登记不等于启动；20 行明确并行叶执行者为子智能体，主会话负责验收。SKILL.md:16、73 对应隔离实例与独立 ready 节点可并行派发。 |
| 插入任务完成后检查原任务并返回继续 | 存在 | request-routing.md:30–34 要求插入请求结束时读取当前主任务、task_context、session_work；原任务未结束按 return_to 和依赖继续，独立任务完成后再次检查而非直接结束整轮。SKILL.md:18、61 同样要求重新锚定当前主任务与剩余工作。 |
| 明确用户停止优先 | 存在 | request-routing.md:11 明确停止/取消/替换按用户指令协调在途工作，不得用队列抵消用户意图；35 行明确用户停止优先于继续队列。SKILL.md:18、286 同样规定用户停止时不强行继续。 |

四项均有明确文档依据，未发现这四项规则缺失。本检查只确认上述安装缓存文件的文字约定，不据此证明宿主运行时自动执行了这些规则。

获派状态已于读取文档前通过真实已安装 MCP 服务确认：实例 `697c19b5-5905-4237-8300-fb5221c8b19e` 为 active，节点 `check-recovery-doc` 为 running，当前 attempt 为 `c15a22a8-8092-45f9-9547-3f7ad0f71501`；Q 的 activated_from 为原任务 A，原任务 A 已 completed。

产出仅此文件；执行结果提交后须由主会话独立 review，本子智能体不验收 root。
