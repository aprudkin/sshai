# Public-release sanitization report

## Scope and result

This report covers private `github:aprudkin/sshai` at revision `244807cb6f10c7ee856a25fd26b48ebdea1d298c`. It prepares a future clean public staging package; it does not change visibility, create a repository, publish a release, or publish a package.

The reviewed set is allowlist-first: [release/allowlist.txt](../../release/allowlist.txt) is the only source for a future staging tree. The manifest records provenance and exclusions in [release/release-manifest.yaml](../../release/release-manifest.yaml).

## Inventory classification

| Class | Decision | Rationale |
| --- | --- | --- |
| Go source, tests, module files, and install script | Candidate | Repository-authored functional bundle with a reproducible install path. |
| Repository-owned SVG assets | Candidate | Original text-based artwork without an external download dependency. |
| Public-facing usage, parity, and benchmark reports | Candidate after staging scan | Useful documentation with bounded benchmark claims. |
| `CLAUDE.md` and `docs/superpowers/` | Exclude | Internal instructions, plans, and design history. |
| Historical benchmark manifests and digest sidecar | Exclude | Environment-specific local-path provenance. |
| Git metadata, runtime state, logs, artifacts, environments, build output, and binaries | Exclude | Non-source material that can contain private or generated data. |

## Secret and privacy review

A filename-and-marker-only review found 37 files matching the configured secret-pattern expression. The matches are redaction rules, policy/help text, tests, dependency checksums, or benchmark terminology; no secret value was printed during review. The clean staging tree must be scanned again before publication.

Eight tracked content files matched the configured environment-path expression. Internal plans/specifications and historical benchmark manifests are excluded. Remaining matches are path-handling test fixtures and must be reconfirmed as synthetic in clean staging.

The repository contains no tracked file larger than 10 MiB and no reviewed symlink, archive, certificate, or secret-key candidate in the distribution set.

## Remaining release gate

This preparation reaches `PACKAGE-READY` only after a fresh staging copy made exclusively from the allowlist passes the documented scans. Public release remains blocked on explicit owner approval for the exact public repository and artifact set. Dependency and future non-source-asset license compatibility must be checked at that time; this is an engineering provenance review, not legal advice.
