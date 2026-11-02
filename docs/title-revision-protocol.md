# 当前显示标题修订协议

`revise_task_title(session_id, task_instance_id, task_id, display_title, reason, expected_epoch, expected_revision)` 只修订当前 HTN 节点的展示名称。pending、running、finished、failed、cancelled 节点以及分组和根节点均可修订；独立任务实例已经归档、取消整个请求或旧 epoch 的节点不接受迟到写入。

调用前读取目标实例当前 `epoch` 与 `revision`。必须提供准确的实例、当前节点和这两个版本，以及去除首尾空白后非空的显示标题和原因。API 在会话锁内核对版本后追加记录、增加 revision 并原子保存。版本过期、缺失节点、空标题/原因或相同显示标题均报错，文件不变。工具写回执遵循现有精简协议；完整标题和记录通过读取工具获取。

持久化 `node.title` 始终保留创建时的原标题。每次改名只在该节点 `title_revisions` 追加修订 ID、实例、epoch、节点、原标题、修订前后标题、原因、时间和来源 revision。`display_title` 是查询时由该节点最新修订计算的只读 projection；没有修订时回退原标题。当前查询及历史快照分别使用自身的修订记录，不向旧快照反向套用新名称。读取不会回写文件或补写旧执行记录。

新 attempt 在开始时固定保存当时的显示标题至 `attempt.title`。改名不修改已有 attempt、submission、review、计划快照、旧轮次或归档任务；旧 attempt 没有标题时展示该快照的原标题，并保留旧记录原样。可用 `get_progress(view="records", task_id=..., record_kind="title_revisions")` 分页读取修订链，或者显式 full 读取。精简树包含当前 `display_title`、原标题和修订数量；完整修订链按需加载。

标题是展示元数据，不改变依赖/依赖策略、顺序、父子结构、scope、执行角色、状态、outcome、证据或根验收，也不使现有验收、盘点或覆盖的结果绑定失效。`revise_task_plan` 对已开始分支、受影响分组和依赖消费者的保护保持原有要求，不能用改名接口修改结构。

当前树、依赖名称、历史树和离线报告使用对应快照的显示标题。节点详情保留原标题与修订理由；执行历史显示每次 attempt 当时的标题。XLSX 任务清单同时保留当前显示名和原标题，执行尝试保留当时标题及当前显示名，标题修订工作表提供可读审计。HTML/XLSX 的“快照与修订”还保留完整原始修订 JSON（长记录分片），避免可读单元格长度上限丢失记录。所有标题和理由通过文本节点或 HTML/XML 转义插入，不能作为标签或脚本执行。
