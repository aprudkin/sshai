# Public-release sanitization report

## Scope and result

This report covers the `v1.0.0` release of `github:aprudkin/sshai`, based on source revision
`918c712113c6e2e4b247c29cb4694508af8c350c` plus the release-gate metadata and license-notice
updates recorded with this report.

The reviewed distribution set contains the 88 regular files listed in
[`release/allowlist.txt`](../../release/allowlist.txt). The release target is
`github:aprudkin/sshai`; the Homebrew formula target is `github:aprudkin/homebrew-tap`.

**Result:** `PUBLISHED`. The exact `v1.0.0` artifact set recorded in
[`release/release-manifest.yaml`](../../release/release-manifest.yaml) was explicitly authorized and
published on 2026-09-03. The release is available from
[`aprudkin/sshai`](https://github.com/aprudkin/sshai/releases/tag/v1.0.0), and the formula is in
[`aprudkin/homebrew-tap`](https://github.com/aprudkin/homebrew-tap).

## Inventory classification

| Class | Decision | Rationale |
| --- | --- | --- |
| Go source, tests, module files, and installer | Include | Repository-authored functional bundle with a reproducible build path. |
| Agent skill | Include | Repository-authored Agent Skills instructions distributed with the CLI. |
| Release scripts and Homebrew template | Include | Repository-authored packaging files verified before publication. |
| Documentation and governance | Include | Public-facing usage, safety, contribution, and benchmark documentation. |
| SVG and Mermaid assets | Include | Repository-owned text assets without external binary dependencies. |
| Internal agent instructions and design history | Exclude | Not required to build, install, or use the public release. |
| Environment-specific benchmark manifests | Exclude | Historical manifests contain private local-path provenance. |
| Git metadata, runtime state, logs, and generated output | Exclude | Non-source material that can contain private or generated data. |

## Secret and privacy review

A clean staging tree was built only from `release/allowlist.txt`. Filename checks found no secret,
credential, certificate, key, database, log, archive, or environment-file candidates. The staging
tree contained no symlinks and no files larger than 10 MiB.

Nineteen files matched broad secret-related content markers. Every match was security guidance,
redaction implementation or tests, a synthetic test fixture, a dependency checksum, or this
release metadata. A separate high-confidence credential-pattern scan found no credential
candidate. Six files matched environment-path patterns; every match was a documented default path
or a synthetic Unix or Windows test fixture.

## License review

The project source and bundled agent skill use the MIT license. The release assets are
repository-owned SVG and Mermaid source files.

The compiled CLI links 13 external Go modules. Every linked module provided a license file in the
resolved module source. The release archives include those files, additional third-party notices
shipped by the modules, and the Go runtime license and patent grant under
`THIRD_PARTY_LICENSES/`.

## Verification

The release candidate passed `go test ./...`, `go vet ./...`, shell syntax and ShellCheck validation,
six target builds, archive checksum verification, Homebrew style validation, a local Homebrew
source build, and the formula's functional test. The repository CI run for source revision
`918c712113c6e2e4b247c29cb4694508af8c350c` also completed successfully.
