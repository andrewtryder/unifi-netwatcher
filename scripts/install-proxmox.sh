#!/usr/bin/env bash
# Proxmox VE host installer: create an unprivileged Debian LXC for NetWatcher.
# Run on the Proxmox VE host as root. Docker runs INSIDE the CT, not on PVE.
set -Eeuo pipefail
umask 077

APP_VERSION="${APP_VERSION:-0.5.1}"
BRIDGE="${BRIDGE:-vmbr0}"
CT_NAME="${CT_NAME:-netwatcher}"
CT_MEMORY="${CT_MEMORY:-2048}"
CT_DISK="${CT_DISK:-12}"
CT_CORES="${CT_CORES:-2}"
VMID="${VMID:-}"
ROOT_STORAGE="${ROOT_STORAGE:-}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-}"

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
info() { printf '\n==> %s\n' "$*"; }
for command in pct pveam pvesm pvesh awk grep sort ip; do
  command -v "$command" >/dev/null 2>&1 || die "Missing $command; run on a Proxmox VE host."
done
[[ "$EUID" -eq 0 ]] || die 'Run as root in the Proxmox host shell.'
[[ -t 0 ]] || die 'An interactive terminal is required for UniFi credentials.'
[[ "$APP_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die 'APP_VERSION must be like 0.5.1.'
[[ "$CT_NAME" =~ ^[a-z0-9][a-z0-9-]*$ ]] || die 'CT_NAME must be a lowercase DNS hostname.'
[[ "$BRIDGE" =~ ^[a-zA-Z0-9_.-]+$ ]] || die 'BRIDGE has invalid characters.'
[[ "$CT_MEMORY" =~ ^[0-9]+$ && "$CT_DISK" =~ ^[0-9]+$ && "$CT_CORES" =~ ^[0-9]+$ ]] || die 'Resources must be numbers.'
(( CT_MEMORY >= 512 && CT_DISK >= 8 && CT_CORES >= 1 )) || die 'Minimums: 512 MiB RAM, 8 GiB disk, 1 core.'

active_storages() { pvesm status --content "$1" | awk 'NR>1 && $3=="active" {print $1}'; }
choose_storage() {
  local kind="$1" preferred="$2" available
  available="$(active_storages "$kind")"
  [[ -n "$available" ]] || die "No active Proxmox storage supports $kind."
  if grep -Fxq "$preferred" <<<"$available"; then printf '%s' "$preferred";
  else head -n 1 <<<"$available"; fi
}
ROOT_STORAGE="${ROOT_STORAGE:-$(choose_storage rootdir local-lvm)}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-$(choose_storage vztmpl local)}"
active_storages rootdir | grep -Fxq "$ROOT_STORAGE" || die "ROOT_STORAGE '$ROOT_STORAGE' is unavailable or lacks rootdir support."
active_storages vztmpl | grep -Fxq "$TEMPLATE_STORAGE" || die "TEMPLATE_STORAGE '$TEMPLATE_STORAGE' is unavailable or lacks vztmpl support."

if [[ -z "$VMID" ]]; then
  VMID="$(pvesh get /cluster/nextid --output-format json | tr -d '"[:space:]')"
fi
[[ "$VMID" =~ ^[1-9][0-9]{2,8}$ ]] || die 'VMID must be a valid Proxmox CT ID (100+).'
if pct config "$VMID" >/dev/null 2>&1; then die "CT $VMID already exists; nothing changed."; fi
ip link show "$BRIDGE" >/dev/null 2>&1 || die "Bridge '$BRIDGE' does not exist."

printf 'This will create an UNPRIVILEGED Debian LXC with nested Docker.\n'
printf 'CT ID: %s | Name: %s | Bridge: %s (DHCP)\n' "$VMID" "$CT_NAME" "$BRIDGE"
printf 'Storage: %s (disk %s GiB) | Template: %s | RAM: %s MiB\n' "$ROOT_STORAGE" "$CT_DISK" "$TEMPLATE_STORAGE" "$CT_MEMORY"
printf 'NetWatcher image: docker.io/andrewtryder/unifi-netwatcher:%s\n' "$APP_VERSION"
printf 'Nested Docker in LXC has kernel-sharing limitations; use a Debian VM for stronger isolation.\n'
read -r -p 'Create this CT? [y/N] ' confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo 'Cancelled; nothing created.'; exit 0; }

read -r -p 'UniFi Network URL (e.g. https://192.168.1.1): ' unifi_url
[[ "$unifi_url" =~ ^https?://[^[:space:]]+$ ]] || die 'Enter a full http(s) controller URL.'
read -r -p 'UniFi site [default]: ' unifi_site
unifi_site="${unifi_site:-default}"
read -r -p 'API mode [legacy/official] (default legacy): ' api_mode
api_mode="${api_mode:-legacy}"
[[ "$api_mode" == legacy || "$api_mode" == official ]] || die 'Use legacy or official.'
if [[ "$api_mode" == legacy ]]; then
  read -r -p 'UniFi username: ' unifi_username
  [[ -n "$unifi_username" ]] || die 'A UniFi username is required.'
  read -r -s -p 'UniFi password (hidden): ' unifi_password; echo
  [[ -n "$unifi_password" ]] || die 'A UniFi password is required.'
else
  read -r -s -p 'UniFi API key (hidden): ' unifi_api_key; echo
  [[ -n "$unifi_api_key" ]] || die 'An API key is required.'
fi
read -r -p 'Verify the UniFi TLS certificate? [Y/n] ' verify_reply
case "$verify_reply" in
  ''|y|Y) verify_ssl=true ;;
  n|N) verify_ssl=false; printf 'Warning: disabling TLS verification is less secure.\n' ;;
  *) die 'Answer y or n.' ;;
