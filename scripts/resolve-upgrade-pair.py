#!/usr/bin/env python3
"""resolve-upgrade-pair.py — the published version to upgrade FROM, and TO.

The upgrade exit test (exit-test-published-upgrade-kind) needs two published
chart versions: the newest, and the one before it. Getting "the one before it"
wrong makes the test prove something other than what it claims, so the
resolution lives here with a self-test rather than inline in YAML.

TWO WAYS THIS GOES WRONG, both of which it did on the first attempt:

  1. A TRUNCATED TAG LIST. ghcr.io's /v2/.../tags/list paginates. Without
     `n=1000` and without following the Link header it returned 50 tags ending
     at 0.132.14, so "the previous version" resolved four minor releases too
     old and the test would have upgraded across a span nobody promotes.
  2. STRING COMPARISON. `awk '$0 < cur'` and plain `sort` order 0.9.0 after
     0.10.0 and 0.132.14 after 0.132.9. Versions compare as integer tuples or
     not at all.

So: paginate, compare as tuples, and refuse a list too short to be the real
one rather than reporting a pair from a partial read.

  resolve-upgrade-pair.py --to 0.136.1    print the version to upgrade from
  resolve-upgrade-pair.py --to 0.136.6 --from 0.136.2
                                          upgrade from a NAMED version: the one an
                                          environment pins, or the last one that
                                          installs when the previous one cannot
  resolve-upgrade-pair.py                 resolve both from the registry
  resolve-upgrade-pair.py --selftest      prove the ordering and the floor
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

REPO = "zeroroot-ai/charts/gibson"
REGISTRY = "https://ghcr.io"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
# A floor, not a guess: this chart has published far more than this. A list
# shorter than the floor is a partial read, and a pair from a partial read is
# the bug this script exists to prevent.
MIN_TAGS = 20


def key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def previous(tags: list[str], to: str) -> str | None:
    """The highest published version strictly below `to`, by version order."""
    sem = sorted({t for t in tags if SEMVER.match(t)}, key=key)
    below = [t for t in sem if key(t) < key(to)]
    return below[-1] if below else None


def named(tags: list[str], to: str, frm: str) -> str | None:
    """Why `frm` cannot be the version to upgrade from, or None when it can.
    A named version is checked as hard as a resolved one: it must be published,
    and it must be below the version under test."""
    if not SEMVER.match(frm):
        return f"--from {frm} is not a bare semver"
    if frm not in tags:
        return f"--from {frm} is not a published version"
    if key(frm) >= key(to):
        return f"--from {frm} is not below {to}: that is not an upgrade"
    return None


def token() -> str:
    pw = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    url = f"{REGISTRY}/token?scope=repository:{REPO}:pull&service=ghcr.io"
    req = urllib.request.Request(url)
    if pw:
        import base64
        basic = base64.b64encode(f"x:{pw}".encode()).decode()
        req.add_header("Authorization", f"Basic {basic}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["token"]


def all_tags(tok: str) -> list[str]:
    """Every tag, following the Link header. Never one page."""
    url = f"{REGISTRY}/v2/{REPO}/tags/list?n=1000"
    out: list[str] = []
    pages = 0
    while url and pages < 50:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            out += json.load(r).get("tags") or []
            link = r.headers.get("Link") or ""
            pages += 1
        url = None
        if 'rel="next"' in link:
            nxt = link.split(";")[0].strip("<> ")
            url = nxt if nxt.startswith("http") else REGISTRY + nxt
    return out


def selftest() -> int:
    # Ordering: a string sort gets each of these wrong.
    cases = [
        (["0.9.0", "0.10.0", "0.10.1"], "0.10.1", "0.10.0"),
        (["0.132.9", "0.132.14", "0.136.0"], "0.136.1", "0.136.0"),
        (["0.132.9", "0.132.14"], "0.136.1", "0.132.14"),
        (["1.0.0"], "1.0.0", None),
        ([], "1.0.0", None),
    ]
    for tags, to, want in cases:
        got = previous(tags, to)
        if got != want:
            print(f"SELFTEST FAIL: previous({tags}, {to}) = {got}, want {want}")
            return 1
    # Non-semver tags (latest, sha-…) are ignored rather than ordered.
    if previous(["latest", "sha-abc1234", "0.5.0"], "0.6.0") != "0.5.0":
        print("SELFTEST FAIL: a non-semver tag was not ignored")
        return 1
    named_cases = [
        ("0.136.2", None), ("0.136.6", "not below"), ("0.137.0", "not a published"),
        ("9.9.9", "not a published"), ("latest", "not a bare semver"),
    ]
    for frm, want in named_cases:
        got = named(["0.136.2", "0.136.5", "0.136.6", "latest"], "0.136.6", frm)
        if (got is None) != (want is None) or (want and want not in got):
            print(f"SELFTEST FAIL: named(--from {frm}) = {got}, want {want}")
            return 1
    print("resolve-upgrade-pair: selftest OK — version order not string order, "
          "non-semver tags ignored, an empty or equal-only list yields nothing, and a "
          "named version must be published and below the target")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    to = None
    if "--to" in sys.argv:
        to = sys.argv[sys.argv.index("--to") + 1]
    tags = all_tags(token())
    sem = [t for t in tags if SEMVER.match(t)]
    if len(sem) < MIN_TAGS:
        print(f"::error::resolve-upgrade-pair: the registry returned only {len(sem)} semver "
              f"tag(s) for {REPO}, below the floor of {MIN_TAGS}. That is a partial read, and "
              f"a version pair from a partial read would upgrade across the wrong span. "
              f"Refusing.", file=sys.stderr)
        return 1
    if to is None:
        to = max(sem, key=key)
    frm = sys.argv[sys.argv.index("--from") + 1] if "--from" in sys.argv else ""
    if frm:
        why = named(tags, to, frm)
        if why:
            print(f"::error::resolve-upgrade-pair: {why}", file=sys.stderr)
            return 1
        prev = frm
    else:
        prev = previous(tags, to)
    if prev is None:
        print(f"::error::resolve-upgrade-pair: no published version below {to} in "
              f"{len(sem)} tag(s); there is nothing to upgrade FROM", file=sys.stderr)
        return 1
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"to={to}\nfrom={prev}\n")
    print(f"upgrade path under test: {prev} -> {to}  ({len(sem)} published versions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
