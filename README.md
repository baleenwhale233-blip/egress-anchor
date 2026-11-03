# EgressAnchor / 出口锚点

A local subscription gateway for stable, policy-bound egress.

EgressAnchor compiles a complete Mihomo configuration so selected policy groups
must use a configured HTTP or SOCKS5 landing proxy. Unmanaged routing remains
semantically unchanged. If a required front hop or landing fails, the managed
path fails instead of falling back to an ordinary upstream exit.

EgressAnchor does not promise account safety, bypass platform enforcement, or
grant permission to ignore provider terms. AI tools are an important use case,
not the product boundary.

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

Packaging and disposable client smoke tests remain future work.

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

1. Choose **添加订阅**, enter a name and a complete Mihomo subscription URL,
   then choose **保存并读取**.
2. The URL is used once and discarded after the validated snapshot is stored.
3. Add an HTTP or SOCKS5 landing proxy.
4. Enable selected policy groups, choose their front-hop policy, and save to
   activate that source.
5. Import more sources when needed and switch between their saved snapshots.
6. Copy the stable local URL into a Mihomo-compatible client.

Runtime state is stored under the ignored `dev-data/` directory by default.
The state and generated client YAML contain plaintext landing credentials and
use filesystem mode `0600`; do not upload or share that directory.

## Test a managed chain

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

Relevant Mihomo documentation:

- [dialer-proxy](https://wiki.metacubex.one/en/config/proxies/dialer-proxy/)
- [proxy groups](https://wiki.metacubex.one/en/config/proxy-groups/)
- [built-in outbound policies](https://wiki.metacubex.one/en/config/proxies/built-in/)

## License

MIT. See [LICENSE](LICENSE).
