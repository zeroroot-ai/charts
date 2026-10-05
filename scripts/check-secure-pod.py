#!/usr/bin/env python3
"""check-secure-pod.py: each rendered pod is a secure pod (ADR-0165).

A platform pod is a secure pod when six rules hold. This guard renders each
shipped profile and checks each pod template against them.

  1. restricted   The pod meets the Kubernetes Pod Security Standard "restricted".
  2. read-only    The root filesystem of each container is read-only.
  3. token        The pod mounts a ServiceAccount token only when its
                  ServiceAccount has a grant on helm/gibson/rbac-allowlist.yaml.
  4. network      A policy denies all traffic in the namespace by default, a
                  policy selects the pod, and no egress rule of the pod is open
                  to each host. The guard reads NetworkPolicy and
                  CiliumNetworkPolicy.
  5. image        Each image comes from ghcr.io/zeroroot-ai/ and has a digest.
  6. exceptions   Each exception is a named entry with a reason in
                  helm/gibson/secure-pod-exceptions.yaml. The key is the
                  workload and the rule. The value holds a digest of the
                  findings. A new finding moves the digest and fails. An entry
                  with no finding in any render fails.

The guard checks also the Pod Security label of each namespace that holds a
pod. A namespace needs `pod-security.kubernetes.io/enforce: restricted`, or
an entry. The chart does not render the release namespace, so the guard reads
its label from the install script (scripts/baseline-up.sh).

  check-secure-pod.py             exit 1 on a finding, 0 when clean
  check-secure-pod.py --selftest  prove that one fixture for each rule fails
  check-secure-pod.py --print     print each finding, with or without an entry
  check-secure-pod.py --entries   print the entry lines for the current render
"""
import copy
import hashlib
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCEPTIONS = os.path.join(ROOT, "helm", "gibson", "secure-pod-exceptions.yaml")
RBAC_ALLOWLIST = os.path.join(ROOT, "helm", "gibson", "rbac-allowlist.yaml")
INSTALL_SCRIPT = os.path.join(ROOT, "scripts", "baseline-up.sh")
NAMESPACE = "gibson"
# The same profiles that scripts/golden.sh renders.
PROFILES = (
    ("values-baseline.yaml",),
    ("values-baseline.yaml", "values-eks.yaml"),
    ("values-baseline.yaml", "values-guest.yaml"),
)
# gibson-velero is its own release in the same namespace. The guard adds its
# pods to each profile, so the policies of the umbrella apply to them.
SIDE_RELEASES = ("helm/gibson-velero",)
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod")
POLICIES = ("NetworkPolicy", "CiliumNetworkPolicy")
REGISTRY = "ghcr.io/zeroroot-ai/"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
ENFORCE = "pod-security.kubernetes.io/enforce"
RULES = ("restricted", "read-only", "token", "network", "image", "namespace-label")
# Volume sources that the "restricted" standard allows.
VOLUMES = {"configMap", "csi", "downwardAPI", "emptyDir", "ephemeral", "persistentVolumeClaim", "projected", "secret"}
SAFE_SYSCTLS = {
    "kernel.shm_rmid_forced", "net.ipv4.ip_local_port_range", "net.ipv4.ip_unprivileged_port_start",
    "net.ipv4.tcp_syncookies", "net.ipv4.ping_group_range", "net.ipv4.ip_local_reserved_ports",
    "net.ipv4.tcp_keepalive_time", "net.ipv4.tcp_fin_timeout", "net.ipv4.tcp_keepalive_intvl",
    "net.ipv4.tcp_keepalive_probes",
}
SELINUX_TYPES = {None, "", "container_t", "container_init_t", "container_kvm_t", "container_engine_t"}
OPEN_CIDRS = {"0.0.0.0/0", "::/0"}


# ------------------------------------------------------------------ render

def template(chart: str, values: tuple[str, ...]) -> list[dict]:
    args = ["helm", "template", os.path.basename(chart), chart, "--namespace", NAMESPACE]
    inputs = f"helm/testdata/render-inputs/{os.path.basename(chart)}.yaml"
    if os.path.exists(os.path.join(ROOT, inputs)):
        args += ["-f", inputs]
    for v in values:
        args += ["-f", f"{chart}/{v}"]
    text = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(text) if d]


