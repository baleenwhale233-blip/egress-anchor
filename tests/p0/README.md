# P0 隔离验证

本页说明独立内核验证；真实 Linux 客户端流程另见 [一次性 VM 说明](client_vm.md)。
两者都不访问日常客户端数据。以下独立内核命令不会启动 EgressAnchor 后台或桌面客户端，不开启 TUN。
所有目标、HTTP/SOCKS5 前置与落地、TCP/UDP 回显及控制接口均为本机临时端口。

## 固定版本

版本、发布源码提交和校验和见 [versions.json](versions.json)。当前为 macOS arm64 的
Mihomo v1.19.33；独立内核结果不等于客户端内置内核及 GUI 流程验收。
二进制保存在被 Git 忽略的 dev-data/p0/tools/，不进入源码或交付包。

首次准备（已有匹配二进制可跳过）：

```bash
mkdir -p dev-data/p0/tools
curl --fail --location --output dev-data/p0/tools/mihomo-darwin-arm64-v1.19.33.gz https://github.com/MetaCubeX/mihomo/releases/download/v1.19.33/mihomo-darwin-arm64-v1.19.33.gz
shasum -a 256 dev-data/p0/tools/mihomo-darwin-arm64-v1.19.33.gz
```

确认归档 SHA-256 与 versions.json 完全一致，再解压：

```bash
gzip -dk dev-data/p0/tools/mihomo-darwin-arm64-v1.19.33.gz
chmod u+x dev-data/p0/tools/mihomo-darwin-arm64-v1.19.33
```

执行前还会校验解压后哈希，拒绝运行未固定的二进制。运行依赖沿用项目的 Python/PyYAML。

## 执行

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/p0/kernel_checks.py --binary dev-data/p0/tools/mihomo-darwin-arm64-v1.19.33 --report dev-data/p0/kernel-report.json
```

执行需要允许本机临时端口绑定。脚本只终止自己启动的内核子进程，结束时清理临时配置。
结果包括范围、UTC 时间、检查项和模拟代理路径；未预期的异常也会写入新报告，避免读取旧结果。

当前预期返回非零：原编译输出的 HTTP 落地 UDP 路径存在已复现的绕出缺口。
这是未加固基线；按严格模式策略加入显式拒绝规则后，受保护 UDP 应被拦截，无关 UDP 保持。
拒绝 UDP 的规则只添加在对照夹具中，未修改生产编译器，不能据对照通过宣称缺口已修复。
此脚本不随默认 unittest 自动运行；后续修复后应更新预期并加入正式回归测试。

## 代码职责

- loopback_fixtures.py：回显、转发代理和协议客户端积木，拒绝非本机目标。
- kernel_session.py：一个固定内核子进程的配置、生命周期与控制接口。
- kernel_checks.py：P0 场景编排与证据输出，不实现产品服务策略。
- gpt_strict_checks.py：GPT/Codex 合成入口的独立落地、UDP 拒绝和无关策略保持对照。
- automatic_front_checks.py：以真实新连接验证原生前置故障切换、固定落地及全部前置失效。

自动前置夹具在 1 秒间隔下模拟新拨号故障。lazy 健康检查需要近期使用，测试保持服务请求，
不通过手动选节点或强制组测速实现切换；本地结果不代表默认间隔、真实链路或零请求失败。

阶段结论和未完成项目见 [F009](../../功能列表/F009-P0验证.md)；
原生客户端的实际结果与限制见 [F011](../../功能列表/F011-隔离客户端验证.md)。
Verge 热应用/恢复、优先级和脚本失败保护的后续结果见 [F012](../../功能列表/F012-P0覆写优化.md)。
