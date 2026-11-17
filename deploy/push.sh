#!/usr/bin/env bash
# Copy the API (and the deploy kit) to the server and restart it. Run from the repo root on your PC:
#   bash deploy/push.sh ubuntu@<server-ip> [path/to/ssh-key]
# First time: then run  ssh ... 'sudo bash /opt/petalboard/deploy/setup.sh'
set -euo pipefail
HOST=$1
KEY=${2:-}
SSH=(ssh -o StrictHostKeyChecking=accept-new ${KEY:+-i "$KEY"})
tar --exclude='__pycache__' --exclude='.pytest_cache' -czf - api deploy |
  "${SSH[@]}" "$HOST" 'sudo mkdir -p /opt/petalboard && sudo tar -xzf - -C /opt/petalboard &&
     (id petalboard >/dev/null 2>&1 && sudo chown -R petalboard:petalboard /opt/petalboard/api || true) &&
     (systemctl is-active --quiet petalboard && sudo systemctl restart petalboard || true)'
echo "Copied to $HOST:/opt/petalboard"
