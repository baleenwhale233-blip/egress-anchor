# EgressAnchor / 出口锚点

A local subscription gateway for stable, policy-bound egress.

EgressAnchor compiles a complete Mihomo configuration so selected policy groups
must use a configured HTTP or SOCKS5 landing proxy. Unmanaged routing remains
semantically unchanged. For TCP, if a required front hop or landing fails, the
managed path fails instead of falling back to an ordinary upstream exit.

EgressAnchor does not promise account safety, bypass platform enforcement, or
grant permission to ignore provider terms. AI tools are an important use case,
not the product boundary.

The new service compiler explicitly rejects UDP for ISP routes, verified with
pinned Mihomo v1.19.33. The legacy policy-group compiler still has the UDP
fall-through limitation documented in the [P0 findings](功能列表/F009-P0验证.md#3-udp-缺口与后续要求).
Neither result establishes complete strict-mode, DNS/WebRTC or desktop-client protection.

## Status

V0.1 includes the fixture-tested compiler and a loopback-only local gateway:

```python
compile_subscription(source_yaml, policy) -> CompileResult
```

- Chinese management UI on `127.0.0.1:8788`
- tokenized `/sub/<token>` subscription endpoint
- one-time import of up to 20 local source snapshots
- client-style selection and fail-closed source activation
- HTTP/SOCKS5 landing management
- `source`, `fixed`, `automatic`, and `direct` front-hop policies
- last-known-good compiled generations
- per-group front-hop/chain delay tests and direct landing authentication/exit-IP checks
- service dashboard with Claude, ChatGPT/Codex, DeepSeek and custom service settings
- four service exits, domain exclusions, conflict validation and per-record revision checks
- generated service rules and ISP UDP rejection, with last-good delivery during source changes

P1 service configuration, generation, APIs and the core UI are connected. See
[implementation scope](功能列表/F017-服务出口UI与生成.md) and [frontend acceptance](docs/frontend-qa-2026-11-06.md).
Client application remains manual: the UI reports pending updates and unverified
paths. The settings drawer separates protection presets/details, domain scope,
verification and advanced parameters. Recommended/custom protection remains disabled.
The CLI now discovers FlClash metadata read-only on macOS/Linux and optionally
reads loopback `GET /configs` for mode/TUN information; discovery is not application
or service-path verification. See [current scope](功能列表/F018-服务设置与只读接入.md)
and [latest frontend acceptance](docs/frontend-qa-2026-11-07.md).
Disposable Linux client prototypes have partial validation evidence in
[F013](功能列表/F013-新订阅与FlClash接入.md); production adapters, macOS acceptance
and packaging remain unfinished.

## Project navigation

Start with the [feature index](功能列表/索引.md) and [current work](任务进展/当前.md),
then follow only the links relevant to the task. The
[maintenance boundaries](功能列表/F008-维护与分层.md) describe incremental module splitting.
Product and development plans describe future work; the runtime instructions below describe the implemented local workflow.
The [V0.1 engineering design](DESIGN.md) records the current implementation contract;
the [changelog](CHANGELOG.md) summarizes changes and known limitations.

## Why the topology fails closed

For a managed group named `AI`, the compiler transforms this:

```text
AI ──> upstream-a | upstream-b | provider nodes
```

into this:

```text
AI ──> generated landing ──dialer-proxy──> generated front group
                                               ├── upstream-a
                                               ├── upstream-b
                                               └── provider nodes
```

`AI` becomes a single-member `select`. A client cannot restore an old ordinary
node selection because that node is no longer a valid member of `AI`. The front
group may select or health-check upstream hops, but every successful managed
connection still ends at the landing proxy.

## Requirements

- Python 3.9+
- PyYAML 6.x

Install the runtime dependency in an isolated environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Run the local gateway

```bash
python3 app.py
```

Open <http://127.0.0.1:8788> and follow the page:

1. Open **设置 → 导入订阅**, enter a name and a complete Mihomo subscription URL,
   then choose **保存并读取**.
2. The URL is used once and discarded after the validated snapshot is stored.
3. Add an HTTP or SOCKS5 proxy under **落地代理** if an ISP exit is needed.
4. Under **服务出口**, choose an exit and save. All builtin services initially
   inherit the subscription; none silently enables direct routing or strict mode.
5. **仅走机场** offers fixed source nodes/groups without direct routes. Dynamic
   provider groups and legacy-managed groups are currently excluded.
6. Copy the local subscription address from **设置** into a Mihomo-compatible
   client and update it. Local generation does not confirm client application.

Switching source snapshots preserves the previous delivery until a new complete
configuration is generated. Missing references can be repaired one service at a
time. Custom services can be added or removed; domain exclusions resume original
subscription routing rather than forcing direct access.

The panel checks FlClash once on opening. **设置 → 客户端 → 重新检查** refreshes
the observation through `GET /api/client`; normal state reads do not scan the client.
Known data directories, SQLite schemas 2/21 and macOS plist preferences are supported
for metadata reads. The database/WAL is copied into a private temporary directory;
changing or unsupported snapshots are rejected. No source files, selected profile,
TUN or running mode are changed. Credentials and subscription URLs are not returned.
This does not establish compatibility for automatic application or recovery.

The original policy-group interface and diagnostics remain under **设置 → 旧版策略维护**
at `/legacy`. Navigation/action SVGs are sourced from official Lucide; service
marks use Lobe Icons. Licenses and provenance are in [static/assets](static/assets/SOURCES.md).

Runtime state is stored under the ignored `dev-data/` directory by default.
The state and generated client YAML contain plaintext landing credentials and
use filesystem mode `0600`; do not upload or share that directory.

## Test a managed chain

These are legacy policy-group diagnostics at `/legacy`; service-level probes on
the new dashboard remain unverified until client integration is implemented.

1. Save and activate the source, then update the local subscription in your client.
2. Expand **客户端连接设置** and enter the client's loopback external-controller
   address and optional access key. The default is `http://127.0.0.1:9090`.
3. Choose **测试** beside a saved, enabled policy group.

The expanded report separates:

- **整链**: a real delay request through the generated landing outbound, including
  its front hop; a timeout remains a failed result.
- **组内选中**: whether the running client has the singleton landing selected.
- **前置**: the running client's selected front node and its individual delay.
  Direct mode has no front-hop measurement.
- **落地 ISP**: an independent direct HTTP/SOCKS5 authentication and exit-IP lookup.
  Its elapsed verification time is labelled separately from network delay.

Diagnostics describe the client's current running configuration. Refresh the
client subscription after source, landing, or binding changes. The controller
API does not expose the gateway's generation ID, so it cannot prove which local
subscription generation the client imported. This feature never switches client
selections, reloads the client, or rewrites its files. Unsaved rows and outdated
gateway-page requests are rejected; results are discarded if local configuration
changes while a test runs. At most two tests run together, and subscription
delivery stays available during tests.

The access key stays in mode-`0600` local state and is omitted from API responses
and reports. Controller requests bypass environment proxies, reject redirects,
and accept loopback endpoints only. Delay requests use the
[Mihomo individual proxy delay API](https://wiki.metacubex.one/en/api/)
with an HTTPS connectivity target; landing verification contacts
[ipify](https://www.ipify.org/) through that landing. No download-bandwidth test
or batch test of all candidates is performed.

## Compiler interface

```python
from pathlib import Path

from egress_anchor import CompileError, compile_subscription

source = Path("tests/fixtures/inline.yaml").read_text(encoding="utf-8")
policy = {
    "version": 1,
    "managed_groups": {
        "AI": {
            "landing": {
                "type": "socks5",
                "server": "landing.example.invalid",
                "port": 1080,
                "username": "fixture-user",
                "password": "fixture-password",
                "udp": True,
            },
            "front_hop": {"mode": "source"},
        }
    },
}

try:
    result = compile_subscription(source, policy)
except CompileError as error:
    print(error.code, error.safe_message, error.line, error.column)
else:
    Path("/tmp/egress-anchor-fixture.yaml").write_text(
        result.compiled_yaml,
        encoding="utf-8",
    )
```

Generated client YAML necessarily contains landing credentials. The result
report and `CompileResult` representation do not.

## Front-hop modes

- `source`: preserve the managed source group's original selection policy and
  provider `use` behind the landing.
- `fixed`: allow exactly one named inline proxy or group as the front hop.
- `automatic`: build a `url-test` group from explicit or source candidates.
- `direct`: connect from the local core directly to the landing proxy.

All non-direct modes use `empty-fallback: REJECT`. Generated names are stable,
exact collisions fail compilation, and existing user-owned `GW·...` or `EA·...`
names are never removed by prefix.

## Tests

Run the standard-library suite:

```bash
python3 -m unittest discover -s tests -v
```

Fixtures cover compiler behavior plus source import and switching, URL-free
state migration, LKG activation failures, loopback HTTP APIs, Host validation,
tokenized delivery, and the management UI.

Tests use disposable loopback HTTP/controller/proxy fixtures. They do not read
FlClash files, preferences, databases, live controllers, production state, or
external network services.

UI acceptance uses [disposable browser fixtures](tests/browser/README.md).
Service traffic checks use the existing pinned core:

```bash
python3 tests/p0/service_checks.py --binary dev-data/p0/tools/mihomo-darwin-arm64-v1.19.33 --report dev-data/ui-qa/service-kernel.json
```

The service checks disable TUN and keep all traffic, including health checks,
on loopback. They do not verify desktop-client import or automatic application.

## Development isolation

- Treat any existing gateway repository and active client data as read-only.
- Never point development at real client profiles, databases, preferences,
  controllers, runtime files, or credential stores.
- EgressAnchor HTTP binds only to `127.0.0.1:8788` by default.
- Development state belongs under this repository's ignored `dev-data/` path.
- Client fixtures belong under `tests/fixtures/` only.

## Compatibility

The compiler targets current Mihomo-compatible full configurations. Node-only
subscriptions and historical Clash dialects are out of scope for V0.1.

Before claiming client compatibility, generated fixtures still need validation
with a pinned Mihomo binary and disposable import smoke tests in at least FlClash
and Clash Verge Rev. No real local client profile may be used for those tests.

The first [pinned-core P0 checks](tests/p0/README.md) now exercise loopback traffic,
reloads and selective connection closure. They do not complete desktop-client
acceptance and intentionally report the UDP limitation above as a failing check.

Relevant Mihomo documentation:

- [dialer-proxy](https://wiki.metacubex.one/en/config/proxies/dialer-proxy/)
- [proxy groups](https://wiki.metacubex.one/en/config/proxy-groups/)
- [built-in outbound policies](https://wiki.metacubex.one/en/config/proxies/built-in/)

## License

MIT. See [LICENSE](LICENSE).
