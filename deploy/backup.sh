#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
umask 077
mkdir -p backups
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TEMP="backups/campus-$STAMP.dump.partial"
FINAL="backups/campus-$STAMP.dump"
docker compose exec -T db pg_dump -U postgres -d campus -Fc > "$TEMP"
mv "$TEMP" "$FINAL"
printf 'Backup criado: %s\nGuarde a chave market.key separadamente.\n' "$FINAL"
