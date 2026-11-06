/** Same-origin JSON transport. Server error codes stay separate from UI copy. */
const messages = {
  SERVICE_DOMAIN_INVALID: '请填写域名，例如 example.com；不要包含网址前缀、端口或通配符。',
  SERVICE_DOMAIN_CONFLICT: '域名与其他服务重叠。请调整域名范围，或在原服务中排除这部分域名。',
  SERVICE_DOMAINS_REQUIRED: '请至少填写一个服务域名。',
  SERVICE_POLICY_INVALID: '请检查服务名称、域名和出口选择。',
  SERVICE_CHANGED: '此服务已在其他窗口修改。草稿已保留，请关闭后重新打开最新设置。',
  SOURCE_CHANGED: '当前订阅已变化。草稿已保留，请刷新页面后重试。',
  SOURCE_NAME_CONFLICT: '已有同名订阅，请换个名称。',
  SOURCE_NAME_INVALID: '请填写订阅名称。',
  SOURCE_ACTIVE: '此订阅正在提供本地配置，请先生成其他订阅的配置。',
  SOURCE_NOT_CONFIGURED: '请先导入一份完整的 Mihomo 订阅。',
  UPSTREAM_URL_INVALID: '请输入以 https:// 或 http:// 开头的订阅地址。',
  UPSTREAM_FETCH_FAILED: '暂时无法下载订阅。请检查地址和网络后重试。',
  UPSTREAM_HTTP_ERROR: '订阅服务未返回有效配置。请检查订阅地址或稍后重试。',
  UPSTREAM_FORMAT_HTML: '这个地址返回了网页，请使用完整的 Mihomo 配置订阅地址。',
  UPSTREAM_FORMAT_NODE_ONLY: '这份订阅只有节点，请使用包含策略组和规则的完整 Mihomo 配置。',
  UPSTREAM_FORMAT_URI_LIST: '暂不支持节点链接列表，请使用完整的 Mihomo 配置。',
  FULL_CONFIG_REQUIRED: '需要包含节点、策略组和规则的完整 Mihomo 配置。',
  LANDING_URL_INVALID: '代理地址格式不正确，请填写 SOCKS5 或 HTTP 地址。',
  LANDING_INVALID: '请检查代理名称、类型、服务器和端口（1–65535）。',
  LANDING_IN_USE: '此代理仍被服务使用，请先更改服务出口。',
  GENERATED_NAME_COLLISION: '订阅中已有同名的生成策略，请更换订阅或调整服务。',
  INTERNAL_ERROR: '本地服务未能完成操作。草稿已保留，请稍后重试。',
};
export async function request(path, {method = 'GET', data} = {}) {
  let response;
  try {
    response = await fetch(path, {method, cache: 'no-store',
      headers: data === undefined ? {} : {'Content-Type': 'application/json'},
      body: data === undefined ? undefined : JSON.stringify(data)});
  } catch {
    throw new Error('无法连接本地服务。请确认出口锚点正在运行，然后重试。');
  }
  let result;
  try { result = await response.json(); }
  catch { throw new Error('本地服务返回了无法读取的结果，请重试。'); }
  if (!response.ok || !result.ok) {
    const detail = result.error || {};
    const safeChinese = typeof detail.message === 'string' && /[\u3400-\u9fff]/.test(detail.message);
    const error = new Error(messages[detail.code] || (safeChinese ? detail.message : '操作未完成，请检查输入后重试。'));
    Object.assign(error, {code: detail.code, field: detail.field, serviceIds: detail.service_ids || []});
    throw error;
  }
  return result;
}
