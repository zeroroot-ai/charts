#!/usr/bin/env python3
"""check-edge-access-log-no-credentials.py: no Envoy access log records a credential header's value.

An access log line that carries a request's Authorization header carries the
caller's whole bearer token into the pod log, and from there into every log
sink. Measured 2026-09-29 on staging: the api chain's `authorization_present`
field was `%REQ(AUTHORIZATION)%`, under a comment that said the value was not
logged, and every line held the caller's JWT. A comment cannot hold that
promise. This guard can.

The rule, on every access log in every rendered Envoy config:

1. A format operator that reads a credential header (Authorization,
   Proxy-Authorization, Cookie, Set-Cookie, X-Api-Key, or any header whose
   name ends in -token or -secret) must truncate its value to at most eight
   characters, which is room for the scheme word ("Bearer") and nothing of
   the credential. `%REQ(AUTHORIZATION):6%` passes; `%REQ(AUTHORIZATION)%`
   and `%REQ(AUTHORIZATION):64%` fail.
2. A Cookie or Set-Cookie header is never logged at all, truncated or not:
   its first characters are a cookie name and the start of its value.
3. The request path is logged without its query string (%PATH(NQ)%), never
   as %REQ(:PATH)% or %PATH% with the query. A query carries the token of a
   query-token extra route (charts#561) and the code of a sign-in callback.

  check-edge-access-log-no-credentials.py             exit 1 on a finding, 0 when clean
  check-edge-access-log-no-credentials.py --selftest  prove each kind of finding fails
"""

from __future__ import annotations

import copy
import re
import subprocess
import sys

import yaml

ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
NAMESPACE = "gibson"
MAX_TRUNCATION = 8
NEVER = ("cookie", "set-cookie")
CREDENTIAL = ("authorization", "proxy-authorization", "x-api-key")
CREDENTIAL_SUFFIXES = ("-token", "-secret")
# %REQ(NAME)% / %REQ(NAME?ALT)% / %RESP(NAME)% / %TRAILER(NAME)%, with an
# optional :N truncation. Envoy upper-cases nothing; names are case-insensitive.
OPERATOR = re.compile(r"%(REQ|RESP|TRAILER)\(([^)]*)\)(?::(\d+))?%")
# A format operator that logs the request path with its query string:
# %REQ(:PATH)% and %PATH% or %PATH(WQ...)%. %PATH(NQ...)% drops the query.
PATH_WITH_QUERY = re.compile(r"%REQ\(\s*:PATH\s*(?:\?[^)]*)?\)(?::\d+)?%|%PATH(?:\((?!NQ)[^)]*\))?(?::\d+)?%",
                             re.IGNORECASE)


# ------------------------------------------------------------------ render

def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", NAMESPACE],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def envoy_configs(docs: list[dict]) -> list[tuple[str, str, dict]]:
    """(ConfigMap name, data key, parsed config) for every Envoy bootstrap in the render."""
    out = []
    for d in docs:
        if d.get("kind") != "ConfigMap":
            continue
        for key, value in (d.get("data") or {}).items():
            if isinstance(value, str) and "static_resources" in value:
                out.append((d["metadata"]["name"], key, yaml.safe_load(value)))
    return out


def access_log_strings(node, path: str = "access_log"):
    """Yield (path, string) for every string under every access_log key in the config."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "access_log":
                yield from _strings(v, f"{path}")
            else:
                yield from access_log_strings(v, path)
    elif isinstance(node, list):
        for item in node:
            yield from access_log_strings(item, path)


def _strings(node, path: str):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _strings(item, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


# ------------------------------------------------------------------- audit

def is_credential(name: str) -> bool:
    n = name.lower()
    return n in CREDENTIAL or n in NEVER or n.endswith(CREDENTIAL_SUFFIXES)


def audit(docs: list[dict]) -> list[str]:
    configs = envoy_configs(docs)
    if not configs:
        return ["no Envoy config in the render: this guard is blind"]
    bad, logs = [], 0
    for cm, key, cfg in configs:
        for path, s in access_log_strings(cfg):
            logs += 1
            if PATH_WITH_QUERY.search(s):
                bad.append(f"{cm}/{key} {path}: logs the request path with its query string; "
                           "log %PATH(NQ)% so a query token never reaches the log")
            for m in OPERATOR.finditer(s):
                kind, names, trunc = m.group(1), m.group(2), m.group(3)
                for name in names.split("?"):
                    name = name.strip()
                    if not is_credential(name):
                        continue
                    where = f"{cm}/{key} {path}"
                    if name.lower() in NEVER:
                        bad.append(f"{where}: logs {kind}({name}); a cookie header is never logged, truncated or not")
                    elif trunc is None:
                        bad.append(f"{where}: logs the whole {kind}({name}) header; truncate it to at most "
                                   f"{MAX_TRUNCATION} characters (:{MAX_TRUNCATION}) so only the scheme word is kept")
                    elif int(trunc) > MAX_TRUNCATION:
                        bad.append(f"{where}: logs {kind}({name}):{trunc}; {trunc} characters reach into the "
                                   f"credential, at most {MAX_TRUNCATION} is the scheme word")
    if logs == 0:
        bad.append("no access_log in any rendered Envoy config: this guard is blind")
    return bad


# ---------------------------------------------------------------- self-test

def _edit(docs: list[dict], fn) -> list[dict]:
    """A copy of the render with fn applied to every Envoy config's first access log json_format."""
    out = copy.deepcopy(docs)
    for d in out:
        if d.get("kind") != "ConfigMap":
            continue
        for k, value in (d.get("data") or {}).items():
            if not isinstance(value, str) or "static_resources" not in value:
                continue
            cfg = yaml.safe_load(value)
            if fn(cfg):
                d["data"][k] = yaml.safe_dump(cfg)
    return out


