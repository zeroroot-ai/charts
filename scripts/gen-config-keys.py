#!/usr/bin/env python3
"""gen-config-keys.py — the config keys the gibson daemon BINDS, as a contract.

The chart renders a gibson.yaml ConfigMap. The daemon loads it with viper, which
IGNORES an unknown key: a line the daemon does not bind is dropped in silence,
so the ConfigMap can accumulate settings that look like configuration and
configure nothing. gibson#599 deleted 46 such keys from the daemon side; 16 of
them were still being rendered here.

Same shape as gen-env-readers.py and for the same reason: the consumer lives in
another repository, so it cannot be read at PR time. The bound set is extracted
from a gibson checkout and COMMITTED as helm/contracts/gibson-config-keys.txt,
which check-config-consumed.py reads with no network and no sibling checkout.
env-contract-fresh.yml re-extracts it against the real repos.

WHAT COUNTS AS BOUND

  1. a struct tag — mapstructure, yaml or json — anywhere in the repo. viper
     binds through mapstructure; the other two appear on the same fields and a
     field carrying only `yaml` is still a key the loader reads.
  2. a dotted path appearing as a Go string literal, for the paths read through
     `viper.GetString("core.timeout")` style calls rather than a struct.
     A literal whose last segment is a file extension is a file name, not a
     key: `"queue_redis_adapter.go"`, `"tls.key"` and `"config.yaml"` all match
     the dotted shape, and none is a viper path (charts#716).

Extracted repo-wide, NOT from internal/infra/config alone. Scoping to that one
package produced nine false positives on the first pass, including
security.key_provider.* — which is the system-tenant KEK path, and deleting it
would have broken tenant secret encryption.

  gen-config-keys.py --write    rewrite the contract from the sibling checkout
  gen-config-keys.py --check    fail when the committed contract is stale
  gen-config-keys.py --selftest prove a file name is not read as a key
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from pinned_source import pinned_tag, tree_at

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "helm" / "contracts" / "gibson-config-keys.txt"

TAG = re.compile(r'(?:mapstructure|yaml|json):"([a-zA-Z0-9_]+)')
DOTTED = re.compile(r'"([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)"')
# `// Example: "core.parallel_limit"` is prose, not a binding.
LINE_COMMENT = re.compile(r"//[^\n]*")
# The last segment of a file name. No key the daemon binds ends in one of these.
FILE_EXTS = frozenset((
    "go", "mod", "sum", "proto", "yaml", "yml", "json", "toml", "txt", "md",
    "sh", "py", "sql", "db", "log", "tar", "gz", "zip", "csv", "html", "js", "ts",
    "pem", "crt", "key", "cert", "der", "p12", "pub", "lock", "tpl",
))


def is_file_name(path: str) -> bool:
    return path.rsplit(".", 1)[-1] in FILE_EXTS


def extract_text(text: str) -> tuple[set[str], set[str]]:
    """The leaves and dotted paths that one Go source text binds."""
    leaves: set[str] = set()
    paths: set[str] = set()
    text = LINE_COMMENT.sub("", text)
    for m in TAG.finditer(text):
        name = m.group(1)
        if name not in ("-", ""):
            leaves.add(name)
    for m in DOTTED.finditer(text):
        if not is_file_name(m.group(1)):
            paths.add(m.group(1))
    return leaves, paths


def extract(repo: Path) -> tuple[set[str], set[str]]:
    leaves: set[str] = set()
    paths: set[str] = set()
    for f in repo.rglob("*.go"):
        # Production files only. A key read by a test, or named in a doc comment
        # showing a name conversion, is not a key the daemon binds — both put
        # core.parallel_limit in this set on the first pass and it is unbound.
        if "/vendor/" in str(f) or f.name.endswith("_test.go"):
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        got_l, got_p = extract_text(text)
        leaves |= got_l
        paths |= got_p
    return leaves, paths


def selftest() -> int:
    src = (
        'type C struct { T int `mapstructure:"timeout"` }\n'
        'var a = viper.GetString("core.parallel_limit")\n'
        'var b = map[string]string{"x": "queue_redis_adapter.go"}\n'
        'var c = filepath.Join(dir, "tls.key")\n'
        'var d = "mission.yaml"\n'
        '// Example: "core.in_a_comment"\n'
    )
    leaves, paths = extract_text(src)
    cases = [
        ("a struct tag is a leaf", "timeout" in leaves),
        ("a dotted literal is a path", "core.parallel_limit" in paths),
        ("FIXTURE a Go file name is not a path", "queue_redis_adapter.go" not in paths),
        ("FIXTURE a key file name is not a path", "tls.key" not in paths),
        ("FIXTURE a YAML file name is not a path", "mission.yaml" not in paths),
        ("a dotted literal in a comment is not a path", "core.in_a_comment" not in paths),
        ("the filter refuses a file name", is_file_name("queue_redis_adapter.go")),
        ("the filter keeps a key", not is_file_name("security.key_provider")),
    ]
    failed = 0
    for desc, ok in cases:
        failed += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {desc}")
    print(f"gen-config-keys --selftest: {len(cases) - failed} passed, {failed} failed")
    return 1 if failed else 0


def render(tag: str, leaves: set[str], paths: set[str]) -> str:
    head = (
        "# gibson-config-keys.txt — GENERATED by scripts/gen-config-keys.py.\n"
        "# Do not hand-edit. Refresh with `make config-contract-sync`.\n"
        "#\n"
        "# Every config key name the gibson daemon BINDS: a mapstructure/yaml/json\n"
        "# struct tag anywhere in the repo, or a dotted path read as a literal.\n"
        "# viper drops an unbound key in silence, so a ConfigMap line absent from\n"
        "# this list configures nothing (gibson#599).\n"
        "#\n"
        f"# {len(leaves)} leaf name(s) and {len(paths)} dotted path(s).\n"
        "#\n"
        f"# Source: zeroroot-ai/gibson @ {tag}\n"
        "#\n"
        "# [leaves]\n"
    )
    body = "".join(f"{n}\n" for n in sorted(leaves))
    mid = "# [paths]\n"
    tail = "".join(f"{p}\n" for p in sorted(paths))
    return head + body + mid + tail


def parse(text: str) -> tuple[set[str], set[str]]:
    leaves, paths, cur = set(), set(), None
    for line in text.split("\n"):
        if line.startswith("# [leaves]"):
            cur = leaves
            continue
        if line.startswith("# [paths]"):
            cur = paths
            continue
        if not line or line.startswith("#") or cur is None:
            continue
        cur.add(line.strip())
    return leaves, paths


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    write = "--write" in sys.argv
    check = "--check" in sys.argv
    if not (write or check):
        print(__doc__)
        return 2
    tag = pinned_tag("gibson")
    with tree_at("gibson", tag) as tree:
        leaves, paths = extract(Path(tree))
    repo = f"gibson {tag}"
    if len(leaves) < 200:
        print(f"::error::gen-config-keys: extracted only {len(leaves)} struct tag(s) from "
              f"{repo}, which cannot be right. Refusing to write a contract over nothing.",
              file=sys.stderr)
        return 1
    text = render(tag, leaves, paths)
    if write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text)
        print(f"  wrote {OUT.relative_to(ROOT)}  ({len(leaves)} leaves, {len(paths)} paths)")
        return 0
    if not OUT.is_file():
        print(f"::error::gen-config-keys: {OUT.relative_to(ROOT)} does not exist; run "
              f"`make config-contract-sync`", file=sys.stderr)
        return 1
    have_l, have_p = parse(OUT.read_text())
    if (have_l, have_p) != (leaves, paths):
        added_l, gone_l = sorted(leaves - have_l), sorted(have_l - leaves)
        added_p, gone_p = sorted(paths - have_p), sorted(have_p - paths)
        print(f"::error::gen-config-keys: the committed contract no longer matches gibson at {tag}. "
              "Run `make config-contract-sync` and commit.", file=sys.stderr)
        for label, items in (("leaves gained", added_l), ("leaves lost", gone_l),
                             ("paths gained", added_p), ("paths lost", gone_p)):
            if items:
                print(f"  {label}: {items[:12]}{' …' if len(items) > 12 else ''}",
                      file=sys.stderr)
        return 1
    print(f"  ✓ gibson-config-keys.txt matches gibson at {tag} ({len(leaves)} leaves, {len(paths)} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
