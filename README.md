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
- upstream group discovery
- HTTP/SOCKS5 landing management
- `source`, `fixed`, `automatic`, and `direct` front-hop policies
- last-known-good compiled generations

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

1. Save one complete Mihomo subscription URL and refresh it.
2. Add an HTTP or SOCKS5 landing proxy.
3. Enable selected policy groups and choose their front-hop policy.
4. Save to generate the local subscription URL.
5. Copy that URL into a Mihomo-compatible client.

Runtime state is stored under the ignored `dev-data/` directory by default.
The state and generated client YAML contain plaintext landing credentials and
use filesystem mode `0600`; do not upload or share that directory.

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

Fixtures cover compiler behavior plus gateway state, LKG refresh failures,
loopback HTTP APIs, Host validation, tokenized delivery, and the management UI.

Tests do not read FlClash files, preferences, databases, controllers, production
state, or network services.

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
