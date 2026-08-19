#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_TRANSACTION_DSN:=${DATABASE_DSN:?DATABASE_TRANSACTION_DSN or DATABASE_DSN must be set}}"
DIR="$(dirname "$0")/../supabase/migrations"

LATEST_MIGRATION=$(ls "$DIR" | sort -V | tail -n 1)

if [ -z "$LATEST_MIGRATION" ]; then
  echo "No migration files found in $DIR" >&2
  exit 1
fi

echo "Applying latest migration: $LATEST_MIGRATION …" >&2
psql "$DATABASE_TRANSACTION_DSN" -f "$DIR/$LATEST_MIGRATION"
