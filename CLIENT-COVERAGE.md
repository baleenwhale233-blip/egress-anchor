# 桌面客户端覆盖调研

调研日期：2026-11-03（Asia/Shanghai）。
状态：适配优先级建议，尚未进行客户端安装、配置写入或兼容性实测。

## 结论与范围

用户已选择桌面端优先，并确定 Clash Verge Rev 与 FlClash 为首批完整适配目标。
v2rayN 的 Mihomo 模式作为额外候选，若复用成本可控且通过验收则追加。
后续再扩展 Clash Party、Clash Nyanpasu、Sparkle，ClashMi 桌面版与
GUI.for.Clash 继续专项评估。

订阅的添加、更新和切换继续由原客户端完成；出口锚点读取当前来源，
叠加服务分流与落地策略，并回显实际生效状态。

## 数据口径

- 未找到这些项目可统一比较、可核验的活跃用户统计。
- GitHub 星标仅作为项目关注度指标，不能换算为用户人数或市场份额。
- 星标是累计数据，跨平台、多内核项目的星标也不能全部归给桌面 Mihomo 用户。
- 分支项目的受众可能重叠，不叠加星标计算覆盖率。
- 以下数值为本次检索的近似值；同时核对官方项目说明、Mihomo 客户端名单和发布页。

## 桌面候选

