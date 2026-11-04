function eaP0Owned(name) {
  return typeof name === "string" && name.indexOf("EA-P0-") === 0;
}

function eaP0Clean(config) {
  config.proxies = (config.proxies || []).filter(function(p) { return !eaP0Owned(p.name); });
  config["proxy-groups"] = (config["proxy-groups"] || []).filter(function(g) { return !eaP0Owned(g.name); });
  config.rules = (config.rules || []).filter(function(r) { return r.indexOf(",EA-P0-") < 0; });
  return config;
}

function eaP0Deny(config) {
  config = eaP0Clean(config);
  config["proxy-groups"].push({name:"EA-P0-Deny",type:"select",proxies:["REJECT"]});
  config.rules.unshift("DOMAIN,managed.p0.invalid,EA-P0-Deny");
  return config;
}

function eaP0Apply(config, landingPort) {
  config = eaP0Clean(config);
  var candidates = config.proxies.map(function(p) { return p.name; });
  if (!candidates.length) throw new Error("P0 fixture needs inline source nodes");
  config.proxies.push({name:"EA-P0-Landing",type:"http",server:"127.0.0.1",port:landingPort,"dialer-proxy":"EA-P0-Front"});
  config["proxy-groups"] = config["proxy-groups"].concat([
    {name:"EA-P0-Front",type:"select",proxies:candidates,"empty-fallback":"REJECT"},
    {name:"EA-P0-Managed",type:"select",proxies:["EA-P0-Landing"]},
    {name:"EA-P0-Deny",type:"select",proxies:["REJECT"]}
  ]);
  config.rules = [
    "AND,((NETWORK,UDP),(DOMAIN,managed.p0.invalid)),EA-P0-Deny",
    "DOMAIN,managed.p0.invalid,EA-P0-Managed"
  ].concat(config.rules);
  return config;
}

function main(config, profileName) {
  var original = JSON.parse(JSON.stringify(config));
  try {
    // Parse the user's source inside the catch boundary, including syntax errors.
    var userMain = eval("(function(){\n" + eaP0Settings.userSource + "\nreturn main;\n})()");
    var result = userMain(JSON.parse(JSON.stringify(config)), profileName);
    if (!result || typeof result !== "object" || Array.isArray(result))
      throw new Error("user script did not return a configuration object");
    return eaP0Apply(result, eaP0Settings.landingPort);
  } catch (error) {
    return eaP0Deny(original);
  }
}