def render() -> list[list[dict]]:
    """One list of documents for each profile."""
    side = [d for chart in SIDE_RELEASES for d in template(chart, ())]
    return [template("helm/gibson", values) + side for values in PROFILES]


def ns_of(d: dict) -> str:
    return (d.get("metadata") or {}).get("namespace") or NAMESPACE


def pod_of(d: dict) -> tuple[dict, dict]:
    """(pod labels, pod spec) of a workload."""
    if d["kind"] == "Pod":
        return (d["metadata"].get("labels") or {}), d.get("spec") or {}
    tmpl = d["spec"]["jobTemplate"]["spec"]["template"] if d["kind"] == "CronJob" else d["spec"]["template"]
    return ((tmpl.get("metadata") or {}).get("labels") or {}), tmpl.get("spec") or {}


def containers(spec: dict) -> list[dict]:
    return [c for k in ("initContainers", "containers", "ephemeralContainers") for c in spec.get(k) or []]


# ---------------------------------------------------------- the six rules

def rule_restricted(spec: dict) -> list[str]:
    bad = []
    psc = spec.get("securityContext") or {}
    for field in ("hostNetwork", "hostPID", "hostIPC"):
        if spec.get(field):
            bad.append(f"{field} is true")
    for v in spec.get("volumes") or []:
        kinds = set(v) - {"name"}
        if not kinds <= VOLUMES:
            bad.append(f"volume {v.get('name')}: type {', '.join(sorted(kinds - VOLUMES))}")
    for s in psc.get("sysctls") or []:
        if s.get("name") not in SAFE_SYSCTLS:
            bad.append(f"sysctl {s.get('name')}")
    if (psc.get("windowsOptions") or {}).get("hostProcess"):
        bad.append("hostProcess is true")
    for key, value in ((spec.get("metadata") or {}).get("annotations") or {}).items():
        if key.startswith("container.apparmor.security.beta.kubernetes.io/") and value == "unconfined":
            bad.append("AppArmor profile is unconfined")
    for c in containers(spec):
        n = c.get("name")
        sc = c.get("securityContext") or {}
        if sc.get("privileged"):
            bad.append(f"container {n}: privileged")
        if sc.get("allowPrivilegeEscalation") is not False:
            bad.append(f"container {n}: allowPrivilegeEscalation is not false")
        caps = sc.get("capabilities") or {}
        if "ALL" not in (caps.get("drop") or []):
            bad.append(f"container {n}: capabilities do not drop ALL")
        extra = sorted(set(caps.get("add") or []) - {"NET_BIND_SERVICE"})
        if extra:
            bad.append(f"container {n}: capabilities add {', '.join(extra)}")
        non_root = sc.get("runAsNonRoot", psc.get("runAsNonRoot"))
        if non_root is not True:
            bad.append(f"container {n}: runAsNonRoot is not true")
        if sc.get("runAsUser", psc.get("runAsUser")) == 0:
            bad.append(f"container {n}: runAsUser is 0")
        seccomp = (sc.get("seccompProfile") or psc.get("seccompProfile") or {}).get("type")
        if seccomp not in ("RuntimeDefault", "Localhost"):
            bad.append(f"container {n}: seccompProfile is not RuntimeDefault or Localhost")
        if sc.get("procMount") not in (None, "Default"):
            bad.append(f"container {n}: procMount {sc.get('procMount')}")
        for holder in (sc, psc):
            se = holder.get("seLinuxOptions") or {}
            if se.get("type") not in SELINUX_TYPES or se.get("user") or se.get("role"):
                bad.append(f"container {n}: seLinuxOptions")
            if (holder.get("appArmorProfile") or {}).get("type") == "Unconfined":
                bad.append(f"container {n}: AppArmor profile is unconfined")
        for p in c.get("ports") or []:
            if p.get("hostPort"):
                bad.append(f"container {n}: hostPort")
    return sorted(set(bad))


def rule_read_only(spec: dict) -> list[str]:
    return [f"container {c.get('name')}: readOnlyRootFilesystem is not true" for c in containers(spec)
            if (c.get("securityContext") or {}).get("readOnlyRootFilesystem") is not True]


