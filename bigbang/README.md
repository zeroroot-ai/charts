# Gibson — DoD Platform One / Big Bang distribution

This directory is the **federal distribution artifact**: the versioned OCI
umbrella chart (`oci://ghcr.io/zeroroot-ai/charts/gibson`) wrapped as a
[Big Bang](https://repo1.dso.mil/big-bang/bigbang) package — a Flux
`HelmRelease` plus the `GitRepository` / `OCIRepository` / `Kustomization`
sources Big Bang's Flux consumes — with an air-gap image mirror list.

It is the **third** install path, deliberately separate from the other two:

| Path | Consumer | Entry point |
|---|---|---|
| Hosted fleet (us) | ArgoCD App-of-Apps | `enterprise/gitops/` |
| Customer self-host | `helm install gibson` / customer Argo | `helm/gibson/` |
| **Federal / Big Bang (this dir)** | **DoD Platform One Flux** | **`bigbang/`** |

All three install the *same* OCI umbrella artifact; only the
controller wrapping it differs.

## Big-Bang-*compatible*, not Big-Bang-*native*

This package does **not** adopt Big Bang's Istio service mesh, nor its
monitoring/policy platform stack. Gibson keeps its own auth edge —
**Envoy + ext-authz + SPIRE/SPIFFE** — which would collide head-on with an
Istio mTLS/AuthZ mesh (rejected option 2).

Concretely:

- `package/namespace.yaml` sets **`istio-injection: disabled`** on the `gibson`
  namespace, so even on a cluster already running the Big Bang Istio mesh, no
  Istio sidecar is injected into platform workloads. The Envoy/SPIRE edge is
  preserved end to end.
- No `VirtualService`, `Gateway`, `PeerAuthentication`, `AuthorizationPolicy`,
  or any other `istio.io` / `networking.istio.io` resource is introduced by
  this package. (`grep -ri istio bigbang/` returns only this guarantee and the
  disable label.)

## Layout

```
bigbang/
  package/                     # the deployable package (what Flux reconciles)
    namespace.yaml             # gibson ns, istio-injection: disabled
    ocirepository.yaml         # Flux OCIRepository -> oci://.../charts/gibson:<version>
    helmrelease.yaml           # Flux HelmRelease -> chartRef the OCIRepository
    kustomization.yaml         # kustomize bundle: `kustomize build bigbang/package`
  flux/                        # GitOps bootstrap (how Big Bang consumes the package)
    gitrepository.yaml         # GitRepository -> this deploy repo
    flux-kustomization.yaml    # Flux Kustomization -> path ./bigbang/package
    kustomization.yaml         # kustomize bundle: `kustomize build bigbang/flux`
  images/
    generate-image-list.py     # GENERATES the two lists below from `helm template`
    images.yaml                # structured air-gap manifest (generated)
    images.txt                 # flat mirror list for skopeo/crane loops (generated)
    resolve-digests.sh         # re-pin @sha256 digests at mirror time
```

`package/` is the workload; `flux/` is the one-time GitOps wiring that points
Big Bang's Flux at `package/` from git. A customer integrating Gibson into an
existing Big Bang deployment can instead register `package/` directly under
their Big Bang `packages:` values — the resources are standard Flux objects.

## Versioning / promotion

A chart **version is the deployable**. Promotion is a bump of
`package/ocirepository.yaml` `ref.tag` (or `ref.digest` for an immutable
air-gap pin) — never a branch merge.

That bump is **automatic**: the tag carries an `x-release-please-version`
annotation and the file is listed under `extra-files` in
`release-please-config.json`, so every release moves it in the same PR that
cuts the version. It was manual until deploy#1171, and it had been sitting at
`0.104.0` while the repo shipped 0.110.x — the package pointed at a chart six
minor versions behind. Do not hand-edit the tag; `generate-image-list.py`
fails if it disagrees with `.release-please-manifest.json`.

## Air-gap mirroring

1. **Mirror images.** `images/images.txt` lists every image the chart pulls,
   plus the `dispatchTime` group: the images setec pulls when a mission
   dispatches a tool, an agent or a connector. Those never appear in a
   render. They are read from the daemon's component catalog at the gibson
   commit in `images/gibson-catalog.ref`.

   It is **generated**, not hand-maintained — `images/generate-image-list.py`
   renders the umbrella across every `ci/` profile and derives the list, and
   the `airgap-image-list` CI job fails the build when the committed copy goes
   stale. Regenerate with `./bigbang/images/generate-image-list.py`; never edit
   the two lists by hand. (They were hand-maintained until deploy#1171, by
   which point not one first-party entry matched the chart.)

   Almost every image is private now — first-party `ghcr.io/zeroroot-ai/*` and
   the `ghcr.io/zeroroot-ai/mirror/*` re-mirrored upstreams alike — so digests
   are resolved at mirror time: run `images/resolve-digests.sh` after
   `docker login ghcr.io`.

   Use `cosign copy`, not `crane copy`: it moves the cosign signature with
   the image, so the verifier inside the perimeter can still check it.

   ```sh
   while read -r img; do
     [ "${img#\#}" = "$img" ] || continue
     cosign copy "$img" "registry.il.example.mil/${img#*/}"
   done < images/images.txt
   ```

2. **Mirror the chart, with its signature.** The publish workflow signs
   every chart with cosign keyless and stores the Rekor bundle on the
   signature, so verification needs no network once the signature travels
   with the chart. Copy the chart the same way and verify it offline:

   ```sh
   ver="$(grep -oE 'tag: "[^"]+"' package/ocirepository.yaml | cut -d'"' -f2)"
   cosign copy "ghcr.io/zeroroot-ai/charts/gibson:${ver}" \
     "registry.il.example.mil/zeroroot-ai/charts/gibson:${ver}"
   cosign verify --offline \
     --certificate-identity-regexp '^https://github\.com/zeroroot-ai/charts/\.github/workflows/publish-umbrella-chart\.yml@' \
     --certificate-oidc-issuer https://token.actions.githubusercontent.com \
     "registry.il.example.mil/zeroroot-ai/charts/gibson:${ver}"
   ```

   `cosign verify --offline` still needs the Sigstore trust root. Fetch it
   once on a connected host with `cosign initialize` and carry
   `~/.sigstore/root` across.

3. **Repoint sources.** In `package/ocirepository.yaml` set `url` to the
   mirrored chart and add `secretRef: { name: private-registry }`; in
   `package/helmrelease.yaml` the `global.imagePullSecrets` value already
   references `private-registry` (the chart's `validateGhcrCredentials` guard
   fails the render if private images are pulled without a pull secret).

4. **Override image repos** to the mirror via the customer overlay /
   `HelmRelease.spec.values` (the umbrella keys image repositories per
   sub-chart in `helm/gibson*/values.yaml`).

5. **Mirror the trivy databases.** The tool runner's trivy fetches its
   vulnerability database from `ghcr.io/aquasecurity/trivy-db:2` and its
   Java index from `ghcr.io/aquasecurity/trivy-java-db:1` at scan time.
   Copy both OCI artifacts with `oras cp`, then pass the mirror to every
   trivy call with `--db-repository` and `--java-db-repository`. The
   executor's argument policy accepts an image reference there and nothing
   else.

### Hardening notes

- **Mutable tags forbidden for federal.** `images.yaml` flags any reference
  whose tag is mutable inline, under `hardening:`. The chart itself now
  digest-pins its first-party images in the staging/prod surface (a `make
  digest-pin-check` gate enforces it), and deploy#1343 removed the last
  mutable reference — the Bitnami redis subchart's `bitnami/redis:latest`,
  which had no consumer — so no reference in the manifest carries a mutable
  tag today.
- **`billing` and `www` are not in the list at all** — they ship in
  `helm/saas-overlay/*`, which this package does not deploy. The generated
  header records that exclusion, and the reason for each of the others, so a
  short list is never mistaken for a forgotten one.
- **`stripe-mock` and `mailpit` are dev/test only** — present in the list
  (kind and CI pull them) but flagged `DEV/TEST ONLY` under `hardening:`;
  exclude them from a federal mirror.

## Local validation

No live cluster is required to validate the manifests:

```sh
kustomize build bigbang/package    # renders ns + OCIRepository + HelmRelease
kustomize build bigbang/flux       # renders GitRepository + Flux Kustomization
grep -ri istio bigbang/            # only the disable guarantee, no mesh resources
./bigbang/images/generate-image-list.py --check   # air-gap manifest matches the chart
```

With the `flux` CLI available, `flux build` / `flux diff` will dry-render the
`HelmRelease` against a cluster. Live reconcile (HelmRelease -> Ready) requires
a Flux-equipped cluster with registry access and is performed in the target
Platform One environment.
