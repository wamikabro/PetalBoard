#!/usr/bin/env bash
# One-time (and re-runnable) server setup for the PetalBoard API on Ubuntu 22.04/24.04,
# sized for an Oracle Always Free VM.Standard.E2.1.Micro (1 GB RAM).
#
#   sudo DOMAIN=api.example.com ORIGINS=https://petalboard.example.com bash setup.sh
#
# DOMAIN   the HTTPS name for the API. Without one, <ip>.sslip.io is used (works anywhere, no DNS needed).
# ORIGINS  comma-separated web app addresses allowed to call the API (the Cloudflare URL).
#
# Expects the API code in /opt/petalboard/api (copied by deploy/push.sh) and installs:
# swap, PostgreSQL (tuned small), a Python venv, a systemd service, Caddy (automatic HTTPS),
# firewall rules for 80/443 and a daily database backup kept for 30 days.
set -euo pipefail

APP=/opt/petalboard
ENV_FILE=/etc/petalboard.env
IP=$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
DOMAIN=${DOMAIN:-api.${IP}.sslip.io}
ORIGINS=${ORIGINS:-http://localhost:4321}

echo "== PetalBoard setup: https://${DOMAIN}"

# --- 2 GB swap, so package installs do not run out of memory ---
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 >/dev/null && echo 'vm.swappiness=10' > /etc/sysctl.d/90-swap.conf
fi

# --- packages ---
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq postgresql python3-venv python3-pip caddy iptables-persistent cron curl

# --- PostgreSQL, tuned for 1 GB ---
PGCONF=$(ls -d /etc/postgresql/*/main | tail -1)/conf.d
mkdir -p "$PGCONF"
cat > "$PGCONF/petalboard.conf" <<'EOF'
listen_addresses = 'localhost'
max_connections = 20
shared_buffers = 64MB
work_mem = 4MB
maintenance_work_mem = 32MB
effective_cache_size = 256MB
EOF
systemctl restart postgresql

if [ ! -f "$ENV_FILE" ]; then
  DB_PASS=$(openssl rand -hex 24)
  sudo -u postgres psql -qc "CREATE ROLE petalboard LOGIN PASSWORD '${DB_PASS}'" || true
  sudo -u postgres psql -qc "CREATE DATABASE petalboard OWNER petalboard" || true
  cat > "$ENV_FILE" <<EOF
DATABASE_URL=postgresql://petalboard:${DB_PASS}@127.0.0.1:5432/petalboard
ALLOWED_ORIGINS=${ORIGINS}
SESSION_DAYS=365
EOF
  chmod 600 "$ENV_FILE"
else
  sed -i "s#^ALLOWED_ORIGINS=.*#ALLOWED_ORIGINS=${ORIGINS}#" "$ENV_FILE"
fi

# --- app user, venv ---
id petalboard >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin petalboard
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/api/requirements.txt"
chown -R petalboard:petalboard "$APP"
chown root:petalboard "$ENV_FILE" && chmod 640 "$ENV_FILE"

# --- service ---
install -m 644 "$APP/deploy/petalboard.service" /etc/systemd/system/petalboard.service
systemctl daemon-reload
systemctl enable --now petalboard
systemctl restart petalboard

# --- Caddy: HTTPS in front of the API ---
sed "s#{DOMAIN}#${DOMAIN}#" "$APP/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl reload caddy || systemctl restart caddy

# --- firewall (Oracle Ubuntu images reject everything but SSH by default) ---
for port in 80 443; do
  iptables -C INPUT -p tcp --dport $port -m state --state NEW -j ACCEPT 2>/dev/null ||
    iptables -I INPUT 5 -p tcp --dport $port -m state --state NEW -j ACCEPT
done
netfilter-persistent save >/dev/null

# --- network: Oracle VMs use 9000-byte packets, which stall larger replies on the way to most
#     home and mobile connections. Use the normal 1500, and clamp TCP segments for smaller links. ---
NIC=$(ip -o -4 route show default | awk '{print $5}' | head -1)
ip link set "$NIC" mtu 1500
for f in /etc/netplan/*.yaml; do sed -i -E 's/^(\s*mtu:\s*)9000/\11500/' "$f"; done
echo "network: {config: disabled}" > /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
iptables -t mangle -C POSTROUTING -o "$NIC" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360 2>/dev/null ||
  iptables -t mangle -A POSTROUTING -o "$NIC" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360
netfilter-persistent save >/dev/null

# --- daily backup at 02:30, keep 30 days ---
install -m 755 "$APP/deploy/backup.sh" /usr/local/bin/petalboard-backup
echo "30 2 * * * root /usr/local/bin/petalboard-backup" > /etc/cron.d/petalboard-backup

echo "== done. API: https://${DOMAIN}/health"
echo "   Create the owner:  sudo -u petalboard bash -c 'set -a; . ${ENV_FILE}; cd ${APP}/api && ${APP}/venv/bin/python manage_users.py create <username> \"<Name>\" owner --generate'"
