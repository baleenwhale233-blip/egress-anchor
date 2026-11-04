# F014 macOS 虚拟机预检

日期：2026-11-04（Asia/Shanghai）。状态：只读预检完成，未安装/运行 macOS VM。
用户确认没有外置磁盘，本轮先完成预检和 Linux 隔离验证，不下载恢复镜像。

## 本机实测

| 项目 | 结果 |
| --- | --- |
| 宿主 | Apple Silicon，macOS 26.5.2 / 25F84 |
| 内存 | 16 GiB |
| Apple Virtualization 支持 | true |
| 最新兼容恢复镜像 | macOS 26.6.2 / 25G83，Apple API 返回 supported=true |
| 镜像大小（仅 HEAD） | 19,772,231,540 bytes，约 18.4 GiB |
| 镜像声明最低配置 | 2 个虚拟 CPU、4 GiB 内存 |
| 本轮估计空间预算 | 镜像 + 40 GiB 系统/回滚余量，合计约 58.4 GiB；不是 Apple 最低磁盘要求 |
| 当时可用空间 | 39,030,493,184 bytes，约 36.4 GiB，未满足上述预算 |

恢复目录初次查询失败；按 Apple 要求给自有预检二进制添加 virtualization entitlement 后查询成功。
没有下载 IPSW、创建磁盘或 VM，没有配置宿主代理、DNS、TUN 或其他网络设置。
只读结果：dev-data/p0/macos-vm/preflight-signed.json。恢复 URL 来自 Apple API，未使用第三方镜像。

## 复现与后续

源码为 [macos_vm_preflight.swift](../tests/p0/macos_vm_preflight.swift)；配套签名声明只用于自有测试二进制。
它只查询硬件、镜像元数据及磁盘预算，不包含安装或启动 VM 的代码。

后续有足够磁盘空间时，可用 Apple Virtualization 建立独立 macOS 来宾，不共享日常数据目录，
用于 TUN、权限、原生应用启动和恢复专项；当前 Linux 结果不能替代这一步。
机制依据 [Apple 官方示例](https://developer.apple.com/documentation/virtualization/running-macos-in-a-virtual-machine-on-apple-silicon)
与 [恢复镜像查询 API](https://developer.apple.com/documentation/virtualization/vzmacosrestoreimage/latestsupported)。
