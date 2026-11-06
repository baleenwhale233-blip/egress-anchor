# Changelog

## Unreleased

### Added

- Build the warm dark service dashboard with official Lucide SVGs, service drawers, landing management and source settings; retain the original UI at `/legacy`.
- Persist builtin/custom service settings with domain exclusions, conflict checks, stable references and stale-edit protection.
- Generate service-priority routes and ISP TCP chains with explicit UDP rejection; preserve original routing outside service scope.
- Separate saved/generated settings from client application and verification, including honest unsupported states for automatic integration and strict mode.
- Exercise the new flow with browser acceptance and pinned-core service traffic fixtures.
- Test a saved policy group's running chain and front hop, with a separate landing authentication and exit-IP check.
- Save a loopback controller connection while keeping its access key out of reports and API responses.
- Run opt-in, checksum-pinned Mihomo traffic checks with disposable loopback proxies.
- Record the service-based product scope, client coverage, development stages, UI requirements and acceptance cases.

### Fixed

- Repair multiple missing service references after a source switch without publishing partial configurations.
- Keep failed service drafts, constrain drawer keyboard focus, and restore focus and scroll on close.
- Keep the background page still while a dialog is open, allow long dialogs to scroll, and restore focus and scroll position on close.
- Discard diagnostics when saved bindings, sources or client connection settings change.

### Known limitations

- Legacy policy-group compiler output can let unsupported UDP match a later direct rule. The new service compiler generates ISP UDP rejection and passes isolated traffic checks.
- Airport-only selection currently excludes dynamic/provider groups and legacy-managed groups.
- Desktop client adapters, automatic application, service probes, independent recovery and complete strict-mode verification remain in development.