def rule_token(ns: str, spec: dict, accounts: dict, granted: set[str]) -> list[str]:
    sa = spec.get("serviceAccountName") or "default"
    mounts = spec.get("automountServiceAccountToken")
    if mounts is None:
        mounts = (accounts.get((ns, sa)) or {}).get("automountServiceAccountToken")
    if mounts is None:
        mounts = True
    projected = any("serviceAccountToken" in src for v in spec.get("volumes") or []
                    for src in (v.get("projected") or {}).get("sources") or [])
    if (mounts or projected) and f"{ns}/{sa}" not in granted:
        return [f"mounts a ServiceAccount token, and {ns}/{sa} has no grant on the RBAC allowlist"]
    return []


def selects(selector: dict | None, labels: dict) -> bool:
    sel = selector or {}
    for e in sel.get("matchExpressions") or []:
        k, op, vals = e.get("key"), e.get("operator"), e.get("values") or []
        if op == "In" and labels.get(k) not in vals:
            return False
        if op == "NotIn" and labels.get(k) in vals:
            return False
        if op == "Exists" and k not in labels:
            return False
        if op == "DoesNotExist" and k in labels:
            return False
    return all(labels.get(k) == v for k, v in (sel.get("matchLabels") or {}).items())


def policy_selector(p: dict) -> dict:
    spec = p.get("spec") or {}
    return spec.get("endpointSelector" if p["kind"] == "CiliumNetworkPolicy" else "podSelector") or {}


def denies_all(p: dict) -> bool:
    """True for a policy that selects each pod and allows nothing in either direction."""
    spec = p.get("spec") or {}
    sel = policy_selector(p)
    if sel.get("matchLabels") or sel.get("matchExpressions"):
        return False
    if p["kind"] == "CiliumNetworkPolicy":
        return spec.get("ingress") == [{}] and spec.get("egress") == [{}]
    types = set(spec.get("policyTypes") or [])
    return {"Ingress", "Egress"} <= types and not spec.get("ingress") and not spec.get("egress")


def limits_egress(p: dict) -> bool:
    spec = p.get("spec") or {}
    if p["kind"] == "CiliumNetworkPolicy":
        return "egress" in spec or "egressDeny" in spec
    return "Egress" in (spec.get("policyTypes") or [])


def open_egress(p: dict) -> bool:
    """True when an egress rule of the policy reaches each host."""
    spec = p.get("spec") or {}
    for rule in spec.get("egress") or []:
        if p["kind"] == "CiliumNetworkPolicy":
            if {"world", "all"} & set(rule.get("toEntities") or []):
                return True
            cidrs = list(rule.get("toCIDR") or []) + [c.get("cidr") for c in rule.get("toCIDRSet") or []]
            if OPEN_CIDRS & set(cidrs):
                return True
            if any(f.get("matchPattern") in ("*", "**") for f in rule.get("toFQDNs") or []):
                return True
            continue
        if not rule.get("to"):
            return True
        if any((peer.get("ipBlock") or {}).get("cidr") in OPEN_CIDRS for peer in rule["to"]):
            return True
    return False


def rule_network(ns: str, labels: dict, spec: dict, policies: list[dict]) -> list[str]:
    if spec.get("hostNetwork"):
        # A policy does not apply to a pod on the host network. Rule 1 reports the pod.
        return []
    mine = [p for p in policies if ns_of(p) == ns]
    bad = []
    if not any(denies_all(p) for p in mine):
        bad.append(f"namespace {ns} has no policy that denies all traffic by default")
    chosen = [p for p in mine if not denies_all(p) and selects(policy_selector(p), labels)]
    if not chosen:
        bad.append("no policy selects the pod")
    elif not any(limits_egress(p) for p in chosen) and not any(denies_all(p) for p in mine):
        bad.append("no policy limits the egress of the pod")
    for p in chosen:
        if open_egress(p):
            bad.append(f"{p['kind']}/{p['metadata']['name']} has an egress rule that is open to each host")
    return bad


