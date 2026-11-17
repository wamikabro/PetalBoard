#!/usr/bin/env bash
# Daily database dump, kept 30 days in /var/backups/petalboard.
# Copy these off the server too (or use "Download full backup" in the app's Settings).
set -euo pipefail
DIR=/var/backups/petalboard
mkdir -p "$DIR" && chmod 700 "$DIR"
sudo -u postgres pg_dump -Fc petalboard > "$DIR/petalboard-$(date +%F).dump"
find "$DIR" -name 'petalboard-*.dump' -mtime +30 -delete
