# Task Progress

简体中文 | [English](README.en.md)

**让 Codex 的多步骤任务进展一目了然。**

Task Progress 是一个 Codex 任务进度插件。它把任务计划、执行状态和结果汇总到一个面板中，方便你查看正在做什么、哪些已完成，以及接下来要做什么。

[交互演示](https://carpe-horam.github.io/task-progress-codex/) · [下载 0.2.0](https://github.com/carpe-horam/task-progress-codex/releases/tag/v0.2.0) · [更新记录](CHANGELOG.md)

![Task Progress 任务面板](docs/assets/task-progress-0.2.0.png)

*使用演示数据的界面预览。*

## 主要功能

- **查看进展**：任务按层级编号，显示状态、执行者和耗时，展开即可查看结果摘要。
- **跟踪复杂任务**：记录任务拆分、执行顺序与并行工作，计划调整后同步更新。
- **处理临时插入的请求**：让 Codex 将独立请求排队或并行处理，同时保留原任务的进度。
- **保存与回顾**：保留任务历史和重试记录，支持导出 Excel 与 HTML 报告。

## 安装

以下适用于 macOS，需要支持插件的 Codex 和 Python 3。在终端执行：

```sh
codex plugin marketplace add carpe-horam/task-progress-codex --ref v0.2.0
codex plugin add task-progress@task-progress-codex
python3 "${CODEX_HOME:-$HOME/.codex}/plugins/cache/task-progress-codex/task-progress/0.2.0/service_manager.py" install
```

三条命令全部完成后，**重启 Codex**。

已有旧版？查看[升级命令](docs/releases/0.2.0.md#从旧版本升级)。源码安装、服务管理及其他平台说明见[安装文档](docs/installation.md)。

## 开始使用

安装后，直接在 Codex 中描述任务，例如：

> 用 Task Progress 帮我规划并实现这个功能，持续更新任务进度，完成后检查结果。

也可以在工作过程中告诉 Codex：

| 想做什么 | 可以这样说 |
| --- | --- |
| 查看当前进度 | “打开任务进度。” |
| 调整计划 | “先完成接口，再做界面，更新任务顺序。” |
| 插入独立任务 | “把整理文档加入队列，当前任务完成后再做。” |
| 并行处理 | “让子智能体并行检查文档，主任务继续。” |

面板会自动刷新。点击任务可展开详情，点击“导出”可保存报告到 `~/Downloads/Task Progress Exports`。

“待主会话验收”表示结果已提交、仍需检查。任务的实际执行与协调由 Codex 完成，Task Progress 负责记录和展示。任务数据保存在本机。

如果面板没有出现，可明确要求“打开任务进度”；交互面板需要客户端支持 MCP Apps。

## 更多资料

- [交互手册与功能演示](https://carpe-horam.github.io/task-progress-codex/)
- [安装与服务管理](docs/installation.md)
- [反馈问题](https://github.com/carpe-horam/task-progress-codex/issues)
- 开发者参考：[执行协议](docs/responsibility-protocol.md) · [测试说明](tests/README.md)

## 许可证

[MIT](LICENSE)
