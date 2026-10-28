<p align="center">
  <img src="docs/assets/readme-hero.svg" alt="Task Progress brand illustration: a blue and violet task network" width="1200">
</p>

<p align="center"><a href="README.md">简体中文</a> · English</p>

<p align="center"><strong>See how your multi-step Codex tasks are progressing.</strong></p>
<p align="center">Plans, execution and review. One task workspace.</p>

<p align="center">
  <a href="https://carpe-horam.github.io/task-progress-codex/">Interactive demo</a> ·
  <a href="https://github.com/carpe-horam/task-progress-codex/releases/tag/v0.2.0">Get 0.2.0</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

## Features

| See the work clearly | Keep complex work moving |
| --- | --- |
| Numbered tasks show status, executor and elapsed time. Expand for results. | Track task breakdowns, dependencies and parallel work as the plan changes. |
| **Handle new requests**<br>Ask Codex to queue independent work or run it in parallel, preserving the original task. | **Review the results**<br>Keep acceptance, history and retries. Export Excel and HTML reports. |

## Installation

For macOS, with Python 3 and a Codex client that supports plugins. Run in a terminal:

```sh
codex plugin marketplace add carpe-horam/task-progress-codex --ref v0.2.0
codex plugin add task-progress@task-progress-codex
python3 "${CODEX_HOME:-$HOME/.codex}/plugins/cache/task-progress-codex/task-progress/0.2.0/service_manager.py" install
```

Complete all three commands, then **restart Codex**. Already installed? See [upgrade commands](docs/releases/0.2.0.md#从旧版本升级). Platform notes and service management are in the [installation guide](docs/installation.md) (Chinese).

## Getting started

Describe your task in Codex:

> Use Task Progress to plan and implement this feature, keep the progress updated, and check the results when finished.

| Goal | Example prompt |
| --- | --- |
| View progress | “Open Task Progress.” |
| Adjust the plan | “Finish the API before the UI, and update the task order.” |
| Queue another task | “Queue the documentation work for after the current task.” |
| Work in parallel | “Have a subagent check the docs in parallel while the main task continues.” |

The panel refreshes automatically. Expand a task for details, or select **Export** to save reports. “Awaiting main-session review” means submitted results still need checking. Codex executes and coordinates the work; Task Progress records and displays it. Data stays on your computer. The interactive panel requires MCP Apps support.

<details>
<summary><strong>Explore the task panel</strong> · Actual interface, demo data</summary>

![Task Progress 0.2.0 panel with demonstration data](docs/assets/task-progress-0.2.0.png)

</details>

## Learn more

[Interactive guide](https://carpe-horam.github.io/task-progress-codex/) · [Installation guide](docs/installation.md) (Chinese) · [Report an issue](https://github.com/carpe-horam/task-progress-codex/issues) · [Execution protocol](docs/responsibility-protocol.md) · [Test instructions](tests/README.md)

[MIT License](LICENSE)
