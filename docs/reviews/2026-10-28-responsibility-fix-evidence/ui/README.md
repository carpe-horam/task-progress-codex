# UI responsibility validation

2026-10-28，`codex/task-progress-main-session-tab`，基线 `52f7b19abc452ce1bf2aa7cd23485df7db2028f6`。实际 app 资源、隔离 server 与 Chrome 156.0.8078.48；MCP Apps 父宿主由测试模拟。未安装或重启真实服务，未写真实 session，未验证当前桌面安装插件。

独立评审 P1/P2 修正后，最终职责 **7 组**通过；当前源码起止哈希一致，见 [source-binding.json](source-binding.json)。此前 owner 验证另有 app 10、export 3、interaction 15、audit 7、shared HTTP app 3 组通过，这 38 组没有在 P2 后重复运行。合计记录 45 组，不能将其中旧运行说成最终源码的全套验收。完整命令、绑定范围、Chrome 版本和最终 app/server/相关测试 SHA-256 见 [validation.json](validation.json)。

P2 修复：只有没有 legacy 标记的新职责协议记录，才适用 main 自执行无需独立提交复核或新分组待综合验收文案。已知 main_session 角色的旧 leaf、group、attempt 均保留执行者，验收统一显示未记录；旧 completed group 不重新进入待综合验收。真实旧 synthesis 的原 kind、摘要、证据仍展示为综合验收执行记录，Excel 另列记录类型，接受/退回决定留空，不补造 review。

最终职责覆盖：提交后依赖不放行；绑定 ID 与计划分工独立；退回后重试保留历史；接受 open 报告仍未解决；接受 resolved 才放行；新 main 自执行原因与新 group 文案；真实 root synthesis 单列综合验收；未知角色及已知 main 角色的旧记录不回填验收。界面、HTML、Excel 均断言。

`app-pending-pending.png` 展示新 root 待综合验收与子任务待主会话复核；`app-accepted-open.png` 展示接受报告但任务仍 open。新增 `legacy-known-role-unreviewed-app.png` / `-export.png` 展示旧角色不产生当前复核豁免；`legacy-known-role-synthesis-app.png` / `-export.png` 展示真实旧综合验收记录。对应 HTML、XLSX 和 fixture JSON 可审查原始内容。两类新增截图已目视检查。

截图为隔离 Chrome 渲染，不能证明桌面真实宿主调度、真实 agent 身份或当前安装插件行为。绑定智能体是经实际 bind_task_agent API 上报的 fixture ID，没有声称测试真实 spawn。初次 fixture/选择器/导出期望调整以及评审后的兼容遗漏均在 validation.json 记录；最终运行已通过。
