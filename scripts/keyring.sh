#!/usr/bin/env bash
# keyring.sh — the bringup keyring: generate, fingerprint, verify (ADR-0083).
#
# The bringup keyring is the small set of secrets without which nothing in
# the durable bucket is readable. It outlives every cluster and never lives
# in a cluster, in the bucket, or in OpenBao. This file is the ONE definition
# of its shape. Stage 0 of every environment (kind/substrate/substrate.sh,
# eks/gibson-bootstrap/substrate.sh) generates it with `generate` and records
# its fingerprints in substrate.env with `fingerprints`. preflight proves it
# with `verify`.
#
# File format (mode 0600, one member per line, no quotes, no `export`):
#
#   OPENBAO_SEAL_KEY=<base64 of 32 random bytes, 44 chars>
#   OPENBAO_SEAL_KEY_PREVIOUS=<the seal key before the last rotation, or empty>
#   VELERO_REPO_PASSWORD=<base64 of 32 random bytes, 44 chars>
#   VELERO_REPO_PASSWORD_PREVIOUS=<the repository password before the last rotation, or empty>
#   SETEC_DISK_SIGNING_SEED=<base64 of 32 random bytes, 44 chars>
#   BUCKET_ACCESS_KEY=<20 chars, A-Z0-9>
#   BUCKET_SECRET_KEY=<base64 of 30 random bytes, 40 chars>
#   SMTP_PASSWORD=<at least 20 chars, no whitespace>
#   GHCR_PULL_TOKEN=<a GitHub token with read:packages, or empty>
#   DNS_ACCESS_KEY=<the access key id of the DNS credential, or empty>
#   DNS_SECRET_KEY=<its secret access key, or empty>
#
# The first three are always generated here. The next three are the shape
# of an AWS access key pair and of a relay password: on kind `generate`
# makes them up and stage 0 configures MinIO and Mailpit with them; on AWS
# stage 0 gets them from Terraform and passes them to `generate` in a
# provided file, so the keyring holds the credential the cloud issued. The
# SMTP password is opaque because the relay issues it (an SES SMTP password
# is 44 characters, Mailpit takes anything), so only a floor is checked.
#
# The last three are INPUT members: values the keyring cannot generate.
# GHCR_PULL_TOKEN (deploy#1732) is the one seed value the platform consumes.
# The bringup writes it into the keyring Secret as `ghcr-pull-token`, and
# the openbao-auto-init sidecar copies it into OpenBao (`ghcr-pull-secret`,
# property `pat`). It is the ONE exception to "never in OpenBao": an input
# the platform consumes, not a key that opens the bucket. Empty is allowed
# and means "not supplied": the sidecar seeds the key empty, and an operator
# fills it later with scripts/baseline-set-secret.sh. An input member may be
# absent from an older keyring file; that reads as empty. There is no LLM
# key member: the platform holds no LLM credential of its own. A tenant's
# providers and their keys live in that tenant's provider configuration.
# DNS_ACCESS_KEY and DNS_SECRET_KEY are the Route53 credential cert-manager
# (DNS-01) and external-dns use: an access key pair scoped to the
# environment's zone. On AWS stage 0 mints it and `set`s it; on kind there
# is no zone and both stay empty. The bringup writes them into the Secret
# `route53-credential` (keys access-key-id, secret-access-key) when set,
# never into OpenBao.
# `set` writes an input member into an existing keyring; the other members
# never change after `generate`.
#
# Comment lines start with `#`. The file is never sourced: a secret file
# that can run shell is a hazard, so it is read line by line.
#
# Fingerprint lines, as `fingerprints` prints them and substrate.env holds
# them:
#
#   KEYRING_FINGERPRINT_<MEMBER>=sha256:<hex of sha256(value)>
#   KEYRING_FINGERPRINT=sha256:<hex of sha256 over the member lines above>
#
# Commands:
#   keyring.sh generate <keyring-file> [<provided>]    write a new keyring, refuse to overwrite.
#                                                      <provided> holds MEMBER=value lines that
#                                                      stage 0 got from the substrate; the rest
#                                                      is generated
#   keyring.sh fingerprints <keyring-file>             print the fingerprint lines
#   keyring.sh verify <keyring-file> <substrate.env>   presence, length, fingerprint, one line per miss
#   keyring.sh get <keyring-file> <MEMBER>             print one member's value
#   keyring.sh set <keyring-file> <MEMBER> <value>     write an INPUT member (GHCR_PULL_TOKEN,
#                                                      DNS_ACCESS_KEY, DNS_SECRET_KEY) into an
#                                                      existing keyring
#   keyring.sh rotate <keyring-file> <MEMBER>          keep the value as <MEMBER>_PREVIOUS and
#                                                      generate a new one. Refuses while a
#                                                      previous value is kept
#   keyring.sh retire <keyring-file> <MEMBER>          clear <MEMBER>_PREVIOUS, after the
#                                                      cluster runs on the new value
#
# ROTATION (ADR-0171, row keyring-generated-keys). Only a member with a
# <MEMBER>_PREVIOUS member rotates in place: OPENBAO_SEAL_KEY and
# VELERO_REPO_PASSWORD. It runs at stage 0 on the operator workstation, never
# in a workflow (ADR-0083). The seal key:
#   1. keyring.sh rotate <file> OPENBAO_SEAL_KEY
#   2. keyring-to-cluster.sh, then restart OpenBao. The static seal unwraps
#      with either key and wraps with the new one.
#   3. After OpenBao runs unsealed on the new key, keyring.sh retire <file>
#      OPENBAO_SEAL_KEY, keyring-to-cluster.sh, and restart OpenBao again.
#   4. make substrate, so substrate.env records the new fingerprints.
# rotate refuses while a previous value is kept: a second rotation before
# step 3 would drop the key that still wraps the store, and the store would
# stay sealed.
# The Velero repository password: kopia cannot change the password of a
# repository, so a new password starts a new repository at a new path.
#   1. keyring.sh rotate <file> VELERO_REPO_PASSWORD
#   2. Set the chart value bucket.prefix of gibson-velero to a new path under
#      backups/velero/ (the one prefix where the platform credential may
#      delete, for kopia) in the values of the environment, and merge it.
#   3. keyring-to-cluster.sh. The next scheduled backup starts the new
#      repository with the new password.
#   4. The previous password reads the old repository, for a restore from a
#      backup older than the rotation, until the bucket lifecycle expires the
#      old path (180 days). Then keyring.sh retire <file> VELERO_REPO_PASSWORD.
#
# Exit codes: 0 ok, 1 the keyring fails a check, 2 the command could not run.

