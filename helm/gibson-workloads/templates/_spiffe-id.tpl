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
              (defaults to "zeroroot.ai" — the canonical SPIFFE trust domain
              memorialised in memory feedback_zero_day_ai_domain.md). Spec
              zero-trust-hardening Req 12 purged the legacy "gibson.io"
              hard-code from this helper.
  - Pod selector: app.kubernetes.io/component: <kind>-<name> (or just <name> for platform)
  - Namespace selector: release namespace

Backward compatibility: callers that omit the Values arg fall back to the
hard-coded "zeroroot.ai" default so the trust-domain name continues to match
templates/spire-server.yaml's spire.trustDomain default.
*/}}