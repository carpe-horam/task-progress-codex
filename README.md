<p align="center">
  <img src="docs/assets/readme-hero.svg" alt="Task Progress — 为智能体任务提供可见的计划、执行与复核路径。品牌流程插画，非产品截图。" width="1200">
</p>

<p align="center"><strong>任务交给智能体，进展留在眼前。</strong></p>
<p align="center">把复杂工作展开成一张任务树：谁在做、卡在哪里、结果是否验收，随时看清。</p>

<p align="center">
  <a href="#开始使用"><strong>开始使用 →</strong></a> ·
  <a href="https://carpe-horam.github.io/task-progress-codex/">先看交互演示</a> ·
  <a href="README.en.md">English</a>
</p>

<p align="center"><sub>Cindy / Codex / DeepSeekRuntime · 任务数据本机保存 · Excel / HTML 导出 · MIT 开源</sub></p>

## 让长任务，也有清晰的进展

开发一个功能、排查一个难复现的 bug、让多个 agent 分工协作——任务越复杂，越需要知道下一步。**Task Progress 把计划、执行记录和结果验收放到同一个面板里。**

<p align="center">
  <a href="docs/assets/task-progress-preview-0.3.8.png">
    <img src="docs/assets/task-progress-preview-0.3.8.png" alt="Task Progress 0.3.8 面板：使用模拟数据展示任务树、执行状态与依赖。" width="640">
  </a>
</p>
<p align="center"><sub>0.3.8 真实插件界面 · 模拟宿主与演示数据 · 点击图片放大。</sub></p>

| 你想知道 | 打开面板就能查看 |
| --- | --- |
| **现在做到了哪一步？** | 展开的任务树、当前执行者、状态与耗时。 |
| **为什么还没继续？** | 前置依赖、等待原因，以及尚未解决的工作。 |
| **做完之后，检查过了吗？** | 提交的结果、主会话复核与对应证据，区分执行结束和验收完成。 |

## 开始使用

### 1. 选择宿主并安装

[下载或检出 v0.3.8 源码](https://github.com/carpe-horam/task-progress-codex/releases/tag/v0.3.8)。该 release 是源码版，没有安装附件。以下路径均相对于解压或克隆后的仓库。

- **Cindy**：按 [Cindy 构建说明](plugins/task-progress/cindy/README.md)运行 `prepare.py --stage`，将生成目录交给 Cindy Forge 打包，再在 Cindy 中安装产出的插件。
- **Codex**：需要支持插件和 stdio MCP 的 Codex，及 PATH 中名为 `python` 的 Python 3。执行：

  ```sh
  codex plugin marketplace add carpe-horam/task-progress-codex
  codex plugin add task-progress@task-progress-codex
  ```

  交互面板还需要客户端支持 MCP Apps。安装后重启 Codex，首次连接会安装或更新本机后台服务。平台前提见[安装说明](docs/installation.md)。
- **DeepSeekRuntime**：在 Plugins → Add plugin → Local directory 选择 `plugins/task-progress/dsh`，或者通过 `plugin_manager` 的 `install_bundle` 指定该目录绝对路径；安装后重新加载页面或重启应用。详见 [dsh 说明](plugins/task-progress/dsh/README.md)。

### 2. 给它一个真实任务

把下面这段话复制到所用宿主，替换方括号里的内容：

```text
用 Task Progress 帮我完成 [描述你的任务]。
先拆出计划和验收标准，再开始执行；持续更新进度，
有阻塞就说明原因，完成后检查结果并保留证据。
```

### 3. 随时打开进度

在会话中说 **“打开任务进度”**。面板会自动刷新；展开任务看详情，完成后用「导出」保存 Excel 和 HTML 报告。

## 从规划到交付

**计划可以变，执行记录可以回看。** 拆分任务、调整依赖、重试失败步骤，或在中途加入新请求，都能保留对应的计划与尝试记录。面板的百分比按已解决且通过验收的叶任务计算。所有叶任务达到 100% 后，主会话还需验收整体交付。

| 从规划到推进 | 从结果到交付 |
| --- | --- |
| **任务拆解**<br>将复杂目标拆成有依赖的子任务，按层查看进展。 | **结果复核**<br>查看提交、验收结论和证据，找出尚未解决的部分。 |
| **协作可见**<br>记录主会话与子智能体的分工和执行尝试。 | **历史可查**<br>回看计划变化、重试和先前结果。 |
| **接住新请求**<br>让 Codex 将独立请求排队，或在条件允许时并行处理。 | **报告可带走**<br>导出 Excel 与 HTML，留档、复盘或分享。 |

## 下次遇到这些任务，试试这样说

| 场景 | 直接发给智能体 |
| --- | --- |
| **开发功能** | “用 Task Progress 规划这个功能，先确认接口，再实现界面，最后验证完整流程。” |
| **排查问题** | “用 Task Progress 跟踪这个 bug：复现、定位、修复、验证，每步留下证据。” |
| **多智能体协作** | “让子智能体分别检查代码与文档，用 Task Progress 记录分工，由主会话复核结果。” |
| **临时插入工作** | “把整理使用文档加入队列，当前任务完成后再做。” |
| **交付与复盘** | “检查是否还有未解决任务，汇总验收结果，然后打开进度面板供我导出报告。” |

Cindy Lead 获准委派时，为一次性子任务和跨阶段工作包创建或复用 Orca Worker。Worker 可在获派范围内按授权使用原生子智能体。Lead 的 Task Progress 任务树保留为主账本，Worker 的回报与内部记录作为验收证据。具体步骤见 [Cindy Orca Worker 路由](plugins/task-progress/skills/track-progress/references/cindy-orca-worker.md)。

## 它如何与你的智能体配合

宿主智能体负责规划、执行和协调；Task Progress 负责保存与展示任务状态、依赖和证据。任务数据保存在本机，多个会话各自保留进度。各宿主通过不同的插件包装接入。

<details>
<summary><strong>使用前，了解这三个约定</strong></summary>

- **状态来自实际记录。** 面板随智能体写入的进展更新；插件不会自行唤醒或控制宿主 agent，也不会自动执行待办任务。
- **提交后还要复核。** 「待主会话验收」表示执行者已提交结果，需要主会话检查；任务执行结束不代表全部问题已解决。
- **展示取决于宿主支持。** Cindy 使用插件面板，Codex 使用 MCP Apps，DeepSeekRuntime 在会话中展示进度卡片。

需要了解验收契约、执行尝试与协作边界，请阅读[执行与职责协议](docs/responsibility-protocol.md)。

</details>

## 继续探索

[交互演示与手册](https://carpe-horam.github.io/task-progress-codex/) · [安装与服务管理](docs/installation.md) · [更新记录](CHANGELOG.md) · [发布版本](https://github.com/carpe-horam/task-progress-codex/releases) · [测试说明](tests/README.md)

用得顺手，欢迎 **Star 收藏**，下次复杂任务直接用起来。也欢迎带着真实场景[反馈问题或建议](https://github.com/carpe-horam/task-progress-codex/issues)。

[MIT License](LICENSE)
