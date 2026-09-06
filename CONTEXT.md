# Charts

SEED for `zeroroot-ai/charts/CONTEXT.md`. Move it with `git mv` when the repo
exists. This context owns the Gibson platform as a **thing a stranger can
install**: the umbrella chart, its sub-charts, the profiles, and the guards
that keep customer-facing config free of ZeroRoot's own estate. The hosted
SaaS lives in `zeroroot-ai/hosted` and is one consumer of this context, not
its owner.

## Language

**Chart**:
The `gibson` umbrella and its sub-charts, published as one signed OCI artifact
per version. A chart version IS the deployable (ADR-0004).
_Avoid_: the helm, the deployment, the manifests

**Vanilla cluster**:
A Kubernetes cluster with a default StorageClass and nothing else assumed. No
cloud IAM, no cloud KMS, no cloud DNS, no cloud load balancer. The supported
self-hosted target (ADR-0010). kind is one.
_Avoid_: bare metal, on-prem cluster, plain k8s

**Substrate overlay**:
A values file carrying only one provider's deltas, layered on top of
`values-vanilla.yaml`. `values-eks.yaml`, `values-gke.yaml`, `values-aks.yaml`.
_Avoid_: env values, cloud profile

**Trust domain**:
The SPIFFE trust domain, `zeroroot.ai`. It appears in every SPIFFE ID and every
component JWT audience. It is a product invariant and ships in the public chart.
A customer does NOT change it.
_Avoid_: domain, the trust root

**Serving domain**:
The DNS domain an install serves its own hosts on, set by `global.domain`.
Every external host derives from it through the `gibson-common` helpers. A
customer replaces it. ZeroRoot's value is `zeroroot.ai`, which is why the two
concepts read as one string and must not be.
_Avoid_: domain, hostname, the base domain

**Operator-substitution seam**:
A `condition:` on a cluster-service dependency that selects **whose** operator
runs, never **whether** one runs. Every install still runs exactly one
cert-manager. `certManager.enabled: false` means the cluster brought its own.
_Avoid_: optional service, feature flag, toggle

**Guest install**:
Installing onto a cluster that already runs cluster services — cert-manager,
External Secrets, Prometheus Operator. Big Bang is one. The operator-substitution
seams exist for this shape.
_Avoid_: brownfield, existing cluster

**Hook-in resource**:
A resource the chart emits for a controller it does not ship: `ServiceMonitor`,
`PrometheusRule`, and a dashboard ConfigMap labeled `grafana_dashboard: "1"`.
Gated on `.Capabilities.APIVersions.Has`, so it renders when the cluster has the
controller and vanishes when it does not. The chart ships no observability
workload in any profile.
_Avoid_: monitoring, metrics config

**Bringup keyring**:
The one bundle of operator-supplied inputs written into `Secret/bringup-keyring`
before the chart installs: the bucket credential, the OpenBao seal key, the
Velero repository password, the registry credential, and the model API keys.
`scripts/keyring-to-cluster.sh` is its single producer.
_Avoid_: secrets file, bootstrap secrets, seed values

**Registry credential path**:
The one route a private image credential takes: bringup keyring, then the
openbao-auto-init sidecar into OpenBao, then an ExternalSecret into a
dockerconfigjson Secret. Registry-neutral by ADR-0016. There is no second route.
_Avoid_: pull secret setup, image auth

## Relationships

- A **Chart** version installs onto a **vanilla cluster**, optionally with one
  **substrate overlay** on top.
- A **guest install** turns off one or more **operator-substitution seams**.
- A **hook-in resource** renders only when the cluster supplies its controller.
- The **bringup keyring** is written before the **Chart**, never by it.
- The **registry credential path** starts in the **bringup keyring**.
- **hosted** consumes a published **Chart** version. It never holds chart source.

## Example dialogue

> **Dev:** "The customer is on Big Bang and already runs cert-manager. Do we
> ship a profile that turns observability and cert-manager off?"
> **Owner:** "Observability is never on, in any profile. That is not a toggle,
> it is gone. cert-manager is an **operator-substitution seam**: they set
> `certManager.enabled: false` because their cluster owns it. One cert-manager
> either way."
> **Dev:** "And the SPIFFE IDs still say `zeroroot.ai` on their cluster?"
> **Owner:** "Yes. That is the **trust domain**, not the **serving domain**.
> They change `global.domain`. They never change the trust domain."

## Flagged ambiguities

- `zeroroot.ai` was used to mean both the SPIFFE **trust domain** and the SaaS
  **serving domain**. Resolved: these are distinct concepts. The trust domain
  ships in the public chart and is fixed. The serving domain is `global.domain`
  and every customer replaces it. `scripts/check-no-hardcoded-hostnames.py`
  polices the serving-domain plane only, which is what its "SECOND addressing
  plane" comment means.
- `observability.provider` read as a choice between two working shapes. It was
  not: `external-grafana-cloud` gated off the in-chart stack while the
  grafana-agent that value implies was never in the chart. Resolved: the enum is
  deleted, the chart ships no observability workload, and it emits **hook-in
  resources** only.
- "the operator must supply the image pull secret" in `values-vanilla.yaml`
  read as a second credential path beside the keyring. Resolved: stale prose,
  superseded by deploy#1732. There is one **registry credential path**.
