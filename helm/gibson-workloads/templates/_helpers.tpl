{{/*
  the workloads chart's own helpers: Secret names, the setec dispatch leg,
  the Envoy edge and the node heap.

  Anything more than one chart names lives in gibson-common, documented at
  its define and nowhere else (charts#304). This file held hundreds of lines of
  headers for helpers that moved there; scripts/check-helper-docs.py now
  fails the build on a header with no define under it.
*/}}

{{/*
stripe-mock host — dev-only stub that replaces api.stripe.com in kind so
the tenant-operator readyz Stripe probe passes without a live Stripe key.
Disabled in production overlays (stripeMock.enabled: false).
*/}}
{{- define "gibson.stripeMock.host" -}}
{{- printf "%s-stripe-mock" .Release.Name }}
{{- end }}

{{/*
gibson.emailSmtpSecret.name — the K8s Secret name the daemon's SMTP
credentials ExternalSecret materialises (gibson.email.smtp.externalSecret).
*/}}
{{- define "gibson.emailSmtpSecret.name" -}}
{{- printf "%s-email-smtp" .Release.Name }}
{{- end }}

{{/*
Impersonation signing key Secret name. The daemon mounts the
GIBSON_IMPERSONATION_KEY env var from this Secret; key persistence is
required (gibson#103) so tokens issued before a restart remain valid and
HA replicas agree on signatures.
*/}}
{{- define "gibson.impersonationSecret.name" -}}
{{- printf "%s-impersonation-key" (include "gibson.fullname" .) }}
{{- end }}

{{/*
Capability-Grant JWT signing key Secret name (GHSA-3957, gibson#1288).

Release-prefixed like the impersonation key: nothing outside this chart
references it by literal string — the daemon reaches it through a volume
mount, not a values-supplied *SecretRef — so the prefix is safe and keeps two
releases in one namespace from colliding.
*/}}
{{- define "gibson.cgSigningKeySecret.name" -}}
{{- printf "%s-cg-signing-key" (include "gibson.fullname" .) }}
{{- end }}

{{/*
Stripe credentials Secret name.

This name is REFERENCED by literal string `gibson-stripe-credentials` in
helm/gibson-workloads/values-kind.yaml (dashboard.billing.stripeSecretKeySecretRef)
and consumed by the dashboard Deployment's wait-for-stripe-secrets init
container and STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET env entries.

Do NOT prefix with .Release.Name — the value in dashboard.billing.*SecretRef
is the literal Secret name and is intentionally environment-stable.
*/}}
{{- define "gibson.stripeSecrets.name" -}}
gibson-stripe-credentials
{{- end }}

{{/*
Billing-webhook shared-secret Secret name (deploy#1314).

Materialised by templates/secrets/billing-webhook-secret.yaml with the single
key GIBSON_BILLING_WEBHOOK_SECRET. Both ends of the SetTenantBillingActive hop
reference it by this literal name — the daemon StatefulSet's secretKeyRef and,
once dashboard#1016 is decided, the caller workload — so it is intentionally
NOT release-prefixed and environment-stable, exactly like the Stripe Secret.
*/}}
{{- define "gibson.billingWebhookSecret.name" -}}
gibson-billing-webhook-secret
{{- end }}

{{/*
gibson.envoyEdge.caRequired — TRUE when consumers must mount the Envoy
edge CA into their trust store, FALSE when the system trust bundle is
sufficient.

The decision is keyed on the cert-manager Issuer that signs gibson-envoy-tls
(certManager.envoyEdge.issuer):

  - selfsigned-ca        → TRUE: the self-signed root is NOT in any system
                                 bundle; consumers must mount it explicitly
                                 (kind / on-prem self-hosted).
  - letsencrypt-*        → FALSE: Let's Encrypt's intermediates chain to
                                  ISRG Root X1 which IS in every modern
                                  system trust store; no consumer mount.
  - awspca-issuer        → FALSE: AWS Private CA chains to a root the
                                  workload's IAM trust policy already
                                  trusts (assumed).
  - openbao-issuer         → TRUE:  openbao-issued certs chain to the in-
                                  cluster Vault PKI root; consumers must
                                  mount it explicitly.

Used by dashboard (NODE_EXTRA_CA_CERTS) and the daemon (SSL_CERT_DIR /
the /etc/ssl/envoy-ca mount). Single decision point per deploy#126 —
no consumer template re-derives the same rule inline. PRD deploy#337
Wave 5 slice C.
*/}}
{{- define "gibson.envoyEdge.caRequired" -}}
{{- $issuer := (.Values.certManager.envoyEdge).issuer | default "" -}}
{{- if or (eq $issuer "selfsigned-ca") (eq $issuer "openbao-issuer") -}}
true
{{- else -}}
false
{{- end -}}
{{- end }}

{{/*
gibson.setecFullname — the setec subchart's object-name prefix.
Canonically `setec` via setec.fullnameOverride; falls back to the
release-derived name the subchart would otherwise pick.
*/}}
{{- define "gibson.setecFullname" -}}
{{- .Values.setec.fullnameOverride | default (printf "%s-setec" .Release.Name) -}}
{{- end }}

{{/*
gibson.setecNamespace — the namespace the setec subchart installs into.
NOT the release namespace; the frontend Deployment, its Service and its TLS
Secrets all live here.
*/}}
{{- define "gibson.setecNamespace" -}}
{{- .Values.setec.namespace | default "setec-system" -}}
{{- end }}

{{/*
gibson.setecFrontendName — the frontend Service / Deployment name.
*/}}
{{- define "gibson.setecFrontendName" -}}
{{- (.Values.setec.frontendService).name | default (printf "%s-frontend" (include "gibson.setecFullname" .)) -}}
{{- end }}

