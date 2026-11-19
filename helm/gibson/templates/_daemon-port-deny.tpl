{{/* gibson.daemonPortDeny: a CiliumNetworkPolicy that refuses one port of the
     daemon to each pod of the release namespace except the named peers.

     The platform rule (network-policies.yaml) lets each platform pod reach
     each port of each other platform pod. An allow rule cannot narrow that,
     so a listener of the daemon that accepts one peer only gets this deny
     rule. A Cilium deny rule wins over each allow rule. The TLS layer of the
     listener pins the SVID of the peer as a second control.

     A peer in another namespace (the agentgateway proxy) reaches the port
     through its own allow rule. That namespace is not the release namespace,
     so the deny does not touch it. With no peer in the release namespace,
     the rule refuses the port to each pod of the namespace.

     Arguments (a dict):
       ctx          the root context
       name         the suffix of the policy name, after the release name
       description  the description of the policy
       port         the port of the daemon
       peers        the app.kubernetes.io/component values of the pods in
                    the release namespace that may reach the port (a list,
                    may be empty)

     scripts/check-agentgateway.py reads the rendered rules. */}}
{{- define "gibson.daemonPortDeny" -}}
{{- $ctx := .ctx -}}
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: {{ $ctx.Release.Name }}-{{ .name }}
  namespace: {{ $ctx.Release.Namespace }}
  labels:
    {{- include "gibson.labels" $ctx | nindent 4 }}
    app.kubernetes.io/component: network-{{ .name }}
  annotations:
    argocd.argoproj.io/sync-wave: "-20"
spec:
  description: {{ .description | quote }}
  endpointSelector:
    matchLabels:
      app.kubernetes.io/component: daemon
      gibson.zeroroot.ai/net-role: platform
  ingressDeny:
    - fromEndpoints:
        {{- if .peers }}
        - matchExpressions:
            - key: app.kubernetes.io/component
              operator: NotIn
              values:
                {{- toYaml .peers | nindent 16 }}
        {{- else }}
        - {}
        {{- end }}
      toPorts:
        - ports:
            - port: {{ .port | quote }}
              protocol: TCP
{{- end -}}
