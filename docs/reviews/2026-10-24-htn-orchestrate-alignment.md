# Task Progress 与 HTN-Orchestrate 的设计对照

- 日期：2026-10-24（Asia/Shanghai）。
- 插件基线：`main` / `dafb74a`。
- 论文：[HTN-Orchestrate，arXiv:2610.22195v2](https://arxiv.org/html/2610.22195v2)，2026-10-18 修订；阅读范围为方法 §3.1–3.3、算法 1/2、附录 B，并参考附录 C 的机制区分。
- 官方实现：`ycdeng-ntu/htn-orchestrate`，读取时 `main` 为 `0bccf18d813298e5c2a33961dbf56b907bd8b133`。核对了 `planner.py`、`task_node.py`、`task_tree.py`、`recovery.py`；未运行其基准实验，也未假定当前公开源码等于论文实验时的版本。
- 性质：设计一致性审查及建议；不包含插件实现修改。

## 结论

**部分对齐。当前插件实现了 HTN 形态的任务记录、依赖查询和历史管理，但没有完整实现论文的编排机制。**

论文把全局规划与叶任务内的多智能体协调分开；规划会等待前置结果，综合结果沿层级返回，重建则使用已有证据。[论文方法 §3](https://arxiv.org/html/2610.22195v2#S3)

插件 README 和 skill 明确把实际执行与智能体派发交给 Codex 主会话。这个产品边界本身合理；但仅具备数据结构和操作工具，不能证明外部调用方执行了论文流程。当前仓库也没有端到端轨迹或测试证明完整流程已落实。此结论是行为对照，不推断开发者是否曾以该论文作为设计来源。

## 逐项对照

| 机制 | 插件现状 | 判定与依据 |
| --- | --- | --- |
| 层级任务与依赖 | 有 `parent_id/children/depends_on`、节点结果与历史。 | **结构部分对齐。** 官方实现限定兄弟依赖；插件只校验目标存在和合并图无环，未限制同一父节点，因此会接受祖先依赖。见本地 `server.py:337` 与 [官方依赖校验](https://github.com/ycdeng-ntu/htn-orchestrate/blob/0bccf18d813298e5c2a33961dbf56b907bd8b133/workflows/dynamic_htn_informed_research/htn/task_tree.py#L438)。 |
| 区分待规划与可执行叶子 | 当前叶子只有一个 ready 概念；节点有标题，没有独立的结构化目标或规划阶段字段。 | **缺失。** 官方节点区分 `task` 与 `title`，并以 `needs_decomposition` 路由规划/执行。见本地 `server.py:214`、`get_ready_tasks` 与 [官方节点模型](https://github.com/ycdeng-ntu/htn-orchestrate/blob/0bccf18d813298e5c2a33961dbf56b907bd8b133/workflows/dynamic_htn_informed_research/htn/task_node.py#L60)。 |
| 前置结果驱动分解 | 能追加子节点和读取前驱摘要，但 `decompose_task` 不要求节点 ready，也不记录这次分解实际使用了哪些前驱证据。 | **关键约束未实现。** 官方 Planner 在分解前检查 `is_ready` 和规划标志。见本地 `server.py:398` 与 [官方 Planner](https://github.com/ycdeng-ntu/htn-orchestrate/blob/0bccf18d813298e5c2a33961dbf56b907bd8b133/workflows/dynamic_htn_informed_research/htn/planner.py#L98)。 |
| 执行结束与任务解决 | 有 `execution_status` 与 `outcome=resolved/open`，但默认依赖策略是 resolved。 | **概念对齐，放行语义不同。** 官方 ready 检查前驱 completed，不要求 resolved。本插件可选 finished 策略又把 failed/cancelled 一起放行，比正常返回 open 的语义更宽。见本地 `server.py:453` 与 [官方 ready 判定](https://github.com/ycdeng-ntu/htn-orchestrate/blob/0bccf18d813298e5c2a33961dbf56b907bd8b133/workflows/dynamic_htn_informed_research/htn/task_tree.py#L68)。 |
| 全局 Planner 与叶内 Orchestrator | 一次 node attempt 记录一个 assignment；skill 要求每个委派 agent 挂成子任务。 | **专用的两层编排协议缺失。** 没有独立的叶内 worker 集合、回报集合、dispatch/wait/finish 决策或预算状态；将 worker 全部表示为 HTN 子节点，会混合两种层级。论文在 §3.3、算法 2 区分它们。[论文 §3.3](https://arxiv.org/html/2610.22195v2#S3.S3) |
| 父级综合验收 | 所有直接子任务终止后，允许主会话提交父级结果。 | **部分对齐。** 已有汇总前置门槛；但 failed/cancelled 也计入，系统不执行综合器、不验证目标覆盖或结果依据。重试后父级验收仍然有效，是额外的一致性缺陷。见本地 `server.py:691` 与 [官方结果传播](https://github.com/ycdeng-ntu/htn-orchestrate/blob/0bccf18d813298e5c2a33961dbf56b907bd8b133/workflows/dynamic_htn_informed_research/htn/task_tree.py#L305)。不能把 AND 简化为子任务计数 100%。 |
| 重建与证据继承 | 禁止有 running 节点时重建；保存旧 epoch，然后新建空根节点。 | **历史保留已实现，证据准备未实现。** 没有独立的恢复上下文、已支持事实、引用校验或未解决关系；调用方仍可自行读取历史，不能说证据已经丢失。见本地 `server.py:738` 与 [官方恢复模块](https://github.com/ycdeng-ntu/htn-orchestrate/blob/0bccf18d813298e5c2a33961dbf56b907bd8b133/workflows/dynamic_htn_informed_research/htn/recovery.py#L262)。 |
| 规模与执行预算 | 有 profile、attempt、epoch，但没有深度、分支、重建次数或执行轮数上限。 | **缺失。** 没有预算耗尽后的统一收尾入口。结构限制可由状态服务承担，执行预算应由调用方兑现。见 [官方分解预算](https://github.com/ycdeng-ntu/htn-orchestrate/blob/0bccf18d813298e5c2a33961dbf56b907bd8b133/workflows/dynamic_htn_informed_research/htn/planner.py#L183)；论文算法 1/2 定义执行边界。[论文方法](https://arxiv.org/html/2610.22195v2#S3) |

## 本次补充复现

在临时目录加载当前 `server.py`，没有操作真实任务数据：

1. A 正常结束但 `outcome=open`；默认依赖 A 的 B 保持 blocked，显式 `dependency_policy=finished` 的 C 进入 ready。两者都读取同一份前驱结果。
2. 对仍 blocked 的 B 调用 `decompose_task`，操作成功，新子任务 B1 随即 ready。这说明问题发生在规划入口，不只是界面显示。
3. 重建后保留一轮 history，但新 root 的 summary 为空、evidence 为空，返回模型没有 `recovery_context`。旧证据仍可在 history 中找到，系统尚未提炼成下一轮规划输入。

更完整的缺陷复现、优先级和原始边界见 [插件审查记录](2026-10-24-plugin-audit.md)。

## 建议的对齐路线

以下是针对本插件的实施建议，并非论文的逐字设计，也不是已获授权的修改计划。

1. **先修任务语义。** 增加明确的规划阶段与独立目标；分解仅允许满足前置条件的待规划叶子；限定兄弟依赖，或者另行完整定义跨层依赖；修复重试的验收失效规则。
2. **明确依赖策略。** 保留工程任务需要的 resolved 门槛，同时提供“正常返回即放行”的策略，准确区分 open、failed、cancelled。不要仅为对齐论文就把现有默认策略静默改成更宽松的 finished。
3. **提供可审计的上下文与综合验收接口。** 为规划/执行生成根目标、当前目标、祖先约束及前驱证据的上下文；为父级提供待汇总子任务及验收要求。保存上下文来源与验收者，而不只保存显示用摘要。
4. **增加叶内编排记录。** 单独记录 orchestrator、worker assignments、reports 和决策；由 Codex 的协作工具执行派发/等待/停止，MCP 服务负责校验和保存，不必在 Python 服务里再嵌入一个模型运行时。
5. **重建前形成证据摘要。** 保存经检查的发现、来源和剩余缺口，显式关联新 epoch 的规划输入；历史快照继续用于审计。增加结构和执行预算及未解决收尾。
6. **用受控轨迹验收。** 验证前驱 open 后的后继选择、拿到结果后再拆分、叶内多轮调度、逐层综合、带证据重建、迟到结果拒绝和预算耗尽。使用受控执行器即可，不必先跑完整研究基准。

会话自动恢复、历史分页、UI 乱序保护、模型信息降级和安装兼容性是本插件的产品/工程需求；不应把它们全部说成论文要求。

## 记录状态

用户随后明确要求加入论文中的任务拆分、调度、分发。实际开发范围和取舍见 [开发决策](2026-10-24-development-decisions.md)，这些核心机制已列为必做。

- 已记录一般审查与论文对照，并追加项目记忆索引。
- 插件实现和 skill 未修改；没有提交、推送或发布。
- 未运行论文的多智能体系统或基准实验；不能据此声称插件具备论文报告的性能。
