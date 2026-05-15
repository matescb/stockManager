#!/usr/bin/env bash
set -euo pipefail

env_file="${1:-.env.prod}"
placeholder="replace-me-with-the-output-of-openssl-rand-hex-32"
confirm_phrase="I HAVE ESCROWED PASSWORD_PEPPER"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

if [ ! -t 0 ] || [ ! -t 1 ]; then
  die "bootstrap-pepper must be run interactively on the VPS, not from CI."
fi

if [ ! -f "${env_file}" ]; then
  die "${env_file} does not exist. Copy deploy/.env.prod.example first."
fi

if [ ! -w "${env_file}" ]; then
  die "${env_file} is not writable by the current user."
fi

pepper_count=$(grep -Ec '^PASSWORD_PEPPER=' "${env_file}" || true)
if [ "${pepper_count}" -gt 1 ]; then
  die "multiple PASSWORD_PEPPER entries found in ${env_file}; resolve manually."
fi

pepper_line=$(grep -E '^PASSWORD_PEPPER=' "${env_file}" | tail -n 1 || true)
pepper_value="${pepper_line#PASSWORD_PEPPER=}"

if [ -n "${pepper_line}" ] && [ -n "${pepper_value}" ] && [ "${pepper_value}" != "${placeholder}" ]; then
  echo "PASSWORD_PEPPER is already set in ${env_file}; refusing to overwrite it."
  exit 0
fi

if command -v openssl >/dev/null 2>&1; then
  new_pepper=$(openssl rand -hex 32)
else
  new_pepper=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
fi

cat <<EOF
This will bootstrap PASSWORD_PEPPER in ${env_file}.

Changing this value after users have logged in prevents peppered password
hashes from verifying. Store the value below in the operator password manager
alongside SESSION_SECRET before continuing.

PASSWORD_PEPPER=${new_pepper}
EOF

printf "\nAfter escrow, type '%s' to write it: " "${confirm_phrase}"
IFS= read -r confirmation
if [ "${confirmation}" != "${confirm_phrase}" ]; then
  die "confirmation did not match; ${env_file} was not changed."
fi

tmp_file=$(mktemp "${env_file}.XXXXXX")
trap 'rm -f "${tmp_file}"' EXIT

python3 - "${env_file}" "${tmp_file}" "${new_pepper}" "${placeholder}" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

env_path = Path(sys.argv[1])
tmp_path = Path(sys.argv[2])
new_pepper = sys.argv[3]
placeholder = sys.argv[4]

lines = env_path.read_text().splitlines()
pepper_indexes = [
    idx for idx, line in enumerate(lines) if line.startswith("PASSWORD_PEPPER=")
]

if len(pepper_indexes) > 1:
    raise SystemExit(
        "multiple PASSWORD_PEPPER entries found; resolve the ambiguity manually"
    )

if pepper_indexes:
    idx = pepper_indexes[0]
    current = lines[idx].split("=", 1)[1].strip()
    if current and current != placeholder:
        raise SystemExit("PASSWORD_PEPPER is already set; refusing to overwrite")
    lines[idx] = f"PASSWORD_PEPPER={new_pepper}"
else:
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(f"PASSWORD_PEPPER={new_pepper}")

tmp_path.write_text("\n".join(lines) + "\n")
PY

chmod 600 "${tmp_file}"
mv "${tmp_file}" "${env_file}"
trap - EXIT

echo "PASSWORD_PEPPER written to ${env_file}; keep the escrowed copy off the VPS."
