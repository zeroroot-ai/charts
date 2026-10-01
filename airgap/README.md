# Air-gap image list

On-prem and air-gapped operators mirror every image the chart pulls into their
own registry before they install. This directory holds that list, the inventory
behind it, and the tools that produce and re-pin them.

```
airgap/
  generate-image-list.py   # GENERATES the two lists below from `helm template`
  images.txt               # flat mirror list for cosign/skopeo loops (generated)
  images.yaml              # structured manifest with licenses and hardening notes (generated)
  gibson-catalog.ref       # the gibson commit the dispatchTime group is read from
  resolve-digests.sh       # re-pin @sha256 digests at mirror time
```

There is no wrapper package here, and no GitOps controller is assumed. An
operator installs the same signed OCI umbrella chart everyone else installs,
with `helm install`, or with their own Argo or Flux, and pins the version
there.

## The lists are generated

`images.txt` and `images.yaml` are rendered output, never hand-maintained.
`generate-image-list.py` renders the umbrella across every `ci/` profile and
derives the list. The `airgap-image-list` CI job fails the build when the
committed copy goes stale, and the release workflow regenerates it onto the
release PR itself, so the version bump and the list that copies it land
together.

```sh
./airgap/generate-image-list.py           # regenerate
./airgap/generate-image-list.py --check   # what CI runs
```

A hand-maintained copy of a rendered artifact always drifts, and it drifts
invisibly: the failure lands in a disconnected customer environment
(deploy#1171). By the time the generator landed, not one first-party entry
matched the chart.

`images.txt` also carries the `dispatchTime` group: the images setec pulls when
a mission dispatches a tool, an agent or a connector. Those never appear in a
render, so they are read from the daemon's component catalog at the gibson
commit in `gibson-catalog.ref`.

## Mirroring

**1. Mirror the images.** Almost every image is private — first-party
`ghcr.io/zeroroot-ai/*` and the `ghcr.io/zeroroot-ai/mirror/*` re-mirrored
upstreams alike — so resolve digests at mirror time: run
`./airgap/resolve-digests.sh` after `docker login ghcr.io`.

Use `cosign copy`, not `crane copy`. It carries the cosign signature with the
image, so the verifier inside the perimeter can still check it.

```sh
while read -r img; do
  [ "${img#\#}" = "$img" ] || continue
  cosign copy "$img" "registry.internal.example.com/${img#*/}"
done < airgap/images.txt
```

**2. Mirror the chart, with its signature.** The publish workflow signs every
chart with cosign keyless and stores the Rekor bundle on the signature, so
verification needs no network once the signature travels with the chart.

```sh
ver="$(python3 -c 'import json;print(json.load(open(".release-please-manifest.json"))["."])')"
cosign copy "ghcr.io/zeroroot-ai/charts/gibson:${ver}" \
  "registry.internal.example.com/zeroroot-ai/charts/gibson:${ver}"
cosign verify --offline \
  --certificate-identity-regexp '^https://github\.com/zeroroot-ai/charts/\.github/workflows/publish-umbrella-chart\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "registry.internal.example.com/zeroroot-ai/charts/gibson:${ver}"
```

`cosign verify --offline` still needs the Sigstore trust root. Fetch it once on
a connected host with `cosign initialize` and carry `~/.sigstore/root` across.

**3. Point the install at the mirror.** Set `global.imagePullSecrets` to a
secret holding the mirror credential — the chart's `validateGhcrCredentials`
guard fails the render if private images are pulled without one — and override
the per-sub-chart image repositories in your own values file. The umbrella keys
image repositories per sub-chart in `helm/gibson*/values.yaml`.

**4. Mirror the trivy databases.** The tool runner's trivy fetches its
vulnerability database from `ghcr.io/aquasecurity/trivy-db:2` and its Java
index from `ghcr.io/aquasecurity/trivy-java-db:1` at scan time. Copy both OCI
artifacts with `oras cp`, then pass the mirror to every trivy call with
`--db-repository` and `--java-db-repository`. The executor's argument policy
accepts an image reference there and nothing else.

## Hardening notes

- **Mutable tags.** `images.yaml` flags any reference whose tag is mutable,
  inline under `hardening:`. The chart digest-pins its first-party images in
  the staging and prod surface, enforced by `make digest-pin-check`, and the
  last mutable reference is gone, so no reference in the manifest carries a
  mutable tag today.
- **`billing` and `www` are absent on purpose.** They ship in
  `helm/saas-overlay/*`, which an air-gapped install does not deploy. The
  generated header records that exclusion, and the reason for each of the
  others, so a short list is never mistaken for a forgotten one.
- **`stripe-mock` and `mailpit` are dev and test only.** They are in the list
  because kind and CI pull them, flagged `DEV/TEST ONLY` under `hardening:`.
  Exclude them from a hardened mirror.
- **Neo4j Community Edition is GPL-3.0** while its chart is Apache-2.0.
  Mirroring it is redistribution of a GPL-3.0 work and carries that license's
  source-offer obligation. See `NOTICE`.
