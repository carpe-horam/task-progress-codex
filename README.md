# Task Progress

为 Codex 提供按会话隔离的任务进度跟踪：HTN 任务树、依赖与并行任务、计划历史、执行者/模型记录，以及 Excel 与可视化 HTML 导出。Codex 内使用交互式进度卡片。

**宣传手册：** <https://machengling.github.io/task-progress-codex/>

## 从 Codex marketplace 安装

```sh
codex plugin marketplace add machengling/task-progress-codex
```

添加后，在 Codex 桌面端重载应用，打开 **Plugins Directory**，选择 **Task Progress** 并安装。

本地克隆后也可以这样添加：

```sh
codex plugin marketplace add "/path/to/task-progress-codex"
```

## 运行要求

- 当前 `.mcp.json` 使用本机 stdio MCP server；运行进度数据留在使用者自己的设备上，不通过本仓库共享。
- 需要 Python 3。当前启动配置面向 macOS/Linux；Windows 启动器已随包提供，但 Windows MCP 配置尚未验证。
- 导出的 Excel 与 HTML 保存到使用者本机的 `Downloads/Task Progress Exports`。
- Codex 模型运行仍需要用户自己的 Codex 账号和服务连接。

## 仓库内容

- `plugins/task-progress/`：插件、MCP server、UI 和 `track-progress` skill。
- `.agents/plugins/marketplace.json`：Codex 本地 marketplace 清单。
- `docs/index.html`：GitHub Pages 宣传手册。

## License

MIT，见 [LICENSE](LICENSE)。
