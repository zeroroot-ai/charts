{{/*
gibson.egressFqdnGroups: the egress host groups (D76). A pod with the label
egress-fqdn: <group> reaches only the hosts of its group.
network-policies.yaml renders one policy for each group. The
values of the release give the hosts, so a group follows the configuration
of the install. A group with no host renders no policy: the pod then reaches
no host outside the cluster.

A host in the cluster (a name with no dot, or a name under .svc or
.cluster.local) is not in a group. The shared label policies cover it.

  zitadel          The SMTP relay of ZITADEL
                   (gibson-operators.platformBootstrap.zitadel.smtp.host).
                   The ZITADEL chart gives the login UI the same labels.
  tenant-operator  The SMTP relay of the welcome mail
                   (gibson-operators.tenantOperator.smtp.host), an external
                   Vault, an external JWKS URL, an external tenant Postgres,
                   and AWS KMS and STS when gibson-operators.kms.keyARN is set.
  cert-manager     The ACME directory of Let's Encrypt and, for the http01
                   solver, the public host names of the edge certificate
                   (the self-check). For dns01-route53: the Route53 API, STS
                   and the Route53 name servers (the propagation check).
                   Nothing when the Let's Encrypt issuers are off.
  external-dns     The API of the DNS provider. Nothing for the inmemory
                   provider.
  object-store     The durable bucket: AWS S3 (any region) and STS, or the
                   S3 endpoint of platformPostgres.backup.endpointURL. The
                   Postgres instances, the CloudNativePG Jobs and the Redis
                   backup CronJob use it.

Each entry is a Cilium FQDN selector, {matchName: <host>} or
{matchPattern: <pattern>}, or {cidr: <address>/32} for a host that a value
names by its IP address (the MinIO of the kind rungs). In a pattern, "*"
matches one DNS label.
*/}}
{{- define "gibson.egressFqdnGroups" -}}
{{- $ops := index .Values "gibson-operators" | default dict -}}
{{- $wl := index .Values "gibson-workloads" | default dict -}}
{{- $s3 := list (dict "matchName" "s3.amazonaws.com") (dict "matchPattern" "*.s3.amazonaws.com") (dict "matchPattern" "s3.*.amazonaws.com") (dict "matchPattern" "*.s3.*.amazonaws.com") -}}
{{- $sts := list (dict "matchName" "sts.amazonaws.com") (dict "matchPattern" "sts.*.amazonaws.com") -}}

{{- /* zitadel */ -}}
{{- $zitadel := list -}}
{{- with include "gibson.externalHost" (((($ops.platformBootstrap | default dict).zitadel | default dict).smtp | default dict).host) -}}
{{- $zitadel = append $zitadel (include "gibson.egressHostEntry" . | fromYaml) -}}
{{- end -}}

{{- /* tenant-operator */ -}}
{{- $to := list -}}
{{- $toHosts := list
  ((($ops.tenantOperator | default dict).smtp | default dict).host)
  ((($ops.dataPlane | default dict).vault | default dict).addr)
  ((($ops.vault | default dict).jwtAuth | default dict).spireOidcJwksURL)
  ((($ops.dataPlane | default dict).postgres | default dict).host)
-}}
{{- range $h := $toHosts -}}
{{- with include "gibson.externalHost" $h -}}
{{- $to = append $to (include "gibson.egressHostEntry" . | fromYaml) -}}
{{- end -}}
{{- end -}}
{{- if ($ops.kms | default dict).keyARN -}}
{{- $region := ($ops.aws | default dict).region | default "us-east-1" -}}
{{- $to = append $to (dict "matchName" (printf "kms.%s.amazonaws.com" $region)) -}}
{{- $to = concat $to $sts -}}
{{- end -}}

{{- /* cert-manager */ -}}
{{- $cm := list -}}
{{- $le := ((($wl.certManager | default dict).issuers | default dict).letsencrypt | default dict) -}}
{{- if $le.enabled -}}
{{- $cm = append $cm (dict "matchName" "acme-v02.api.letsencrypt.org") -}}
{{- $cm = append $cm (dict "matchName" "acme-staging-v02.api.letsencrypt.org") -}}
{{- if eq ($le.solver | default "http01") "dns01-route53" -}}
{{- $cm = append $cm (dict "matchName" "route53.amazonaws.com") -}}
{{- $cm = concat $cm $sts -}}
{{- range $tld := list "com" "net" "org" "co.uk" -}}
{{- $cm = append $cm (dict "matchPattern" (printf "ns-*.awsdns-*.%s" $tld)) -}}
{{- end -}}
{{- else -}}
{{- $names := (($wl.certManager | default dict).envoyEdge | default dict).dnsNames | default (include "gibson.tlsSans" . | fromYamlArray) -}}
{{- range $n := $names -}}
{{- with include "gibson.externalHost" $n -}}
{{- $cm = append $cm (include "gibson.egressHostEntry" . | fromYaml) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- /* external-dns */ -}}
{{- $dns := list -}}
{{- $provider := (((index .Values "external-dns" | default dict).provider | default dict).name | default "inmemory") -}}
{{- if eq $provider "aws" -}}
{{- $dns = append $dns (dict "matchName" "route53.amazonaws.com") -}}
{{- $dns = concat $dns $sts -}}
{{- else if ne $provider "inmemory" -}}
{{- fail (printf "external-dns provider %q has no egress host group. Add the API hosts of the provider to gibson.egressFqdnGroups (helm/gibson/templates/_egress-fqdn.tpl)." $provider) -}}
{{- end -}}

{{- /* object-store */ -}}
{{- $obj := concat $s3 $sts -}}
{{- with include "gibson.externalHost" (((.Values.platformPostgres | default dict).backup | default dict).endpointURL) -}}
{{- $obj = append $obj (include "gibson.egressHostEntry" . | fromYaml) -}}
{{- end -}}

{{- toYaml (dict "zitadel" $zitadel "tenant-operator" $to "cert-manager" $cm "external-dns" $dns "object-store" $obj) -}}
{{- end -}}

{{/*
gibson.externalHost: the host of a URL, a host:port or a host, when the host
is outside the cluster. Empty for no input and for a host in the cluster.
*/}}
{{- define "gibson.externalHost" -}}
{{- if . -}}
{{- $in := toString . | trim -}}
{{- $host := $in -}}
{{- if contains "://" $in -}}
{{- $host = (urlParse $in).host -}}
{{- end -}}
{{- $host = (splitList ":" $host | first) | lower | trimSuffix "." -}}
{{- $local := or (not (contains "." $host)) (hasSuffix ".svc" $host) (contains ".svc." $host) (hasSuffix ".cluster.local" $host) -}}
{{- if not $local -}}
{{- $host -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
gibson.egressHostEntry: the entry of one external host in an egress host
group. A host name is {matchName: <host>}. An IPv4 address is
{cidr: <address>/32}, because a toFQDNs rule names a host.
*/}}
{{- define "gibson.egressHostEntry" -}}
{{- if regexMatch "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$" . -}}
cidr: {{ printf "%s/32" . }}
{{- else -}}
matchName: {{ . }}
{{- end -}}
{{- end -}}
