# View registry专项审查 — HEAD 8be27675b7815310d93e58cae6f3c8cca9ea957a

审查 `plugins/task-progress/server.py` register/check/unregister、租约、inline排序与任务切换。仅读取产品源码，复现通过原服务函数与隔离SESSION_DIR；未运行浏览器，未修改产品代码或用户数据。与此前初始化幂等/线性attempt泛审查不同，本轮只报告视图状态序列。`time.time()`用mock推进租约，不真实等待35秒。

## R01 [P2] 同一个工具结果的多个挂载实例同时成为 latest，无法去重

位置：server.py:1423–1427、1458–1467。

触发：取得一次合法render/init/get工具结果；两个不同view_id用完全相同state_revision/view_sequence/task_instance_id登记inline。两次register均返回registered；两个check均返回latest。

原因：latest_inline只保存来源三元组，没有唯一owner view_id；同序来源登记既不拒绝后者也不替换owner，check只比较来源元组。因此单个结果在宿主重挂载或同时保留两个iframe时，两者都完整展示/轮询，不能实现单卡片去重。这不同于旧审计F11的“较旧结果抢新结果”：此处结果完全相同。

建议：对来源元组维护一个live owner/mount generation，登记同源新挂载时明确唯一归属；check对owner身份校验。不要只比较source tuple。

证据：results.json → same_result_two_mounts；两个视图都latest，registry.views有两个inline记录。

## R02 [P2] 最新inline移除后留下不可认领水位，存活卡片永久superseded

位置：server.py:1489–1498（删除view但保留latest_inline）；1423–1424（旧来源无法重新登记）；1462–1467（检查依据已删除来源）。

触发：A inline登记；用同任务较后工具结果登记B inline；A变superseded；unregister B。此时views里只剩A，latest_inline仍指B来源，A check与重新register均继续superseded。没有存活view能成为latest，除非新调用render并挂载一个更新来源。

fullscreen关联序列也已复现：A inline→tab fullscreen→任务状态变更，tab刷新取得新revision→tab转inline用新来源登记→A superseded→tab teardown unregister→A仍永久superseded。这是用户“关闭后不恢复”的后端解释候选。是否宿主实际执行tab→inline→teardown需UI代理核对。

前端会把superseded卡片sourceSuperseded设为true并停止检查，见app.html:252–272，因此修复服务端选择策略时还需考虑卡片重新激活；本报告没有浏览器证明。回退规则不能简单取消全部顺序保护，否则会重现旧卡片抢占F11，应区分“最新已发放来源水位”和“当前活跃owner”，并明确关闭/重挂载恢复策略。

建议：unregister时更新live owner状态，并给仍存活卡片可恢复的检查/通知路径；保留独立来源水位抵御迟到历史卡片。

证据：results.json → unregister_latest_has_no_live_owner、fullscreen_polled_source_becomes_inline_owner_then_removed。

## R03 [P2] fullscreen转inline被排序拒绝时，没有释放原fullscreen租约

位置：server.py:1423–1424提前返回；1427–1428本应覆盖display_mode；1475–1485仍按旧记录隐藏inline。

触发：A inline来源seq1；tab以seq1登记fullscreen；同一revision较后render生成seq2并登记B inline；tab转inline仍使用seq1。register返回superseded，却没有把views[tab]从fullscreen移除/转换。B在第0秒与第34秒check仍hidden_by_fullscreen；第36秒变latest。

影响：实际标签页已不在fullscreen，卡片仍被隐藏，最长35秒。前端收到superseded设置viewRegistered=false（app.html:307、collapseSupersededView），resource-teardown只对viewRegistered调用unregister（app.html:1440），因此可能不会主动释放这个残留租约。请UI代理核对真实界面路径。

建议：已登记view的mode变更或退出fullscreen应先原子释放旧租约，再独立决定inline来源能否成为owner。inline排序拒绝不能保留与实际模式不符的fullscreen记录。

证据：results.json → rejected_fullscreen_to_inline_keeps_fullscreen_lease。

## R04 [P2，时序条件] 关闭后的迟到heartbeat可复活租约

位置：server.py:1497仅pop，无released generation/tombstone；1427–1428任意相同view_id后续register重新写入35秒租约。

触发：tab已fullscreen登记→unregister tab→inline check=latest→在途旧heartbeat随后到达register(tab, fullscreen)→接受registered→inline再次hidden_by_fullscreen。

影响：关闭标签页后卡片先恢复又消失，直到残留租约过期。这是确定的服务端状态序列；触发前提为宿主将关闭和已有heartbeat乱序送达/执行，尚未对原生宿主确认。

建议：登记返回lease/mount generation；unregister绑定并终结该generation，迟到renew需拒绝。新挂载使用新view_id或generation。不能仅靠前端disposed检查阻止已经发出的请求。

证据：repro_unregister_race.py、unregister-race-results.json。

## 其他核对（暂不另计缺陷）

任务A fullscreen旧租约存在时，初始化任务B并登记B inline，旧tab用A实例续租返回superseded，但旧租约仍保留；B fullscreen新tab登记返回duplicate_fullscreen、B inline hidden_by_fullscreen。证据results.json → task_switch_retains_old_fullscreen_lease。当前产品按session限制单fullscreen且旧tab预期刷新到新任务，此行为可能是设计，需要与真实tab生命周期合并判断，不应直接宣称跨任务错误隔离。旧tab失联时至多保留35秒，属于现有租约设计边界。

过期fullscreen租约可在普通inline check中自动移除并恢复latest，已有测试覆盖的正常关闭/到期路径没有发现新的数据写锁问题。

## 复现命令

均不在Dagu白名单，直接本地运行；约0.1秒，无浏览器，无产品目录写入：

```sh
python3 /tmp/task-progress-tab-audit-registry/repro_registry.py
python3 /tmp/task-progress-tab-audit-registry/repro_unregister_race.py
```

第一个脚本断言4个异常序列并记录任务切换观察；第二个断言关闭/迟到heartbeat竞态。results.json、run.log和unregister-race-results.json保存完整registry快照。
