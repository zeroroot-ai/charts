#!/usr/bin/env python3
"""Print why one Argo Application is not Synced+Healthy (deploy#1737).

Reads `kubectl -n argocd get app <name> -o json` on stdin. The app list alone
says an app is unhappy and never WHICH object made it so, which is the one
thing needed to fix it.
"""

import json
import sys


def main() -> int:
    d = json.load(sys.stdin)
    st = d.get("status", {})
    op = st.get("operationState", {})
    print("  operation:", op.get("phase"), "|", (op.get("message") or "")[:300])
    print("  retries:", op.get("retryCount"))
    for c in st.get("conditions") or []:
        print("  condition:", c.get("type"), (c.get("message") or "")[:200])
    bad = [
        r for r in (st.get("resources") or [])
        if r.get("status") not in (None, "Synced")
        or (r.get("health") or {}).get("status") not in (None, "Healthy")
    ]
    print(f"  {len(bad)} resource(s) not Synced+Healthy:")
    for r in bad[:40]:
        h = r.get("health") or {}
        print(
            f"    {r.get('kind')}/{r.get('name')}"
            f" sync={r.get('status')}"
            f" health={h.get('status')}"
            f" {(h.get('message') or '')[:160]}"
        )
    # A resource that is OutOfSync but has no health problem is almost always
    # a custom resource a controller mutates after Argo applies it. Naming it
    # is not enough to fix it — the FIELD OWNERS say which manager wrote what.
    # Emit a machine-readable line per drifting object so the caller can dump
    # them while the cluster still exists; after the run it is gone.
    for r in bad:
        if r.get("status") == "OutOfSync":
            print(f"DRIFT\t{r.get('kind')}\t{r.get('name')}\t{r.get('namespace') or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
