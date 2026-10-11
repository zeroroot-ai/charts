#!/usr/bin/env python3
"""A fake of the parts of OpenBao and the Kubernetes API that the token
functions of the openbao-auto-init sidecar call (tests/openbao-pg-login-lease.bats).

OpenBao: a token has a period and ends when it is not renewed in time, or
when it is revoked. Each lease belongs to the token that read it, and ends
with its TTL or with its token, the way OpenBao revokes the leases of a token.

  fake.py <state.json> <now> bao <METHOD> <path> <token> <body> <out file>
  fake.py <state.json> <now> kube <METHOD> <secret name or ""> <body> <out file>
  fake.py <state.json> <now> creds <role>      ESO reads a login role with the Secret token
  fake.py <state.json> <now> alive <lease id>  exit 0 when the lease is live
  fake.py <state.json> <now> holder            a holder of a copy of each token renews it
  fake.py <state.json> <now> oldest            print the age of the oldest live token
A token with an explicit maximum TTL never lives past it, and a lease never
outlives the maximum of its token, as in OpenBao.
Each request form prints the HTTP status.
"""
import base64
import json
import os
import sys

state_file, now = sys.argv[1], int(sys.argv[2])
st = json.load(open(state_file)) if os.path.exists(state_file) else {
    "n": 0, "tokens": {}, "leases": {}, "secret": None}
LEASE_TTL = 48 * 3600


def secs(p: str) -> int:
    return int(p[:-1]) * {"s": 1, "m": 60, "h": 3600}[p[-1]]


def expire():
    for tid, t in st["tokens"].items():
        if t["live"] and t["expires"] <= now:
            t["live"] = False
    for lid, lease in st["leases"].items():
        if lease["live"] and (lease["expires"] <= now or not st["tokens"][lease["token"]]["live"]):
            lease["live"] = False
            lease["ended_by"] = "ttl" if lease["expires"] <= now else "token"


def save():
    json.dump(st, open(state_file, "w"))


def tok(tid):
    t = st["tokens"].get(tid)
    return t if t and t["live"] else None


expire()
kind = sys.argv[3]
if kind == "bao":
    method, path, token, body, out = sys.argv[4:9]
    t, code, resp = tok(token), 404, {}
    if path == "auth/token/lookup-self":
        if t:
            code, resp = 200, {"data": {"ttl": t["expires"] - now, "policies": t["policies"],
                                        "creation_time": t["created"], "accessor": t["accessor"]}}
        else:
            code = 403
    elif path == "auth/token/renew-self":
        if t:
            t["expires"] = min(now + t["period"], t["max_end"])
            code = 200
        else:
            code = 403
    elif path == "auth/token/create":
        b = json.loads(body)
        st["n"] += 1
        tid, acc = f"tok-{st['n']}", f"acc-{st['n']}"
        max_end = now + secs(b["explicit_max_ttl"]) if b.get("explicit_max_ttl") else 2**62
        st["tokens"][tid] = {"accessor": acc, "policies": b["policies"], "period": secs(b["period"]),
                             "created": now, "expires": min(now + secs(b["period"]), max_end),
                             "max_end": max_end, "live": True}
        code, resp = 200, {"auth": {"client_token": tid}}
    elif path == "auth/token/revoke-accessor":
        acc = json.loads(body)["accessor"]
        for t2 in st["tokens"].values():
            if t2["accessor"] == acc:
                t2["live"] = False
        code = 204
    expire()
    open(out, "w").write(json.dumps(resp, separators=(",", ":")))
    save()
    print(code)
elif kind == "kube":
    method, name, body, out = sys.argv[4:8]
    s, code, resp = st["secret"], 404, {"kind": "Status", "code": 404}
    if method == "GET":
        if s:
            code, resp = 200, {"kind": "Secret", "metadata": {"annotations": s["annotations"]},
                               "data": {"VAULT_ADMIN_TOKEN": s["token_b64"]}}
    elif method == "POST":
        if s:
            code = 409
        else:
            st["secret"] = {"annotations": {}, "token_b64": json.loads(body)["data"]["VAULT_ADMIN_TOKEN"]}
            code = 201
    elif method == "PATCH" and s:
        b = json.loads(body)
        if "data" in b:
            s["token_b64"] = b["data"]["VAULT_ADMIN_TOKEN"]
        for k, v in ((b.get("metadata") or {}).get("annotations") or {}).items():
            if v is None:
                s["annotations"].pop(k, None)
            else:
                s["annotations"][k] = v
        code = 200
    open(out, "w").write(json.dumps(resp, separators=(",", ":")))
    save()
    print(code)
elif kind == "creds":
    tid = base64.b64decode(st["secret"]["token_b64"]).decode()
    if not tok(tid):
        sys.exit(f"ESO read {sys.argv[4]} with a token that is not live: {tid}")
    st["n"] += 1
    lid = f"lease-{st['n']}"
    st["leases"][lid] = {"token": tid, "expires": min(now + LEASE_TTL, st["tokens"][tid]["max_end"]),
                         "live": True}
    save()
    print(lid)
elif kind == "holder":
    for t in st["tokens"].values():
        if t["live"]:
            t["expires"] = min(now + t["period"], t["max_end"])
    save()
elif kind == "oldest":
    ages = [now - t["created"] for t in st["tokens"].values() if t["live"]]
    print(max(ages) if ages else 0)
elif kind == "alive":
    lease = st["leases"][sys.argv[4]]
    if not lease["live"]:
        print(f"{sys.argv[4]} ended by its {lease.get('ended_by')} (token {lease['token']})")
        sys.exit(1)