set -euo pipefail

# name:kind:size — kind b64 means base64 of exactly <size> bytes, kind alnum
# means exactly <size> characters from A-Z0-9, kind opaque means at least
# <size> characters with no whitespace (generated as base64 of 30 bytes),
# kind input means any value without whitespace, empty and absent allowed,
# never generated (size is unused and 0). Kind prev means empty, or base64 of
# exactly <size> bytes: the value of <MEMBER> before its last rotation. Empty
# and absent are allowed, and generate writes it empty.
# Order is the file order and the fingerprint order.
MEMBERS=(
  OPENBAO_SEAL_KEY:b64:32
  OPENBAO_SEAL_KEY_PREVIOUS:prev:32
  VELERO_REPO_PASSWORD:b64:32
  VELERO_REPO_PASSWORD_PREVIOUS:prev:32
  SETEC_DISK_SIGNING_SEED:b64:32
  BUCKET_ACCESS_KEY:alnum:20
  BUCKET_SECRET_KEY:b64:30
  SMTP_PASSWORD:opaque:20
  GHCR_PULL_TOKEN:input:0
  DNS_ACCESS_KEY:input:0
  DNS_SECRET_KEY:input:0
)

die() { printf 'keyring: %s\n' "$*" >&2; exit 2; }

# envfile_get FILE KEY — print the value of the first `KEY=value` line.
# Prints nothing and returns 1 when the key is absent.
envfile_get() {
  local line
  line="$(grep -E "^$2=" "$1" | head -n1)" || return 1
  printf '%s' "${line#*=}"
}

