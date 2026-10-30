# 安装与服务管理

[返回 README](../README.md) · [0.2.5 发布说明](releases/0.2.5.md)

本文描述 Task Progress 0.2.5 的跨平台自动服务启动与管理。需要固定版本时，可在首次添加 Marketplace 时使用 `--ref <release-tag>`。

### 平台与运行前提

需要支持插件与 stdio MCP 的 Codex，以及 **Codex 进程 PATH 中可通过 `python` 启动的 Python 3**。先在终端运行 `python --version`；macOS/Linux 如果仅有 `python3`，需要安装或配置可供 Codex 使用的 Python 3 `python` 命令，然后重新启动 Codex。Windows 可使用 Python 官方安装器并启用 PATH；仅有 `py` 启动器或 Microsoft Store 安装别名不足以满足此入口。不要将 Python 2 配置为 `python`。

- macOS：通过当前图形登录会话的 LaunchAgent `io.task-progress.mcp` 托管。
- Linux：通过 `systemctl --user` 安装并启用用户服务，需要 systemd 用户管理器和用户 D-Bus 会话可用。无 systemd 的容器、某些 WSL/SSH 会话不满足自动安装前提；插件会报告启动失败，不会悄悄启动临时后台进程。默认随用户会话运行；本插件不配置 linger。
- Windows：通过 Windows PowerShell 的 ScheduledTasks 模块，在当前用户下登记登录触发的计划任务，无需管理员权限。任务使用当前用户的交互登录令牌，注销后不保证运行；不能将它当作机器级 Windows 服务。受组织策略禁止登记任务时，插件会报告失败。

跨平台入口与安装器实现不等于已完成各平台原生验收。Linux/systemd 与 Windows Task Scheduler 的实际安装、登录恢复和异常重启仍需在对应系统验证。

### 首次安装

```sh
codex plugin marketplace add carpe-horam/task-progress-codex
codex plugin add task-progress@task-progress-codex
```

安装后**重启 Codex**。MCP 入口执行 `python mcp_bridge.py`，先调用 `service_manager.py` 的幂等 `install` 操作，完成安装或更新并验证服务健康，再转发 MCP 请求。首次连接可能需要等待原生服务管理器完成启动；失败原因通过 MCP 进程 stderr 返回。无需手动执行第三条安装命令。

服务会保存 Python 解释器的绝对路径，请使用稳定安装的 Python。更换或移除解释器后，应确保 `python` 指向新解释器，再重新连接插件或手动运行安装命令。

### 从旧版本升级

已有同名 Marketplace 时，重新登记当前发布标签，再重新安装插件。固定旧标签的 Marketplace 执行 upgrade 仍会停留在该标签：

```sh
codex plugin marketplace remove task-progress-codex
codex plugin marketplace add carpe-horam/task-progress-codex --ref v0.2.5
codex plugin add task-progress@task-progress-codex
```

随后**重启 Codex**。新 MCP 连接会自动更新后台运行版本，重新发现工具；只刷新面板或 Marketplace 不足以升级已加载的插件。若尚未登记 Marketplace，先运行首次安装的来源添加命令。升级不删除任务；任务数据独立保存在 `~/.cache/task-progress/sessions/`，不会因升级或服务卸载而删除。已连接的旧客户端可能缓存旧工具，应一并重启；无需手动终止未知进程。

如果需要试用未发布的源码改动，可使用下面的本地源码方式。

### 从本地源码安装

```sh
git clone https://github.com/carpe-horam/task-progress-codex.git
codex plugin marketplace add ./task-progress-codex
codex plugin add task-progress@task-progress-codex
```

安装后重启 Codex，首次 MCP 连接自动安装后台服务。已有同名 Marketplace 时先移除旧登记；更新源码后需重新安装插件并重启 Codex。此方式安装检出的源码，请在需要固定版本时自行检出相应标签或提交。

### 共享后台服务与连接桥接

本机用户的 Codex 连接共享 `http://127.0.0.1:53421/mcp` 上的 **一个 Python 后台服务进程**。每条 MCP stdio 连接另有轻量 Python 桥接进程，仅负责启动检查、读取私有凭据与 HTTP 转发，不载入任务引擎或持有任务状态。关闭连接只结束桥接；服务由原生服务管理器托管，继续运行。请求不会经过 `http_headers_helper`；凭据助手不负责启动或托管后台服务。

会话连接和 UI 标签页仍独立，任务按 `session_id` 隔离。服务只监听环回地址，校验 Host 和 Bearer 凭据并拒绝浏览器跨域请求。桥接使用直连 HTTP，不使用系统代理，也不会自动重试可能已经执行的写请求。

安装器将后台运行文件复制到持久版本目录，插件缓存更新不会删除正在使用的文件：

| 平台 | 后台运行版本 | 服务定义 |
| --- | --- | --- |
| macOS | `~/Library/Application Support/Task Progress/releases/` | `~/Library/LaunchAgents/io.task-progress.mcp.plist` |
| Linux | `~/.local/share/task-progress/releases/` | `~/.config/systemd/user/io.task-progress.mcp.service` |
| Windows | `~/AppData/Local/Task Progress/releases/` | 用户任务计划；本地定义在 `~/.cache/task-progress/service/scheduled-task.xml` |

所有平台凭据位于 `~/.cache/task-progress/service/http-headers.json`。POSIX 使用目录 0700、凭据 0600；Windows 的 `chmod` 不等价于 POSIX 权限隔离，实际访问控制取决于用户目录继承的 ACL；本安装器尚未单独收紧 ACL。不要分享该文件。

### 手动管理与排查

以下命令从本地仓库根目录执行，PowerShell 和 POSIX shell 均可使用：

```sh
python plugins/task-progress/service_manager.py status --json
python plugins/task-progress/service_manager.py install
python plugins/task-progress/service_manager.py stop
python plugins/task-progress/service_manager.py uninstall
```

`install` 安装、更新或启动已停止的服务，健康且版本与解释器一致时不重启；多个连接的安装操作通过锁串行处理。`stop` 影响所有连接，下次插件建立连接会重新启动服务。`uninstall` 移除本插件的用户服务定义，保留任务、凭据与运行版本；若要保持卸载状态，也需停用插件，避免新连接再次安装。

已安装插件也可使用其缓存目录中的 `service_manager.py` 执行上述操作。macOS/Linux 示例（将 `<installed-version>` 替换为实际缓存版本目录）：

```sh
python "${CODEX_HOME:-$HOME/.codex}/plugins/cache/task-progress-codex/task-progress/<installed-version>/service_manager.py" status --json
```

Windows PowerShell 示例（自定义 CODEX_HOME 时替换该根目录）：

```powershell
python "$HOME/.codex/plugins/cache/task-progress-codex/task-progress/<installed-version>/service_manager.py" status --json
```

端口被非本插件服务占用或服务定义身份校验失败时安装会报错，不会终止未知进程。macOS 服务 stdout/stderr 在 `~/.cache/task-progress/service/service.{stdout,stderr}.log`；Linux 使用 `journalctl --user -u io.task-progress.mcp.service`，Windows 查看任务计划程序的运行状态与历史。初始化失败先检查 Codex 中的 MCP stderr、Python PATH 和原生用户服务管理器。
