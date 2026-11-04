# 一次性 Linux 客户端验证

只用于 macOS ARM64 宿主上的 QEMU/HVF 隔离实验，不是安装器或产品客户端适配器。
首轮结果见 [F011](../../功能列表/F011-隔离客户端验证.md)，后续优化与边界见 [F012](../../功能列表/F012-P0覆写优化.md)。

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
| guest_verge_files.py / guest_verge_runtime.py | Verge 多文件计划、原核心热应用与成功前真实路由核对 |
| guest_core_api.py / guest_script_eval.py | 限定测试控制器传输、guest 已有 JavaScriptCore 的无界面计算 |
| guest_client_probe.py / guest_restore_conflict.py | 原生内核运行证据、实际流量与用户修改冲突场景 |
| guest_verge_hot_cycle.py / guest_verge_safety_checks.py | 真实长连接热应用/恢复及脚本失败的 TCP/UDP 对照 |

guest 入口拒绝在非 Linux 或非 egress-p0-client-vm 主机运行。P0 模块保持在 tests/p0，
不反向成为应用依赖；没有为每个能力增加空层或包装类。

## 准备与离线切换

依赖已有 QEMU（qemu-system-aarch64、qemu-img）、macOS hdiutil、Python/PyYAML。
按 client_vm_assets.json 的官方 URL 下载三个固定文件到 dev-data/p0/client-vm/。
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

安装只登记当时已有订阅；后来新建/导入的订阅不具备相同静态失败保护，不能据全局脚本
继承正常就声称持续严格约束通过。全局/逐订阅完整扩展、并发及原生缓存仍受 F012 限制。

动作入口如下，动作名按上述顺序选择，不需要 EgressAnchor 后台。单纯改文件仍不触发
原生配置重建；新 install/restore 是显式加载原核心，不是给客户端新增了文件监视能力。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_verge_overlay.py install
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_client_probe.py verge-managed --managed --source a-v2 --user-rule
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_restore_conflict.py verge
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_verge_overlay.py restore
```

已有 A/B 与合成用户规则时，guest_verge_hot_cycle.py 可单独检查安装/恢复不重启客户端、
只关闭目标旧连接且另一条真实长连接保持。它会真实安装并恢复覆写，不与手动安装交叠。
guest_verge_safety_checks.py 提供 inject user-throw/native-syntax/native-limit、verify-denied LABEL、
verify-udp LABEL 和 clear；每次注入后原生更新，再验证；clear 后重新更新并确认健康再测下一项。
它只适用于已安装静态保护的测试订阅，清理仅撤销自己的故障注入。

## FlClash 场景

1. 停止测试 Verge，启动测试 FlClash；使用声明按当次授权处理，不能设置标记跳过协议。
2. 原生导入 A、关闭系统代理/TUN。seed-user 只对新标准测试订阅执行一次：加入已知
   合成追加规则并开启仅 guest 回环的测试 API。此项测试设置不属于产品自动接入能力。
3. install-naive 不迁移标准追加规则，运行证据应显示 user_rule_present=false；restore 清理。
4. install 只显式迁移已知合成 DOMAIN 规则；验证实际落地后更新 A，再导入/切换新 B。
   B 没有关联脚本，预期走原机场，属于 P0 失败对照，不能视为持续保护成功。
5. 切回 A 并重启验证；修改脚本冲突检查后，切到更新后的 B 执行 restore，再切回 A 核验。
   自动运行关闭时，重开需原生点击启动；探测必须在启动后执行，不把重启成功等同恢复运行。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_flclash_overlay.py seed-user
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_flclash_overlay.py install
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_client_probe.py flclash-managed --client flclash --managed --source a-v3 --user-rule
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_restore_conflict.py flclash
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py guest -- python3 /home/egress-test/p0-assets/guest_flclash_overlay.py restore
```

上述原型只支持指定测试 schema 和合成标准规则；遇到其他输入会拒绝。
不覆盖运行中的数据库，不回滚整个数据库/目录，不支持任意用户扩展迁移或中断恢复事务。

## 收尾与结果解释

本地 P0 边界回归可单独运行（不启动 VM 或绑定端口）：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/p0 -p 'test_*.py'
```

这 14 项检查不能替代上面的原生流量场景，也不并入原有 139 项的通过数量。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py collect-evidence
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/client_vm.py stop
```

证据导出只复制自有 reports、合成源版本/端口/访问记录，并生成 SHA-256 清单；不复制偏好、
数据库、恢复收据、控制密钥或真实配置。镜像、密钥、原始报告均留在被忽略的 dev-data/p0/。
报告标签避免重复；探测失败会更新该标签的错误报告并返回非零，不能沿用旧成功结果。
源版本按实测动态变化，命令中的 a-v2/a-v3 是示例，需对应实际源与客户端当前配置。
