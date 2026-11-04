# EgressAnchor V0.1 Design

Status: compiler and loopback gateway implemented; multi-source snapshot model locked

Date: 2026-08-19
Product: EgressAnchor / 出口锚点

Current verification note (2026-11-04): the singleton topology constrains the
tested TCP paths. Unsupported UDP can continue to a later rule; explicit
service UDP constraints are still pending. See the [P0 findings](功能列表/F009-P0验证.md#3-udp-缺口与后续要求).

## 1. Product contract

EgressAnchor is a local subscription gateway for Mihomo-compatible clients. It
binds selected policy groups to a stable landing proxy, keeps unmanaged routing
semantically intact, and fails closed when the required landing path is not
usable.

It is not an anti-ban product, does not promise access to any platform, and does
not bypass provider policy. AI tools are one use case alongside allowlists,
fixed-region access, streaming, desktop agents, browsers, and CLI tools.

### V0.1 goals

- Compile one complete Mihomo YAML configuration plus a policy into a complete
  Mihomo YAML configuration and a sanitized report.
- Import up to 20 complete subscriptions as local source snapshots through
  one-time URLs, then switch between snapshots without retaining those URLs.
- Make a managed group structurally unable to select an ordinary upstream exit.
- Preserve every unmanaged proxy, group, provider, rule, and global setting at
  the parsed-data level.
- Support HTTP and SOCKS5 landing proxies.
- Support source-policy, fixed, automatic, and direct front-hop policies.
- Be deterministic, all-or-nothing, and safe to call repeatedly.
- Serve one stable tokenized local subscription while source snapshots are
  selected, configured, and activated independently.
- Offer on-demand per-group diagnostics: current front-hop delay, complete-chain
  connectivity/delay, selected landing, and separate landing authentication/IP.

### Not in scope

- Node-only subscription input. V0.1 accepts full Mihomo configurations only.
- Legacy FlClash profile injection, controller mutations, database writes, plist
  changes, reloads, and runtime YAML writes.
- DNS, TUN, IPv6, WebRTC, UDP policy, LAN proxy sharing, and other global client
  settings.
- A polished UI, packaging, installers, containers, or release automation.
- Runtime traffic dashboards or a second proxy-selection engine.
- Merging nodes, rules, DNS, or global settings from multiple subscriptions.
- Automatic source refresh. Re-importing a changed source is a later explicit
  workflow; V0.1 treats a successful import as an immutable local snapshot.
- Claims about account safety, policy evasion, or universal client compatibility.

## 2. Non-negotiable isolation

- Any pre-existing gateway repository and its data directory are production
  references and read-only.
- Active client profiles, databases, preferences, controllers, runtime files,
  and credential stores are outside every development and test path.
- Development HTTP binds to `127.0.0.1:8788` by default.
- All tests use sanitized files under `tests/fixtures/`.
- No test reads an environment-dependent FlClash path.
- Published repository history excludes machine-specific handoff context and
  production runtime state.

## 3. Architecture

The compiler is the deep module. It has one small interface and owns parsing,
validation, topology construction, collision handling, deterministic emission,
and report redaction. Delivery concerns stay in shallow adapters outside this
seam.

```text
                                HTTP adapter (127.0.0.1:8788)
 one-time URL ──fetch──> raw candidate ─┐
                                       │
 policy store ─────────────────────────┼──> compile_subscription()
                                       │          │
 last-known-good raw ──────────────────┘          ├── compiled YAML
                                                  └── sanitized report
                                                           │
                                      atomic cache commit <─┘
                                                           │
 tokenized GET /sub/<token> ───────────────────────────────┘

 Pure compiler seam:

     source_yaml: str + policy: mapping
                     │
                     ▼
       egress_anchor.compiler.compile_subscription
                     │
                     ├── CompileResult(compiled_yaml, report)
                     └── raises CompileError(code, safe_message, line, column)
```

The external compiler interface is synchronous, pure, and side-effect free:

```python
compile_subscription(source_yaml: str, policy: Mapping[str, object]) -> CompileResult
```

Callers need to know only the policy schema, success value, safe error taxonomy,
size limit, and deterministic behavior. Helpers, YAML loader details, graph
validation, and name generation are internal seams and are not test interfaces.

## 4. Policy schema

The V0.1 policy is a JSON-compatible mapping. `managed_groups` is keyed by the
exact source group name, removing list-order ambiguity and duplicate bindings.
At least one managed group is required; an empty policy is rejected rather than
producing a marked pass-through subscription.

```yaml
version: 1
managed_groups:
  AI:
    landing:
      type: socks5             # socks5 | http
      server: landing.invalid
      port: 1080
      username: fixture-user   # optional
      password: fixture-pass   # optional
      udp: true                # optional
    front_hop:
      mode: source             # source | fixed | automatic | direct
```

Front-hop modes:

- `source`: move the source group's original selection behavior and candidates
  behind the landing. This preserves subscription updates and provider `use`.
- `fixed`: require one named inline proxy or group in `proxy`; missing or stale
  references fail compilation.
- `automatic`: create a `url-test` front group. Explicit `proxies`/`use` may be
  supplied; otherwise candidate fields come from the source group. `url` and
  `interval` have conservative defaults and may be overridden.
- `direct`: omit `dialer-proxy`; the core connects directly to the landing proxy.
  The final exit is still the landing, so failure remains closed.

The policy never contains a fallback to an ordinary exit. A required front group
uses `empty-fallback: REJECT` so an empty provider cannot silently become direct.

## 5. Fail-closed transformation

For a managed source group named `AI`:

```text
BEFORE

rules ──> AI (select/url-test/fallback/...)
             ├── upstream-a
             ├── upstream-b
             └── provider nodes

AFTER

rules ──> AI (single-member select)
             └── EA·<id>·landing
                       │ dialer-proxy
                       ▼
                 EA·<id>·front
                   ├── upstream-a
                   ├── upstream-b
                   └── provider nodes
```

The target group keeps its original name because rules and other groups refer to
that name. Its new `proxies` list contains exactly one generated landing proxy,
and it has no `use` or include-all fields. A client may remember any stale choice,
but the only valid current choice is the landing proxy.

The generated landing proxy is the final HTTP/SOCKS5 exit. Its
`dialer-proxy` points to the generated front group unless the policy explicitly
uses `direct`. If the front hop, landing authentication, or landing server fails,
the landing connection fails. No ordinary source proxy remains in the target
group as a fallback.

The source group is copied to the generated front group before the target is
wrapped. Presentation fields `icon`, `hidden`, and `disable-udp` may remain on the
public wrapper; selection and health-check fields live only on the front group.
`hidden: true` is added to the front group as a UI hint, never as a security
mechanism.

If a front group uses `include-all` or `include-all-proxies`, the compiler merges
an exact-name exclusion for every generated landing into `exclude-filter`. This
prevents the runtime expansion from adding a landing back into its own dialer
group. The exclusion names only compiler-generated landings and does not match
unrelated user-owned `EA·...` content.

Mihomo documents that `dialer-proxy` may name either an outbound proxy or a proxy
group, and that the named proxy establishes its connection through that dialer.
It also documents that `hidden` requires front-end support. These facts support
the topology but not the visibility assumption:

- <https://wiki.metacubex.one/en/config/proxies/dialer-proxy/>
- <https://wiki.metacubex.one/en/config/proxy-groups/>

### Nested managed groups

Generated front groups rewrite references to other managed public groups to the
other groups' generated front names. This preserves the original pre-landing
selection graph without accidentally stacking two landing exits.

```text
source A ──> source B

public A ──> landing A ──> front A ──> front B
public B ──> landing B ──> front B
```

The compiler validates the explicit proxy/group/dialer dependency graph before
emission. Any cycle, including a cycle introduced by nested bindings, rejects the
whole compilation.

## 6. Names, ownership, determinism, and repeated calls

Generated names are stable functions of the exact managed group name:

```text
EA·<first 16 hex characters of SHA-256(group UTF-8)>·landing
EA·<first 16 hex characters of SHA-256(group UTF-8)>·front
```

Rules:

1. Proxy and group names must be unique in their shared reference namespace.
2. Existing `GW·...`, `EA·...`, or any other prefixed content is ordinary source
   content and is never stripped by prefix.
3. An exact generated-name collision rejects the whole compile. The compiler does
   not invent a suffix because output names must remain deterministic.
4. Managed groups are processed in Unicode code-point order, independent of
   policy mapping insertion order.
5. Generated proxies and groups are emitted in that same stable order.
6. PyYAML is pinned to the 6.x line. Dump settings fix key order, block style,
   Unicode output, line endings, and terminal newline.

Output begins with a schema marker, a non-secret policy-structure fingerprint,
and a body-integrity fingerprint. The policy fingerprint covers managed group
names and canonical front-hop settings, but never landing endpoint or credential
fields. The body fingerprint detects any change to emitted candidates or topology
and contains no plaintext values; adapters must still treat the entire generated
subscription, including its headers, as secret material. If marked input has both
expected fingerprints and a structurally valid singleton landing topology whose
landing content matches the supplied policy, the compiler returns it byte-for-byte.
A marker or fingerprint alone is never trusted. A marked but mismatched topology
fails with `ALREADY_COMPILED_MISMATCH`; adapters must retain and recompile the raw
upstream source when policy changes.

Repeated `(source, policy)` calls are byte-identical. Feeding a valid result back
with the same policy does not add another wrapper or generated object.

## 7. Parsing and semantic preservation

- Input is UTF-8 text, one YAML document, and at most 8 MiB in V0.1.
- A strict `SafeLoader` rejects duplicate mapping keys instead of accepting
  last-value-wins behavior.
- The root must be a mapping with a `proxy-groups` list. Node-only documents are
  rejected explicitly.
- `proxies`, `proxy-groups`, `proxy-providers`, references, landing fields, and
  managed policy are type-checked before mutation.
- Duplicate proxy/group names and ambiguous references are rejected.
- Malformed YAML, excessive nesting, and unsupported custom tags return a safe
  code plus line/column when available; exception text never includes the
  offending source line.
- YAML anchors and aliases are accepted when `SafeLoader` can resolve them.
  Comments, quoting, anchors, and flow/block style are not byte-preserved.
- Unmanaged values are deep-copied without logical edits. Tests compare parsed
  unmanaged subtrees, not serialized bytes.

The subscription adapter serves a compiled document, so semantic preservation is
the V0.1 contract. Byte-preserving surgery belongs only to the deferred legacy
file adapter.

## 8. Safe error interface

Compilation is all-or-nothing. `CompileError` exposes only:

- a stable machine code;
- a short safe message;
- optional one-based line and column numbers.

Initial codes:

| Code | Meaning |
| --- | --- |
| `SOURCE_EMPTY` / `SOURCE_TOO_LARGE` | Input boundary failure |
| `YAML_INVALID` / `YAML_DUPLICATE_KEY` | Strict parser failure |
| `SOURCE_NOT_MAPPING` | Root is not a mapping |
| `FULL_CONFIG_REQUIRED` | No usable `proxy-groups` list |
| `SOURCE_SCHEMA_INVALID` | A required source field has the wrong shape |
| `DUPLICATE_RUNTIME_NAME` | Proxy/group names are ambiguous |
| `POLICY_INVALID` | Policy or landing fields are invalid |
| `MANAGED_GROUP_MISSING` | Saved group is stale or removed upstream |
| `FRONT_REFERENCE_MISSING` | Fixed/automatic reference is stale |
| `GENERATED_NAME_COLLISION` | Source owns an exact generated name |
| `TOPOLOGY_CYCLE` | Explicit reference graph contains a cycle |
| `ALREADY_COMPILED_MISMATCH` | Marked input is not valid for this policy |

The compiler does not log. `CompileResult.report` contains group names, generated
names, front mode, counts, and warnings, but never source YAML, upstream URLs,
landing server, username, password, or their hashes. Credential fields use a
redacted `repr` if represented by an internal object.

## 9. HTTP subscription adapter contract

The HTTP layer is an implemented delivery adapter outside the compiler module.
Its external interface is tokenized `GET /sub/<token>` on loopback port `8788`,
plus local JSON endpoints used by the management UI.

### Imported source profiles

An upstream URL is a one-time import credential, not durable configuration.
The adapter fetches and validates it within one request, stores the accepted raw
Mihomo document as a mode-`0600` source snapshot, and then discards the complete
URL. Durable source metadata contains a user-supplied name, a display origin
(scheme, host, and optional port), the sanitized catalog, per-source bindings,
and import timestamps. Display origins exclude user information, paths, queries,
and fragments. Existing saved display URLs are reduced to origins when state is
loaded.

```text
 ephemeral URL
      │ request-local only
      ▼
 fetch ──> format adaptation ──> inspect_subscription()
   │                │                    │
   └──── failure ───┴────────────────────┘  no source created; active output unchanged
                                           │
                                           ▼ success
                        sources/<id>/source.yaml + source metadata
                                           │
                         select for editing │ activate after compile
                                           ▼
                                  immutable generation pointer
```

At most 20 source profiles are stored. One profile may be selected for editing
while another remains active and served. Landings are global local resources;
catalogs and bindings belong to their source profile. Activating a profile
compiles its saved snapshot and bindings first, then replaces
`active_source_id` and the generation pointer in one state commit. A failed
activation leaves the prior active profile and compiled output untouched.

`PUT /api/bindings` requires both `source_id` and `bindings`. The source ID must
match the currently selected source under the same lock used to save bindings.
A stale tab receives `SOURCE_CHANGED` with HTTP 409 and must reload before
saving; it cannot overwrite another source's bindings after a selection change.

The management UI exposes an “Add subscription” modal and a source list. Adding
performs “save and read” as one operation. Saved profiles may be renamed locally
without re-fetching or recompiling. There is no separate save-address or refresh
action. Deleting the active profile is rejected until another profile is
activated.

Landing input accepts either the existing explicit fields, one request-local
`socks5://` / `http://` proxy URL, or a common scheme-less proxy string:
`host:port`, `host:port:username:password`, `username:password@host:port`, or
`username:password:host:port`. A URL scheme is authoritative for proxy type;
scheme-less strings use the explicit form type. UDP and the local display name
remain separate fields. Credentials in URLs are percent-decoded. Ambiguous
four-part strings, unbracketed IPv6, paths, queries, fragments, unsupported
schemes, and missing ports are rejected. The raw input is never stored or
returned in public state.

Security defaults:

- Bind `127.0.0.1`; LAN serving is an explicit later option.
- Tokens contain at least 256 bits of randomness and are never logged.
- Request logging omits the path and query string.
- Complete import URLs and authorization headers are never persisted or included
  in logs, public state, or errors.
- Raw and compiled cache files use mode `0600`; directories use `0700`.
- Responses use `Cache-Control: no-store` and a body-derived ETag.
- Fetches enforce scheme allowlists, redirect limits, connect/read timeouts,
  and response-size limits.

### Last-known-good state machine

```text
          import success                 bind + compile success
 EMPTY ──────────────────> SNAPSHOT_READY ─────────────────────> ACTIVE
   │                              │                                │
   │ GET /sub                     │ GET /sub                       │ import/select
   ▼                              ▼                                ▼
 503, never raw                  503                         ACTIVE + selected draft
                                                                  │
                                        activate success ─────────┤ next ACTIVE
                                        activate failure ─────────┘ prior ACTIVE
```

Network errors, non-2xx responses, oversized bodies, invalid YAML, missing managed
groups, stale fixed hops, collisions, and compiler errors never replace the
last-known-good generation. If no successful compiled result exists, the subscription
endpoint returns 503. It never falls back to the uncompiled upstream document.

Imports, selections, activation, binding edits, and landing edits share the
gateway's in-process lock. Concurrent subscription requests serve the current
immutable compiled snapshot. A successful activation replaces compiled output,
report metadata, active source, and the generation pointer together; partial
generations are never visible. State changes are persisted before the candidate
is published in memory, so a failed state write leaves the prior in-memory state
and served generation unchanged.

## 10. Compatibility and verification layers

### On-demand chain diagnostics

`POST /api/bindings/probe` takes `source_id`, `generation`, `group`, and the
page's `binding`. The gateway snapshots the saved active binding, generated names
from its compiler report, landing credentials, and controller settings under its
lock. Unsaved/inactive/stale requests are rejected before network access. Network
work runs after releasing the lock, then the gateway verifies the generation,
selected source, and controller settings again before returning results.

The separate diagnostics module uses only individual Mihomo proxy/selection GETs
and individual proxy-delay GETs. It never calls group-wide delay endpoints, which
can change pinned automatic selections, or any client mutation endpoint. It
requires the public group to select its singleton generated landing, resolves
nested front selections with a bounded walk, and measures the landing outbound
and selected front separately. A changed front selection is reported. Direct
mode skips the front measurement. Failures remain explicit; successful component
checks are never added together to invent an end-to-end delay.

`PUT /api/diagnostics/controller` stores a validated loopback-only address and an
optional key in private local state. Empty key input preserves a saved key;
`clear_secret: true` removes it. Public state exposes only `url` and `has_secret`.
GETs bypass environment proxy configuration and refuse redirects. Raw controller
errors, keys, and landing credentials are excluded from reports.

Landing verification authenticates directly through an HTTP CONNECT or SOCKS5
tunnel and retrieves an HTTPS exit-IP response. Its `elapsed_ms` is verification
duration, distinct from a core's `ms` delay. A failed direct verification can
coexist with a working chain when the local network cannot reach the landing.
The UI labels these scopes separately. Tests use disposable proxy fixtures.

Two diagnostics may run concurrently. Results remain request-local; no test
changes bindings, activates a source, or replaces a compiled generation. The UI
discards results when source/configuration inputs change. The client API does
not expose subscription generation identity: reports describe its named running
outbounds, and users update the client subscription after gateway changes.

| Layer | V0.1 expectation | Environment |
| --- | --- | --- |
| Strict parser + semantic assertions | Required on every test run | sanitized fixtures only |
| Pure compiler behavior | Required on every test run | Python 3.9+ / PyYAML 6.x |
| Pinned Mihomo config check | Add when a binary and checksum are intentionally vendored/downloaded | isolated temp config dir |
| FlClash import smoke test | Required before claiming compatibility | separate disposable fixture profile only |
| Clash Verge Rev import smoke test | Required before claiming compatibility | separate disposable fixture profile only |

No verification layer may point at the installed FlClash application data,
controller, preferences, or production service. Until the final two smoke tests
exist, public wording is “Mihomo-compatible,” not “all Clash clients.”

## 11. Test matrix

All tests call the public compiler interface. Internal helpers are not mocked.

```text
CODE PATHS                                      BEHAVIOR FIXTURES
[ ] strict parse                                [ ] inline proxies
 ├── valid mapping                              [ ] block + flow group styles
 ├── duplicate YAML key                         [ ] nested groups
 ├── malformed YAML                             [ ] proxy-providers + use
 └── custom tag                                 [ ] duplicate runtime names
[ ] source + policy validation                  [ ] missing/stale managed group
 ├── full config only                           [ ] existing GW· and EA· names
 ├── landing HTTP/SOCKS5                        [ ] exact generated collision
 ├── managed group exists                       [ ] malformed YAML
 └── front references exist                     [ ] source update adds/removes nodes
[ ] topology compile                            [ ] fixed/source/automatic/direct
 ├── singleton public wrapper                   [ ] multi-group all-or-nothing
 ├── landing always final                       [ ] nested managed-reference rewrite
 ├── front policy moved/generated               [ ] explicit graph cycle
 └── cycle detection                            [ ] credentials absent from report/error
[ ] deterministic emit
 ├── repeat call byte equality
 ├── policy map order independence
 └── compiled-input structural idempotence
```

TDD proceeds as vertical tracer bullets, one failing behavior and one minimal
implementation at a time:

1. Valid inline source produces a singleton managed wrapper and landing proxy.
2. Unmanaged parsed subtrees remain equal.
3. Source-mode front group preserves source candidates.
4. Repeated compilation is deterministic and structurally idempotent.
5. Missing groups and exact name collisions fail without partial output.
6. Provider `use`, flow style, and nested groups compile safely.
7. Fixed, automatic, and direct modes enforce their distinct contracts.
8. Duplicate keys/names, malformed YAML, custom tags, and cycles fail safely.
9. Subscription update fixtures add/remove candidates while retaining the wrapper.
10. Reports and exceptions contain no fixture credentials or landing endpoint.

## 12. Failure-mode review

| Failure | Handling | Test | User-visible result |
| --- | --- | --- | --- |
| Upstream removes a managed group | Reject new generation; retain LKG | fixture update | stale status, prior compiled subscription remains |
| Client remembers an ordinary node | Ordinary node is absent from wrapper | topology assertion | landing remains the only valid selection |
| Landing or front hop is down | No alternate wrapper member | structural + later Mihomo check | connection fails instead of bypassing |
| Provider becomes empty | `empty-fallback: REJECT` | provider fixture | chain fails closed |
| Existing generated-looking name | Preserve unrelated names; exact collision rejects | collision fixtures | actionable safe error |
| Multiple managed groups, one invalid | No output returned | all-or-nothing test | prior LKG remains |
| Malformed YAML contains a password | Sanitize parser error | secret fixture | code + location only |
| Concurrent refreshes | In-process lock + immutable generation pointer | gateway integration tests | stable prior or next generation, never partial |
| Crash during cache write | temp file + fsync + atomic replace | adapter integration test later | prior generation survives |
| Short-lived import URL expires | URL is used only during import | gateway import test | saved snapshot remains usable |
| New source import fails | Do not create or select a partial profile | gateway import test | prior active source keeps serving |
| Source activation fails | Do not move active source/generation pointers | switch integration test | prior active source keeps serving |
| Active source deletion requested | Reject until another source is active | gateway source test | no accidental outage |

No planned compiler failure is silent. Adapter-only failures are documented here
and must receive integration tests when that adapter is implemented.

## 13. What already exists

The predecessor implementation is maintained separately and is not included in
EgressAnchor's published tree or history. Its design lessons informed V0.1, but
it is not an implementation dependency:

- Historical `engine.py` discovers proxies and groups and informs catalog
  vocabulary.
- Historical `engine.py` builds landing proxies and in-memory chain fragments.
- Historical `app.py` proves why selection repair is needed in the legacy
  topology.
- Historical `app.py` contains client-specific reload logic that must stay out
  of the compiler.

The legacy transformation prepends the landing while retaining ordinary members.
That does not meet the invariant because a remembered selection can bypass the
landing. V0.1 reuses the Mihomo `dialer-proxy` concept and field validation lessons,
but replaces the transformation with the singleton wrapper above.

## 14. Performance

Compilation is `O(B + V + E)` time and `O(B + V)` memory, where `B` is YAML size,
`V` is proxies/groups, and `E` is explicit references. One deep copy is acceptable
under the 8 MiB V0.1 input limit. There is no compiler-level cache or concurrency.

The gateway adapter owns conditional HTTP requests, in-process refresh
coordination, and last-known-good storage. This keeps nondeterminism and I/O
outside the compiler seam.

## 15. Sequential implementation

The first slice is intentionally sequential because every step touches the same
compiler interface and its fixtures:

```text
fixture → one failing public-interface test → minimal compiler behavior → green
       → next fixture/test → refactor only while green → full safety audit
```

There is no useful worktree parallelization opportunity for this slice. HTTP,
cache, and UI work begin only after the compiler contract and fixtures are green.
