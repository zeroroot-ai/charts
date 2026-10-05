#!/usr/bin/env python3
"""check-no-render-time-secrets.py: no first-party template makes secret material at render time.

ADR-0014: "No template calls randAlphaNum, genCA, genPrivateKey, or lookup."
and "The chart renders no Secret object and makes no secret material at
render time." A random value in a template changes on each render, so each
upgrade rotates it and Argo sees a permanent diff. `lookup` returns nil in
`helm template`, so the golden files cannot show a call: the render stays the
same and the snapshot test passes.

So this guard reads the template SOURCE, not the render. In each file under
helm/<chart>/templates of a first-party chart it fails on:

  1. a call of `lookup`, or of a Sprig function that makes a random value, a
     key or a certificate (BANNED), inside a template action;
  2. a document with `kind: Secret` and a `data:` or `stringData:` key.

Template comments (`{{/* ... */}}`) and YAML comments are not code, and the
guard does not read them. Vendored charts (helm/*/charts/) are not first-party.

  check-no-render-time-secrets.py             exit 1 on a finding, 0 when clean
  check-no-render-time-secrets.py --selftest  prove a fixture for each function fails
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The four functions of ADR-0014, and the other Sprig functions of the same two families.
BANNED = (
    "lookup",
    "randAlphaNum", "randAlpha", "randNumeric", "randAscii", "randBytes", "derivePassword", "htpasswd",
    "genCA", "genCAWithKey", "genPrivateKey", "genSelfSignedCert", "genSelfSignedCertWithKey",
    "genSignedCert", "genSignedCertWithKey",
)
TEMPLATE_COMMENT = re.compile(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", re.S)
ACTION = re.compile(r"\{\{.*?\}\}", re.S)
STRING = re.compile(r'"(?:[^"\\]|\\.)*"|`[^`]*`')
CALL = re.compile(r"(?<![\w.$])(" + "|".join(BANNED) + r")(?![\w])")
YAML_COMMENT = re.compile(r"^\s*#.*$", re.M)
SECRET_KIND = re.compile(r"^kind:\s*[\"']?Secret[\"']?\s*$", re.M)
SECRET_DATA = re.compile(r"^(data|stringData):", re.M)


def template_files(root: str = ROOT) -> list[str]:
    out = []
    helm = os.path.join(root, "helm")
    for chart in sorted(os.listdir(helm)):
        base = os.path.join(helm, chart, "templates")
        for dirpath, _, names in os.walk(base):
            out += [os.path.join(dirpath, n) for n in sorted(names) if n.endswith((".yaml", ".yml", ".tpl", ".txt"))]
    return out


def findings(text: str) -> list[str]:
    bad = []
    code = TEMPLATE_COMMENT.sub("", text)
    for action in ACTION.findall(code):
        for name in CALL.findall(STRING.sub('""', action)):
            bad.append(f"calls {name}")
    # A document is the text between two `---` lines. Template actions and comments are not YAML keys.
    plain = YAML_COMMENT.sub("", ACTION.sub("", code))
    for doc in re.split(r"^---\s*$", plain, flags=re.M):
        if SECRET_KIND.search(doc) and SECRET_DATA.search(doc):
            bad.append("renders a Secret with data")
    return sorted(set(bad))


def audit(files: list[str], root: str = ROOT) -> list[str]:
    out = []
    for f in files:
        for item in findings(open(f, encoding="utf-8").read()):
            out.append(f"{os.path.relpath(f, root)}: {item}")
    return out


CLEAN = """{{- /* a comment that names lookup and randAlphaNum is not a call */ -}}
# lookup in a YAML comment is not a call
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "x.fullname" . }}
  annotations:
    note: {{ "the word genCA in a string is not a call" | quote }}
data:
  lookup: {{ .Values.lookup | quote }}
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata: {name: x}
spec:
  data: []
---
apiVersion: v1
kind: Secret
metadata: {name: empty-shell}
type: Opaque
"""

SECRET = """apiVersion: v1
kind: Secret
metadata: {name: x}
%s:
  password: {{ .Values.password | b64enc }}
"""


def selftest() -> int:
    cases = {"clean.yaml": (CLEAN, 0)}
    for name in BANNED:
        arg = '"v1" "Secret" .Release.Namespace "x"' if name == "lookup" else "32"
        cases[f"{name}.yaml"] = ("data:\n  v: {{ %s %s | quote }}\n" % (name, arg), 1)
    cases["piped.yaml"] = ("data:\n  v: {{ 32 | randAlphaNum | b64enc }}\n", 1)
    cases["multiline.tpl"] = ('{{- define "x" -}}\n{{- $ca := (\n  genCA "x" 365\n) -}}\n{{- end -}}\n', 1)
    cases["secret-data.yaml"] = (SECRET % "data", 1)
    cases["secret-stringdata.yaml"] = (SECRET % "stringData", 1)
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "helm", "fixture", "templates")
        os.makedirs(base)
        for name, (text, _) in cases.items():
            open(os.path.join(base, name), "w").write(text)
        if len(template_files(tmp)) != len(cases):
            print("SELFTEST FAIL: the file walk did not find each fixture")
            return 1
        for name, (_, want) in cases.items():
            got = audit([os.path.join(base, name)], tmp)
            if len(got) != want:
                print(f"SELFTEST FAIL: {name}: want {want} finding(s), got {got}")
                return 1
    print(f"  ✓ selftest: a call of each of {len(BANNED)} functions fails, a piped call and a multi-line call fail, "
          "a Secret with data or stringData fails, the clean file passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    files = template_files()
    if len(files) < 50:
        print(f"read {len(files)} template file(s): the walk found too few, so this guard is blind", file=sys.stderr)
        return 1
    bad = audit(files)
    if bad:
        print("a first-party template makes secret material at render time (ADR-0014):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ no-render-time-secrets: {len(files)} template files call no lookup or random function and render no Secret with data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
