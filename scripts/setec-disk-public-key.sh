#!/usr/bin/env bash
# setec-disk-public-key.sh — the ed25519 public key of the keyring seed that
# signs the sandbox disks (ADR-0166).
#
# The setec disk builder signs each image disk with the seed of the keyring
# member SETEC_DISK_SIGNING_SEED (base64 of 32 bytes). Each launcher checks
# the signature with the public key, which the chart takes as the value
# setec.launcher.diskBuilder.publicKeys. This script derives the key from the
# seed, so the bringup never stores a second copy that can drift from it.
#
# Usage:
#   scripts/setec-disk-public-key.sh <keyring file>     # prints the key, base64
#   scripts/setec-disk-public-key.sh --selftest          # a known seed gives its known key
#
# openssl 1.1.1 or later: the seed is wrapped as a PKCS#8 ed25519 private key
# (the fixed 16-byte prefix, then the 32-byte seed), and the last 32 bytes of
# the DER public key are the raw key.
set -euo pipefail

public_key_of_seed() {
  local seed_b64="$1"
  {
    printf '\x30\x2e\x02\x01\x00\x30\x05\x06\x03\x2b\x65\x70\x04\x22\x04\x20'
    printf '%s' "$seed_b64" | base64 -d
  } | openssl pkey -inform DER -pubout -outform DER 2>/dev/null | tail -c 32 | base64 -w0
}

if [ "${1:-}" = "--selftest" ]; then
  # RFC 8032 test vector 1: seed 9d61b19d...1a0a1b4f -> public key d75a9801...3f09.
  want="11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="
  got="$(public_key_of_seed "nWGxne/9WmC6hEr0kuwsxERJxWl7MmkZcDusAxyuf2A=")"
  [ "$got" = "$want" ] || { echo "SELFTEST FAIL: want ${want}, got ${got}" >&2; exit 1; }
  echo "  ✓ selftest: the RFC 8032 seed gives its public key"
  exit 0
fi

KEYRING_FILE="${1:-}"
[ -s "$KEYRING_FILE" ] || { echo "usage: setec-disk-public-key.sh <keyring file>" >&2; exit 2; }
seed="$(grep -E '^SETEC_DISK_SIGNING_SEED=' "$KEYRING_FILE" | head -n1 | cut -d= -f2- || true)"
[ "${#seed}" -eq 44 ] || { echo "FATAL: ${KEYRING_FILE} has no 44-character SETEC_DISK_SIGNING_SEED member (base64 of 32 bytes); generate a new keyring with scripts/keyring.sh generate" >&2; exit 1; }
key="$(public_key_of_seed "$seed")"
[ "${#key}" -eq 44 ] || { echo "FATAL: openssl could not derive the ed25519 public key of SETEC_DISK_SIGNING_SEED" >&2; exit 1; }
printf '%s\n' "$key"
