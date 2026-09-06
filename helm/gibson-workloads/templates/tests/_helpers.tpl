{{/*
==============================================================================
helm-test pod helpers — full post-install cluster-shape verification
==============================================================================

`helm test gibson-workloads` runs every Pod under templates/tests/*.yaml that
carries the `helm.sh/hook: test` annotation. Each pod runs a single assertion
script with `set -euo pipefail` and exits non-zero on failure; helm aggregates
per-pod outcomes and reports overall PASS/FAIL.

Scope: chart deploy#355 / production-readiness epic 5.10. These tests run
post-install against a fully-bootstrapped cluster — they ARE NOT a substitute
for chart-template-lint (templates/tests/*.bats) which runs at PR time via
`helm template` + bats.

Lifecycle:

  helm.sh/hook: test                   → opt-in test pod (helm test only)
  helm.sh/hook-delete-policy:
    before-hook-creation,hook-succeeded → leaves the failed pod's logs around
                                          so operators can `kubectl logs` after
                                          a failing `helm test` run; the next
                                          successful invocation cleans them up

Image policy:

  All test pods use ghcr.io/zeroroot-ai/mirror/curlimages-curl (mirror of
  curlimages/curl:8.10.1) by default. The image carries curl + sh; specific
  tests that need extra tooling (psql, redis-cli, cypher-shell) use a dedicated
  per-test image specified in this helper.

Override via .Values.tests.image.* in any overlay.

Documentation: helm/gibson-workloads/README.md, docs/runbooks/RUNBOOK-helm-test.md.
*/}}

{{/*
gibson-workloads.tests.image — common curl-bearing image for HTTP-probe tests.
Outputs a single string repo:tag image reference suitable for the `image:` field.
*/}}
{{- define "gibson-workloads.tests.image" -}}
{{- $img := .Values.tests | default dict -}}
{{- $imgImg := $img.image | default dict -}}
{{- $repo := $imgImg.repository | default "curlimages/curl" -}}
{{- $tag := $imgImg.tag | default "8.10.1" -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}

{{/*
gibson-workloads.tests.psqlImage — postgres client image for data-plane tests.
*/}}
{{- define "gibson-workloads.tests.psqlImage" -}}
{{- $img := .Values.tests | default dict -}}
{{- $imgImg := $img.psqlImage | default dict -}}
{{- $repo := $imgImg.repository | default "ghcr.io/zeroroot-ai/mirror/bitnami-postgresql" -}}
{{- $tag := $imgImg.tag | default "16.4.0-debian-12-r0" -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}

{{/*
gibson-workloads.tests.redisImage — redis-cli-bearing image.
*/}}
{{- define "gibson-workloads.tests.redisImage" -}}
{{- $img := .Values.tests | default dict -}}
{{- $imgImg := $img.redisImage | default dict -}}
{{- $repo := $imgImg.repository | default "redis" -}}
{{- $tag := $imgImg.tag | default "7.4.1-alpine" -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}

{{/*
gibson-workloads.tests.kubectlImage — kubectl-bearing image for K8s-API probes.
*/}}
{{- define "gibson-workloads.tests.kubectlImage" -}}
{{- $img := .Values.tests | default dict -}}
{{- $imgImg := $img.kubectlImage | default dict -}}
{{- $repo := $imgImg.repository | default "ghcr.io/zeroroot-ai/mirror/alpine-k8s" -}}
{{- $tag := $imgImg.tag | default "1.31.0" -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}

{{/*
gibson-workloads.tests.labels — common labels for every helm-test pod.
Includes the standard chart labels PLUS `helm-test/keep: "false"` so an
external janitor (e.g. `kubectl get pods -l helm-test/keep=false`) can
sweep stragglers if `helm.sh/hook-delete-policy` is misconfigured.

NOTE: this helper deliberately does NOT emit `app.kubernetes.io/component`
because individual test pods need to set their own component to match
existing selectors (e.g. the spire-attestation pod uses `daemon` to match
the daemon's ClusterSPIFFEID selector). Each test pod is responsible for
adding its own `app.kubernetes.io/component` label.
*/}}
{{- define "gibson-workloads.tests.labels" -}}
{{ include "gibson.labels" . }}
helm-test/keep: "false"
{{- end -}}

{{/*
gibson-workloads.tests.annotations — common annotations for every helm-test
pod. Carries the helm.sh/hook + hook-delete-policy + hook-weight so per-test
files only need to add their own weight.

Default hook-weight = "0" — overridable per-test by ADDING a second
annotation in the test pod (helm merges annotations from this helper
with anything else the pod manifest declares).
*/}}
{{- define "gibson-workloads.tests.annotations" -}}
"helm.sh/hook": test
"helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
{{- end -}}

{{/*
gibson-workloads.tests.errorPrefix — render a uniform "::error::" prefix the
test pod scripts use so GitHub-Actions style log scrapers pick up the failure
line at-a-glance. Run as: echo "$(prefix) message".

This is a string template, not a label — used inside shell scripts:
  echo "{{ include "gibson-workloads.tests.errorPrefix" . }} probe failed: ..."
*/}}
{{- define "gibson-workloads.tests.errorPrefix" -}}
::error file=helm-test::
{{- end -}}
