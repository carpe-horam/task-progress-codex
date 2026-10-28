# Task Progress 0.2.0 面板修正

本轮解决首次任务规划后不自动请求标签页、等待图标偏移、重复详情与顶部元信息过多，并加入用户确认的动态层级编号。此前的依赖文字提示方案已由用户撤回，没有实现。

## 行为

- 主会话首次粗规划后调用一次展示入口。新 v4 资源首选 fullscreen；旧 v2/v3 仍首选 inline，避免旧消息被动升级。展示回执只包含身份和一次性意图，进度仍由 App 独立读取。
- 最新意图只消费一次，结合现有 fullscreen 租约避免重复自动请求；轮询、恢复和关闭后不重发。手动打开仍可用。公开协议由宿主决定实际显示，工具成功不是可见标签页证明。
- 标题前显示 `1`、`1-1` 等编号。同级按依赖投影稳定排序，无约束保留计划顺序；插入、依赖或层级变更及重规划后重新计算。内部 ID、DOM、展开、焦点和选区继续按任务身份绑定。历史使用自己的快照。交错并行分支保留计划相对次序，不把层级编号解释为严格串行时间线。
- 任务行保留角色、实际智能体 ID、模型及思考强度。当前执行详情删除重复身份、正常绑定和验收状态，保留结果摘要、提交、证据与历史。配置来源移入“更多详情”。顶部仅保留任务计划轮次、主会话模型。
- 等待图标的外圈、表盘、指针和轴点共用一个 20px SVG 坐标系。保留三种宽度的页面截图与 6 倍实际浏览器渲染检查图。

## 验证

自动展示协议阶段通过 54 项 Python 检查及 3 个 Chrome 脚本。编号阶段通过 7 组编号场景和 15 项交互检查。最终时钟、顶部、详情版本又通过编号、队列、责任记录及基础 App 4 个相关脚本；380/600/1000px 布局已检查。初次基础 App 检查因配置来源文案断言过期失败，修正断言为现有正式文案后通过。

上述浏览器使用真实 Chrome 和隔离 Python 服务，MCP Apps 父页面是模拟宿主。编号改序用明确标识的合成快照；重规划与历史来自真实隔离后端。证据见 [清单](2026-10-28-panel-refinement-evidence/final-source.json)、[界面](2026-10-28-panel-refinement-evidence/final-ui.png)、[时钟](2026-10-28-panel-refinement-evidence/clock-6x.png)。协议与编号阶段清单各自标记当时源码，最终 UI 身份以 final-source.json 为准。

## 安装与宿主边界

保持版本 0.2.0。代码提交后的稳定 marketplace 快照、安装缓存、运行目录及原生 transport 核验记录保存在本机 `Task Progress/install-records/<commit前12位>/`；实际标签页检查单独记录。原生 Codex App 自动化访问被安全策略禁止，本轮只使用允许的 MCP Apps 标签页 DOM 接口，不绕过该限制。

8f5b7b5 安装后通过两路原生 app-server 客户端检查，发现 24 个工具；源码、缓存与运行文件相符，原有任务文档未改变。用户关闭旧页并重新加载插件后，MCP Apps 标签页列表从空变为新页 2，主会话未点击打开按钮；DOM 已显示动态编号和两项顶部字段，最新展示意图已消费并登记为 fullscreen。该回读发现宿主延迟通知后旧 fallback 提示仍存在，随后补一行清理并通过 10 组自动展示回归。最终清单将这处补修与此前 UI 截图的源码身份分别保留；实际记录见 [原生回读](2026-10-28-panel-refinement-evidence/native-visible-before-feedback-fix.json)。

宿主可能在 JavaScript 初始化前创建 fullscreen 页面；重复或历史页面只能挂载后请求退回 inline，不能保证无瞬时出现。已挂载的旧资源不热替换，关闭后重新打开才会加载新版。公开协议没有跨视图 focus/reuse 和历史聊天状态接口。参考 [OpenAI 展示模式](https://github.com/openai/mcp-extensions/blob/main/docs/spec.md#display-modes) 与 [MCP Apps 模式协议](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx#display-modes)。
