#!/usr/bin/env python3
"""check-mirror-digests.py — every mirror image the chart runs is pinned by digest.

First-party images carried @sha256 digests; the utility images from
ghcr.io/zeroroot-ai/mirror did not. Those run as init containers and
bootstrap Jobs with pods/exec and Secret access. A mutable tag in the mirror
changes what runs on the next pod restart, and the org mirror re-copies
floating tags on purpose. This guard renders the umbrella for every profile
and fails any container, init container or inline image field that names a
mirror image without a digest, whatever file the reference came from
(values, a profile, a template literal or a helper). values-kind.yaml tracks
moving tags for the dev loop and is not a rendered profile here.

  check-mirror-digests.py             exit 1 on an unpinned mirror image, 0 when clean
  check-mirror-digests.py --selftest  prove a bare tag fails
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES = [
    ["-f", "helm/gibson/values-baseline.yaml"],
    ["-f", "helm/gibson/values-baseline.yaml", "-f", "helm/gibson/values-eks.yaml"],
    ["-f", "helm/gibson/values-baseline.yaml", "-f", "helm/gibson/values-guest.yaml"],
]
# A mirror reference as it appears in a rendered image field: repository,
# tag, and everything after the tag up to the end of the value.
MIRROR = re.compile(r'(?:ghcr\.io/)?zeroroot-ai/mirror/[a-z0-9-]+:[^"\s]+')
# The one shape a pinned reference may take. A digest appended twice
# (charts#140 rendered kubectl:1.31.4@sha256:…@sha256:… and every pod with
# that init container sat in Init:InvalidImageName) fails here, as does any
# other suffix the kubelet would refuse.
PINNED = re.compile(r'^[a-z0-9.\-/]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$')


def render(profile: list[str]) -> str:
    return subprocess.run(
        ["helm", "template", "gibson", "helm/gibson", *profile,
         "-f", "helm/testdata/render-inputs/gibson.yaml", "--namespace", "gibson",
         "--api-versions", "monitoring.coreos.com/v1"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout


def judge(text: str, name: str) -> list[str]:
    out = []
    for i, ln in enumerate(text.split("\n"), 1):
        if ln.lstrip().startswith("#"):
            continue
        for m in MIRROR.finditer(ln):
            ref = m.group(0)
            if "@sha256:" not in ref:
                out.append(f"{name}:{i}: {ref} carries no digest")
            elif not PINNED.match(ref):
                out.append(f"{name}:{i}: {ref} is not a valid pinned reference (one tag, one digest)")
    return sorted(set(out))


def selftest() -> int:
    bad = 'image: ghcr.io/zeroroot-ai/mirror/curl:8.10.1\nimage: "zeroroot-ai/mirror/kubectl:1.31.4"\n'
    got = judge(bad, "fixture")
    if len(got) != 2:
        print(f"SELFTEST FAIL: two bare refs must fail, got {got}")
        return 1
    good = 'image: "ghcr.io/zeroroot-ai/mirror/kubectl:1.31.4@sha256:' + "b" * 64 + '"\n# image: zeroroot-ai/mirror/commented:1\n'
    if judge(good, "fixture"):
        print(f"SELFTEST FAIL: a pinned ref and a comment must pass, got {judge(good, 'fixture')}")
        return 1
    # THE FIXTURE charts#140 NEEDED: a digest appended twice is not a reference.
    doubled = 'image: ghcr.io/zeroroot-ai/mirror/kubectl:1.31.4@sha256:' + "b" * 64 + '@sha256:' + "b" * 64 + '\n'
    got = judge(doubled, "fixture")
    if len(got) != 1 or "not a valid pinned reference" not in got[0]:
        print(f"SELFTEST FAIL: a doubled digest must fail, got {got}")
        return 1
    live = judge(render(PROFILES[0]), "baseline")
    if live:
        print("SELFTEST FAIL: the baseline render must pass:\n  " + "\n  ".join(live))
        return 1
    print("OK: bare refs and a doubled digest fail, a pinned ref passes, the baseline render is pinned")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = []
    for p in PROFILES:
        got += judge(render(p), " ".join(p[1::2]))
    if got:
        print("❌ a mirror image runs by tag only (resolve its digest from the org package and pin tag@sha256):\n  " + "\n  ".join(got))
        return 1
    print("✓ mirror-digests: every mirror image in every rendered profile carries its digest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