def _first_json_format(cfg) -> dict | None:
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            if k == "json_format" and isinstance(v, dict):
                return v
            got = _first_json_format(v)
            if got is not None:
                return got
    elif isinstance(cfg, list):
        for item in cfg:
            got = _first_json_format(item)
            if got is not None:
                return got
    return None


def _plant(field: str, value: str):
    def fn(cfg):
        jf = _first_json_format(cfg)
        if jf is None:
            return False
        jf[field] = value
        return True
    return fn


def selftest(docs: list[dict]) -> int:
    cases = [
        ("whole Authorization header", _plant("authz", "%REQ(AUTHORIZATION)%"), "whole"),
        ("Authorization truncated too long", _plant("authz", "%REQ(AUTHORIZATION):64%"), "reach into"),
        ("lower-case authorization", _plant("authz", "%REQ(authorization)%"), "whole"),
        ("Authorization behind an alternate", _plant("authz", "%REQ(X-REQUEST-ID?AUTHORIZATION)%"), "whole"),
        ("Cookie header, truncated", _plant("cookie", "%REQ(COOKIE):4%"), "never logged"),
        ("Set-Cookie response header", _plant("cookie", "%RESP(SET-COOKIE)%"), "never logged"),
        ("a -token header", _plant("tok", "%REQ(X-CSRF-TOKEN)%"), "whole"),
        ("X-Api-Key", _plant("key", "%REQ(X-API-KEY):32%"), "reach into"),
        ("the path with its query", _plant("path", "%REQ(:PATH)%"), "query string"),
        ("the PATH operator with its query", _plant("path", "%PATH(WQ:ORIG)%"), "query string"),
        ("the bare PATH operator", _plant("path", "%PATH%"), "query string"),
    ]
    rc = 0
    clean = audit(docs)
    if clean:
        print("SELFTEST FAIL: the render itself has findings:\n  " + "\n  ".join(clean), file=sys.stderr)
        rc = 1
    passes = [
        ("Authorization truncated to the scheme word", _plant("authz", "%REQ(AUTHORIZATION):6%")),
        ("a non-credential header in full", _plant("ua", "%REQ(USER-AGENT)%")),
        ("the path without its query", _plant("path", "%PATH(NQ:ORIG)%")),
    ]
    for name, fn, needle in cases:
        findings = audit(_edit(docs, fn))
        if any(needle in f for f in findings):
            print(f"  ok   {name} fails")
        else:
            print(f"  FAIL {name}: expected a finding containing {needle!r}, got {findings}", file=sys.stderr)
            rc = 1
    for name, fn in passes:
        findings = audit(_edit(docs, fn))
        if not findings:
            print(f"  ok   {name} passes")
        else:
            print(f"  FAIL {name}: expected no finding, got {findings}", file=sys.stderr)
            rc = 1
    print("SELFTEST " + ("PASS" if rc == 0 else "FAIL"))
    return rc


def main() -> int:
    docs = render()
    if "--selftest" in sys.argv:
        return selftest(docs)
    bad = audit(docs)
    if bad:
        print("edge-access-log-no-credentials: FAIL\n  " + "\n  ".join(bad))
        return 1
    print("edge-access-log-no-credentials: OK (no access log records a credential header's value)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
