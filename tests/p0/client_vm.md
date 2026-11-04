# 一次性 Linux 客户端验证

只用于 macOS ARM64 宿主上的 QEMU/HVF 隔离实验，不是安装器或产品客户端适配器。
首轮结果见 [F011](../../功能列表/F011-隔离客户端验证.md)，后续优化见 [F012](../../功能列表/F012-P0覆写优化.md)，
新订阅与两版 FlClash 的最新范围以 [F013](../../功能列表/F013-新订阅与FlClash接入.md) 为准。

## 隔离边界与职责

虚拟机使用自有磁盘、专用 SSH 密钥和测试用户，无宿主目录共享。
仅包安装阶段在线；测试前必须关机、离线启动并检查隔离，不能直接在日常客户端尝试。
GUI 使用 noVNC；自动操作仍由受支持的 UI 工具完成，不通过隐藏 WebKit/Tauri 接口调用菜单。
不得读写旧生产网关、8787、宿主 FlClash/Verge 数据、系统代理、DNS 或 TUN。

| 文件 | 职责 |
| --- | --- |
| client_vm_seed.py / client_vm_assets.json | 固定资产校验、专用密钥、NoCloud 与磁盘准备 |
| client_vm.py | 自有 VM 的 QMP/SSH/资产复制、UI 隧道、证据导出 |
| check_vm_isolation.py | 受控宿主服务及外部 TCP 不可达对照 |
| guest_desktop.py / guest_client.py | 虚拟桌面与身份校验后的原生客户端生命周期 |
| guest_subscriptions.py | 版本化本地订阅和可观测前置/落地；重启复用已记录端口 |
| guest_overlay_common.py / guest_overlay_script.js | 共用的脚本生成、原子写入及受控进程能力 |
| guest_verge_overlay.py / guest_flclash_overlay.py | 各自原生持久化与选择性恢复流程 |
| guest_client_context.py / guest_flclash_store.py | 固定测试身份、版本路径和 schema 2/21 原生规则存储 |
| guest_flclash_guards.py / guest_flclash_runtime.py / guest_flclash_follow.py | 不改标准模式的拒绝保护、热启用及原生重建跟随 |
| guest_verge_files.py / guest_verge_runtime.py | Verge 多文件计划、原核心热应用与成功前真实路由核对 |
| guest_core_api.py / guest_script_eval.py | 限定测试控制器传输、guest 已有 JavaScriptCore 的无界面计算 |
| guest_overlay_compute.py / guest_native_validation.py | 共用候选计算、只读 geodata 资源别名及固定验证器 |
| guest_client_probe.py / guest_restore_conflict.py | 原生内核运行证据、实际流量与用户修改冲突场景 |
| guest_verge_hot_cycle.py / guest_verge_safety_checks.py | 真实长连接热应用/恢复及脚本失败的 TCP/UDP 对照 |
| guest_transition_samples.py | 原生更新窗口的逐连接证据采样；不把未知成功计为通过 |

guest 入口拒绝在非 Linux 或非 egress-p0-client-vm 主机运行。P0 模块保持在 tests/p0，
不反向成为应用依赖；没有为每个能力增加空层或包装类。

## 准备与离线切换

