# 会话卡片 / 标签页生命周期专项审查

源码 HEAD `8be2767`，原始 app.html 与原始 server.py。Chrome 156.0.8078.48；仅使用 `/tmp/task-progress-tab-audit-ui/sessions` 内独立进程数据，MCP Apps 父宿主为模拟实现。所有 hold 均扣住真实服务端响应后延迟投递，不改写产品函数或伪造服务端状态。未操作真实用户任务。宿主外层原生标签页关闭行为仍需另作宿主验证。

复现脚本 `repro.cjs` 退出0；四项断言全部复现。主结果 `results.json`，每例对应 `*-trace.json` 含真实调用参数、返回状态、模式请求及高度通知。

## T1 / P2：迟到的 inline check 会冻结已经打开的 fullscreen

位置：app.html:275-285（尤其282-285）；相关209、1413-1418。

顺序：
1. main 以 inline 登记；打开另一 fullscreen tab。
2. main 的 check_ui_view 已得到真实 hidden_by_fullscreen，但宿主延迟交付该响应。
3. 正常关闭 tab，main 切到 fullscreen，main 的 fullscreen 注册成功。
4. 交付先前 inline 的 hidden_by_fullscreen 响应。
5. 后台启动 root running，等待5.6秒。

实际：check返回分支不复查当前displayMode或请求世代，将fullscreen的hiddenByFullscreen重新设true；页面仍可见，但canPoll永久false。界面root停在“可执行”，服务端root为running；get_progress调用数1→1，自动刷新文案“自动刷新暂停”。heartbeat登记不会清除这个flag，只有再次切换模式等外部动作才恢复。

建议：check、register响应绑定发出时的模式/任务来源/生命周期世代；返回时拒绝已过时的结果。hidden_by_fullscreen只允许作用于仍处在对应inline模式的视图。

证据：`late-check-fullscreen.png`、`late-check-fullscreen-trace.json`、results case late_inline_check_freezes_fullscreen。截图中的main [inline]是宿主标记的初始挂载模式，响应交付时已通过host-context-changed切换fullscreen，trace中有成功fullscreen注册。

## T2 / P2：注册响应未到就关闭，遗漏 unregister，隐藏会话卡片约35秒

位置：app.html:1439-1442（尤其1440）；viewRegistered仅在319置true。

顺序：main inline正常登记；新tab的fullscreen register服务端已经registered，但响应仍在宿主传输；此时向tab发送ui/resource-teardown并移除iframe。

实际：tab此时viewRegistered=false，teardown根本不发unregister。原main在check中隐藏；tab关闭后3.2秒仍是card tab-hidden，注册表存在剩余28秒的fullscreen租约。闭合frame前后均没有unregister调用。

建议：退出处理应覆盖已发出、尚未确认的注册；给注册/退出增加生命周期令牌或撤销标记，使晚到的注册不能在退出后保留或复活租约。不能仅依据已收到成功响应的viewRegistered判断是否需要注销。

证据：`close-during-register.png`、`close-during-register-trace.json`、results case close_before_register_reply_leaves_lease。

## T3 / P2：较旧tab回到inline被拒绝，但遗留fullscreen租约仍隐藏最新卡片

位置：app.html:307、264-267；server.py:1423-1424（superseded提前返回，未迁移已有views[vid]的fullscreen模式）。

顺序：先以来源sequence1打开fullscreen tab；再以更新来源sequence2挂载main inline；main正常因tab隐藏；tab切回inline。

实际：tab使用较旧来源register inline，得到superseded后折叠为card inline-stale compact并停止heartbeat；server仍把tab登记为fullscreen。main保持card tab-hidden，只剩折叠按钮可见，完整会话卡片需等租约过期才能恢复。

建议：把模式迁移/旧租约释放与inline拥有权竞争分开；即便不能成为最新inline，也应撤掉同view原fullscreen租约。前端在确认退出fullscreen时也应显式释放该模式登记。

证据：`older-tab-return.png`、`older-tab-return-trace.json`；返回序列fullscreen registered → inline superseded，注册表fullscreen数量仍1。

## T4 / P2：刷新过的tab回到inline再关闭，会使所有存活会话卡片永久折叠

位置：app.html:1386-1389、1424-1426、264-267；server.py:1425-1428、1496-1498。

顺序：main inline为revision1；打开tab fullscreen；后台root启动，使revision2；tab刷新到revision2；tab切回inline，登记成为latest_inline；main在check中superseded并停止全部轮询；随后关闭tab并正常unregister。

实际：唯一剩余main仍card inline-stale compact；注册表只剩main，却保留已经退出tab的latest_inline来源revision2/sequence4。关闭后3.3秒main的check调用数2→2，不会自动重新竞争或恢复完整卡片；继续等35秒也无fullscreen租约可过期来改变这个分支。

建议：保持会话卡片的来源顺序与fullscreen刷新数据版本独立；fullscreen返回inline不应自动替换已有会话卡片拥有权。注销拥有者时应选取仍存活的inline回退者，并允许因临时拥有权失去资格的卡片重新检查恢复。

证据：`newer-tab-close.png`、`newer-tab-close-trace.json`，results case refreshed_tab_return_then_close_folds_all_inline。此例与单纯历史卡片折叠不同：被提升的tab已经退出，当前只有一张存活会话卡片。

## T5 / P3 附注：隐藏高度0会被ResizeObserver改写为10

位置：app.html:197-198 与1500-1502；body最终padding为5px（35）。

T2 trace捕获真实通知：main height0 → 1毫秒后height10 → 下次约3秒check时height0。ResizeObserver从documentElement计算body剩余padding高度，没有遵循tab-hidden的0高度。按宿主最后通知调整iframe时，隐藏位置会短暂留下10px空白/产生高度跳动。

建议：所有height通知共用函数；tab-hidden时稳定返回0。

证据：`close-during-register-trace.json` 的sizes数组。未将模拟宿主固定500px iframe边框形成的大块留白作为此问题的证据；实际产品通知仅为10px。

## 原有回归测试

按主会话要求，另原样串行运行一次 `tests/test_view_mode_regressions.cjs`，日志保存在 `original-test_view_mode_regressions.log`；该测试夹具本身没有atomic_reason，需区分规划契约拒绝与本轮产品生命周期复现。没有修改该测试源码。

原测试最终exit1：`planning_required: 空的根任务不能开工`。`tests/test_view_mode_regressions.cjs:113`未指定atomic_reason，119启动root被服务端拒绝，尚未进入124-134的关闭/恢复步骤。因此该失败证明现有夹具与规划契约不一致，不能用这次测试声称关闭恢复通过或失败。本轮四个专用复现全部使用合法atomic_reason，不受该夹具问题影响。
