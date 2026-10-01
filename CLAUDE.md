# charts — CLAUDE.md

> **Workflow rules:** see [`zeroroot-ai/.github` → `AGENTS.md`](https://github.com/zeroroot-ai/.github/blob/main/AGENTS.md) — canonical for branching / commits / PRs / releases / merging. Conventional Commits MANDATORY. Never push to main. Never force-push.

This file is the per-repo addendum. Workspace-wide concerns live in the workspace `CLAUDE.md` and in [`AGENTS.md`](https://github.com/zeroroot-ai/.github/blob/main/AGENTS.md).

## TL;DR

The umbrella Helm chart that **is** the product, and nothing else. One versioned, signed OCI artifact every consumer installs: our hosted fleet through Argo, a customer through `helm install`, their own Argo, or Flux. Start with `make check`, which runs every guard that needs no cluster.

## Architecture

`helm/gibson` is the umbrella: CRDs → operators → workloads as ordered dependencies, plus the ExternalSecrets, bringup Jobs and CNPG cluster. `gibson-crds` stays a sibling only because Helm cannot install a CRD and its CR in one release. **There is only ever one releasable, `gibson`.**

| Chart | Is |
|---|---|
| `helm/gibson` | the umbrella, the deployable |
| `helm/gibson-common` | library chart of cross-chart helpers; where a name two charts share belongs |
| `helm/gibson-operators`, `helm/gibson-workloads` | the ordered dependencies |
| `helm/gibson-crds`, `helm/gibson-operator-crds` | CRDs, installed first |
| `helm/gibson-velero` | the backup seam |

A **chart version is the deployable**: values pin every first-party image by digest at package time, so a version names an exact set of digests and a rollback is a version pin. `airgap/` holds the generated image list an on-prem or air-gapped operator mirrors into their own registry.

The chart is a **guest on the cluster**. Observability ships with the estate, never the product. The four operator seams (cert-manager, external-secrets, CNPG, Velero) are `condition:`-gated, and a condition selects **whose** operator runs, never **whether** one runs.

## Commands

```bash
make check            # every guard that runs without a cluster — the gate
make golden           # snapshot test
make golden-update    # regenerate snapshots after an intended render change
make chart-deps       # vendor the sub-chart tarballs
make render-diff      # resource-level delta against a ref
./airgap/generate-image-list.py --check   # the air-gap manifest matches the chart
```

## Gotchas

- **`helm template` renders the PACKAGED subchart, not your working tree.** Edit a file under `helm/gibson-operators/templates/` and the umbrella render will not see it until you re-run `make chart-deps`. A render-based check will give you a confident false pass. Repackage, then verify.
- **A rendered change means `make golden-update`, and the diff is the review.** If the snapshot diff is larger than the change you intended, that is the finding.
- **A name two charts share belongs in `gibson-common`.** The tenant-operator dialed a literal `gibson-workloads-mailpit` while the Service rendered as `gibson-mailpit`, because the name was written twice (charts#114). One definition, every caller includes it.
- **Every image is on `ghcr.io`.** First-party, or re-mirrored upstream under `ghcr.io/zeroroot-ai/mirror/`. `make image-registry` fails on anything else, including a bare `busybox:`.
- **No `enabled: false` on a top-level service.** A `condition:` on one of the four operator seams selects whose operator runs. Turning it off means the cluster already has that operator.
- **Never hand-edit the air-gap lists.** `airgap/images.{txt,yaml}` are generated; the release workflow regenerates them onto the release PR.

## Links

- Org-level workflow: [`AGENTS.md`](https://github.com/zeroroot-ai/.github/blob/main/AGENTS.md)
- The consumer of this chart is the private `hosted` repository, which installs a published version of it and holds no chart source.
- Air-gap mirroring: [`airgap/README.md`](airgap/README.md)
- Glossary and settled decisions: [`CONTEXT.md`](CONTEXT.md)
