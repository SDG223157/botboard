#!/usr/bin/env bash
set -euo pipefail

# Deploy memory-lancedb-pro plugin to all 8 OpenClaw bot servers.
#
# Prerequisites:
#   1. SSH key access configured (run scripts/setup-ssh-keys.sh first)
#   2. JINA_API_KEY environment variable set
#
# Usage:
#   JINA_API_KEY=jina_xxxx ./scripts/deploy-memory-plugin.sh
#   JINA_API_KEY=jina_xxxx ./scripts/deploy-memory-plugin.sh 168.231.127.215   # single server

PLUGIN_REPO="https://github.com/win4r/memory-lancedb-pro.git"
PLUGIN_DIR="/root/.openclaw/plugins/memory-lancedb-pro"
SSH_KEY="$HOME/.ssh/botboard_servers"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

if [ -f "$SSH_KEY" ]; then
  SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
fi

ALL_SERVERS=(
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

if [ -z "${JINA_API_KEY:-}" ]; then
  echo "Error: JINA_API_KEY is not set."
  echo "Get a key at https://jina.ai/ then run:"
  echo "  JINA_API_KEY=jina_xxxx $0"
  exit 1
fi

# Allow targeting a single server via argument
if [ $# -ge 1 ]; then
  TARGETS=("$@")
  TARGET_NAMES=()
  for t in "${TARGETS[@]}"; do
    found=false
    for i in "${!ALL_SERVERS[@]}"; do
      if [ "${ALL_SERVERS[$i]}" = "$t" ]; then
        TARGET_NAMES+=("${BOT_NAMES[$i]}")
        found=true
        break
      fi
    done
    if ! $found; then
      TARGET_NAMES+=("unknown")
    fi
  done
else
  TARGETS=("${ALL_SERVERS[@]}")
  TARGET_NAMES=("${BOT_NAMES[@]}")
fi

echo "=== Deploying memory-lancedb-pro to ${#TARGETS[@]} server(s) ==="
echo ""

# The remote script: clone plugin, npm install, patch openclaw.json, restart
REMOTE_SCRIPT=$(cat <<'REMOTE_EOF'
set -e

PLUGIN_DIR="/root/.openclaw/plugins/memory-lancedb-pro"
CONFIG="/root/.openclaw/openclaw.json"
JINA_KEY="__JINA_API_KEY__"

echo "--- Step 1: Install plugin ---"
mkdir -p /root/.openclaw/plugins
if [ -d "$PLUGIN_DIR" ]; then
  echo "Plugin directory exists, pulling latest..."
  cd "$PLUGIN_DIR" && git pull --ff-only
else
  echo "Cloning memory-lancedb-pro..."
  git clone https://github.com/win4r/memory-lancedb-pro.git "$PLUGIN_DIR"
fi

echo "--- Step 2: npm install ---"
cd "$PLUGIN_DIR"
npm install --production 2>&1 | tail -3

echo "--- Step 3: Update openclaw.json ---"
if [ ! -f "$CONFIG" ]; then
  echo "ERROR: $CONFIG not found!"
  exit 1
fi

python3 << PYEOF
import json, sys, copy

config_path = "$CONFIG"
jina_key = "$JINA_KEY"

with open(config_path) as f:
    cfg = json.load(f)

backup_path = config_path + ".bak"
with open(backup_path, "w") as f:
    json.dump(cfg, f, indent=2)
print(f"Backed up config to {backup_path}")

if "plugins" not in cfg:
    cfg["plugins"] = {}

plugins = cfg["plugins"]

# Set load paths
if "load" not in plugins:
    plugins["load"] = {}
paths = plugins["load"].setdefault("paths", [])
plugin_path = "plugins/memory-lancedb-pro"
if plugin_path not in paths:
    paths.append(plugin_path)

# Disable built-in memory-lancedb if present
if "entries" not in plugins:
    plugins["entries"] = {}
if "memory-lancedb" in plugins["entries"]:
    plugins["entries"]["memory-lancedb"]["enabled"] = False
    print("Disabled built-in memory-lancedb plugin")

# Add memory-lancedb-pro entry
plugins["entries"]["memory-lancedb-pro"] = {
    "enabled": True,
    "config": {
        "embedding": {
            "apiKey": jina_key,
            "model": "jina-embeddings-v5-text-small",
            "baseURL": "https://api.jina.ai/v1",
            "dimensions": 1024,
            "taskQuery": "retrieval.query",
            "taskPassage": "retrieval.passage",
            "normalized": True
        },
        "autoCapture": True,
        "autoRecall": True,
        "retrieval": {
            "mode": "hybrid",
            "vectorWeight": 0.7,
            "bm25Weight": 0.3,
            "minScore": 0.3,
            "rerank": "cross-encoder",
            "rerankApiKey": jina_key,
            "rerankModel": "jina-reranker-v2-base-multilingual",
            "candidatePoolSize": 20,
            "recencyHalfLifeDays": 14,
            "recencyWeight": 0.1,
            "filterNoise": True,
            "hardMinScore": 0.35,
            "timeDecayHalfLifeDays": 60
        },
        "enableManagementTools": False,
        "sessionMemory": {
            "enabled": False,
            "messageCount": 15
        }
    }
}

# Set memory slot
if "slots" not in plugins:
    plugins["slots"] = {}
plugins["slots"]["memory"] = "memory-lancedb-pro"

with open(config_path, "w") as f:
    json.dump(cfg, f, indent=2)

print("Config updated successfully")
PYEOF

echo "--- Step 4: Restart gateway ---"
systemctl --user restart openclaw-gateway
sleep 2

if systemctl --user is-active --quiet openclaw-gateway; then
  echo "Gateway restarted successfully"
else
  echo "WARNING: Gateway may not be running. Check: journalctl --user -u openclaw-gateway --no-pager -n 20"
fi

echo "--- Done ---"
REMOTE_EOF
)

# Replace placeholder with actual key
REMOTE_SCRIPT="${REMOTE_SCRIPT//__JINA_API_KEY__/$JINA_API_KEY}"

SUCCESS=0
FAIL=0
FAILED_SERVERS=()

for i in "${!TARGETS[@]}"; do
  IP="${TARGETS[$i]}"
  NAME="${TARGET_NAMES[$i]}"
  echo "============================================"
  echo "[$((i+1))/${#TARGETS[@]}] $NAME ($IP)"
  echo "============================================"

  if ssh $SSH_OPTS "root@$IP" "$REMOTE_SCRIPT" 2>&1; then
    echo ""
    echo "  => $NAME: SUCCESS"
    ((SUCCESS++))
  else
    echo ""
    echo "  => $NAME: FAILED"
    ((FAIL++))
    FAILED_SERVERS+=("$IP ($NAME)")
  fi
  echo ""
done

echo "============================================"
echo "SUMMARY: $SUCCESS succeeded, $FAIL failed out of ${#TARGETS[@]}"
echo "============================================"

if [ ${#FAILED_SERVERS[@]} -gt 0 ]; then
  echo ""
  echo "Failed servers:"
  for s in "${FAILED_SERVERS[@]}"; do
    echo "  - $s"
  done
  echo ""
  echo "To retry a single server:"
  echo "  JINA_API_KEY=$JINA_API_KEY $0 <IP>"
fi
