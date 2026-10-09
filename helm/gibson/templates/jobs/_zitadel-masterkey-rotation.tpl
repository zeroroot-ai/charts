{{/*
The three steps of the CronJob zitadel-masterkey-rotation (see its template).
They pass the masterkeys through /work, a memory emptyDir. The define
gibson.masterkeyRotation.guard is the init container of each Zitadel workload. The bats test
tests/zitadel-masterkey-rotation.bats runs the rendered steps.
*/}}

{{- define "gibson.masterkeyRotation.toolContainer" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
volumeMounts:
{{- include "gibson.openbaoCAMount" . | nindent 0 }}
- name: work
  mountPath: /work
- name: tmp
  mountPath: /tmp
env:
{{- include "gibson.openbaoCAEnv" . | nindent 0 }}
- name: HOME
  value: /tmp
- name: BAO_ADDR
  value: {{ include "gibson.openbaoAddr" . | quote }}
# The token of this Job alone, policy zitadel-masterkey-rotation: read and
# update secret/data/zitadel-masterkey. The openbao-auto-init sidecar mints it.
- name: VAULT_TOKEN
  valueFrom:
    secretKeyRef:
      name: gibson-openbao-masterkey-rotation
      key: VAULT_ADMIN_TOKEN
{{- end -}}

{{- define "gibson.masterkeyRotation.read" -}}
# Step 1: write value and next of secret/zitadel-masterkey to /work.
c="$(curl -sS -o /work/kv.json -w '%{http_code}' -H "X-Vault-Token: ${VAULT_TOKEN}" \
  "${BAO_ADDR}/v1/secret/data/zitadel-masterkey")"
[ "$c" = 200 ] || { echo "[masterkey] FATAL: read secret/zitadel-masterkey returned HTTP ${c}" >&2; exit 1; }
jq -j '.data.data.value // ""' /work/kv.json > /work/value
jq -j '.data.data.next // ""' /work/kv.json > /work/next
rm -f /work/kv.json
echo none > /work/action
{{- end -}}

{{- define "gibson.masterkeyRotation.write" -}}
# Step 3: when step 2 rewrapped the rows with next, write value = next and
# next = "". ESO then updates gibson-zitadel-masterkey, and Reloader restarts
# Zitadel on the new masterkey.
action="$(cat /work/action)"
if [ "$action" != flip ]; then
  echo "[masterkey] ${action}: secret/zitadel-masterkey does not change"
  exit 0
fi
c="$(jq -nc --rawfile v /work/next '{data:{value:$v,next:""}}' \
  | curl -sS -o /work/kvw.json -w '%{http_code}' -X POST -H "X-Vault-Token: ${VAULT_TOKEN}" \
      -H 'Content-Type: application/json' --data-binary @- "${BAO_ADDR}/v1/secret/data/zitadel-masterkey")"
rm -f /work/kvw.json
[ "$c" = 200 ] || [ "$c" = 204 ] || { echo "[masterkey] FATAL: write secret/zitadel-masterkey returned HTTP ${c}; the next run moves it" >&2; exit 1; }
echo "[masterkey] moved the next masterkey to value"
{{- end -}}

{{- define "gibson.masterkeyRotation.crypto" -}}
# The Zitadel AESString of internal/crypto/aes.go, and the marker text. The
# rewrap step and the Zitadel masterkey guard use this one copy.
MARKER_TEXT="gibson-zitadel-masterkey-marker-v1"
hex_of() { od -An -v -tx1 | tr -d ' \n'; }
bin_of() { printf '%b' "$(printf '%s' "$1" | sed 's/../\\x&/g')"; }
# mk_decrypt_hex STRING KEY: the plaintext of a Zitadel AESString, as hex.
mk_decrypt_hex() {
  local raw kh
  raw="$(printf '%s' "$1" | tr '_-' '/+' | base64 -d | hex_of)"
  [ "${#raw}" -ge 32 ] || return 1
  kh="$(printf '%s' "$2" | hex_of)"
  bin_of "${raw:32}" | openssl enc -d -aes-256-cfb -K "$kh" -iv "${raw:0:32}" -nopad | hex_of
}
# mk_encrypt_hex HEX KEY: a Zitadel AESString of the plaintext HEX.
mk_encrypt_hex() {
  local iv kh ct
  iv="$(openssl rand -hex 16)"
  kh="$(printf '%s' "$2" | hex_of)"
  ct="$(bin_of "$1" | openssl enc -aes-256-cfb -K "$kh" -iv "$iv" -nopad | hex_of)"
  bin_of "${iv}${ct}" | base64 -w0 | tr '+/' '-_'
}
marker_hex="$(printf '%s' "$MARKER_TEXT" | hex_of)"
{{- end -}}

{{- define "gibson.masterkeyRotation.rewrap" -}}
# Step 2: prove the marker, and rewrap the Zitadel keys when a rotation is
# pending. It writes the action of step 3 to /work/action.
set -euo pipefail
export PATH="/opt/bitnami/postgresql/bin:$PATH"
PSQL=(psql -X -q -t -A -v ON_ERROR_STOP=1)

{{ include "gibson.masterkeyRotation.crypto" . }}

value="$(cat /work/value)"
next="$(cat /work/next)"
[ "${#value}" -ge 32 ] || { echo "[masterkey] FATAL: the masterkey is shorter than 32 characters" >&2; exit 1; }
cur="${value:0:32}"

"${PSQL[@]}" -c "CREATE SCHEMA IF NOT EXISTS gibson_rotation;
  CREATE TABLE IF NOT EXISTS gibson_rotation.masterkey_marker (id int PRIMARY KEY, marker text NOT NULL)"
marker="$("${PSQL[@]}" -c "SELECT marker FROM gibson_rotation.masterkey_marker WHERE id = 1")"

# 1. No marker yet.
if [ -z "$marker" ]; then
  m="$(mk_encrypt_hex "$marker_hex" "$cur")"
  "${PSQL[@]}" -c "INSERT INTO gibson_rotation.masterkey_marker (id, marker) VALUES (1, '${m}')"
  echo "[masterkey] wrote the marker with the current masterkey"
  exit 0
fi

# 2. No rotation pending.
if [ -z "$next" ]; then
  [ "$(mk_decrypt_hex "$marker" "$cur")" = "$marker_hex" ] \
    || { echo "[masterkey] FATAL: the marker does not decrypt with the masterkey of the store; the Zitadel keys and secret/${MK_KEY} disagree" >&2; exit 1; }
  echo "[masterkey] no rotation pending; the marker matches the current masterkey"
  exit 0
fi
[ "${#next}" -ge 32 ] || { echo "[masterkey] FATAL: the next masterkey is shorter than 32 characters; nothing changes" >&2; exit 1; }
nxt="${next:0:32}"

# 4. An earlier run rewrapped the rows and did not move next to value.
if [ "$(mk_decrypt_hex "$marker" "$nxt")" = "$marker_hex" ]; then
  echo flip > /work/action
  echo "[masterkey] the rows already use the next masterkey; step 3 moves it to value"
  exit 0
fi

# 5. The marker proves neither key.
[ "$(mk_decrypt_hex "$marker" "$cur")" = "$marker_hex" ] \
  || { echo "[masterkey] FATAL: the marker decrypts with neither the current nor the next masterkey; nothing changes" >&2; exit 1; }

# 3. Rewrap every row and the marker with next, in one transaction.
sql=/work/rewrap.sql
: > "$sql"
# One snapshot for the rows and their digest.
"${PSQL[@]}" -F '|' -c "BEGIN ISOLATION LEVEL REPEATABLE READ" \
  -c "SELECT md5(coalesce(string_agg(id || ':' || key, ',' ORDER BY id), '')) FROM system.encryption_keys" \
  -c "SELECT id, key FROM system.encryption_keys ORDER BY id" -c "COMMIT" > /work/rows
rows_md5="$(head -n1 /work/rows)"
sed -i 1d /work/rows
echo "BEGIN;" >> "$sql"
echo "LOCK TABLE system.encryption_keys IN EXCLUSIVE MODE;" >> "$sql"
n=0
while IFS='|' read -r id key; do
  [ -n "$id" ] || continue
  case "$id" in *[!A-Za-z0-9_-]*) echo "[masterkey] FATAL: an encryption key id is not plain" >&2; exit 1 ;; esac
  plain="$(mk_decrypt_hex "$key" "$cur")" || { echo "[masterkey] FATAL: key ${id} does not decrypt" >&2; exit 1; }
  new="$(mk_encrypt_hex "$plain" "$nxt")"
  [ "$(mk_decrypt_hex "$new" "$nxt")" = "$plain" ] || { echo "[masterkey] FATAL: the rewrap of key ${id} does not round-trip" >&2; exit 1; }
  printf "UPDATE system.encryption_keys SET key = '%s' WHERE id = '%s';\n" "$new" "$id" >> "$sql"
  n=$((n + 1))
done < /work/rows
rm -f /work/rows
# Zitadel can insert a key between the read and the COMMIT. The transaction
# locks the table and refuses to commit when its rows are not the rows read.
printf "DO \$\$ BEGIN IF (SELECT md5(coalesce(string_agg(id || ':' || key, ',' ORDER BY id), '')) FROM system.encryption_keys) <> '%s' THEN RAISE EXCEPTION 'system.encryption_keys changed after the read'; END IF; END \$\$;\n" "$rows_md5" >> "$sql"
printf "UPDATE gibson_rotation.masterkey_marker SET marker = '%s' WHERE id = 1;\nCOMMIT;\n" \
  "$(mk_encrypt_hex "$marker_hex" "$nxt")" >> "$sql"
"${PSQL[@]}" -f "$sql"
rm -f "$sql"
echo flip > /work/action
echo "[masterkey] rewrapped ${n} Zitadel encryption keys with the next masterkey; step 3 moves it to value"
{{- end -}}


{{- define "gibson.masterkeyRotation.guard" -}}
# The Zitadel masterkey guard: an init container of each Zitadel workload. It
# refuses to start Zitadel on a masterkey that does not open the marker, so
# no Zitadel process decrypts its keys to garbage after a rewrap (AES-CFB has
# no integrity check). A missing marker passes: the rotation CronJob writes it
# with the masterkey that Zitadel runs with.
set -euo pipefail
export PATH="/opt/bitnami/postgresql/bin:$PATH"
PSQL=(psql -X -q -t -A -v ON_ERROR_STOP=1)
{{ include "gibson.masterkeyRotation.crypto" . }}
mk="${ZITADEL_MASTERKEY:-}"
[ "${#mk}" -ge 32 ] || { echo "[masterkey-guard] FATAL: the masterkey is shorter than 32 characters" >&2; exit 1; }
has=""
for _ in $(seq 1 60); do
  has="$("${PSQL[@]}" -c "SELECT to_regclass('gibson_rotation.masterkey_marker') IS NOT NULL" 2>/dev/null)" && break
  has=""; sleep 2
done
[ -n "$has" ] || { echo "[masterkey-guard] FATAL: cannot read the zitadel database" >&2; exit 1; }
if [ "$has" != t ]; then echo "[masterkey-guard] no marker yet; the rotation CronJob writes it"; exit 0; fi
marker="$("${PSQL[@]}" -c "SELECT marker FROM gibson_rotation.masterkey_marker WHERE id = 1")"
if [ -z "$marker" ]; then echo "[masterkey-guard] no marker yet; the rotation CronJob writes it"; exit 0; fi
[ "$(mk_decrypt_hex "$marker" "${mk:0:32}")" = "$marker_hex" ] \
  || { echo "[masterkey-guard] FATAL: the masterkey of this pod does not open the marker; the Zitadel keys use another masterkey. Zitadel waits for the new Secret." >&2; exit 1; }
echo "[masterkey-guard] the masterkey opens the marker"
{{- end -}}
