# DeepSeek Super-agent 编排研究笔记

日期：2026-11-06。用途：为 [Task Progress 优化方案](2026-11-06-agent-collaboration-optimization-plan.md)补充近期材料。

## 1. 定位到的文章与证据边界

文章是《Super-agent 设计探索：迈向更自主的 Agent 系统》，DeepSeek Harness 团队署名；英文镜像标注 2026-11-01，配套官方原型提交时间为 2026-10-31。英文文本注明为中文原文的机器翻译。

- [中文入口](https://mp.weixin.qq.com/s/zoS7GlReWPhfCjENGxXCdg)：本次无法取得正文。
- [DeepSeek Harness 英文文章入口](https://x.com/DeepSeekHarness/article/2116857257245987236)：本次返回 403。
- [英文原文镜像](https://threadnavigator.com/thread/2116857257245987236/)：用于阅读署名文章内容；这是第三方转载载体，不能说成已直接读到官方页面。
- [官方原型仓库](https://github.com/deepseek-ai/dsh-superagent-prototype/tree/8c32a9dc5d72c033816c6a8110b37f40cf94fba4)：通过 GitHub 连接器读取 README 和设计文档，固定到 `8c32a9dc5d72c033816c6a8110b37f40cf94fba4`。该提交作者与提交时间在 2026-10-31。

官方 README 明确说明：公开代码是研究原型；经过公开适配，与内部版本及文章描述不完全相同，文章有些方向未在原型实现。不能把文章、公开原型和 DeepSeek Harness 产品能力混为一体。[README](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/README.md)

## 2. 文章带来的关键修正

文章区分跨任务 Router 和任务内 Worker：Router 保持全局交互与任务关系，Worker 组织本任务的执行，内部还可使用子智能体。已有任务的后续事件可通过订阅直达 Worker。这个区分提示我们：主控是否过载，要看它是否反复处理任务内部细节和转发，而不能只看 agent 数量。[文章第 2–3 节镜像](https://threadnavigator.com/thread/2116857257245987236/)

该观点适用于 task-process 的设计取舍，但不要求所有单次问答都部署一个长驻 Router，也不能据此断言主会话永远不该执行短操作。

## 3. 官方原型文档能直接确认的机制

| 机制 | 官方文档事实 | 对 Task Progress 的设计启发 |
| --- | --- | --- |
| 人的注意力也是成本 | 000 文档要求少概念、少设置；仅在需要决定或有可用结果时发消息 | 统计人工决定和无效提醒，完整审计可查，避免在用户更新中重复整份派发文本 |
| Ticket 贯穿执行 | 每个 Ticket 有自己的 Executor Session 和目录；retry 复用同一 Session、目录、分支和 PR，Assignment 更新 | 区分长期 owner/context 与短期 attempt；重试不必重建执行者 |
| 后续输入归原任务 | 自己 Thread 上的用户 follow-up 先由 Executor 判断；越界可 hand back；context-only 订阅不授予处理权限 | 同范围反馈优先原 owner；上下文直通不等于授权范围变化 |
| 后台执行与交互解耦 | 后台 shell/subagent 存活时保留 worker slot；结果可唤醒；另保留一份人类消息优先容量 | running、waiting、后台句柄分别表达；不能把 turn 结束当任务完成 |
| 持久化外部效果 | 状态事务内先登记 effect，提交后执行；重试与消息 Outbox 保留记录 | 自动绑定或回执适配需可重放、可对账，避免状态已写但动作丢失 |
| 对象有可追索引用 | 资源按 ID 查询，正文有界，截断时提供详情入口 | 沿用 selective query，传摘要与引用，验收时再读原始证据 |

来源：[000 Philosophy](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/docs/000-philosophy.md)、[001 Domain](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/docs/001-domain.md)、[003 Execution](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/docs/003-execution.md)、[007 Architecture](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/docs/007-architecture.md)。这些机制是文档与源码结构所述，不是本次已部署或复现实验的结果。

## 4. 对实验结论的约束

文章第 7 节使用合成消息流，编码工作由耗时工具模拟。因此它主要评估调度，不证明真实编码质量。带直通与不带直通的方案完成耗时相近，直通的主要观察收益是减少 Router 决策和 cache-read；多 Session 整体也消耗更多 cache-read。不能把这些 token 数当作总费用，或宣称直通普遍缩短交付时间。[第 7 节镜像](https://threadnavigator.com/thread/2116857257245987236/)

本方案不引用其具体耗时倍数作为采用标准。Task Progress 的试点应包含真实任务、最终验收、返工、主会话介入、输入中断与已知 token 来源。

## 5. 纳入、分期与暂缓

直接纳入：长期 owner 与 attempt 分离；任务内自主安排；原 owner 处理反馈；少而可追索的上下文；机械事实记录幂等；人类注意力纳入指标。

分期研究：宿主确有结构化投递与唤醒能力时，对精确关联、已授权且同范围的输入做直通。先验证关联与边界，再减少人工转发。

暂缓：跨渠道主动接单、推测性执行、云端控制本机和 Watchdog 自动修复。当前 Task Progress 是宿主任务账本与协调协议；这些能力需要新的运行时和明确的操作契约，不能通过修改提示词假定已经实现。

也不照搬公开原型的其他完成规则：例如它有些 Ticket 会由 ready 报告或 PR 关闭而结束，不能用来替代 task-process 已有的 submission/review/root 验收语义。[003 Execution](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9dc5d72c033816c6a8110b37f40cf94fba4/docs/003-execution.md)
