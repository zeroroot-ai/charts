{{/*
gibson.egressFqdnGroups: the egress host groups (D76). A pod with the label
egress-fqdn: <group> reaches only the hosts of its group.
network-policies.yaml renders one policy for each group. The
values of the release give the hosts, so a group follows the configuration
of the install. A group with no host renders no policy: the pod then reaches
no host outside the cluster.

A host in the cluster (a name with no dot, or a name under .svc or
.cluster.local) is not in a group. The shared label policies cover it.

  zitadel          The SMTP relay of global.email when its provider is smtp.
                   The ZITADEL chart gives the login UI the same labels.
  tenant-operator  The SMTP relay of global.email when its provider is smtp,
                   an external JWKS URL, an external
                   tenant Postgres, and AWS KMS and STS when
                   gibson-operators.kms.keyARN is set.
  cert-manager     The ACME directory of Let's Encrypt and, for the http01
                   solver, the public host names of the edge certificate
                   (the self-check). For dns01-route53: the Route53 API, STS
                   and the Route53 name servers (the propagation check).
                   Nothing when the Let's Encrypt issuers are off.
  external-dns     The API of the DNS provider. Nothing for the inmemory
                   provider.
  object-store     The durable bucket of the install, on AWS S3 (the bucket of
                   platformPostgres.backup.destinationPath and of the Redis
                   backup, in any region) and STS, or the S3 endpoint of
                   platformPostgres.backup.endpointURL. Only the buckets of
                   the install, never each bucket of S3. The Postgres
                   instances, the CloudNativePG Jobs and the Redis backup
                   CronJob use it.
  <name>           Each group of global.networkPolicy.egressHostGroups: the
                   hosts that the values of the install name, for a pod that
                   another chart of the install deploys. Each entry is a URL,
                   a host:port or a host outside the cluster. A name of a
                   group above and a host in the cluster fail the render.

Each entry is a Cilium FQDN selector, {matchName: <host>} or
{matchPattern: <pattern>}, or {cidr: <address>/32} for a host that a value
names by its IP address (the MinIO of the kind rungs). In a pattern, "*"
matches one DNS label. Each entry also names its ports: 443 for an HTTPS
API, the port of the URL or the value for a host that a value names, and
53 for a name server. network-policies.yaml opens each host on its ports
only.
*/}}
{{- define "gibson.egressFqdnGroups" -}}
{{- $ops := index .Values "gibson-operators" | default dict -}}
{{- $wl := index .Values "gibson-workloads" | default dict -}}
{{- $https := list (dict "port" "443" "protocol" "TCP") -}}
{{- $sts := list (dict "matchName" "sts.amazonaws.com" "ports" $https) (dict "matchPattern" "sts.*.amazonaws.com" "ports" $https) -}}

{{- /* The SMTP relay of global.email, for zitadel and the tenant-operator. */ -}}
{{- /* gibson.email resolves the host through tpl, as every mail reader does. */ -}}
{{- $smtp := list -}}
{{- $mail := include "gibson.email" . | fromJson -}}
{{- if eq $mail.provider "smtp" -}}
{{- with include "gibson.externalHost" $mail.host -}}
{{- $smtp = append $smtp (include "gibson.egressHostEntry" (dict "host" . "port" $mail.port) | fromYaml) -}}
{{- end -}}
{{- end -}}

{{- /* zitadel */ -}}
{{- $zitadel := $smtp -}}

