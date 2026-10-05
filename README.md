# gibson charts

The Gibson platform as a thing you install. One umbrella chart, its sub-charts,
and the profiles that shape it for your cluster.

## Install

The platform is six Helm releases, installed in a fixed order. One script makes
all six: `scripts/baseline-up.sh`. It is the one supported install path, and
the exit tests run the same script.

```sh
make baseline-up                       # from this checkout, onto the current kube context
CHART_VERSION=<version> make baseline-up   # the same, from the published charts
make baseline-verify                   # prove that the install came up
```

On one local node (kind or k3d), add the `developer` rung. The baseline asks
for the honest self-hosted floor, about 2.9 CPU of scheduling reservations,
which a single local node cannot meet:

```sh
RUNG=developer CHART_VERSION=<version> make baseline-up
```

The profile files travel inside the `gibson` artifact, so the script needs
nothing but helm and kubectl. The rung lowers *requests* only, never limits, so
every pod still bursts as it would.

### The six releases, in order

| # | Release | Chart | Namespace | Why it is a release of its own |
|---|---|---|---|---|
| 1 | `toolhive-operator-crds` | `oci://ghcr.io/stacklok/toolhive/toolhive-operator-crds` | `toolhive-system` | The CRDs of the connector runtime. A third-party chart. |
| 2 | `toolhive-operator` | `oci://ghcr.io/stacklok/toolhive/toolhive-operator` | `toolhive-system` | The connector runtime. |
| 3 | `gibson-operator-crds` | `oci://ghcr.io/zeroroot-ai/charts/gibson-operator-crds` | the platform namespace | The CRDs of cert-manager, External Secrets and CloudNativePG. A cluster that has these operators skips this release. |
| 4 | `gibson-crds` | `oci://ghcr.io/zeroroot-ai/charts/gibson-crds` | the platform namespace | The CRDs of the platform. Helm cannot install a CRD and its custom resource in one release. |
| 5 | `velero` | `oci://ghcr.io/zeroroot-ai/charts/gibson-velero` | `velero` | The backup seam. |
| 6 | `gibson` | `oci://ghcr.io/zeroroot-ai/charts/gibson` | the platform namespace | The umbrella chart: operators, then workloads. |

Do not install the `gibson` chart alone. On a cluster without releases 3 and 4
the install fails, because the API server does not know the custom resources
that the chart renders.

Between releases 4 and 5 the script waits for each CRD, reads the archive
bucket from the substrate file, and puts the bringup keyring into the cluster.
The `gibson` release also needs values that only the cluster knows: the
addresses of the API server and the cluster IP of the edge. The script reads
them from the cluster. Read `scripts/baseline-up.sh` for each step and each
environment variable.

## Profiles

| Profile | For |
|---|---|
| `values-baseline.yaml` | a Kubernetes cluster with a default StorageClass and nothing else assumed. **This is the supported self-hosted target.** kind is one. |
| `values-eks.yaml`, `values-gke.yaml`, `values-aks.yaml` | layered on baseline, carrying only that provider's deltas |
| `values-guest.yaml` | layered on baseline, for a cluster that already owns cert-manager, External Secrets, ExternalDNS and CloudNativePG |
| `values-developer.yaml` | layered on baseline, sized to fit ONE local node (kind or k3d). The rung a developer installs. |
| `values-ci.yaml` | layered on baseline, the same content as `developer` under its own name so either may change later |

## The images are public

The chart is Apache-2.0. Every image it references is a public package on
`ghcr.io/zeroroot-ai`, the first-party ones under their own names and the
third-party ones under `mirror/`. An install pulls them with no
credential. The one private package in the org, `billing`, belongs to the
hosted SaaS overlay and is not part of this chart.

A registry credential is needed only when you serve the images from your own
registry. `global.registry` repoints every first-party image at that registry
in one value, and `global.imagePullSecrets` names the Secret the kubelet pulls
with. The bringup keyring carries that credential as `GHCR_PULL_TOKEN`, and
`values-baseline.yaml` documents the one path it takes. An image whose *path*
also changes is repointed by its own `repository` key, which still wins.

## Tests

```sh
make check          # every guard that needs no cluster
make baseline-up     # install onto the current kube context
make baseline-verify # prove it came up
```

`make golden` renders every profile twice — bare, and with the Prometheus
Operator API present. That gap is the guest-versus-greenfield difference, so
both states are captured.

## What is not here

Provisioning a cluster, the hosted estate, and anything that only ZeroRoot
runs. Those live in a separate repository, which is a *consumer* of this one:
it installs a published, signed OCI chart at a pinned version and holds no
chart source.

## License and history

Apache License 2.0. See [LICENSE](LICENSE). Copyright Zero Root AI.

Issue and pull request numbers cited in comments and documents dated before 2026-09-05 refer to the tracker before the history reset, archived offline. They do not resolve on GitHub.