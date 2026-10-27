# 服务端修复复核（2026-10-27）

当前未提交 server.py，HEAD 8be27675b7815310d93e58cae6f3c8cca9ea957a；执行前后 SHA-256 均为 abc3ff032397f01feb7c30cf4b09898bdf9da6b2eb61d3eef9e8df667ed7a2aa。仅导入工作区当前服务端，所有状态与证据在 /tmp/task-progress-fix-verify-registry。设置 dont_write_bytecode，并在 import 时将 Path.home 临时指向隔离目录，以避免默认缓存副作用。未修改产品代码、安装副本、原报告，未运行浏览器。

## 原发现逐项结论

| 编号 | 服务端结论 | 正向验收/实际结果 | 当前 server.py 行号 |
|---|---|---|---|
| UI02 | 已修复（服务端范围） | 旧合格 owner 在新 owner 注销后立即 latest，重新登记 accepted；fullscreen 刷新新 revision 后返回 inline 不再提权，原卡直接恢复 latest，关闭 tab 后仍 latest | 1375–1398、1450–1475、1501–1508、1521–1524 |
| UI03 | 已修复（服务端范围） | 同源 mount-1/mount-2 check 为 superseded/latest；mount-1 重试仍 superseded，不反抢；mount-2 注销后 mount-1 恢复 latest | 1463–1471、1501–1502 |
| UI04A | 已修复（服务端范围） | 登记已接受后注销立即释放租约；先注销再到达初次登记也拒绝 superseded，原卡保持 latest。前端确实发送 teardown 由浏览器代理验证 | 1419–1420、1513–1525 |
| UI04B | 已修复（原顺序场景） | 旧 tab 返回 inline 时仍 superseded，但存储 mode 已 inline/expires_at=null，新的 inline 立即 latest，不必等待35秒 | 1422–1430、1450–1474 |
| UI05 | 已修复（报告中的注销乱序） | unregister 后旧 heartbeat 无论晚1秒或更久均 superseded，原卡 latest；墓碑保留 | 1419–1420、1495–1496、1519–1524 |
| F01 | 未修复 | 无 session 同 request_id 重试产生不同 session/instance；重规划后重放丢失 epoch1/新节点并创建新 instance；A→B running→A重试将 B 归档为 running、当前变 pending A | 772、793–815、1090–1091 |
| F02 | 未修复 | A启动→pending→B启动→A无assignment迟到completed，仍被接受；整体completed，assignment保留B，note来自A；接口没有attempt_id参数 | 1234–1240、1278–1288、1302–1317 |

额外旧卡恢复保护：较新 owner 存活时才到达的旧来源挂载先判 superseded，owner 注销后仍 superseded；真正曾合格的旧卡恢复 latest。此正向断言通过，避免把“所有旧卡放开”误当修复。

## 额外模式乱序协议缺口（条件性）

正向断言“降回inline完成后，之前在途的fullscreen heartbeat不能恢复旧模式”失败。服务端实际：inline owner/tab fullscreen→tab inline（原卡latest）→同tab旧fullscreen register=registered→原卡hidden_by_fullscreen，tab又获得35秒租约。没有 unregister，released_views为空，因此墓碑不保护模式迁移；register_ui_view 参数没有生命周期/模式转换序号，1472–1474按最后到达请求写入。

这不能解释为正常模式切换必现。app.html:321 使用 viewRegistrationStarted 串行登记，:330 检查本地 viewGeneration，因此未超时的正常切换受到保护。:242在15秒后只删除pending并reject，不向宿主发取消；:357–359释放串行锁并在generation变更后补发inline。该缺口要求旧fullscreen请求还没有在宿主转发/执行，超时后的inline先在服务端执行，随后旧fullscreen才执行。只延迟旧响应不足以触发，因为副作用已完成；server.py main 同步逐行handle，本身也不会倒置同一连接已按序收到的请求。未验证真实宿主是否会这样交付。浏览器代理可另行模拟“延迟转发”，但本复核没有浏览器证据。

## 运行与证据

- 正向脚本 verify_registry.py 共13场景，8通过、5失败；5失败为F01三项、F02一项和上述额外模式乱序。results.json逐项保留断言方向、观测值、完整相关registry、失败原因；run.log保留运行输出。
- 复现：`python3 /tmp/task-progress-fix-verify-registry/verify_registry.py`。脚本总结果包含未修复项，预期退出1；外层最初日志展示命令以cat结束，其shell exit0不能当作验收成功。
- 所有命令不在Dagu白名单，按dagu-test-runner本地执行，单次测试约0.7秒。
- 原缺陷脚本已保存original-reference-*副本，未执行这些旧方向断言。当前verify_registry.py是独立改写的正向验收副本，不能将旧脚本失败当修复成功。
- source-binding.json记录HEAD、当前源码哈希、脚本和参考源哈希。服务端通过不代表已验证浏览器自动恢复，更不代表原生Codex端到端已通过。
