# Contributing to `charts`

This repository is the Gibson install chart: one versioned OCI umbrella that installs CRDs, operators and workloads in dependency order.

If anything here is unclear, open an issue rather than guessing — an unclear
contributing guide is a bug in this file.

## Prerequisites

- `helm` 3.18+
- `python3` and `jq`
- No cluster is needed for the offline gates below.

## Build and test

```sh
make chart-deps     # pull subcharts
make check          # the offline gate: golden snapshots, attribution, cloud-free
make golden-update  # accept an intentional render change
```

## The merge gate

`make check` runs three gates and each fails loudly:

- **golden** — every profile is rendered twice (bare, and with `--api-versions monitoring.coreos.com/v1`) and diffed against a committed snapshot. A render change must be accepted deliberately with `make golden-update`. This is also what carries the `randAlphaNum` ban: an inline-minted secret changes the render and fails the diff.
- **attribution** — every vendored redistribution carries upstream, license and NOTICE. Ships with a self-test proving the guard can fail.
- **cloud-free** — the vanilla render reaches no cloud endpoint.

Every pull request runs it. A red gate is a real signal: **do not** disable a
guard to get a PR through. If a guard is wrong, fix the guard in the same PR
and say why — a guard that needs re-pinning after an unrelated edit is a defect
in the guard.

## Pull requests

- **Conventional Commits in the PR title** — `feat:`, `fix:`, `chore:`,
  `docs:`, `ci:`, `test:`, `refactor:`. The subject must start lowercase;
  `pr-title-lint` enforces both.
- **One root cause per PR.** Two unrelated fixes are two pull requests.
- **Rebase, never merge.** `git fetch origin && git rebase origin/main`
- Releases are automatic via release-please. Never hand-tag, never hand-edit a
  version.

## Reporting a security issue

Do not open a public issue. See [SECURITY.md](SECURITY.md).

## License

Apache-2.0 — see [LICENSE](LICENSE). The chart is open. **The images it references are private** and need a registry credential; the credential is the gate, not the license.
