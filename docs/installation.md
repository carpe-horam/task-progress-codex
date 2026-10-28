# 安装与服务管理

[返回 README](../README.md) · [0.2.0 发布说明](releases/0.2.0.md)


### 首次安装 0.2.0（macOS）

安装发布标签 `v0.2.0`，不依赖默认分支。需要 Python 3，以及支持插件、Streamable HTTP 和 `http_headers_helper` 的 Codex CLI/客户端。

```sh
codex plugin marketplace add carpe-horam/task-progress-codex --ref v0.2.0
codex plugin add task-progress@task-progress-codex
python3 "${CODEX_HOME:-$HOME/.codex}/plugins/cache/task-progress-codex/task-progress/0.2.0/service_manager.py" install
```

完成后重启 Codex。插件安装不会单独启动后台服务，最后一条命令是必需步骤。使用稳定安装的 Python 3；服务会记录该解释器的绝对路径。

### 从旧版本升级到 0.2.0

重新登记来源到固定发布标签，重新安装插件和服务，然后重启 Codex：

```sh
codex plugin marketplace remove task-progress-codex
codex plugin marketplace add carpe-horam/task-progress-codex --ref v0.2.0
codex plugin add task-progress@task-progress-codex
python3 "${CODEX_HOME:-$HOME/.codex}/plugins/cache/task-progress-codex/task-progress/0.2.0/service_manager.py" install
```

这会移除并重新添加 Marketplace 登记。任务数据独立保存在本机缓存中，无需迁移。旧 stdio 会话需重启 Codex 后才释放进程；不要仅刷新 Marketplace 就认为插件和服务已升级。若尚未登记该 Marketplace，直接使用首次安装命令。

### 从本地源码安装

```sh
git clone --branch v0.2.0 --depth 1 https://github.com/carpe-horam/task-progress-codex.git
codex plugin marketplace add ./task-progress-codex
codex plugin add task-progress@task-progress-codex
python3 ./task-progress-codex/plugins/task-progress/service_manager.py install
```

安装后重启 Codex。已有同名 Marketplace 时，先按上面的升级方式移除登记，再添加本地来源；更新源码后也须重新安装插件和服务。

### 全局后台服务（macOS）

所有本机 Codex 会话直接连接 `http://127.0.0.1:53421/mcp`，共享 **一个 Python 服务进程**。会话连接和 UI 标签页仍是独立的，任务按 `session_id` 隔离；共享进程不意味着将所有请求串行执行。服务由用户 LaunchAgent `io.task-progress.mcp` 托管，登录后自动启动、异常退出后自动恢复。不会为每个会话额外启动 Python 代理。

安装器将运行文件复制到 `~/Library/Application Support/Task Progress/releases/`，插件缓存更新不会删除正在使用的运行文件。凭据位于 `~/.cache/task-progress/service/http-headers.json`，目录权限为 0700、文件权限为 0600。Codex 通过 `http_headers_helper` 短暂读取凭据；服务只监听本机环回地址，校验 Host 和 Bearer 凭据并拒绝浏览器跨域请求。

从本地仓库管理服务：

```sh
python3 plugins/task-progress/service_manager.py status --json
python3 plugins/task-progress/service_manager.py install   # 安装、更新或重新启动已停止的服务
python3 plugins/task-progress/service_manager.py stop      # 停止当前服务；影响所有使用它的会话
python3 plugins/task-progress/service_manager.py uninstall # 移除 LaunchAgent，保留任务、凭据和运行版本
```

从旧版 stdio 升级时，已经打开的会话可能继续持有旧进程，需重启 Codex 才能全部释放并切换；安装器不会强制终止这些会话。端口被非本插件服务占用时安装会报错，不会停止未知进程。服务日志在 `~/.cache/task-progress/service/service.{stdout,stderr}.log`。

自动后台管理目前支持 macOS。Linux 可先运行 `service_manager.py prepare`，再运行 `shared_service.py`，并自行配置用户服务；Windows 的后台管理与客户端凭据助手尚未验证。