{{- /* tenant-operator */ -}}
{{- $to := $smtp -}}
{{- $dp := $ops.dataPlane | default dict -}}
{{- $urls := list (((($ops.vault | default dict).jwtAuth | default dict)).spireOidcJwksURL) -}}
{{- range $u := $urls -}}
{{- with include "gibson.externalHost" $u -}}
{{- $to = append $to (include "gibson.egressHostEntry" (dict "host" . "port" (include "gibson.urlPort" $u)) | fromYaml) -}}
{{- end -}}
{{- end -}}
{{- $pg := $dp.postgres | default dict -}}
{{- with include "gibson.externalHost" $pg.host -}}
{{- $to = append $to (include "gibson.egressHostEntry" (dict "host" . "port" (include "gibson.urlPort" (dict "url" $pg.host "default" ($pg.port | default 5432)))) | fromYaml) -}}
{{- end -}}
{{- if ($ops.kms | default dict).keyARN -}}
{{- $region := ($ops.aws | default dict).region | default "us-east-1" -}}
{{- $to = append $to (dict "matchName" (printf "kms.%s.amazonaws.com" $region) "ports" $https) -}}
{{- $to = concat $to $sts -}}
{{- end -}}

{{- /* cert-manager */ -}}
{{- $cm := list -}}
{{- $le := ((($wl.certManager | default dict).issuers | default dict).letsencrypt | default dict) -}}
{{- if $le.enabled -}}
{{- $cm = append $cm (dict "matchName" "acme-v02.api.letsencrypt.org" "ports" $https) -}}
{{- $cm = append $cm (dict "matchName" "acme-staging-v02.api.letsencrypt.org" "ports" $https) -}}
{{- if eq ($le.solver | default "http01") "dns01-route53" -}}
{{- $cm = append $cm (dict "matchName" "route53.amazonaws.com" "ports" $https) -}}
{{- $cm = concat $cm $sts -}}
{{- $dns53 := list (dict "port" "53" "protocol" "UDP") (dict "port" "53" "protocol" "TCP") -}}
{{- range $tld := list "com" "net" "org" "co.uk" -}}
{{- $cm = append $cm (dict "matchPattern" (printf "ns-*.awsdns-*.%s" $tld) "ports" $dns53) -}}
{{- end -}}
{{- else -}}
{{- /* The http01 self-check fetches the challenge over plain HTTP. */ -}}
{{- $names := (($wl.certManager | default dict).envoyEdge | default dict).dnsNames | default (include "gibson.tlsSans" . | fromYamlArray) -}}
{{- range $n := $names -}}
{{- with include "gibson.externalHost" $n -}}
{{- $cm = append $cm (include "gibson.egressHostEntry" (dict "host" . "port" 80) | fromYaml) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- /* external-dns */ -}}
{{- $dns := list -}}
{{- $provider := (((index .Values "external-dns" | default dict).provider | default dict).name | default "inmemory") -}}
{{- if eq $provider "aws" -}}
{{- $dns = append $dns (dict "matchName" "route53.amazonaws.com" "ports" $https) -}}
{{- $dns = concat $dns $sts -}}
{{- else if ne $provider "inmemory" -}}
{{- fail (printf "external-dns provider %q has no egress host group. Add the API hosts of the provider to gibson.egressFqdnGroups (helm/gibson/templates/_egress-fqdn.tpl)." $provider) -}}
{{- end -}}

{{- /* object-store: the buckets of the install, never each bucket of S3. */ -}}
{{- $backup := (.Values.platformPostgres | default dict).backup | default dict -}}
{{- $buckets := list -}}
{{- with $backup.destinationPath -}}
{{- $buckets = append $buckets (regexReplaceAll "^s3://([^/]+).*$" . "${1}") -}}
{{- end -}}
{{- $rb := (($wl.redis | default dict).backup | default dict) -}}
{{- if and $rb.enabled $rb.s3Bucket -}}
{{- $buckets = append $buckets $rb.s3Bucket -}}
{{- end -}}
{{- $obj := list -}}
{{- with $backup.endpointURL -}}
{{- with include "gibson.externalHost" $backup.endpointURL -}}
{{- $obj = append $obj (include "gibson.egressHostEntry" (dict "host" . "port" (include "gibson.urlPort" $backup.endpointURL)) | fromYaml) -}}
{{- end -}}
{{- else -}}
{{- range $b := $buckets | uniq -}}
{{- if contains "." $b -}}
{{- fail (printf "the bucket %q has a dot in its name. S3 serves such a bucket only on the shared regional host, which reaches each bucket of S3, so the object-store egress group cannot name it alone. Use a bucket name with no dot." $b) -}}
{{- end -}}
{{- $obj = append $obj (dict "matchName" (printf "%s.s3.amazonaws.com" $b) "ports" $https) -}}
{{- $obj = append $obj (dict "matchPattern" (printf "%s.s3.*.amazonaws.com" $b) "ports" $https) -}}
{{- end -}}
{{- $obj = concat $obj $sts -}}
{{- end -}}

