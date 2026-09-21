#!/usr/bin/env bash
# `make chart-deps` retries a failed sub-chart download (hosted#147).
#
# The retry lived in scripts/helm-dep-update.sh, and the Makefile called it
# with no chart argument, so the script failed its usage check under
# 2>/dev/null and the fallback ran `helm dependency update` once per chart
# with no retry at all. A 504 from github.com then failed the job. This
# check runs the real target against a fake helm on PATH and proves both
# halves: a download that fails twice and then succeeds passes the target,
# and one that never succeeds fails it after every attempt.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/bin"
cat > "$tmp/bin/helm" <<'FAKE'
#!/usr/bin/env bash
# Fake helm: count every call, fail the first FAIL_FIRST calls, or every
# call when FAIL_ALWAYS=1.
count_file="${FAKE_HELM_COUNT:?}"
n=$(( $(cat "$count_file" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$count_file"
if [ "${FAIL_ALWAYS:-0}" = "1" ] || [ "$n" -le "${FAIL_FIRST:-0}" ]; then
  echo "Error: could not download https://github.com/example/releases/download/x.tgz: 504 Gateway Timeout" >&2
  exit 1
fi
exit 0
FAKE
chmod +x "$tmp/bin/helm"

# The charts the target loops over, read from the target itself.
charts=$(grep -A3 '^chart-deps:' "$root/Makefile" | grep -oE 'helm/[a-z-]+' | sort -u | wc -l)
[ "$charts" -gt 0 ] || { echo "FAIL: no chart dirs found under the chart-deps target"; exit 1; }

run() {
  local count="$tmp/count-$1"; : > "$count"
  ( cd "$root" && PATH="$tmp/bin:$PATH" FAKE_HELM_COUNT="$count" HELM_DEP_UPDATE_BACKOFF=0 "$@" make chart-deps >"$tmp/out-$1" 2>&1 ) && rc=0 || rc=$?
  echo "$rc $(cat "$count")"
}

fail=0
# 1. two 504s, then success: the target passes and helm was asked again.
read -r rc calls < <(run env FAIL_FIRST=2)
if [ "$rc" -ne 0 ]; then
  echo "FAIL: chart-deps failed although the download succeeded on the third attempt:"; cat "$tmp/out-env"; fail=1
elif [ "$calls" -ne $((charts + 2)) ]; then
  echo "FAIL: expected helm to be called $((charts + 2)) times (charts + 2 retries), saw $calls"; fail=1
else
  echo "  ✓ a download that fails twice passes chart-deps after retries"
fi

# 2. every attempt fails: the target fails, after all attempts on the first chart.
read -r rc calls < <(run env FAIL_ALWAYS=1)
if [ "$rc" -eq 0 ]; then
  echo "FAIL: chart-deps passed although every download failed"; fail=1
elif [ "$calls" -ne 5 ]; then
  echo "FAIL: expected 5 attempts on the first chart before giving up, saw $calls"; fail=1
elif ! grep -q "504 Gateway Timeout" "$tmp/out-env"; then
  echo "FAIL: the failure must print helm's last output so the reason is readable:"; cat "$tmp/out-env"; fail=1
else
  echo "  ✓ a download that never succeeds fails chart-deps after 5 attempts and names the reason"
fi

exit $fail
