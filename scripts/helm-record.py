#!/usr/bin/env python3
"""helm-record.py: the size of the Helm 3 release record of a chart (charts#815).

Helm stores a release as ONE Secret, sh.helm.release.v1.<name>.vN. The value
of its `release` key is base64(gzip(json(release))). The JSON holds:

  - the rendered manifest,
  - the templates and the files of the TOP-LEVEL chart, each one base64
    inside the JSON (a dependency's own files are not stored),
  - the values and the metadata of that chart, and the user values.

This script builds that document and prints the byte count of the encoded
value, the number the API server compares with its 1 MiB cap. It is the
measurement of scripts/check-helm-record-size.sh.

  helm-record.py <chart-dir> [values-file...]    print the byte count
Exit: 0 ok · 1 the render failed or printed nothing
"""
from __future__ import annotations

import base64
import gzip
import json
import pathlib
import subprocess
import sys

import yaml

# The keys of the top-level chart that Helm stores outside templates and files.
NOT_FILES = {"Chart.yaml", "values.yaml", "Chart.lock", "values.schema.json"}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 1
    chart = pathlib.Path(argv[0])
    values = argv[1:]
    args = ["helm", "template", "release", str(chart), "--namespace", "gibson"]
    for v in values:
        args += ["-f", v]
    # --namespace gibson: the umbrella names the release namespace in the
    # setec namespace policy and refuses another.
    run = subprocess.run(args, capture_output=True, text=True)
    if run.returncode != 0 or not run.stdout.strip():
        sys.stderr.write(run.stderr[-2000:])
        return 1

    def b64(p: pathlib.Path) -> str:
        return base64.b64encode(p.read_bytes()).decode()

    templates, files = [], []
    for p in sorted(chart.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(chart).as_posix()
        if rel.startswith("charts/") or rel.startswith(".") or rel in NOT_FILES:
            continue
        (templates if rel.startswith("templates/") else files).append({"name": rel, "data": b64(p)})
    config: dict = {}
    for v in values:
        config.update(yaml.safe_load(pathlib.Path(v).read_text()) or {})
    release = {
        "name": "release",
        "info": {"status": "deployed", "description": "Install complete"},
        "chart": {
            "metadata": yaml.safe_load((chart / "Chart.yaml").read_text()),
            "templates": templates,
            "values": yaml.safe_load((chart / "values.yaml").read_text()) or {},
            "files": files,
        },
        "config": config,
        "manifest": run.stdout,
        "version": 1,
        "namespace": "gibson",
    }
    raw = json.dumps(release, separators=(",", ":")).encode()
    print(len(base64.b64encode(gzip.compress(raw, compresslevel=9))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