{{- $groups := dict "zitadel" $zitadel "tenant-operator" $to "cert-manager" $cm "external-dns" $dns "object-store" $obj -}}

{{- /* The groups that the values of the install name. */ -}}
{{- range $name, $entries := ((.Values.global).networkPolicy | default dict).egressHostGroups | default dict -}}
{{- if hasKey $groups $name -}}
{{- fail (printf "global.networkPolicy.egressHostGroups.%s: the chart owns the egress host group %q. Use another name." $name $name) -}}
{{- end -}}
{{- if not (regexMatch "^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$" $name) -}}
{{- fail (printf "global.networkPolicy.egressHostGroups.%s: a group name is a label value: lower case letters, digits and dashes." $name) -}}
{{- end -}}
{{- $hosts := list -}}
{{- range $e := $entries -}}
{{- $host := include "gibson.externalHost" $e -}}
{{- if not $host -}}
{{- fail (printf "global.networkPolicy.egressHostGroups.%s: %q is not a host outside the cluster. A host in the cluster is not in an egress group: the shared label policies cover it." $name (toString $e)) -}}
{{- end -}}
{{- if contains "*" $host -}}
{{- fail (printf "global.networkPolicy.egressHostGroups.%s: %q has a wildcard. Name each host." $name (toString $e)) -}}
{{- end -}}
{{- $hosts = append $hosts (include "gibson.egressHostEntry" (dict "host" $host "port" (include "gibson.urlPort" $e)) | fromYaml) -}}
{{- end -}}
{{- $_ := set $groups $name $hosts -}}
{{- end -}}

{{- toYaml $groups -}}
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
group, from a dict {host, port}. A host name is {matchName: <host>}. An
IPv4 address is {cidr: <address>/32}, because a toFQDNs rule names a host.
The entry opens the one TCP port of the dict.
*/}}
{{- define "gibson.egressHostEntry" -}}
{{- if regexMatch "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$" .host -}}
cidr: {{ printf "%s/32" .host }}
{{- else -}}
matchName: {{ .host }}
{{- end }}
ports:
  - port: {{ .port | toString | quote }}
    protocol: TCP
{{- end -}}

{{/*
gibson.urlPort: the port of a URL, a host:port or a host. The input is the
string, or a dict {url, default}. With no port in the input: the default of
the dict, else 443 for https and for no scheme, and 80 for http.
*/}}
{{- define "gibson.urlPort" -}}
{{- $in := . -}}
{{- $def := "" -}}
{{- if kindIs "map" . -}}
{{- $in = .url -}}
{{- $def = .default | toString -}}
{{- end -}}
{{- $in = toString $in | trim -}}
{{- $hostport := $in -}}
{{- $scheme := "" -}}
{{- if contains "://" $in -}}
{{- $u := urlParse $in -}}
{{- $hostport = $u.host -}}
{{- $scheme = $u.scheme -}}
{{- end -}}
{{- $parts := splitList ":" $hostport -}}
{{- if and (eq (len $parts) 2) (regexMatch "^[0-9]+$" (last $parts)) -}}
{{- last $parts -}}
{{- else if $def -}}
{{- $def -}}
{{- else if eq $scheme "http" -}}
80
{{- else -}}
443
{{- end -}}
{{- end -}}
