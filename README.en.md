# Task Progress

[简体中文](README.md) | English

**See how your multi-step Codex tasks are progressing.**

Task Progress is a Codex plugin that brings task plans, execution status, and results into one panel. See what is running, what is complete, and what comes next.

[Interactive demo](https://carpe-horam.github.io/task-progress-codex/) · [Get 0.2.0](https://github.com/carpe-horam/task-progress-codex/releases/tag/v0.2.0) · [Changelog](CHANGELOG.md)

![Task Progress panel](docs/assets/task-progress-0.2.0.png)

*Interface preview using demonstration data.*

## Features

- **See progress**: numbered task hierarchies show status, executor, and elapsed time. Expand a task to read its result summary.
- **Track complex work**: keep task breakdowns, dependencies, and parallel work together as the plan changes.
- **Handle new requests**: ask Codex to queue independent requests or handle them in parallel while preserving the original task's progress.
- **Review results**: retain task history and retries, and export Excel and HTML reports.

## Installation

These commands are for macOS. You need Python 3 and a Codex client with plugin support. Run them in a terminal:

```sh
codex plugin marketplace add carpe-horam/task-progress-codex --ref v0.2.0
codex plugin add task-progress@task-progress-codex
python3 "${CODEX_HOME:-$HOME/.codex}/plugins/cache/task-progress-codex/task-progress/0.2.0/service_manager.py" install
```

Complete all three commands, then **restart Codex**.

Already installed? See the [upgrade commands](docs/releases/0.2.0.md#从旧版本升级). Source installation, service management, and platform notes are in the [installation guide](docs/installation.md) (Chinese).

## Getting started

Describe your task in Codex, for example:

> Use Task Progress to plan and implement this feature, keep the progress updated, and check the results when finished.

You can also give instructions as work proceeds:

| Goal | Example prompt |
| --- | --- |
| View progress | “Open Task Progress.” |
| Adjust the plan | “Finish the API before the UI, and update the task order.” |
| Queue another task | “Queue the documentation work for after the current task.” |
| Work in parallel | “Have a subagent check the docs in parallel while the main task continues.” |

The panel refreshes automatically. Expand a task for details, or select **Export** to save reports to `~/Downloads/Task Progress Exports`.

“Awaiting main-session review” means results have been submitted and still need checking. Codex performs and coordinates the work; Task Progress records and displays it. Task data stays on your computer.

If the panel does not appear, ask Codex to “Open Task Progress.” The interactive panel requires a client with MCP Apps support.

## Learn more

- [Interactive guide and demo](https://carpe-horam.github.io/task-progress-codex/)
- [Installation and service management](docs/installation.md) (Chinese)
- [Report an issue](https://github.com/carpe-horam/task-progress-codex/issues)
- Developer references: [execution protocol](docs/responsibility-protocol.md) · [test instructions](tests/README.md)

## License

[MIT](LICENSE)