依赖已有 QEMU（qemu-system-aarch64、qemu-img）、macOS hdiutil、Python/PyYAML。
按 client_vm_assets.json 的官方 URL 下载镜像、两版 FlClash、Verge 和旧版 keybinder 兼容库到 dev-data/p0/client-vm/。
prepare 会逐个校验 SHA-256，磁盘/密钥/种子已存在时不重新创建。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py prepare
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py start --online
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- cloud-init status --wait
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py copy-assets
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- sudo apt-get install -y /home/egress-test/p0-assets/FlClash-0.9.2-linux-arm64.deb /home/egress-test/p0-assets/Clash.Verge_2.5.10_arm64.deb
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py stop
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py start
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/check_vm_isolation.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py ui
```

stop 请求正常关机并确认 QMP 已退出，不强停未知进程。引导已完成的现有磁盘直接离线启动，
无需再次在线安装。ui 仅在离线且隔离检查通过后工作，输出本机 SSH 隧道 URL。
没有现成 macOS 测试环境时，本实验只能证明 Linux 共享配置行为。

## 启动合成数据与原生客户端

复制 guest 夹具及 loopback_fixtures.py；此命令只复制明确列出的 P0 文件：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -c 'import sys; from pathlib import Path; sys.path.insert(0,"tests/p0"); from client_vm import copy_files; copy_files([str(p) for p in Path("tests/p0").glob("guest_*.py")]+["tests/p0/guest_overlay_script.js","tests/p0/loopback_fixtures.py"])'
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- sh -c 'nohup python3 /home/egress-test/p0-assets/guest_subscriptions.py > /home/egress-test/p0-subscriptions.log 2>&1 < /dev/null &'
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_client.py start verge
```

订阅为 guest 回环 http://127.0.0.1:18880/a.yaml 和 b.yaml。
POST /advance/a 或 /advance/b 只增加源版本，再由原生界面更新，不直接替换客户端下载缓存。
源、前置、落地和回显全部在 guest 回环，域名为 *.p0.invalid。
同时仅测试一个客户端，关闭系统代理和 TUN。

## Verge 场景

1. 原生导入 A；用 seed-user 写入合成用户全局脚本，原生更新以建立运行基线。
2. 原生导入 B。安装前用 profile-conflict-on 添加当前订阅的用户 DIRECT 对照并原生更新；
   此时路径应为 direct。不能在工具挂载后覆盖其脚本来冒充安装前用户扩展。
3. install 组合已有全局/逐订阅脚本、静态保护和必要合并字段，原核心直接热加载并验证路径。
   再原生更新，验证用户脚本仍保留且最终路径经过指定落地。
4. 增加源版本并分别原生更新、切 A/B、重启；证据关联本次 TCP 源端口，不复用其他连接。
5. 冲突检查后 restore 热恢复；确认当前来源、后续节点和原用户 DIRECT 保留。
   再用 profile-conflict-off 清理合成用户对照，原生更新以回到原机场基线。

安装登记当时已有订阅。全局入口在新订阅缺少静态登记时先拒绝，并跳过用户脚本；
reconcile 保留原字节收据并补新挂载。若已经选中新订阅，本轮原型受控重开客户端做完整原生生成，
不从缺少用户扩展的部分快照启用服务；这是有重开影响的路径，产品须先说明。
全局挂载完全失效、大配置、任意扩展和并发仍受 F013 限制，不能泛称无条件持续保护通过。

