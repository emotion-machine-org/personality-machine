#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_TRANSACTION_DSN:=${DATABASE_DSN:?DATABASE_TRANSACTION_DSN or DATABASE_DSN must be set}}"
DIR="$(dirname "$0")/../supabase/migrations"
FROM="${1:-0032}"
TO="${2:-9999}"

echo "Applying migrations from $FROM to $TO..." >&2

for f in $(ls "$DIR" | sort); do
  num="${f%%_*}"
  if [[ ! "$num" < "$FROM" ]] && [[ ! "$num" > "$TO" ]]; then
    echo "Applying $f &" >&2
    psql "$DATABASE_TRANSACTION_DSN" -f "$DIR/$f"
  fi
done

echo "Done." >&2