| 客户端 | GitHub 星标约数 | 平台与范围 | 本次核对的发布标签 | 建议 |
| --- | ---: | --- | --- | --- |
| [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev) | 15.6 万 | Windows、macOS、Linux；Mihomo | [v2.5.10](https://github.com/clash-verge-rev/clash-verge-rev/releases/tag/v2.5.10) | 首批完整自动化目标 |
| [FlClash](https://github.com/chen08209/FlClash) | 6.1 万 | Windows、macOS、Linux，另有 Android；本轮评估桌面版 | [v0.9.2](https://github.com/chen08209/FlClash/releases/tag/v0.9.2) | 首批完整自动化目标，已有自用基线 |
| [Clash Party](https://github.com/mihomo-party-org/clash-party) | 2.7 万 | Windows、macOS、Linux；需确认实际运行内核和版本 | [v2.0.4](https://github.com/mihomo-party-org/clash-party/releases/tag/v2.0.4) | 后续优先验证 |
| [Clash Nyanpasu](https://github.com/libnyanpasu/clash-nyanpasu) | 1.3 万 | Windows、macOS、Linux；多内核，限定 Mihomo 模式 | [v2.0.1](https://github.com/libnyanpasu/clash-nyanpasu/releases/tag/v2.0.1) | 后续优先验证 |
| [ClashMi](https://github.com/KaringX/clashmi) | 1.0 万 | Windows、macOS、Linux，另有移动版；本轮评估桌面版 | [v1.0.31.1703](https://github.com/KaringX/clashmi/releases/tag/v1.0.31.1703) | 验证桌面接入与持久覆写能力 |
| [Sparkle](https://github.com/xishang0128/sparkle) | 8000 | Windows、macOS、Linux；Mihomo，Clash Party 分支 | [1.26.10](https://github.com/xishang0128/sparkle/releases/tag/1.26.10) | 后续优先验证，保留与 Party 的共同适配机会 |
| [GUI.for.Clash](https://github.com/GUI-for-Cores/GUI.for.Clash) | 2800 | Windows、macOS、Linux | [v1.28.1](https://github.com/GUI-for-Cores/GUI.for.Clash/releases/tag/v1.28.1) | 长尾候选，评估扩展与配置接口 |

其他有关注度的桌面项目：

- [v2rayN](https://github.com/2dust/v2rayN)：约 12 万星标，支持多个内核，
  不将全部关注度计入 Mihomo 受众。其官方文档包含 Mihomo 端口和配置说明，
  值得单独验证 Mihomo 模式的配置生成、Mixin 和订阅切换行为。
- [ClashX.Meta](https://github.com/MetaCubeX/ClashX.Meta)：约 6300 星标，macOS 候选；
  内核版本、维护情况及自动化入口仍待针对性验证。

## 扩大覆盖的实现原则

1. 共用服务策略模型与 Mihomo 配置生成逻辑，避免按客户端复制业务规则。
2. 每个客户端分别实现识别、来源读取、持久覆写、生效加载与必要重启。
3. 将兼容能力分开记录：配置生成兼容、只读状态与检测、完整自动应用、严格设置。
4. 配置格式相同不等于自动化流程相同；某项能力未验证时，不宣称完整兼容。
5. 不依靠用户切换订阅后再补写原文件，来宣称严格策略在整个过程中持续生效。
6. 不要求用户把机场订阅管理迁移到出口锚点；只在现有客户端之上管理服务与落地。

## v2rayN 初步可行性

公开源码的 Mihomo 自定义配置生成路径提供 Mixin 合并入口，支持对列表前置、
追加和移除项目，并在写出运行配置之前应用。这为复用服务规则和落地生成逻辑
提供了入口；目前的证据只覆盖该路径，不代表所有 v2rayN 内核或配置类型均可直接接入。

额外需要验证：

- `EnableMixinContent` 的启用方式，以及保留用户原有 Mixin 的合并和撤销机制。
- 来源切换、订阅更新和客户端重启后，工具规则仍能持续生效。
- 实际运行的内核、监听端口、控制端点和配置类型的对应关系。
- 热加载、严格配置与 v2rayN 自身生成的端口、IPv6 和 TUN 设置之间的关系。

因此结论为“具有复用基础，值得做适配验证”，尚非已实现兼容。

## 多客户端识别与应用边界

- 分开记录已安装、正在运行、当前管理对象和已验证的接管路径。
- 结合系统代理/TUN、客户端实例和控制端点识别对象，避免把端口可达视为唯一依据。
- 多客户端或应用专用代理可能使接管关系不唯一，无法确定时让用户指定管理对象，
  同时保留真实的冲突提示。
- 每次应用前复核目标实例和当前来源，不对所有检测到的客户端批量写入配置。
- Mihomo 的 `/configs` 返回基本运行设置，节点、策略组和规则另有接口；
  仍需客户端适配器读取相应来源并负责持久配置，不能把单个接口当作完整配置导出。

## 每款客户端的验收项目

- 自动识别正确实例、内核和当前订阅，回显节点与来源。
- 用户在原客户端更新、切换订阅后，独立服务策略仍正确生效。
- Claude、ChatGPT、DeepSeek 与自定义服务能分别设置出口，其他流量保留原规则。
- 默认前置自动选择，固定前置的配置与失效状态有明确结果。
- 普通改动热加载；严格设置确需重启时，按确认流程执行并复核。
- 客户端重启、电脑唤醒、落地不可用后恢复时，出口约束和状态正确。
- 备份与撤销只处理工具管理的改动，保留用户随后在客户端进行的订阅操作。

## 桌面范围外的后续方向

[Clash Meta for Android](https://github.com/MetaCubeX/ClashMetaForAndroid) 约 4.9 万星标，
[OpenClash](https://github.com/vernesong/OpenClash) 约 2.8 万，
[ShellCrash](https://github.com/juewuy/ShellCrash) 约 1.4 万。
这些项目值得后续关注，但移动系统权限、远程路由器接入和服务管理需要另行设计，
不纳入本轮桌面自动化目标。

## 主要依据

- [Mihomo 官方客户端名单](https://wiki.metacubex.one/en/startup/client/client/)
- [Clash Verge Rev 仓库元数据](https://api.github.com/repos/clash-verge-rev/clash-verge-rev)
- [FlClash 仓库元数据](https://api.github.com/repos/chen08209/FlClash)
- [v2rayN 仓库元数据](https://api.github.com/repos/2dust/v2rayN)
- [v2rayN 发布包与内核说明](https://github.com/2dust/v2rayN/wiki/Release-files-introduction)
- [v2rayN 界面与 Mihomo 控制端口说明](https://github.com/2dust/v2rayN/wiki/Description-of-some-ui)
- [v2rayN Mihomo 配置生成及 Mixin 源码](https://github.com/2dust/v2rayN/blob/master/v2rayN/ServiceLib/Services/CoreConfig/CoreConfigClashService.cs)
- [Mihomo 运行配置、节点和规则 API](https://wiki.metacubex.one/api/)
- [Clash Verge Rev 持久自定义规则说明](https://www.clashverge.dev/guide/rules.html)
- 各候选项目的官方 README 与上表所列发布页。

本次未测量安装量、日活/月活或完整发布历史的下载数；不据此估算覆盖比例。
