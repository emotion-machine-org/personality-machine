#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_TRANSACTION_DSN:=${DATABASE_DSN:?DATABASE_TRANSACTION_DSN or DATABASE_DSN must be set}}"
DIR="$(dirname "$0")/../supabase/migrations"
for f in $(ls "$DIR" | sort); do
  echo "Applying $f …" >&2
  psql "$DATABASE_TRANSACTION_DSN" -f "$DIR/$f"
done
