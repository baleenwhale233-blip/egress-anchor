# 浏览器回归

仅使用临时目录和合成数据。需要允许本机临时端口绑定，不访问日常客户端。

## 服务 UI

启动 `python3 tests/browser/service_ui_fixture.py`，在 Playwright MCP 中打开打印的 localhost 地址。
使用 `browser_run_code_unsafe(filename)` 按顺序执行：

1. `service_ui.js`：新工作区默认值、四种出口、持久保存、资源与服务编辑、响应式和键盘。
2. `service_ui_failures.js`：断网、过期修订、单行提交、失效引用、订阅切换与背景滚动。
3. `service_settings.js`：三档保护预设、分层详情、草稿关闭提示、只读发现、响应式和失败重试。
4. `client_states.js`：未发现/不支持/失败/多目录/运行信息与快速添加落地返回草稿。

另启动 `python3 tests/browser/service_ui_fixture.py --empty`，打开新地址后执行 `service_ui_empty.js`。
该脚本验证未导入订阅、无落地资源、先保存服务以及长名称。

以上五个脚本在独立 BrowserContext 窗口内运行，并在 `finally` 中关闭测试窗口，
保留可见预览的原始宽度与路由。不要对交给用户的预览调用 `page.setViewportSize`：
它会固定页面视口，导致浏览器窗口缩放后页面宽度仍不变。
结束后在首页运行 `window_resize.js`，直接改变真实窗口宽度验证页面随窗口伸缩；
该检查不会设置模拟视口，并会恢复原窗口尺寸。

脚本返回检查列表与数量；截图写入 `dev-data/ui-qa/`。当前脚本的截图路径为本机仓库绝对路径，
迁移工作区时需调整。先核对实际 `innerWidth/innerHeight`，避免浏览器记忆的站点缩放影响截图。
失败注入通过 Playwright 拦截完成，预期 HTTP 错误不代表产品正常加载时存在控制台错误。

首次脚本要求全新夹具；重新运行时重启夹具。结束时按 Ctrl+C 清理临时状态。
非空夹具还创建合成 FlClash SQLite 与偏好文件，只读发现限定在该临时目录；空夹具不设置客户端目录。
客户端异常与多目录界面通过合成响应检查，原文件只读和控制器访问边界由 `tests/test_client_discovery.py` 验证。

## 旧版维护

启动 `python3 tests/browser/modal_fixture.py`，打开打印的 `/legacy` 地址。
原有 `modal_scroll.js` 和 `diagnostic_states.js` 仍在此入口运行；旧 HTML 内容保持不变。