def rule_image(spec: dict) -> list[str]:
    bad = []
    for c in containers(spec):
        image = str(c.get("image") or "")
        if not image.startswith(REGISTRY):
            bad.append(f"container {c.get('name')}: the image is not from {REGISTRY}")
        if not DIGEST.search(image):
            bad.append(f"container {c.get('name')}: the image has no digest")
    return bad


# -------------------------------------------------------------------- audit

def granted_accounts(path: str = RBAC_ALLOWLIST) -> set[str]:
    data = yaml.safe_load(open(path)) or {}
    return {key.split(" -> ")[0] for key in data.get("grants") or {}}


def install_enforce_label(path: str = INSTALL_SCRIPT) -> str | None:
    """The Pod Security level that the install script sets on the release namespace."""
    m = re.search(re.escape(ENFORCE) + r"=([a-z]+)", open(path).read())
    return m.group(1) if m else None


def findings(renders: list[list[dict]], granted: set[str], release_label: str | None) -> dict[str, set[str]]:
    """'<Kind>/<namespace>/<name> <rule>' -> the findings, over each render."""
    out: dict[str, set[str]] = {}

    def add(key: str, rule: str, items: list[str]) -> None:
        if items:
            out.setdefault(f"{key} {rule}", set()).update(items)

    for docs in renders:
        accounts = {(ns_of(d), d["metadata"]["name"]): d for d in docs if d.get("kind") == "ServiceAccount"}
        policies = [d for d in docs if d.get("kind") in POLICIES]
        labels_of_ns = {d["metadata"]["name"]: (d["metadata"].get("labels") or {})
                        for d in docs if d.get("kind") == "Namespace"}
        used = set()
        for d in docs:
            if d.get("kind") not in WORKLOADS:
                continue
            ns = ns_of(d)
            used.add(ns)
            key = f"{d['kind']}/{ns}/{d['metadata']['name']}"
            labels, spec = pod_of(d)
            add(key, "restricted", rule_restricted(spec))
            add(key, "read-only", rule_read_only(spec))
            add(key, "token", rule_token(ns, spec, accounts, granted))
            add(key, "network", rule_network(ns, labels, spec, policies))
            add(key, "image", rule_image(spec))
        for ns in sorted(used):
            if ns in labels_of_ns:
                level = labels_of_ns[ns].get(ENFORCE)
            elif ns == NAMESPACE:
                level = release_label
            else:
                add(f"Namespace/{ns}", "namespace-label", ["the chart renders a pod in a namespace that it does not render"])
                continue
            if level != "restricted":
                add(f"Namespace/{ns}", "namespace-label", [f"the label {ENFORCE} is not restricted"])
    return out


def digest(items: set[str]) -> str:
    return hashlib.sha256("\n".join(sorted(items)).encode()).hexdigest()[:16]


def load_exceptions(path: str = EXCEPTIONS) -> dict:
    data = yaml.safe_load(open(path)) or {}
    for group in ("pods", "runtime"):
        for key, entry in (data.get(group) or {}).items():
            if not isinstance(entry, dict) or not str(entry.get("reason") or "").strip():
                raise SystemExit(f"{path}: entry {key!r} must give a reason (reason:)")
    for key, entry in (data.get("runtime") or {}).items():
        if entry.get("rule") not in RULES or not entry.get("created_by"):
            raise SystemExit(f"{path}: runtime entry {key!r} must name a rule and the workload that creates the pod (created_by:)")
    return data


def audit(renders: list[list[dict]], exceptions: dict, granted: set[str], release_label: str | None) -> list[str]:
    if not any(d.get("kind") in WORKLOADS for docs in renders for d in docs):
        return ["no pod in the render: this guard is blind"]
    found = findings(renders, granted, release_label)
    listed = exceptions.get("pods") or {}
    bad = []
    for key in sorted(found):
        want = digest(found[key])
        entry = listed.get(key)
        if entry is None:
            bad.append(f"{key}: " + "; ".join(sorted(found[key]))
                       + f"\n      fix the pod, or add an entry with a reason: '{key}': {{digest: {want}, reason: ...}}")
        elif entry.get("digest") != want:
            bad.append(f"{key}: the findings changed (digest {want}, the entry has {entry.get('digest')}): "
                       + "; ".join(sorted(found[key])))
    for key in sorted(listed):
        if key not in found:
            bad.append(f"stale entry in {os.path.relpath(EXCEPTIONS, ROOT)} (no render has this finding): {key!r}")
    # A runtime entry covers a pod that an operator creates. The chart renders
    # no such pod, so the target of the entry is the workload that creates it.
    rendered = {f"{d['kind']}/{ns_of(d)}/{d['metadata']['name']}"
                for docs in renders for d in docs if d.get("kind") in WORKLOADS}
    for key, entry in sorted((exceptions.get("runtime") or {}).items()):
        if entry["created_by"] not in rendered:
            bad.append(f"stale runtime entry in {os.path.relpath(EXCEPTIONS, ROOT)}: {key!r} names "
                       f"{entry['created_by']}, and no render has that workload")
    return bad


