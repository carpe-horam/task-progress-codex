# Task Progress 全局共享后台改造与验收

日期：2026-10-27（Asia/Shanghai）。工作区：`/Users/machengling/.codex/worktrees/4d11/task-process`。版本：0.2.0。本报告记录提交前的验收结果，包含此前的 UI、状态协议与 hooks 修复；验收时尚未提交或推送代码。

## 结果与生效边界

已将默认插件连接从每个 MCP 上下文启动 Python stdio 服务，改为所有本机用户会话直接连接同一个认证 HTTP 后台。已安装并启动用户 LaunchAgent，且通过正常 Codex CLI 安装流程安装 0.2.0 插件。当前后台 PID 为 **61385**，只监听 `127.0.0.1:53421`。

两个新建的独立 Codex app-server 客户端通过**已安装插件配置**连接成功，均读取 14 个工具和 0.2.0 UI 资源；连接前后后台 PID 和 instance_id 一致，产生 18 个请求，没有创建 Codex 会话或发送模型 turn。

**当前桌面内已存在的 stdio 上下文没有强制迁移。** 安装后快照仍有 20 个旧 `server.py` 进程由桌面 app-server PID 5314 持有，工作目录经 lsof 核验属于本插件。需要正常重启 Codex 才能全部释放并重新连接共享后台。本次未停止这些进程，也没有重启用户桌面。因此“新连接共享一个后台”已验证，“当前所有旧上下文都已切换”尚未成立。进程数量是当时快照，不将此前的 22/23 个当作当前数量。

## 实现

- `.mcp.json` 使用 `http://127.0.0.1:53421/mcp`，移除每会话 `command/args/cwd/env_vars`。`http_headers_helper` 仅短暂运行 cat 读取本机凭据，无常驻 Python 代理。
- `shared_service.py` 提供 stateless Streamable HTTP JSON 响应。JSON-RPC request ID 属于各自请求，业务仍由 `session_id`、`task_instance_id` 和 attempt 标识隔离，允许并发执行。
- 固定环回监听、Host 校验、Bearer 认证、拒绝 Origin/CORS、32 个工作线程上限、请求体大小和 socket 超时限制、响应后关闭连接。
- OS 文件锁防止同一状态目录重复启动；正常退出保留已接受写入，崩溃后锁自动释放。持久化任务继续使用已有 session 文件，不迁移、不清理历史。
- 长期存活进程的 session 线程锁改为弱引用，等待和持锁期间保留强引用，空闲锁可释放；避免历史会话数量持续增加带来的锁对象积累。
- `service_manager.py` 实现 prepare/install/status/stop/uninstall；私有凭据、稳定 release、LaunchAgent 管理、端口与所有权保护、升级失败恢复旧安装。
- release 位于 `~/Library/Application Support/Task Progress/releases/0.2.0-bd3b16dcb690fa920170`，与 Codex 可替换插件缓存分离。LaunchAgent 使用稳定 Python 3.12 的绝对路径。
- 自动后台管理目前支持 macOS。Linux 可手动准备凭据并运行 daemon；Windows 安装和客户端助手未验证。

## 验证

| 层级 | 结果 | 证据 |
| --- | --- | --- |
| Python 全量回归 | 108 项通过，6.531 秒 | [python-tests.log](2026-10-27-global-service-evidence/python-tests.log) |
| HTTP 多客户端 | 12 个并发客户端、相同 JSON-RPC ID、独立 session，全部由同一 PID 响应；8 次并发重复初始化保持幂等 | `tests/test_shared_service.py` |
| HTTP 生命周期与边界 | 重复 daemon 拒绝、正常/强制终止后数据恢复、认证与 Host/Origin 检查、客户端断开后继续可用 | 同上；强制终止仅针对隔离测试 fixture |
| 安装器 | 14 项隔离测试，涵盖路径空格、权限、并发 prepare、幂等、端口冲突、未知 job 保护、启动/健康失败回滚、停止卸载保留数据 | `tests/test_service_manager.py`；使用真实 loopback HTTP 和模拟 launchctl |
| 浏览器 HTTP | 3 组通过：会话/全屏隔离、并发更新隔离、关闭一个会话后继续可用；0 JS 异常 | [结果](2026-10-27-global-service-evidence/shared-http-browser.json)、[截图](2026-10-27-global-service-evidence/shared-http-session-b.png) |
| 原生 Codex / 隔离服务 | 2 个客户端并发读取工具和资源成功 | [codex-isolated.json](2026-10-27-global-service-evidence/codex-isolated.json) |
| 原生 Codex / 已安装插件 | 2 个客户端直接使用已安装配置成功 | [codex-installed.json](2026-10-27-global-service-evidence/codex-installed.json) |
| 本机激活 | LaunchAgent loaded、认证健康检查通过、仅环回监听；重复 install 返回 changed=false、PID 不变 | [activation.json](2026-10-27-global-service-evidence/activation.json) |
| 版本一致性 | source、installed、runtime 的 server/app/HTTP daemon/manager/manifest SHA-256 一致；source/installed MCP 配置一致 | 同上；凭据仅记录权限 0600，不保存内容 |
| 旧上下文 | 安装后仍有 20 个旧 stdio 进程；未终止 | [legacy-after-install.json](2026-10-27-global-service-evidence/legacy-after-install.json) |
| 独立集成复核 | HTTP/core 契约、弱引用锁、认证、生命周期、安装回滚未发现阻止激活的问题 | `/root/global_transport` 只读复核 |

浏览器环境：Chrome 156.0.8078.48，真实 app.html + 真实 HTTP daemon + 模拟 MCP Apps 父宿主；截图采集于 2026-10-27。原生传输使用桌面附带 `codex-cli 0.164.0-alpha.11.1` 的真实 app-server；未把这项传输验证等同于原生桌面窗口/标签页的视觉验收。macOS 27.0.1（26A434）。未运行模型 turn、未修改 hook trusted_hash、未停止其他应用的进程。

## 运行方式

参见 [中文 README](../../README.md) 的“全局后台服务”。可从仓库运行：

```sh
python3 plugins/task-progress/service_manager.py status --json
python3 plugins/task-progress/service_manager.py install
```

`install` 同时用于升级和重新启动已停止的服务。`stop` 或 `uninstall` 会影响所有连接该后台的会话，应在用户要求时执行。重启 Codex 后旧 stdio 进程的释放情况需要重新核验；本报告不提前宣称已经完成。

协议依据：[Codex MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)、[Codex 配置 schema](https://learn.chatgpt.com/docs/config-schema.json)、[MCP Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。本机真实客户端验证作为当前插件兼容性的直接证据。