{{/*
gibson.setecFrontendAddress — host:port the daemon dials.
*/}}
{{- define "gibson.setecFrontendAddress" -}}
{{- printf "%s.%s.svc.cluster.local:%d" (include "gibson.setecFrontendName" .) (include "gibson.setecNamespace" .) (int ((.Values.setec.frontendService).port | default 50051)) -}}
{{- end }}

{{/*
gibson.setecFrontendServerName — the TLS serverName the daemon verifies.

Must be a name the frontend's server certificate actually carries. That cert
is minted by templates/setec/frontend-tls.yaml with commonName
`<frontend>.<ns>.svc` and dnsNames covering the short, two-label, `.svc` and
`.svc.cluster.local` forms — the `.svc` form is used here because it is what
setec's own round-trip test defaults to (gibson
internal/engine/harness/setec_roundtrip_setec_test.go), so both callers
verify the same name.
*/}}
{{- define "gibson.setecFrontendServerName" -}}
{{- printf "%s.%s.svc" (include "gibson.setecFrontendName" .) (include "gibson.setecNamespace" .) -}}
{{- end }}

{{/*
gibson.setecClientSecretName — the release-namespace Secret holding the
daemon's client keypair plus the CA that signed the frontend's server cert.
*/}}
{{- define "gibson.setecClientSecretName" -}}
{{- (.Values.gibson.sandbox.setec).clientSecretName | default "gibson-setec-client-tls" -}}
{{- end }}

{{/*
gibson.setecMtlsMountPath — where that Secret is mounted in the daemon pod.
A sibling of /etc/gibson, not a subpath of it: kubelet rejects a mount that
targets a path already occupied by another volume, and the `config`
ConfigMap already owns /etc/gibson (same reason /etc/gibson-kek is a
sibling).
*/}}
{{- define "gibson.setecMtlsMountPath" -}}
{{- (.Values.gibson.sandbox.setec).mtlsMountPath | default "/etc/gibson-setec-mtls" -}}
{{- end }}

{{/*
gibson.envoy.wafDirectives — read a files/coraza/<chain>.conf and emit the
JSON array of directive lines the Coraza WASM filter takes in its
`directives_map` (Edge WAF, deploy#1658). Comment and blank lines are
dropped; a backslash-continued line is joined with the next, because Coraza
reads each array element as one directive. Values from envoy.waf are
substituted (paranoia level, anomaly thresholds) so the numbers live in ONE
place. Usage: include "gibson.envoy.wafDirectives" (dict "ctx" . "file" "files/coraza/browser.conf")
*/}}
{{- define "gibson.envoy.wafDirectives" -}}
{{- $raw := tpl (.ctx.Files.Get .file) .ctx -}}
{{- $lines := list -}}
{{- $acc := "" -}}
{{- range (splitList "\n" $raw) -}}
{{- $t := trim . -}}
{{- if or (eq $t "") (hasPrefix "#" $t) -}}
{{- else if hasSuffix "\\" $t -}}
{{- $acc = printf "%s%s " $acc (trimSuffix "\\" $t) -}}
{{- else -}}
{{- $lines = append $lines (printf "%s%s" $acc $t) -}}
{{- $acc = "" -}}
{{- end -}}
{{- end -}}
{{- toJson $lines -}}
{{- end -}}

{{/*
gibson.nodeHeapMiB — the --max-old-space-size value for a Node container,
derived from that container's own memory limit.

Node sizes its old-space heap from the HOST's memory, not from the cgroup it
runs in. On a 32GiB node a 1GiB container therefore gets a heap ceiling several
times its own limit, so a heavy render grows past the cgroup and the process
dies with no V8 heap error to read. That is dashboard#150: every signed-in page
returned 503, the pod restarted, and nothing named memory as the cause.

So the ceiling has to be stated, and it has to be the same number the limit
states, or the two drift apart silently. One knob: the limit. The heap takes
75% of it and the rest covers the Node binary, buffers and native allocations,
so the process trips the heap ceiling before the cgroup kills it, and fails with
a stack instead of a bare kill.

Usage: include "gibson.nodeHeapMiB" .Values.dashboard.resources.limits.memory
*/}}
{{- define "gibson.nodeHeapMiB" -}}
{{- $q := . | toString -}}
{{- $mib := 0.0 -}}
{{- if hasSuffix "Gi" $q -}}
{{- $mib = mulf (trimSuffix "Gi" $q | float64) 1024 -}}
{{- else if hasSuffix "Mi" $q -}}
{{- $mib = trimSuffix "Mi" $q | float64 -}}
{{- else if hasSuffix "G" $q -}}
{{- $mib = divf (mulf (trimSuffix "G" $q | float64) 1000000000) 1048576 -}}
{{- else if hasSuffix "M" $q -}}
{{- $mib = divf (mulf (trimSuffix "M" $q | float64) 1000000) 1048576 -}}
{{- else -}}
{{- fail (printf "gibson.nodeHeapMiB: memory limit %q needs a Mi/Gi/M/G suffix. A bare or unknown unit would produce the wrong heap ceiling without saying so." $q) -}}
{{- end -}}
{{- $heap := mulf $mib 0.75 | floor | int64 -}}
{{- if lt $heap 256 -}}
{{- fail (printf "gibson.nodeHeapMiB: memory limit %q leaves a %dMiB heap, under the 256MiB a Next.js server needs to render one page." $q $heap) -}}
{{- end -}}
{{- $heap -}}
{{- end -}}