# ---------------------------------------------------------------- self-test

SECURE = """
apiVersion: v1
kind: Namespace
metadata: {name: fixture, labels: {pod-security.kubernetes.io/enforce: restricted}}
---
apiVersion: v1
kind: ServiceAccount
metadata: {name: app, namespace: fixture}
automountServiceAccountToken: false
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: default-deny, namespace: fixture}
spec: {podSelector: {}, policyTypes: [Ingress, Egress]}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: app, namespace: fixture}
spec:
  podSelector: {matchLabels: {app: app}}
  policyTypes: [Ingress, Egress]
  egress:
    - to: [{podSelector: {matchLabels: {app: db}}}]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: app, namespace: fixture}
spec:
  template:
    metadata: {labels: {app: app}}
    spec:
      serviceAccountName: app
      securityContext: {runAsNonRoot: true, seccompProfile: {type: RuntimeDefault}}
      containers:
        - name: app
          image: ghcr.io/zeroroot-ai/app@sha256:0000000000000000000000000000000000000000000000000000000000000000
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: [ALL]}
"""


def _workload(docs: list[dict]) -> dict:
    return next(d for d in docs if d["kind"] == "Deployment")


def _container(docs: list[dict]) -> dict:
    return _workload(docs)["spec"]["template"]["spec"]["containers"][0]


def _privileged(docs):
    _container(docs)["securityContext"]["privileged"] = True


def _host_path(docs):
    _workload(docs)["spec"]["template"]["spec"]["volumes"] = [{"name": "host", "hostPath": {"path": "/"}}]


def _root(docs):
    _workload(docs)["spec"]["template"]["spec"]["securityContext"].pop("runAsNonRoot")


def _writable(docs):
    _container(docs)["securityContext"].pop("readOnlyRootFilesystem")


def _token(docs):
    next(d for d in docs if d["kind"] == "ServiceAccount").pop("automountServiceAccountToken")


def _projected_token(docs):
    _workload(docs)["spec"]["template"]["spec"]["volumes"] = [
        {"name": "t", "projected": {"sources": [{"serviceAccountToken": {"path": "t"}}]}}]


def _no_default_deny(docs):
    docs[:] = [d for d in docs if d["metadata"]["name"] != "default-deny"]


def _no_policy(docs):
    docs[:] = [d for d in docs if not (d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "app")]


