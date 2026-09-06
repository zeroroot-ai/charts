# gibson charts

The Gibson platform as a thing you install. One umbrella chart, its sub-charts,
and the profiles that shape it for your cluster.

```sh
helm install gibson oci://ghcr.io/zeroroot-ai/charts/gibson \
  --version <version> \
  -f values-vanilla.yaml
```

## Profiles

| Profile | For |
|---|---|
| `values-vanilla.yaml` | a Kubernetes cluster with a default StorageClass and nothing else assumed. **This is the supported self-hosted target.** kind is one. |
| `values-eks.yaml`, `values-gke.yaml`, `values-aks.yaml` | layered on vanilla, carrying only that provider's deltas |
| `values-guest.yaml` | layered on vanilla, for a cluster that already owns cert-manager, External Secrets, ExternalDNS and CloudNativePG |

## The images are private

The chart is Apache-2.0. The images it references are not, and they are not
public. An install needs a registry credential, supplied as a member of the
bringup keyring before the chart installs. There is exactly one credential
path and the chart documents it in `values-vanilla.yaml`.

`global.registry` repoints every first-party image at your own registry in one
value. An image whose *path* also changes is repointed by its own `repository`
key, which still wins.

## Tests

```sh
make check          # golden snapshots + attribution + cloud-free. No cluster.
make vanilla-up     # install onto the current kube context
make vanilla-verify # prove it came up
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