# b64_len SIZE — the base64 length of SIZE bytes.
b64_len() { echo $(( (($1 + 2) / 3) * 4 )); }

member_name() { echo "${1%%:*}"; }
member_kind() { local r="${1#*:}"; echo "${r%%:*}"; }
member_size() { echo "${1##*:}"; }

# member_spec NAME — the spec line of one member, or nothing.
member_spec() {
  local spec
  for spec in "${MEMBERS[@]}"; do
    [ "$(member_name "$spec")" = "$1" ] && { echo "$spec"; return 0; }
  done
  return 1
}

# is_input NAME — true for an input member.
is_input() {
  local spec
  spec="$(member_spec "$1")" || return 1
  [ "$(member_kind "$spec")" = input ]
}

# is_optional NAME — true for a member that may be empty or absent: an input
# member, or the previous value of a rotation.
is_optional() {
  local spec
  spec="$(member_spec "$1")" || return 1
  case "$(member_kind "$spec")" in input|prev) return 0 ;; esac
  return 1
}

# member_expected_len SPEC — the character length a member must have: exact
# for b64 and alnum, a floor for opaque.
member_expected_len() {
  case "$(member_kind "$1")" in
    b64)    b64_len "$(member_size "$1")" ;;
    alnum)  member_size "$1" ;;
    opaque) member_size "$1" ;;
    input)  echo 0 ;;
    prev)   b64_len "$(member_size "$1")" ;;
  esac
}

# shape_error SPEC VALUE — print why VALUE does not fit SPEC, or nothing.
shape_error() {
  local spec="$1" value="$2" want have
  want="$(member_expected_len "$spec")"
  have="${#value}"
  if [ "$(member_kind "$spec")" = input ]; then
    if [[ "$value" =~ [[:space:]] ]]; then
      printf 'contains whitespace'; return
    fi
    return
  fi
  if [ "$(member_kind "$spec")" = prev ]; then
    [ -z "$value" ] && return
    spec="$(member_name "$spec"):b64:$(member_size "$spec")"
  fi
  if [ "$(member_kind "$spec")" = opaque ]; then
    if [ "$have" -lt "$want" ]; then
      printf 'has length %s, want at least %s' "$have" "$want"; return
    fi
    if [[ "$value" =~ [[:space:]] ]]; then
      printf 'contains whitespace'
    fi
    return
  fi
  if [ "$have" -ne "$want" ]; then
    printf 'has length %s, want %s' "$have" "$want"; return
  fi
  case "$(member_kind "$spec")" in
    b64)
      local bytes
      bytes="$(printf '%s' "$value" | base64 -d 2>/dev/null | wc -c)" || bytes=0
      if [ "$bytes" -ne "$(member_size "$spec")" ]; then
        printf 'is not base64 of %s bytes' "$(member_size "$spec")"
      fi ;;
    alnum)
      if ! [[ "$value" =~ ^[A-Z0-9]+$ ]]; then
        printf 'is not %s characters of A-Z0-9' "$want"
      fi ;;
  esac
}

sha256_of() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }

gen_value() {
  case "$(member_kind "$1")" in
    input|prev) printf '' ;;
    b64)    head -c "$(member_size "$1")" /dev/urandom | base64 -w0 ;;
    # base32 (A-Z2-7) of enough random bytes, cut to size. No pipe from an
    # endless reader, so no SIGPIPE under pipefail.
    alnum)  head -c "$(member_size "$1")" /dev/urandom | base32 | tr -d '=\n' | cut -c1-"$(member_size "$1")" ;;
    opaque) head -c 30 /dev/urandom | base64 -w0 ;;
  esac
}

