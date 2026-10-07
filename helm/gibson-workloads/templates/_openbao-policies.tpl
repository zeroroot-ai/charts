{{/*
  The OpenBao policies that the openbao-auto-init sidecar writes, one for each
  consumer (ADR-0032). Each lists the paths that the code of its consumer
  calls, with the least capabilities. No policy grants path "*".
  scripts/check-openbao-policies.py renders them and fails on path "*" and on
  sudo outside its named list. Each body goes into a single-quoted shell
  string, so no body holds a single quote.
*/}}

{{/*
gibson.openbaoPolicy.seeder: the openbao-auto-init sidecar after the
bootstrap. kv and transit mounts, the kubernetes auth mount, its config and
roles, every policy, each consumer token (no_parent and a policy the caller
does not hold need sudo on auth/token/create), the platform KV seed keys, the
transit keypairs, and the delete of the legacy policy.
*/}}
{{- define "gibson.openbaoPolicy.seeder" -}}
path "sys/mounts/secret" { capabilities = ["create", "update"] }
path "sys/mounts/transit" { capabilities = ["create", "update"] }
path "sys/auth/kubernetes" { capabilities = ["create", "update", "read", "sudo"] }
path "auth/kubernetes/config" { capabilities = ["create", "update", "read"] }
path "auth/kubernetes/role/*" { capabilities = ["create", "update", "read"] }
path "sys/policies/acl/*" { capabilities = ["create", "update", "read"] }
path "sys/policies/acl/platform-admin" { capabilities = ["delete"] }
path "auth/token/create" { capabilities = ["create", "update", "sudo"] }
path "secret/data/*" { capabilities = ["create", "read", "update"] }
path "transit/keys/*" { capabilities = ["create", "read", "update"] }
path "transit/export/signing-key/*" { capabilities = ["read"] }
{{- end -}}

{{/*
gibson.openbaoPolicy.platformOperator: gibson
operators/platform/internal/clients/vault. The mount table read, the transit
mount, the one transit key and the one KV key of the Zitadel admin token.
*/}}
{{- define "gibson.openbaoPolicy.platformOperator" -}}
path "sys/mounts" { capabilities = ["read"] }
path "sys/mounts/transit" { capabilities = ["create", "update"] }
path "transit/keys/{{ required "openbao.platformTransitKey is required: the transit key of the platform-operator." .Values.openbao.platformTransitKey }}" { capabilities = ["create", "read", "update"] }
path "secret/data/gibson-zitadel-iam-admin-pat" { capabilities = ["create", "read", "update"] }
{{- end -}}

{{/*
gibson.openbaoPolicy.iamAdminEscrow: the iam-admin-pat-escrow Job. It reads
and writes the three KV keys of the Zitadel setup Secrets.
*/}}
{{- define "gibson.openbaoPolicy.iamAdminEscrow" -}}
path "secret/data/gibson-zitadel-iam-admin-pat" { capabilities = ["create", "read", "update"] }
path "secret/data/gibson-zitadel-iam-admin-machinekey" { capabilities = ["create", "read", "update"] }
path "secret/data/gibson-zitadel-login-client-pat" { capabilities = ["create", "read", "update"] }
{{- end -}}

{{/*
gibson.openbaoPolicy.jwtAuthInit: the openbao-jwt-auth-init Job. It enables
the jwt auth mount (sudo) and writes and reads its config.
*/}}
{{- define "gibson.openbaoPolicy.jwtAuthInit" -}}
path "sys/auth/jwt" { capabilities = ["create", "update", "read", "sudo"] }
path "auth/jwt/config" { capabilities = ["create", "update", "read"] }
{{- end -}}

{{/*
gibson.openbaoPolicy.approleInit: the openbao-approle-cert-manager-init Job.
The approle mount (sudo), the PKI mount and its tune, the root CA, the PKI
role, the issuer policy and the approle role.
*/}}
{{- define "gibson.openbaoPolicy.approleInit" -}}
{{- $p := include "gibson.certManagerVaultPaths" . | fromJson -}}
path "sys/auth/{{ $p.approle }}" { capabilities = ["create", "update", "read", "sudo"] }
path "sys/mounts/{{ $p.pkiMount }}" { capabilities = ["create", "update", "read"] }
path "sys/mounts/{{ $p.pkiMount }}/tune" { capabilities = ["create", "update", "read"] }
path "{{ $p.pkiMount }}/ca/pem" { capabilities = ["read"] }
path "{{ $p.pkiMount }}/root/generate/internal" { capabilities = ["create", "update"] }
path "{{ $p.pkiMount }}/roles/{{ $p.pkiRole }}" { capabilities = ["create", "update", "read"] }
path "sys/policies/acl/cert-manager-vault-pki" { capabilities = ["create", "update", "read"] }
path "auth/{{ $p.approle }}/role/cert-manager" { capabilities = ["create", "update", "read"] }
{{- end -}}

{{/*
gibson.openbaoPolicy.approleReader: the two ESO generators of the
cert-manager AppRole credential. A fresh secret_id and the role_id.
*/}}
{{- define "gibson.openbaoPolicy.approleReader" -}}
{{- $p := include "gibson.certManagerVaultPaths" . | fromJson -}}
path "auth/{{ $p.approle }}/role/cert-manager/secret-id" { capabilities = ["create", "update"] }
path "auth/{{ $p.approle }}/role/cert-manager/role-id" { capabilities = ["read"] }
{{- end -}}
