#!/usr/bin/env python3
"""check-openbao-policies.py: no OpenBao policy of the render grants the whole store (ADR-0032).

The openbao-auto-init sidecar writes one policy for each consumer. The check
reads each policy body that the sidecar script writes (vault_write_policy
<token> <name> '<HCL>') in each golden render, and in one render with the
cert-manager Vault issuer on. It fails when:

  - a policy grants path "*",
  - a policy grants sudo on a path that SUDO does not name,
  - a token of JOB_TOKENS, or the platform-operator or seeder policy, has
    no policy body in the script,
  - the transit key of the platform-operator policy is not the keyName of
    the PlatformBootstrap of the same render.

  check-openbao-policies.py             exit 1 on a finding
  check-openbao-policies.py --selftest  prove each finding fails
"""
import glob
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
# Each path where a policy may hold sudo, with the reason.
SUDO = {
    "sys/auth/kubernetes": "the seeder enables and reads the kubernetes auth mount",
    "sys/auth/jwt": "the jwt init Job enables the mount; the tenant-operator reads it",
    "sys/auth/approle": "the approle init Job enables the approle mount",
    "auth/token/create": "the seeder mints no_parent tokens with policies it does not hold",
    "tenant-*": "the tenant-operator administers each tenant namespace",
}
POLICY = re.compile(r'vault_write_policy "\$tok" "?([$A-Za-z_-]+)"? \\\s*\n\s*\'(.*?)\'', re.S)
PATH = re.compile(r'path "([^"]+)" \{ capabilities = \[([^\]]*)\] \}')
VAR = re.compile(r'^\s*([A-Z_]+)="([^"]*)"\s*$', re.M)


def script(docs: list) -> str:
    for d in docs:
        if isinstance(d, dict) and d.get("kind") in ("StatefulSet", "Deployment") and \
                (d.get("metadata") or {}).get("name", "").endswith("-openbao"):
            for c in d["spec"]["template"]["spec"].get("containers") or []:
                if c.get("name") == "openbao-auto-init":
                    return "\n".join(str(a) for a in (c.get("args") or []) + (c.get("command") or []))
    return ""


def transit_key(docs: list) -> str | None:
    for d in docs:
        if isinstance(d, dict) and d.get("kind") == "PlatformBootstrap":
            return ((d.get("spec") or {}).get("vaultTransit") or {}).get("keyName")
    return None


def judge(text: str, key: str | None) -> list[str]:
    if not text:
        return ["no openbao-auto-init script in the render: this check is blind"]
    env = dict(VAR.findall(text))
    pols = {}
    for name, body in POLICY.findall(text):
        name = env.get(name.lstrip("$"), name) if name.startswith("$") else name
        pols[name] = PATH.findall(body)
    out = []
    want = {env.get("SEEDER_POLICY", "openbao-seeder"), env.get("PLATFORM_POLICY", "platform-operator")}
    want |= {p.split(":", 1)[1] for p in env.get("JOB_TOKENS", "").split() if ":" in p}
    for name in sorted(want - set(pols)):
        out.append(f"the token policy {name} has no body in the script")
    for name, paths in sorted(pols.items()):
        for path, caps in paths:
            if path == "*":
                out.append(f"policy {name} grants path \"*\"")
            if "sudo" in caps and path not in SUDO:
                out.append(f"policy {name} grants sudo on {path}, which SUDO does not name")
    plat = pols.get(env.get("PLATFORM_POLICY", "platform-operator"), [])
    keys = [p[len("transit/keys/"):] for p, _ in plat if p.startswith("transit/keys/")]
    if key is not None and keys != [key]:
        out.append(f"the platform-operator policy grants transit keys {keys}, and PlatformBootstrap names {key}")
    return out


def render_with_vault_issuer() -> list:
    r = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
         "-f", "helm/testdata/render-inputs/gibson.yaml", "-f", "helm/gibson/values-baseline.yaml",
         "--set", "gibson-workloads.certManager.issuers.vault.enabled=true",
         "--set", "gibson-workloads.certManager.issuers.vault.path=pki_int/sign/gibson",
         "--set", "gibson-workloads.certManager.issuers.vault.auth.appRole.roleId=example-role-id"],
        cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"the render with the vault issuer failed: {r.stderr.strip()[-400:]}")
    return list(yaml.safe_load_all(r.stdout))


def selftest() -> int:
    head = 'SEEDER_POLICY="openbao-seeder"\nPLATFORM_POLICY="platform-operator"\nJOB_TOKENS="s-a:job-a"\n'
    def pol(name, body):
        return f'vault_write_policy "$tok" {name} \\\n  \'{body}\' || return 1\n'
    seeder = pol('"$SEEDER_POLICY"', 'path "auth/token/create" { capabilities = ["create", "sudo"] }')
    plat = pol('"$PLATFORM_POLICY"', 'path "transit/keys/k" { capabilities = ["read"] }')
    job = pol("job-a", 'path "secret/data/x" { capabilities = ["read"] }')
    good = head + seeder + plat + job
    if judge(good, "k"):
        print(f"SELFTEST FAIL: a narrow set must pass, got {judge(good, 'k')}")
        return 1
    cases = (
        ("path \"*\"", head + seeder + plat + job + pol("legacy", 'path "*" { capabilities = ["read"] }'), "k"),
        ("sudo outside the list", head + seeder + plat + pol("job-a", 'path "secret/data/x" { capabilities = ["read", "sudo"] }'), "k"),
        ("a job token with no policy", head + seeder + plat, "k"),
        ("another transit key", good, "master-kek"),
        ("no script", "", "k"),
    )
    for what, text, key in cases:
        if len(judge(text, key)) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(text, key)}")
            return 1
    print("  ✓ selftest: path \"*\", sudo outside the list, a token with no policy, a wrong transit key and a blind render each fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = [], 0
    renders = [(os.path.basename(f), list(yaml.safe_load_all(open(f))))
               for f in sorted(glob.glob(os.path.join(ROOT, GOLDEN, "values-*.yaml")))]
    renders.append(("baseline with the vault issuer", render_with_vault_issuer()))
    for name, docs in renders:
        seen += 1
        bad += [f"{name}: {x}" for x in judge(script(docs), transit_key(docs))]
    if bad:
        print("an OpenBao policy of the render reaches too far (ADR-0032):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ openbao-policies: {seen} renders, no path \"*\", sudo only on the named paths, one policy for each token")
    return 0


if __name__ == "__main__":
    sys.exit(main())
