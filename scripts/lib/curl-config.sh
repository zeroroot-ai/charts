# curl-config.sh: write one line of a curl config, for `curl -K -`.
#
# A secret on the argv of curl, or of `kubectl exec ... -- curl`, is in the
# process table of the host and in the audit log of the API server
# (GHSA-5c5h-rrh4-5xw3). curl reads a config on stdin with `-K -`, and a
# config line can hold a header or a body. printf is a shell builtin, and
# sed reads the value on stdin, so the value is on no argv.
#
#   { curl_cfg header "Authorization: Bearer ${tok}"; curl_cfg data "$body"; } \
#     | curl -sS -K - "$URL"
#
# curl_cfg <option> <value>: print `option = "value"` with \ and " escaped.
curl_cfg() {
  printf '%s = "%s"\n' "$1" "$(printf '%s' "$2" | sed 's/\\/\\\\/g; s/"/\\"/g')"
}
