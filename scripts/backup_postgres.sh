#!/usr/bin/env bash
# Daily PostgreSQL backup for the recharge-desk app.
#
# WHAT IT DOES
#   * Reads the same /etc/recharge-desk.env that gunicorn / Django read,
#     so credentials, host and port stay in one place.
#   * Runs `pg_dump -Fc` (custom format → smaller, parallel-restore-able)
#     into /var/backups/recharge-desk/db/.
#   * Names the file by date+time, e.g.
#       recharge-desk-2026-04-19_03-00.dump
#   * Rotates: keeps the last $KEEP_DAILY daily files (default 14) and
#     the last $KEEP_WEEKLY Sunday files (default 8), then deletes the
#     rest.
#   * Optionally uploads to a remote (rsync / S3) if BACKUP_REMOTE is
#     set in the env file.
#   * Logs to /var/log/recharge-desk-backup.log and returns a non-zero
#     exit code if anything went wrong, so cron will email root.
#
# INSTALLATION
#   1. Copy this file to /opt/recharge-desk/scripts/backup_postgres.sh
#      and chmod +x it.
#   2. Make sure /etc/recharge-desk.env contains:
#        POSTGRES_DB=...
#        POSTGRES_USER=...
#        POSTGRES_PASSWORD=...
#        POSTGRES_HOST=127.0.0.1
#        POSTGRES_PORT=5432
#      (already required by config/settings/production.py).
#   3. Create the backup directory and grant the postgres user access:
#        sudo mkdir -p /var/backups/recharge-desk/db
#        sudo chown postgres:postgres /var/backups/recharge-desk/db
#        sudo chmod 750 /var/backups/recharge-desk/db
#   4. Add a daily cron entry as the postgres user:
#        sudo crontab -u postgres -e
#        0 3 * * * /opt/recharge-desk/scripts/backup_postgres.sh
#      (3 AM avoids the morning till open and any midnight-timed jobs.)
#
# RESTORE
#   See README.md → "Restoring a PostgreSQL backup".

set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/recharge-desk.env}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/recharge-desk/db}"
LOG_FILE="${LOG_FILE:-/var/log/recharge-desk-backup.log}"
KEEP_DAILY="${KEEP_DAILY:-14}"
KEEP_WEEKLY="${KEEP_WEEKLY:-8}"

log() {
  printf '%s  %s\n' "$(date -Is)" "$*" | tee -a "$LOG_FILE" >&2
}

if [[ ! -r "$ENV_FILE" ]]; then
  log "ERROR: env file $ENV_FILE not readable"
  exit 1
fi

# shellcheck disable=SC1090
set -a
. "$ENV_FILE"
set +a

: "${POSTGRES_DB:?POSTGRES_DB missing in $ENV_FILE}"
: "${POSTGRES_USER:?POSTGRES_USER missing in $ENV_FILE}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD missing in $ENV_FILE}"
POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"

mkdir -p "$BACKUP_ROOT"

stamp="$(date +%Y-%m-%d_%H-%M)"
out="$BACKUP_ROOT/recharge-desk-${stamp}.dump"
tmp="${out}.partial"

log "starting pg_dump for $POSTGRES_DB → $out"

# PGPASSWORD is the documented way to pass the password to pg_dump
# without echoing it on the command line.
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
  --host="$POSTGRES_HOST" \
  --port="$POSTGRES_PORT" \
  --username="$POSTGRES_USER" \
  --dbname="$POSTGRES_DB" \
  --format=custom \
  --no-owner \
  --no-privileges \
  --file="$tmp"

# Atomic move — readers never see a half-written file.
mv "$tmp" "$out"
chmod 640 "$out"

size_kb="$(du -k "$out" | awk '{print $1}')"
log "wrote $out ($size_kb KB)"

# ---------------------------------------------------------------------- rotate
# Daily files older than KEEP_DAILY days that are NOT a Sunday roll
# off; Sunday files survive an extra KEEP_WEEKLY weeks.
log "rotating: keep last $KEEP_DAILY daily + $KEEP_WEEKLY Sunday backups"

# Build a list of files we want to KEEP, then delete everything else.
keep_list="$(mktemp)"
trap 'rm -f "$keep_list"' EXIT

# Daily window.
find "$BACKUP_ROOT" -maxdepth 1 -name 'recharge-desk-*.dump' \
  -mtime -"$KEEP_DAILY" -print >>"$keep_list"

# Weekly window: Sunday files (date +%u == 7) within the longer cap.
weekly_cutoff_days=$(( KEEP_WEEKLY * 7 ))
while IFS= read -r -d '' f; do
  # Extract the YYYY-MM-DD portion of the filename and check its day-of-week.
  base="$(basename "$f")"
  date_part="${base#recharge-desk-}"
  date_part="${date_part%%_*}"
  if [[ "$(date -d "$date_part" +%u 2>/dev/null || true)" == "7" ]]; then
    printf '%s\n' "$f" >>"$keep_list"
  fi
done < <(find "$BACKUP_ROOT" -maxdepth 1 -name 'recharge-desk-*.dump' \
  -mtime -"$weekly_cutoff_days" -print0)

# Now delete anything not in keep_list.
sort -u "$keep_list" -o "$keep_list"
removed=0
while IFS= read -r -d '' f; do
  if ! grep -qxF "$f" "$keep_list"; then
    rm -f "$f"
    removed=$((removed + 1))
  fi
done < <(find "$BACKUP_ROOT" -maxdepth 1 -name 'recharge-desk-*.dump' -print0)

log "rotation removed $removed file(s); $(ls -1 "$BACKUP_ROOT"/recharge-desk-*.dump 2>/dev/null | wc -l) remain"

# ---------------------------------------------------------------- offsite copy
if [[ -n "${BACKUP_REMOTE:-}" ]]; then
  log "copying to $BACKUP_REMOTE"
  if [[ "$BACKUP_REMOTE" == s3://* ]]; then
    aws s3 cp "$out" "$BACKUP_REMOTE/"
  else
    rsync -az --partial "$out" "$BACKUP_REMOTE/"
  fi
  log "offsite copy done"
fi

log "backup OK"