# cmd_generate FILE [PROVIDED] — write a new keyring. A member named in
# PROVIDED (MEMBER=value lines) takes that value and must fit its shape; every
# other member is generated. PROVIDED may name only members of the shape.
cmd_generate() {
  local file="${1:?usage: keyring.sh generate <keyring-file> [<provided>]}"
  local provided="${2:-}"
  [ -e "$file" ] && die "$file exists. To rotate the seal key, run keyring.sh rotate. Another member is rotated with a new keyring and a new cluster"
  if [ -n "$provided" ]; then
    [ -r "$provided" ] || die "cannot read $provided"
    local key
    while IFS= read -r key; do
      case " ${MEMBERS[*]} " in
        *" $key:"*) ;;
        *) die "$provided names $key, which is not a keyring member" ;;
      esac
    done < <(grep -vE '^\s*(#|$)' "$provided" | cut -d= -f1)
  fi
  mkdir -p "$(dirname "$file")"
  local tmp
  tmp="$(umask 077 && mktemp "$(dirname "$file")/.keyring.XXXXXX")"
  {
    printf '# bringup keyring (ADR-0083), written %s by scripts/keyring.sh.\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '# Never commit. Never copy into a cluster, the bucket or OpenBao.\n'
    printf '# Shape and fingerprints: scripts/keyring.sh, substrate.env.\n'
    local spec name value err
    for spec in "${MEMBERS[@]}"; do
      name="$(member_name "$spec")"
      if [ -n "$provided" ] && value="$(envfile_get "$provided" "$name")" && [ -n "$value" ]; then
        err="$(shape_error "$spec" "$value")"
        [ -z "$err" ] || { rm -f "$tmp"; die "provided member $name $err"; }
      else
        # An input member not provided is written empty: "not supplied".
        value="$(gen_value "$spec")"
      fi
      printf '%s=%s\n' "$name" "$value"
    done
  } > "$tmp"
  chmod 0600 "$tmp"
  mv "$tmp" "$file"
  printf 'keyring: wrote %s (%s members)\n' "$file" "${#MEMBERS[@]}"
}

# check_shape FILE — every member present with the right shape. Prints the
# first miss and returns 1.
check_shape() {
  local file="$1" spec name value err
  for spec in "${MEMBERS[@]}"; do
    name="$(member_name "$spec")"
    if is_optional "$name"; then
      # Absent or empty is "not supplied" for an input member, and "no
      # rotation runs" for a previous value.
      value="$(envfile_get "$file" "$name")" || value=""
      err="$(shape_error "$spec" "$value")"
      if [ -n "$err" ]; then
        printf 'member %s %s\n' "$name" "$err"; return 1
      fi
      continue
    fi
    if ! value="$(envfile_get "$file" "$name")" || [ -z "$value" ]; then
      printf 'member %s missing from %s\n' "$name" "$file"; return 1
    fi
    err="$(shape_error "$spec" "$value")"
    if [ -n "$err" ]; then
      printf 'member %s %s\n' "$name" "$err"; return 1
    fi
  done
}

# member_lines FILE — the per-member fingerprint lines.
member_lines() {
  local spec name value
  for spec in "${MEMBERS[@]}"; do
    name="$(member_name "$spec")"
    # An absent input member fingerprints as the empty value.
    value="$(envfile_get "$1" "$name")" || value=""
    printf 'KEYRING_FINGERPRINT_%s=sha256:%s\n' "$name" "$(sha256_of "$value")"
  done
}

cmd_fingerprints() {
  local file="${1:?usage: keyring.sh fingerprints <keyring-file>}"
  [ -r "$file" ] || die "cannot read $file"
  local miss
  if ! miss="$(check_shape "$file")"; then die "$miss"; fi
  local lines
  lines="$(member_lines "$file")"
  printf '%s\n' "$lines"
  printf 'KEYRING_FINGERPRINT=sha256:%s\n' "$(sha256_of "$lines"$'\n')"
}

