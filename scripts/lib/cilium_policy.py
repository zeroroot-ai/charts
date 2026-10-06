"""cilium_policy.py: evaluate the Cilium policies of a render (ADR-0165 rule 4, D76).

The guards that read the network policy of the release share this module:
check-secure-pod.py (rule 4), check-daemon-netpol-admits-callers.py and
check-cnpg-netpol-covers-jobs.sh. A pod is an endpoint: its labels plus its
namespace. Cilium allows a flow only when an egress rule of the source and an
ingress rule of the destination both allow it.

Limits: the module reads endpoint selectors, entities, CIDRs, host names and
ports. It does not read L7 rules, deny rules or service selectors. The chart
renders none of these.
"""

NS_KEY = "io.kubernetes.pod.namespace"
POD_ENTITIES = {"all", "cluster"}
WORLD_ENTITIES = {"all", "world"}
OPEN_CIDRS = {"0.0.0.0/0", "::/0"}
KINDS = ("CiliumNetworkPolicy", "CiliumClusterwideNetworkPolicy")


def norm(key: str) -> str:
    """Cilium writes a label key with a source prefix (k8s:, any:). Drop it."""
    return key.split(":", 1)[1] if ":" in key else key


def selects(selector: dict | None, labels: dict) -> bool:
    sel = selector or {}
    for e in sel.get("matchExpressions") or []:
        k, op, vals = norm(e.get("key")), e.get("operator"), e.get("values") or []
        if op == "In" and labels.get(k) not in vals:
            return False
        if op == "NotIn" and labels.get(k) in vals:
            return False
        if op == "Exists" and k not in labels:
            return False
        if op == "DoesNotExist" and k in labels:
            return False
    return all(labels.get(norm(k)) == v for k, v in (sel.get("matchLabels") or {}).items())


def names_namespace(sel: dict | None) -> bool:
    sel = sel or {}
    keys = list(sel.get("matchLabels") or {}) + [e.get("key") for e in sel.get("matchExpressions") or []]
    return any(norm(k) == NS_KEY for k in keys)


def endpoint(ns: str, labels: dict) -> dict:
    """The labels of a pod as Cilium sees them: its labels and its namespace."""
    return {**labels, NS_KEY: ns}


def rules(policies: list[dict], default_ns: str) -> list[tuple[dict, dict, str | None]]:
    """(policy, rule, namespace) for each rule of each Cilium policy. The
    namespace is None for a clusterwide policy."""
    out = []
    for p in policies:
        if p.get("kind") not in KINDS:
            continue
        ns = None if p["kind"] == "CiliumClusterwideNetworkPolicy" else ((p.get("metadata") or {}).get("namespace") or default_ns)
        for r in ([p["spec"]] if p.get("spec") else []) + list(p.get("specs") or []):
            out.append((p, r, ns))
    return out


def peer_selects(sel: dict | None, ns: str | None, ep: dict) -> bool:
    """A selector of a namespaced policy that names no namespace means the
    namespace of the policy."""
    if ns is not None and not names_namespace(sel) and ep.get(NS_KEY) != ns:
        return False
    return selects(sel, ep)


def rule_selects(rule: dict, ns: str | None, ep: dict) -> bool:
    return peer_selects(rule.get("endpointSelector") or {}, ns, ep)


def port_ok(item: dict, port: int | None) -> bool:
    if port is None or not item.get("toPorts"):
        return True
    return any(str(pp.get("port")) == str(port) for tp in item["toPorts"] for pp in tp.get("ports") or [])


def ingress_admits(rule: dict, ns: str | None, src: dict, port: int | None = None) -> bool:
    for i in rule.get("ingress") or []:
        if not port_ok(i, port):
            continue
        if POD_ENTITIES & set(i.get("fromEntities") or []):
            return True
        if any(peer_selects(s, ns, src) for s in i.get("fromEndpoints") or []):
            return True
    return False


def egress_reaches(rule: dict, ns: str | None, dst: dict, port: int | None = None) -> bool:
    for e in rule.get("egress") or []:
        if not port_ok(e, port):
            continue
        if POD_ENTITIES & set(e.get("toEntities") or []):
            return True
        if any(peer_selects(s, ns, dst) for s in e.get("toEndpoints") or []):
            return True
    return False


def egress_world(rule: dict) -> bool:
    """True when the rule lets a pod reach each host outside the cluster."""
    for e in rule.get("egress") or []:
        if WORLD_ENTITIES & set(e.get("toEntities") or []):
            return True
        cidrs = list(e.get("toCIDR") or []) + [c.get("cidr") for c in e.get("toCIDRSet") or []]
        if OPEN_CIDRS & set(cidrs):
            return True
    return False


def egress_fqdn(rule: dict) -> bool:
    """True when the rule lets a pod reach named hosts outside the cluster."""
    return any(e.get("toFQDNs") for e in rule.get("egress") or [])


def egress_internet(rule: dict) -> bool:
    """True when the rule lets a pod reach hosts outside the cluster."""
    return egress_world(rule) or egress_fqdn(rule)


def reaches(all_rules: list, src: dict, dst: dict, port: int | None = None) -> bool:
    """Both sides allow the flow from src to dst on port."""
    out = any(rule_selects(r, ns, src) and egress_reaches(r, ns, dst, port) for _, r, ns in all_rules)
    inn = any(rule_selects(r, ns, dst) and ingress_admits(r, ns, src, port) for _, r, ns in all_rules)
    return out and inn
