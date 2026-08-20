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

By contributing, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