动作入口如下，动作名按上述顺序选择，不需要 EgressAnchor 后台。单纯改文件仍不触发
原生配置重建；install/restore 显式加载原核心。失败状态恢复会重建原生配置，避免丢失原用户扩展。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_verge_overlay.py install
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_client_probe.py verge-managed --managed --source a-v2 --user-rule
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_restore_conflict.py verge
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_verge_overlay.py restore
```

新订阅用例可先通过 guest_verge_import.py 使用公开本机 deep-link 导入，再原生选择 Fresh-P0，
verify-denied 确认先拒绝，然后运行 guest_verge_overlay.py reconcile。重开后必须再执行实际路径核对，
不能把 reconcile 的“已登记”当作服务已验证。已有挂载被修改时会先拒绝登记/恢复。

已有 A/B 与合成用户规则时，guest_verge_hot_cycle.py 可单独检查安装/恢复不重启客户端、
只关闭目标旧连接且另一条真实长连接保持。它会真实安装并恢复覆写，不与手动安装交叠。
guest_verge_safety_checks.py 提供 inject user-throw/native-syntax/native-limit、verify-denied LABEL、
verify-udp LABEL 和 clear；每次注入后原生更新，再验证；clear 后重新更新并确认健康再测下一项。
它只适用于已安装静态保护的测试订阅，清理仅撤销自己的故障注入。

## FlClash 场景

1. 停止其他测试客户端，启动 FlClash；首次使用声明按当次授权处理，不能写标记跳过。
2. 原生导入 A。seed-user 只对新标准测试订阅执行一次：创建合成用户规则、关闭系统代理，
   开启仅 guest 回环的测试 API；TUN 保持关闭。此测试准备不是产品自动接入能力。
3. install 在预检后新增自有原生拒绝行，保留 standard 模式和用户规则；重开后按提示手动启动。
4. verify-denied 确认目标先拒绝、无关请求可用，再 apply 热启用固定落地。不能把写入成功算成实际生效。
5. 原生导入并选择新标准订阅；后台未启时仍应先拒绝，再启动 guest_flclash_follow.py 自动热启用。
   后台停止后原生更新仍应拒绝；恢复后台后同一落地恢复。可用 guest_transition_samples.py 做 30 秒采样。
6. 停止测试跟随后台，运行冲突检查，再 restore；只移除自有行和包装，不回滚整个数据库。
   关闭自动运行时输出“在 FlClash 点击启动”，这是用户确认的首版方案；启动后再验证独立可用。

旧 install-naive 的负向记录保留在 F011 和历史提交 ecf18f1；当前不再以切换模式作为标准接入方式。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_flclash_overlay.py seed-user
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_flclash_overlay.py install
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_flclash_overlay.py apply
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_client_probe.py flclash-managed --client flclash --managed --source a-v3 --user-rule
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_restore_conflict.py flclash
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_flclash_overlay.py restore
```

接入/恢复的原生规则写入发生在测试客户端停止期间。拒绝行生效后，跟随过程只热更新候选，
不再改变 profile 模式或重开客户端。自定义原生模式、新未登记脚本、守门行修改会拒绝并标为待核验。
不覆盖整库，不把有限版本检查当作完整跨进程事务或任意扩展迁移支持。

## 与日常版本一致的 0.8.94

此版本按用户本机 Info.plist 确认加入，但只在 VM 的 egress-0894 独立账户中运行。
不卸载 0.9.2，不共用客户端目录；代码共享在 /opt/egress-p0-code，用户数据仍各自私有。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- sudo dpkg -i /home/egress-test/p0-assets/libkeybinder-3.0-0_0.3.2-1.1build2_arm64.deb
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- sudo python3 /home/egress-test/p0-assets/guest_0894_prepare.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- sudo -H -u egress-0894 python3 /opt/egress-p0-code/guest_client.py start flclash-0894
```

其数据目录是 .local/share/FlClash，schema 2 的规则归属在 profile_rule_mapping、排序也不同。
其他命令用同一独立用户、/opt/egress-p0-code 路径；支持 --client 的命令传 flclash-0894，启动/冲突命令
保留其客户端位置参数。不能直接写新版表字段。
首次原生导入后再 seed-user；协议接受不能通过该命令代办。脚本模式缺失 QuickJS 桥时，预检在写入前拒绝；
标准模式不依赖该原生脚本引擎，仍可验证。它不代表 macOS 旧版脚本不可用。

测试环境旧版曾在最大化时出现 OpenGL 帧超时；保持默认窗口，noVNC 选“无”缩放并横向滚动完成操作。
这不是日常 macOS 或真实 Linux 硬件的表现结论。测试完恢复 noVNC 的本地缩放。

## 收尾与结果解释

本地 P0 边界回归可单独运行（不启动 VM 或绑定端口）：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/p0 -p 'test_*.py'
```

当前 31 项检查不能替代上面的原生流量场景，也不并入原有 139 项的通过数量。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py collect-evidence
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py stop
```

证据导出只复制自有 reports、合成源版本/端口/访问记录，并生成 SHA-256 清单；不复制偏好、
数据库、恢复收据、控制密钥或真实配置。镜像、密钥、原始报告均留在被忽略的 dev-data/p0/。
报告标签避免重复；探测失败会更新该标签的错误报告并返回非零，不能沿用旧成功结果。
源版本按实测动态变化，命令中的 a-v2/a-v3 是示例，需对应实际源与客户端当前配置。
