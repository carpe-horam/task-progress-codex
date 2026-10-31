<p align="center">
  <img src="docs/assets/readme-hero.svg" alt="Task Progress brand illustration: a blue and violet task network" width="1200">
</p>

<p align="center"><a href="README.md">简体中文</a> · English</p>

<p align="center"><strong>See how your multi-step Codex tasks are progressing.</strong></p>
<p align="center">Plans, execution and review. One task workspace.</p>

<p align="center">
  <a href="https://carpe-horam.github.io/task-progress-codex/">Interactive demo</a> ·
  <a href="https://github.com/carpe-horam/task-progress-codex/releases/tag/v0.2.8">Get 0.2.8</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

## Features

| See the work clearly | Keep complex work moving |
| --- | --- |
| Numbered tasks show status, executor and elapsed time. Expand for results. | Track task breakdowns, dependencies and parallel work as the plan changes. |
| **Handle new requests**<br>Ask Codex to queue independent work or run it in parallel, preserving the original task. | **Review the results**<br>Keep acceptance, history and retries. Export Excel and HTML reports. |

## Installation

On macOS, Linux and Windows, use a Codex client with plugins and stdio MCP, and Python 3 available as `python` on the Codex process PATH. Run in a terminal:

```sh
codex plugin marketplace add carpe-horam/task-progress-codex
codex plugin add task-progress@task-progress-codex
```

Task Progress 0.2.8 installs or updates the shared local service on first connection. **Restart Codex** after installation. Each connection keeps a lightweight stdio-to-HTTP bridge; task processing stays in the shared service. macOS uses LaunchAgent, Linux requires a working systemd user session, and Windows uses Task Scheduler for the signed-in user. See [installation and upgrade details](docs/installation.md) (Chinese), including platform prerequisites and native validation limits.

## Getting started

Describe your task in Codex:

> Use Task Progress to plan and implement this feature, keep the progress updated, and check the results when finished.

| Goal | Example prompt |
| --- | --- |
| View progress | “Open Task Progress.” |
| Adjust the plan | “Finish the API before the UI, and update the task order.” |
| Queue another task | “Queue the documentation work for after the current task.” |
| Work in parallel | “Have a subagent check the docs in parallel while the main task continues.” |

Version 0.2.1 links handed-off work to an exact receiving attempt and archives the source after acceptance. When children include cancelled or unresolved work, the main session explicitly decides whether to continue or accept closure. Original open/cancelled results, attempts and reviews stay intact; retries can reopen the work, and HTML/Excel retain the audit evidence.

The panel refreshes automatically. Expand a task for details, or select **Export** to save reports. “Awaiting main-session review” means submitted results still need checking. Codex executes and coordinates the work; Task Progress records and displays it. Data stays on your computer. The plugin does not automatically wake or control host agents. The interactive panel requires MCP Apps support.

<details>
<summary><strong>Explore the task panel</strong> · Historical 0.2.0 preview, demo data</summary>

![Task Progress 0.2.0 panel with demonstration data](docs/assets/task-progress-0.2.0.png)

</details>

## Learn more

[Interactive guide](https://carpe-horam.github.io/task-progress-codex/) · [Installation guide](docs/installation.md) (Chinese) · [Report an issue](https://github.com/carpe-horam/task-progress-codex/issues) · [Execution protocol](docs/responsibility-protocol.md) · [Test instructions](tests/README.md)

[MIT License](LICENSE)
