#!/usr/bin/env python3
"""check-fga-tls.py: the daemon dials OpenFGA over TLS with the mounted CA (gibson fix/fga-client-uses-tls).

The daemon refuses an https FGA endpoint unless authz.fga.tls.enabled is true.
With the key on, it trusts only the CA in authz.fga.tls.ca_file, and it does
not start when that file is not a readable PEM file.

The check reads the committed golden renders (helm/testdata/golden/), which
`make golden` keeps equal to the chart. For each daemon StatefulSet and its
config ConfigMap, it fails when:

  - the FGA endpoint is https and authz.fga.tls.enabled is not true,
  - tls.enabled is true and tls.ca_file is empty, or
  - tls.ca_file is not under a volumeMount of the daemon container.

It fails as blind when no golden file holds a daemon config.

  check-fga-tls.py             exit 1 on a finding
  check-fga-tls.py --selftest  prove each finding fails
"""
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
CONFIG_KEY = "gibson.yaml"


def daemon_configs(docs):
    """(ConfigMap name, parsed gibson.yaml) for each daemon config."""
    for d in docs:
        if isinstance(d, dict) and d.get("kind") == "ConfigMap":
            raw = (d.get("data") or {}).get(CONFIG_KEY)
            if raw:
                yield d["metadata"]["name"], yaml.safe_load(raw)


def daemon_mounts(docs) -> list[str]:
    """The mountPaths of the gibson container of each daemon StatefulSet."""
    out = []
    for d in docs:
        if isinstance(d, dict) and d.get("kind") == "StatefulSet":
            for c in d["spec"]["template"]["spec"].get("containers") or []:
                if c.get("name") == "gibson":
                    out += [m.get("mountPath", "") for m in c.get("volumeMounts") or []]
    return out


def judge(name: str, cfg: dict, mounts: list[str]) -> list[str]:
    fga = (((cfg or {}).get("authz") or {}).get("fga")) or {}
    endpoint = str(fga.get("endpoint") or "")
    tls = fga.get("tls") or {}
    on = tls.get("enabled") is True
    ca = str(tls.get("ca_file") or "")
    out = []
    if endpoint.startswith("https://") and not on:
        out.append(f"{name}: the FGA endpoint {endpoint} is https and authz.fga.tls.enabled is not true; the daemon refuses it")
    if on and not ca:
        out.append(f"{name}: authz.fga.tls.enabled is true and authz.fga.tls.ca_file is empty; the daemon does not start")
    elif on and not any(m and ca.startswith(m.rstrip("/") + "/") for m in mounts):
        out.append(f"{name}: authz.fga.tls.ca_file {ca} is under no volumeMount of the daemon container")
    return out


def audit(root: str) -> tuple[list[str], int]:
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        docs = list(yaml.safe_load_all(open(f)))
        mounts = daemon_mounts(docs)
        for name, cfg in daemon_configs(docs):
            if not ((cfg or {}).get("authz") or {}).get("fga"):
                continue
            seen += 1
            bad += [f"{os.path.basename(f)}: {x}" for x in judge(name, cfg, mounts)]
    if not seen:
        bad.append("no golden render holds a daemon config with authz.fga: this check is blind")
    return bad, seen


def selftest() -> int:
    mounts = ["/etc/gibson", "/etc/ssl/fga-ca"]

    def cfg(endpoint="https://gibson-openfga:8080", **tls):
        return {"authz": {"fga": {"endpoint": endpoint, "tls": tls}}}

    good = cfg(enabled=True, ca_file="/etc/ssl/fga-ca/ca.crt")
    if judge("ok", good, mounts):
        print(f"SELFTEST FAIL: TLS on with a mounted CA must pass, got {judge('ok', good, mounts)}")
        return 1
    if judge("http", cfg("http://fga:8080", enabled=False), mounts):
        print("SELFTEST FAIL: an http endpoint with TLS off must pass")
        return 1
    for what, c, m in (("TLS off with an https endpoint", cfg(enabled=False), mounts),
                       ("no tls block with an https endpoint", cfg(), mounts),
                       ("TLS on with no CA file", cfg(enabled=True), mounts),
                       ("a CA file under no mount", good, ["/etc/gibson"]),
                       ("a CA file beside a mount of the same prefix", good, ["/etc/ssl/fga"])):
        if len(judge("bad", c, m)) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge('bad', c, m)}")
            return 1
    print("  ✓ selftest: TLS off with https, no tls block, no CA file and a CA file under no mount each fail; TLS on with a mounted CA and plain http pass")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("the daemon cannot dial OpenFGA over TLS in a render:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ fga-tls: {seen} rendered daemon configs dial OpenFGA over TLS with a mounted CA")
    return 0


if __name__ == "__main__":
    sys.exit(main())
