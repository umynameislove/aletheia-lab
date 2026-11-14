#!/usr/bin/env bash
set -euo pipefail
kit_root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
pg_root="$(mktemp -d /tmp/aletheia-r4-pg16-XXXXXXXX)"
mkdir "$pg_root/debs" "$pg_root/root"
cd "$pg_root/debs"
apt-get download postgresql-16 postgresql-client-16 libpq5
for package in ./*.deb; do dpkg-deb -x "$package" "$pg_root/root"; done
pg_bin="$pg_root/root/usr/lib/postgresql/16/bin"
export LD_LIBRARY_PATH="$pg_root/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
"$pg_bin/pg_dump" --version
"$pg_bin/pg_restore" --version
"$pg_bin/initdb" --version
"$pg_bin/pg_ctl" --version
"$pg_bin/postgres" --version
printf '%s\n' "$pg_root" > "$kit_root/evidence/linux-pg16-path.txt"
