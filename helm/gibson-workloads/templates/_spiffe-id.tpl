{{/*
Shared Helm helper to generate a ClusterSPIFFEID CRD resource.
Usage:
  {{ include "gibson.spiffeID" (dict "kind" "tool" "name" "nmap" "Release" .Release "Values" .Values) }}

Parameters:
  - kind:    The workload kind (tool, agent, plugin, platform)
  - name:    The workload name (nmap, opencode, gitlab, dashboard, daemon, etc.)
  - Release: The Helm Release context
  - Values:  The Helm Values context (used to derive the trust domain)

Generates a ClusterSPIFFEID with:
  - SPIFFE ID: spiffe://<trustDomain>/<kind>/<name>
              where trustDomain comes from .Values.gibson.auth.spiffe.trustDomain
              (defaults to "zeroroot.ai", the canonical SPIFFE trust domain).
              The trust domain is a product invariant. A customer changes the
              serving domain, global.domain, and never this value.
  - Pod selector: app.kubernetes.io/component: <kind>-<name> (or just <name> for platform)
  - Namespace selector: release namespace

Backward compatibility: callers that omit the Values arg fall back to the
hard-coded "zeroroot.ai" default so the trust-domain name continues to match
templates/spire-server.yaml's spire.trustDomain default.
*/}}