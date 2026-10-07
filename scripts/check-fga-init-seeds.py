#!/usr/bin/env python3
"""check-fga-init-seeds.py: fga-init seeds each system_tenant relation for its listed holders only (charts#485).

The gibson daemon gates UserService.GetSignupProgress and SetSignupProgress on
the relation signup_service of system_tenant:_system (gibson#761, model.fga).
Only the dashboard's service identity holds it. Without the tuple an install
refuses signup progress with PermissionDenied. The platform_operator relation
stays with the operators and never with the dashboard (hosted#191).

The check reads the committed golden renders (helm/testdata/golden/), which
`make golden` keeps equal to the chart. For each fga-init Job it reads the
seed_tuple and prune_relation calls of the script, and it fails when:

  - signup_service is not seeded for exactly gibson-dashboard-service,
  - platform_operator is seeded for gibson-dashboard-service, or for nobody,
  - a seeded relation has no prune_relation call, or
  - a seeded SA is not an identity-map key: the iam-admin entry or the name
    of a MACHINE_USER OIDCClient of the rendered PlatformBootstrap.

It fails as blind when no golden file holds an fga-init Job.

  check-fga-init-seeds.py             exit 1 on a finding
  check-fga-init-seeds.py --selftest  prove each finding fails
"""
import glob
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
SIGNUP_SA = "gibson-dashboard-service"
IAM_ADMIN_ENTRY = "gibson-iam-admin"

ASSIGN = re.compile(r"^\s*([A-Z_]+)='([^']*)'\s*$", re.M)
SEED = re.compile(r"^\s*seed_tuple\s+(\S+)\s+(\S+)\s*$", re.M)
PRUNE = re.compile(r"^\s*prune_relation\s+(\S+)\s*$", re.M)


def fga_init_scripts(docs):
    """(Job name, script) for each fga-init container of a Job."""
    for d in docs:
        if not (isinstance(d, dict) and d.get("kind") == "Job"):
            continue
        for c in d["spec"]["template"]["spec"].get("containers") or []:
            if c.get("name") == "fga-init":
                yield d["metadata"]["name"], "\n".join(c.get("args") or [])


def machine_users(docs) -> set[str]:
    """The names of the MACHINE_USER OIDCClients of each PlatformBootstrap."""
    out = set()
    for d in docs:
        if isinstance(d, dict) and d.get("kind") == "PlatformBootstrap":
            for c in (d.get("spec") or {}).get("oidcClients") or []:
                if c.get("applicationType") == "MACHINE_USER":
                    out.add(c["name"])
    return out


def seeds(script: str) -> tuple[dict[str, list[str]], set[str]]:
    """relation -> holder names, and the set of pruned relations.

    A seed_tuple argument is a literal name, a $VAR, or a "$VAR"; a variable
    holds a space-separated list. A seed_tuple inside a for loop over a
    variable lists each name of that variable.
    """
    env = {k: v.split() for k, v in ASSIGN.findall(script)}
    loop_var = {}
    for m in re.finditer(r"^\s*for\s+(\w+)\s+in\s+\$(\w+)\s*;?\s*do\s*$", script, re.M):
        loop_var[m.group(1)] = m.group(2)
    out: dict[str, list[str]] = {}
    for relation, arg in SEED.findall(script):
        name = arg.strip('"')
        if name.startswith("$"):
            var = name[1:].strip("{}")
            var = loop_var.get(var, var)
            names = env.get(var, [])
        else:
            names = [name]
        out.setdefault(relation, []).extend(names)
    return out, set(PRUNE.findall(script))


def judge(name: str, script: str, subjects: set[str]) -> list[str]:
    held, pruned = seeds(script)
    out = []
    signup = held.get("signup_service", [])
    if signup != [SIGNUP_SA]:
        out.append(f"{name}: signup_service must be seeded for exactly {SIGNUP_SA}, got {signup}")
    operators = held.get("platform_operator", [])
    if not operators:
        out.append(f"{name}: platform_operator is seeded for nobody")
    if SIGNUP_SA in operators:
        out.append(f"{name}: platform_operator is seeded for {SIGNUP_SA}; the dashboard holds no cross-tenant relation (hosted#191)")
    for relation in held:
        if relation not in pruned:
            out.append(f"{name}: {relation} is seeded and never pruned; an old holder would keep the relation")
    for relation, names in held.items():
        for sa in names:
            if sa != IAM_ADMIN_ENTRY and sa not in subjects:
                out.append(f"{name}: {relation} is seeded for {sa}, which is no MACHINE_USER OIDCClient and never appears in gibson-sa-identity-map")
    return out


def audit(root: str) -> tuple[list[str], int]:
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        docs = list(yaml.safe_load_all(open(f)))
        subjects = machine_users(docs)
        for name, script in fga_init_scripts(docs):
            seen += 1
            bad += [f"{os.path.basename(f)}: {x}" for x in judge(name, script, subjects)]
    if not seen:
        bad.append("no golden render holds an fga-init Job: this check is blind")
    return bad, seen


def selftest() -> int:
    subjects = {"gibson-tenant-operator", SIGNUP_SA}

    def script(signup=SIGNUP_SA, operators="gibson-iam-admin gibson-tenant-operator", prune=("platform_operator", "signup_service")):
        lines = [f"REQUIRED='{operators}'", f"SIGNUP_SERVICE_SA='{signup}'",
                 "for sa in $REQUIRED; do", '  seed_tuple platform_operator "$sa"', "done"]
        if "platform_operator" in prune:
            lines.append("prune_relation platform_operator")
        if signup:
            lines.append('seed_tuple signup_service "$SIGNUP_SERVICE_SA"')
        if "signup_service" in prune:
            lines.append("prune_relation signup_service")
        return "\n".join(lines)

    good = script()
    if judge("ok", good, subjects):
        print(f"SELFTEST FAIL: the listed holders with both prunes must pass, got {judge('ok', good, subjects)}")
        return 1
    # A stale name gives two findings: the wrong holder and the unmapped name.
    for what, s, subs, n in (("no signup_service seed", script(signup=""), subjects, 1),
                             ("signup_service for a stale name", script(signup="gibson-dashboard-sa"), subjects, 2),
                             ("platform_operator for the dashboard", script(operators=f"gibson-iam-admin {SIGNUP_SA}"), subjects, 1),
                             ("platform_operator for nobody", script(operators=""), subjects, 1),
                             ("no signup_service prune", script(prune=("platform_operator",)), subjects, 1),
                             ("no platform_operator prune", script(prune=("signup_service",)), subjects, 1),
                             ("a holder that is no MACHINE_USER", good, {SIGNUP_SA}, 1)):
        if len(judge("bad", s, subs)) != n:
            print(f"SELFTEST FAIL: {what} must give {n} finding(s), got {judge('bad', s, subs)}")
            return 1
    print("  ✓ selftest: a missing, stale, cross-tenant, empty, unpruned or unmapped holder each fail; the listed holders pass")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("fga-init does not seed the system_tenant relations for their listed holders:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ fga-init-seeds: {seen} rendered fga-init Jobs seed signup_service for {SIGNUP_SA} and platform_operator for the operators only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
