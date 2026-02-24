#!/usr/bin/env bash
set -euo pipefail

# Setup SSH key-based access to all 8 OpenClaw bot servers.
# Usage: ./scripts/setup-ssh-keys.sh
# You will be prompted for the SSH password once per server.

SERVERS=(
  "168.231.127.215"  # Kai
  "93.127.213.26"    # River
  "72.61.2.94"       # Yilin
  "187.77.11.156"    # Allison
  "187.77.12.62"     # Chen
  "187.77.22.165"    # Summer
  "187.77.22.125"    # Spring
  "187.77.18.22"     # Mei
)

BOT_NAMES=(
  "Kai ⚡"
  "River 🌊"
  "Yilin 🧭"
  "Allison 📖"
  "Chen ⚔️"
  "Summer ☀️"
  "Spring 🌱"
  "Mei 🍜"
)

KEY_PATH="$HOME/.ssh/botboard_servers"

if [ ! -f "$KEY_PATH" ]; then
  echo "Generating SSH key pair at $KEY_PATH ..."
  ssh-keygen -t ed25519 -f "$KEY_PATH" -N "" -C "botboard-deploy"
  echo ""
fi

echo "Will copy SSH key to ${#SERVERS[@]} servers."
echo "You will be prompted for the root password for each server."
echo ""

SUCCESS=0
FAIL=0

for i in "${!SERVERS[@]}"; do
  IP="${SERVERS[$i]}"
  NAME="${BOT_NAMES[$i]}"
  echo "[$((i+1))/${#SERVERS[@]}] $NAME ($IP) ..."
  if ssh-copy-id -i "$KEY_PATH.pub" -o StrictHostKeyChecking=no "root@$IP" 2>/dev/null; then
    echo "  ✓ Key installed"
    ((SUCCESS++))
  else
    echo "  ✗ Failed — check password and connectivity"
    ((FAIL++))
  fi
  echo ""
done

echo "Done: $SUCCESS succeeded, $FAIL failed."

if [ "$SUCCESS" -gt 0 ]; then
  echo ""
  echo "Add this to ~/.ssh/config for convenience:"
  echo ""
  for i in "${!SERVERS[@]}"; do
    IP="${SERVERS[$i]}"
    # Strip emoji for hostname
    HOSTNAME=$(echo "${BOT_NAMES[$i]}" | awk '{print tolower($1)}')
    echo "Host bot-$HOSTNAME"
    echo "  HostName $IP"
    echo "  User root"
    echo "  IdentityFile $KEY_PATH"
    echo ""
  done
fi

echo "Next step: set JINA_API_KEY and run scripts/deploy-memory-plugin.sh"