def _open_egress(docs):
    next(d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "app")["spec"]["egress"] = [
        {"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}]


def _open_cilium_egress(docs):
    docs.append({"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy",
                 "metadata": {"name": "world", "namespace": "fixture"},
                 "spec": {"endpointSelector": {"matchLabels": {"app": "app"}}, "egress": [{"toEntities": ["world"]}]}})


def _tag_only(docs):
    _container(docs)["image"] = "ghcr.io/zeroroot-ai/app:v1.0.0"


def _other_registry(docs):
    _container(docs)["image"] = "docker.io/library/app@sha256:" + "0" * 64


def _no_label(docs):
    next(d for d in docs if d["kind"] == "Namespace")["metadata"]["labels"] = {}


FIXTURES_THAT_MUST_FAIL = {
    "rule 1: a privileged container": ("restricted", _privileged),
    "rule 1: a hostPath volume": ("restricted", _host_path),
    "rule 1: runAsNonRoot is not set": ("restricted", _root),
    "rule 2: a writable root filesystem": ("read-only", _writable),
    "rule 3: a token mount with no grant": ("token", _token),
    "rule 3: a projected token with no grant": ("token", _projected_token),
    "rule 4: no default-deny policy": ("network", _no_default_deny),
    "rule 4: no policy selects the pod": ("network", _no_policy),
    "rule 4: an egress rule to 0.0.0.0/0": ("network", _open_egress),
    "rule 4: a CiliumNetworkPolicy to the world entity": ("network", _open_cilium_egress),
    "rule 5: an image with a tag and no digest": ("image", _tag_only),
    "rule 5: an image from a different registry": ("image", _other_registry),
    "the namespace has no restricted label": ("namespace-label", _no_label),
}


def selftest() -> int:
    base = [d for d in yaml.safe_load_all(SECURE) if d]
    none = {"pods": {}, "runtime": {}}
    got = audit([base], none, set(), None)
    if got:
        print("SELFTEST FAIL: the secure fixture must pass:\n  " + "\n  ".join(got))
        return 1
    for what, (rule, fn) in FIXTURES_THAT_MUST_FAIL.items():
        docs = copy.deepcopy(base)
        fn(docs)
        got = audit([docs], none, set(), None)
        if not any(f" {rule}: " in line.split("\n")[0] for line in got):
            print(f"SELFTEST FAIL: {what}: the guard gave no {rule} finding")
            return 1
    # Rule 3, the pass case: the same token mount with a grant.
    docs = copy.deepcopy(base)
    _token(docs)
    if audit([docs], none, {"fixture/app"}, None):
        print("SELFTEST FAIL: a token mount with a grant on the allowlist must pass")
        return 1
    # Rule 6: an entry with the right digest passes, a moved digest fails, a stale entry fails.
    docs = copy.deepcopy(base)
    _writable(docs)
    key = "Deployment/fixture/app read-only"
    want = digest(findings([docs], set(), None)[key])
    if audit([docs], {"pods": {key: {"digest": want, "reason": "fixture"}}}, set(), None):
        print("SELFTEST FAIL: a finding with an entry and the right digest must pass")
        return 1
    if not audit([docs], {"pods": {key: {"digest": "0" * 16, "reason": "fixture"}}}, set(), None):
        print("SELFTEST FAIL: an entry with a different digest must fail")
        return 1
    if not any("stale entry" in x for x in audit([base], {"pods": {key: {"digest": want, "reason": "fixture"}}}, set(), None)):
        print("SELFTEST FAIL: an entry with no finding must fail as stale")
        return 1
    runtime = {"runtime": {"connector": {"rule": "image", "created_by": "Deployment/fixture/gone", "reason": "fixture"}}}
    if not any("stale runtime entry" in x for x in audit([base], runtime, set(), None)):
        print("SELFTEST FAIL: a runtime entry whose workload is gone must fail as stale")
        return 1
    if not audit([[]], none, set(), None):
        print("SELFTEST FAIL: a render with no pod must fail as blind")
        return 1
    print(f"  ✓ selftest: {len(FIXTURES_THAT_MUST_FAIL)} insecure pods fail, a moved digest fails, a stale entry fails, "
          "a stale runtime entry fails, a blind render fails, the secure pod passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    renders = render()
    granted = granted_accounts()
    label = install_enforce_label()
    if "--print" in sys.argv or "--entries" in sys.argv:
        for key, items in sorted(findings(renders, granted, label).items()):
            if "--entries" in sys.argv:
                print(f"  '{key}': {{digest: {digest(items)}}}")
            else:
                print(f"{key}\n    " + "\n    ".join(sorted(items)))
        return 0
    exceptions = load_exceptions()
    bad = audit(renders, exceptions, granted, label)
    if bad:
        print("a rendered pod is not a secure pod (ADR-0165), or helm/gibson/secure-pod-exceptions.yaml is stale:",
              file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    pods = sum(1 for docs in renders for d in docs if d.get("kind") in WORKLOADS)
    print(f"  ✓ secure-pod: {pods} pods in {len(renders)} renders meet the six rules, "
          f"with {len(exceptions.get('pods') or {})} entries and {len(exceptions.get('runtime') or {})} runtime entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
