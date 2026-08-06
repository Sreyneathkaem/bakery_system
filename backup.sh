#!/bin/sh
# Backs up the Postgres database into backups/ with a timestamp.
#
# Only needed if you're self-hosting Postgres (the docker-compose "db"
# service). If you're using Supabase, they already back up your database
# automatically — you don't need this script.
#
# Run manually, or add to a daily cron job, e.g.:
#   0 22 * * * /path/to/bakery-app/backup.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DIR/backups"
docker exec bakery-db pg_dump -U bakery bakery > "$DIR/backups/bakery-$(date +%Y-%m-%d_%H%M).sql"
# Keep only the last 30 backups
ls -1t "$DIR/backups"/bakery-*.sql | tail -n +31 | xargs -r rm --