esac

# Compose env_file uses single quotes for literal values, including characters such as $.
quote_env() {
  local value="${1//\\/\\\\}"
  value="${value//\'/\\\'}"
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || die 'Newlines are not allowed in settings.'
  printf "'%s'" "$value"
}
env_tmp="$(mktemp)"
guest_tmp="$(mktemp)"
trap 'rm -f -- "${env_tmp:-}" "${guest_tmp:-}"' EXIT
{
  echo 'APP_ENV=production'
  echo 'APP_SECRET_KEY='
  printf 'UNIFI_URL=%s\n' "$(quote_env "$unifi_url")"
  printf 'UNIFI_SITE=%s\n' "$(quote_env "$unifi_site")"
  printf 'UNIFI_API_MODE=%s\n' "$(quote_env "$api_mode")"
  if [[ "$api_mode" == legacy ]]; then
    printf 'UNIFI_USERNAME=%s\n' "$(quote_env "$unifi_username")"
    printf 'UNIFI_PASSWORD=%s\n' "$(quote_env "$unifi_password")"
  else
    printf 'UNIFI_API_KEY=%s\n' "$(quote_env "$unifi_api_key")"
  fi
  printf 'UNIFI_VERIFY_SSL=%s\n' "$verify_ssl"
  echo 'UNIFI_MOCK_MODE=false'
  echo 'UNIFI_DRY_RUN_BLOCKS=true'
} > "$env_tmp"

cat > "$guest_tmp" <<'GUEST'
#!/usr/bin/env bash
set -Eeuo pipefail
version="$1"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl nano
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: ${VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
install -d -m 0700 /opt/netwatcher
curl -fsSL --retry 3 "https://raw.githubusercontent.com/andrewtryder/unifi-netwatcher/v${version}/compose.yml" -o /opt/netwatcher/compose.yml
sed -i "s|ghcr.io/andrewtryder/unifi-netwatcher:latest|andrewtryder/unifi-netwatcher:${version}|" /opt/netwatcher/compose.yml
grep -Fq "image: andrewtryder/unifi-netwatcher:${version}" /opt/netwatcher/compose.yml
cd /opt/netwatcher
docker compose config -q
docker compose up -d
for attempt in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:8080/readyz >/dev/null 2>&1; then
    echo 'NetWatcher readiness check passed.'
    exit 0
  fi
  sleep 3
done
docker compose ps
exit 1
GUEST

info 'Finding Debian 13 LXC template'
template="$(pveam available --section system | awk '$2 ~ /^debian-13-standard_.*_amd64\.tar\.(zst|gz|xz)$/ {print $2}' | sort -V | tail -n 1)"
[[ -n "$template" ]] || die 'No Debian 13 amd64 template found; refresh Proxmox templates and retry.'
if ! pveam list "$TEMPLATE_STORAGE" | grep -Fq "/$template"; then
  pveam download "$TEMPLATE_STORAGE" "$template"
fi

info "Creating CT $VMID"
pct create "$VMID" "$TEMPLATE_STORAGE:vztmpl/$template" \
  --hostname "$CT_NAME" --ostype debian --unprivileged 1 \
  --features nesting=1,keyctl=1 \
  --cores "$CT_CORES" --memory "$CT_MEMORY" --swap 512 \
  --rootfs "$ROOT_STORAGE:$CT_DISK" \
  --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" --onboot 1
pct start "$VMID"
info 'Waiting for CT networking'
for attempt in $(seq 1 30); do
  if pct exec "$VMID" -- getent hosts deb.debian.org >/dev/null 2>&1; then break; fi
  [[ "$attempt" -lt 30 ]] || die "CT $VMID has no network/DNS; inspect its Proxmox network settings."
  sleep 3
done

info 'Installing Docker and NetWatcher inside the CT'
pct push "$VMID" "$env_tmp" /root/netwatcher.env
pct push "$VMID" "$guest_tmp" /root/netwatcher-init.sh
pct exec "$VMID" -- install -d -m 0700 /opt/netwatcher
pct exec "$VMID" -- mv /root/netwatcher.env /opt/netwatcher/.env
pct exec "$VMID" -- chmod 0600 /opt/netwatcher/.env
pct exec "$VMID" -- bash /root/netwatcher-init.sh "$APP_VERSION"
pct exec "$VMID" -- rm -f /root/netwatcher-init.sh

ip_addr="$(pct exec "$VMID" -- hostname -I | awk '{print $1}')"
info 'Installation complete'
printf 'CT ID: %s\nURL: http://%s:8080\n' "$VMID" "${ip_addr:-CT-IP}"
printf 'Initial login: admin / admin — CHANGE THE PASSWORD IMMEDIATELY in Security.\n'
printf 'Check logs: pct exec %s -- bash -lc '\''cd /opt/netwatcher && docker compose logs -f'\''\n' "$VMID"
printf 'Config: pct enter %s; nano /opt/netwatcher/.env\n' "$VMID"