cmd_verify() {
  local file="${1:?usage: keyring.sh verify <keyring-file> <substrate.env>}"
  local env="${2:?usage: keyring.sh verify <keyring-file> <substrate.env>}"
  [ -r "$env" ] || die "cannot read $env"
  if [ ! -r "$file" ]; then printf 'keyring file %s is missing or unreadable\n' "$file"; exit 1; fi
  local miss
  if ! miss="$(check_shape "$file")"; then printf '%s\n' "$miss"; exit 1; fi
  local spec name want have
  for spec in "${MEMBERS[@]}"; do
    name="$(member_name "$spec")"
    if ! want="$(envfile_get "$env" "KEYRING_FINGERPRINT_$name")" || [ -z "$want" ]; then
      printf 'substrate.env records no fingerprint for member %s\n' "$name"; exit 1
    fi
    have="sha256:$(sha256_of "$(envfile_get "$file" "$name" || true)")"
    if [ "$have" != "$want" ]; then
      printf 'member %s does not match its recorded fingerprint (%s)\n' "$name" "$want"; exit 1
    fi
  done
  local lines set_want set_have
  lines="$(member_lines "$file")"
  set_have="sha256:$(sha256_of "$lines"$'\n')"
  if ! set_want="$(envfile_get "$env" KEYRING_FINGERPRINT)" || [ -z "$set_want" ]; then
    printf 'substrate.env records no KEYRING_FINGERPRINT\n'; exit 1
  fi
  if [ "$set_have" != "$set_want" ]; then
    printf 'keyring set fingerprint %s does not match the recorded %s\n' "$set_have" "$set_want"; exit 1
  fi
  # Report supplied and not-supplied separately. An input member is allowed to
  # be absent — it means "not supplied" — but counting it as "present" is how a
  # missing GHCR_PULL_TOKEN read as a green preflight while two first-party
  # images could not pull (deploy#1795). ADR-0083: a preflight that passes with
  # a missing input is a defect in preflight, so at minimum it must NAME what
  # is not there.
  local unsupplied=() supplied=0 counted=0
  for spec in "${MEMBERS[@]}"; do
    name="$(member_name "$spec")"
    # An empty previous value means no rotation runs, not a missing input.
    [ "$(member_kind "$spec")" = prev ] && continue
    counted=$((counted + 1))
    have="$(envfile_get "$file" "$name" 2>/dev/null || true)"
    if [ -n "$have" ]; then
      supplied=$((supplied + 1))
    else
      unsupplied+=("$name")
    fi
  done
  if [ "${#unsupplied[@]}" -eq 0 ]; then
    printf '%s members present, lengths right, fingerprints match %s\n' \
      "${#MEMBERS[@]}" "${set_have:0:19}"
  else
    printf '%s of %s members supplied, lengths right, fingerprints match %s; NOT SUPPLIED: %s\n' \
      "$supplied" "$counted" "${set_have:0:19}" "$(IFS=, ; echo "${unsupplied[*]}")"
  fi
}

cmd_get() {
  local file="${1:?usage: keyring.sh get <keyring-file> <MEMBER>}"
  local name="${2:?usage: keyring.sh get <keyring-file> <MEMBER>}"
  [ -r "$file" ] || die "cannot read $file"
  local value
  if ! value="$(envfile_get "$file" "$name")"; then
    # An absent input or previous member is empty: print nothing, succeed.
    is_optional "$name" && return 0
    die "member $name missing from $file"
  fi
  printf '%s' "$value"
}

# cmd_set FILE MEMBER VALUE — write one INPUT member into an existing keyring,
# atomically, keeping mode 0600 and every other line. Only input members may
# change after generate: every other member is a rotation and a new cluster.
cmd_set() {
  local file="${1:?usage: keyring.sh set <keyring-file> <MEMBER> <value>}"
  local name="${2:?usage: keyring.sh set <keyring-file> <MEMBER> <value>}"
  local value="${3-}"
  [ -r "$file" ] || die "cannot read $file"
  is_input "$name" || die "$name is not an input member; only GHCR_PULL_TOKEN, DNS_ACCESS_KEY and DNS_SECRET_KEY may be set after generate. Rotation is a new keyring and a new cluster, never an edit here"
  local spec err
  spec="$(member_spec "$name")"
  err="$(shape_error "$spec" "$value")"
  [ -z "$err" ] || die "member $name $err"
  local tmp
  tmp="$(umask 077 && mktemp "$(dirname "$file")/.keyring.XXXXXX")"
  {
    grep -vE "^$name=" "$file"
    printf '%s=%s\n' "$name" "$value"
  } > "$tmp"
  chmod 0600 "$tmp"
  mv "$tmp" "$file"
  if [ -n "$value" ]; then
    printf 'keyring: set %s in %s (sha256:%s)\n' "$name" "$file" "$(sha256_of "$value" | cut -c1-16)"
  else
    printf 'keyring: cleared %s in %s\n' "$name" "$file"
  fi
}

