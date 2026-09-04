# Contributing to sshai

Thanks for considering a contribution.

- Discuss a material change in an issue before implementing it.
- Keep scope narrow and preserve the transport-versus-authorization safety boundary.
- Never commit credentials, private hostnames, captured remote output, local artifacts, or benchmark raw rollouts.

Run the local checks before a pull request:

```bash
go test ./...
go vet ./...
go build ./...
```

Integration tests need explicitly authorized, non-production test hosts. Do not add them to CI or run remote mutations merely to demonstrate a contribution. Format Go with `gofmt` and keep user-facing documentation and comments in English.

## Releases

Stable releases use tags matching `vMAJOR.MINOR.PATCH`. Push the tag only after its commit is on
`main` and CI has passed. The release workflow reruns tests, vulnerability and static checks, builds
six platform archives, verifies checksums, generates release metadata and the Homebrew formula,
and publishes the GitHub Release. A manual workflow dispatch builds and verifies the platform
archive candidate without publishing it. The Homebrew tap discovers stable GitHub releases through
its own autobump workflow.

Release tags must not contain environment-specific benchmark manifests, credentials, private host
aliases, local paths, or captured output. Every tracked release file must be reviewed and listed in
`release/source-allowlist.txt`. Release automation fails closed and does not rewrite an existing
release with different assets.

By contributing, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
