# gibson charts

The Gibson platform as a thing you install. One umbrella chart, its sub-charts,
and the profiles that shape it for your cluster.

```sh
helm install gibson oci://ghcr.io/zeroroot-ai/charts/gibson \
  --version <version> \
  -f values-baseline.yaml
```

On one local node (kind or k3d), add the `developer` rung. The baseline asks
for the honest self-hosted floor, about 2.9 CPU of scheduling reservations,
which a single local node cannot meet:

```sh
helm install gibson oci://ghcr.io/zeroroot-ai/charts/gibson \
  --version <version> \
  -f values-baseline.yaml -f values-developer.yaml
```

Both files travel inside the artifact, so nothing but helm is needed. The rung
lowers *requests* only, never limits, so every pod still bursts as it would.

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
third-party ones under `mirror/`. A plain `helm install` pulls them with no
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
make check          # golden snapshots + attribution + cloud-free. No cluster.
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