# write_members FILE NAME=VALUE... — replace the named members of FILE,
# atomically, keeping mode 0600 and every other line.
write_members() {
  local file="$1"; shift
  local tmp pair pattern=""
  for pair in "$@"; do pattern="${pattern:+$pattern|}^${pair%%=*}="; done
  tmp="$(umask 077 && mktemp "$(dirname "$file")/.keyring.XXXXXX")"
  {
    grep -vE "$pattern" "$file" || true
    for pair in "$@"; do printf '%s\n' "$pair"; done
  } > "$tmp"
  chmod 0600 "$tmp"
  mv "$tmp" "$file"
}

# cmd_rotate FILE MEMBER — keep MEMBER as MEMBER_PREVIOUS and generate a new
# MEMBER. Refuses while a previous value is kept (see ROTATION above).
cmd_rotate() {
  local file="${1:?usage: keyring.sh rotate <keyring-file> <MEMBER>}"
  local name="${2:?usage: keyring.sh rotate <keyring-file> <MEMBER>}"
  [ -r "$file" ] || die "cannot read $file"
  local spec pspec cur prev new miss
  spec="$(member_spec "$name")" || die "$name is not a keyring member"
  pspec="$(member_spec "${name}_PREVIOUS")" && [ "$(member_kind "$pspec")" = prev ] \
    || die "$name has no ${name}_PREVIOUS member, so it does not rotate in place. Rotate it with a new keyring and a new cluster"
  if ! miss="$(check_shape "$file")"; then die "$miss"; fi
  cur="$(envfile_get "$file" "$name")"
  prev="$(envfile_get "$file" "${name}_PREVIOUS" || true)"
  [ -z "$prev" ] || die "${name}_PREVIOUS still holds the key of the last rotation. When the cluster runs on ${name}, run keyring.sh retire first"
  new="$(gen_value "$spec")"
  write_members "$file" "${name}=${new}" "${name}_PREVIOUS=${cur}"
  printf 'keyring: rotated %s in %s (new sha256:%s, previous sha256:%s)\n' "$name" "$file" \
    "$(sha256_of "$new" | cut -c1-16)" "$(sha256_of "$cur" | cut -c1-16)"
}

# cmd_retire FILE MEMBER — clear MEMBER_PREVIOUS, after the cluster runs on
# the new value.
cmd_retire() {
  local file="${1:?usage: keyring.sh retire <keyring-file> <MEMBER>}"
  local name="${2:?usage: keyring.sh retire <keyring-file> <MEMBER>}"
  [ -r "$file" ] || die "cannot read $file"
  local pspec prev
  pspec="$(member_spec "${name}_PREVIOUS")" && [ "$(member_kind "$pspec")" = prev ] \
    || die "$name has no ${name}_PREVIOUS member"
  prev="$(envfile_get "$file" "${name}_PREVIOUS" || true)"
  [ -n "$prev" ] || die "${name}_PREVIOUS is already empty"
  write_members "$file" "${name}_PREVIOUS="
  printf 'keyring: retired the previous %s in %s (sha256:%s)\n' "$name" "$file" "$(sha256_of "$prev" | cut -c1-16)"
}

case "${1:-}" in
  generate)     shift; cmd_generate "$@" ;;
  fingerprints) shift; cmd_fingerprints "$@" ;;
  verify)       shift; cmd_verify "$@" ;;
  get)          shift; cmd_get "$@" ;;
  set)          shift; cmd_set "$@" ;;
  rotate)       shift; cmd_rotate "$@" ;;
  retire)       shift; cmd_retire "$@" ;;
  *) die "usage: keyring.sh generate|fingerprints|verify|get|set|rotate|retire ..." ;;
esac
