# Task6: Compose rendering HOLD adjudication

## Retained observations

Reviewed snapshot: `e54c76720e5c74d028836f16eaf1a58737a21418`.
The new authorized LAN preflight authenticated the receiver and263source files/modes; operator preflight passed with own namespace0/0/0. The subsequent launcher failed its locked rendering check. This failed attempt is retained unchanged, not relabelled as a successful operator run.

The separately authorized read-only diagnostic at2026-11-14T18:38:27Z reports Compose **2.40.3**, render command exit0 and successful JSON parsing. Its DB entrypoint matches the **entire committed double-dollar string**, not the launcher's expected single-dollar string. All other recorded semantic checks passed, including resolved project/labels, exact mount and network structure, and absence of ports/builds/extra volumes/secrets/configs. Host/module/source verification passed before rendering and263source files/modes were reverified afterward. Temporary private Docker config was removed under the existing permanent lock. No container, pull, build, DB, runtime or second diagnostic ran.

Evidence: `.pi/text-delegation-render-diagnostic-e54c767/result.json`, SHA256 `11737a4949142ffb2406f332dfe8ce6225d8837b0137b783a4eb4d5b9c8db4fe`. Previous evidence and launchers remain unchanged. The diagnostic disclosed a tool-generated `/tmp/pi-bash-ef7765f8a6068981.log` inspection-output spill outside its intended new-directory-only write boundary; it contains source/artifact text, not raw rendered config or credentials. No deletion or silent concealment was performed.

## Version-specific primary-source explanation

Main inspected these official pinned sources through bounded HTTPS reads; no Docker execution:

- [Compose v2.40.3 config.go](https://github.com/docker/compose/blob/v2.40.3/cmd/compose/config.go#L170-L186): `runConfig` obtains interpolated model output, then calls `escapeDollarSign(content)` whenever normal interpolation is enabled. Lines513–517 replace **every `$` with `$$`** in serialized output. This also applies to `--format json`, not only YAML.
- [Compose v2.40.3 go.mod](https://github.com/docker/compose/blob/v2.40.3/go.mod): pins `github.com/compose-spec/compose-go/v2 v2.9.1`.
- [compose-go v2.9.1 template.go](https://github.com/compose-spec/compose-go/blob/v2.9.1/template/template.go#L175-L180): escaped-dollar substitution returns the escaped group, rather than treating it as an environment variable.
- [Compose v2.40.3 create.go](https://github.com/docker/compose/blob/v2.40.3/pkg/compose/create.go#L180-L190): the loaded service entrypoint is assigned to the container configuration; config-command serialization is not the container entrypoint.

Therefore a normal **serialized config** containing `$$((i+1))`/`$$i` is not evidence that the shell will receive those doubled dollars. The old launcher's demand for literal single dollars in serialized config contradicts this version's output contract. Changing the committed Compose source to single dollars to satisfy that assertion would be unsafe; retain its reviewed escaping.

## Decision boundary

Independent reviewer **a6002c55-eda8-446d-9f18-f6911c86eafe** confirmed the exact P2 false-negative assertion and found the observations plus pinned sources sufficient for the **enumerated model-render checks**; no additional rendering rerun is necessary for this discrepancy. Parent independently verified the result SHA256/mode0600 and read its closed fields: committed-dollar match true, runtime-dollar match false, all other recorded semantic assertions true, render exit0, source-after passed and private config removed. This is not a blanket acceptance of all booleans; runtime-dollar false is expected specifically in serialized output.

The reviewer inspected parent-retrieved source/provenance, not an independent network fetch or hash verification. The observed binary includes a distribution suffix; upstream source does not certify its complete patch set. Archival Go snippets intentionally lack full module trees: mixed-package/missing-import LSP errors are not application-code defects, were not suppressed, and provide no compilation proof. No dependencies were installed or archival code executed.

**Decision: enumerated model rendering accepted by adjudication; original overall preflight/diagnostic HOLD artifacts remain unchanged. Native acceptance remains separately gated.** No post-render Docker inventory or fresh capacity claim is inferred from the diagnostic. This is a source-grounded explanation of the erroneous launcher expectation, not a rewritten success artifact. Assess model-render verification separately from runtime shell execution. No runtime bootstrap, container hardening, migration0007, SQL/RLS/concurrency, dependency/restart, or cleanup acceptance follows. All native0007 tests remain **UNRUN** and any native run requires a fresh explicit decision after Main's evidence review. No further SSH, cleanup or preflight rerun is authorized by this document.
