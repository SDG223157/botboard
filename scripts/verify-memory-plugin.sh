#!/usr/bin/env bash
set -euo pipefail

# Verify memory-lancedb-pro plugin is installed and running on all bot servers.
# Usage: ./scripts/verify-memory-plugin.sh

SSH_KEY="$HOME/.ssh/botboard_servers"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

if [ -f "$SSH_KEY" ]; then
  SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
fi

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

CHECK_SCRIPT='
PLUGIN_DIR="/root/.openclaw/plugins/memory-lancedb-pro"
CONFIG="/root/.openclaw/openclaw.json"

plugin_installed="no"
plugin_configured="no"
gateway_running="no"

[ -d "$PLUGIN_DIR/node_modules" ] && plugin_installed="yes"

if [ -f "$CONFIG" ]; then
  python3 -c "
import json
with open(\"$CONFIG\") as f:
    c = json.load(f)
e = c.get(\"plugins\",{}).get(\"entries\",{}).get(\"memory-lancedb-pro\",{})
print(\"yes\" if e.get(\"enabled\") else \"no\")
" 2>/dev/null && plugin_configured=$(python3 -c "
import json
with open(\"$CONFIG\") as f:
    c = json.load(f)
e = c.get(\"plugins\",{}).get(\"entries\",{}).get(\"memory-lancedb-pro\",{})
print(\"yes\" if e.get(\"enabled\") else \"no\")
" 2>/dev/null)
fi

systemctl --user is-active --quiet openclaw-gateway 2>/dev/null && gateway_running="yes"

echo "plugin_installed=$plugin_installed plugin_configured=$plugin_configured gateway_running=$gateway_running"
'

printf "%-12s %-18s %-12s %-14s %-10s\n" "Bot" "IP" "Installed" "Configured" "Gateway"
printf "%-12s %-18s %-12s %-14s %-10s\n" "---" "--" "---------" "----------" "-------"

for i in "${!SERVERS[@]}"; do
  IP="${SERVERS[$i]}"
  NAME=$(echo "${BOT_NAMES[$i]}" | awk '{print $1}')

  result=$(ssh $SSH_OPTS "root@$IP" "$CHECK_SCRIPT" 2>/dev/null || echo "plugin_installed=? plugin_configured=? gateway_running=?")

  installed=$(echo "$result" | grep -o 'plugin_installed=[^ ]*' | cut -d= -f2)
  configured=$(echo "$result" | grep -o 'plugin_configured=[^ ]*' | cut -d= -f2)
  gateway=$(echo "$result" | grep -o 'gateway_running=[^ ]*' | cut -d= -f2)

  printf "%-12s %-18s %-12s %-14s %-10s\n" "$NAME" "$IP" "$installed" "$configured" "$gateway"
done
