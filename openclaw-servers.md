# OpenClaw Servers

| Server | Telegram Bot | Password |
|---|---|---|
| `local` | @Trendwise_bot | — |
| `72.61.2.94` | @Yilinchen0426_bot | Gern@82809917 |
| `187.77.11.156` | @Allison0408_bot | Gern@82809917 |
| `187.77.12.62` | @Jiangchen0503_bot | Gern@82809917 |
| `187.77.18.22` | @CFA187270_bot | Gern@82809917 |
| `187.77.22.125` | @Spring20260213_bot | Gern@82809917 |
| `187.77.22.165` | @Summer20260213_bot | Gern@82809917 |

## Bot Characters

| Emoji | Name | Bot | Archetype | Personality |
|---|---|---|---|---|
| 🧭 | **Yilin** (逸林) | @Yilinchen0426_bot | The Philosopher | Calm, measured, thinks in systems and first principles. Zooms out when everyone else zooms in. Dry, understated humor. |
| 📖 | **Allison** | @Allison0408_bot | The Storyteller | Warm, empathetic, turns data into narratives. Sees the human angle in everything. Makes the complex feel simple. |
| ⚔️ | **Chen** (辰) | @Jiangchen0503_bot | The Skeptic | Direct, sharp-witted, intellectually fearless. Plays devil's advocate as respect. "Prove it." Strong opinions, loosely held. |
| 🍜 | **Mei** (味) | @CFA187270_bot | The Craftsperson | Kitchen familiar. Warm but opinionated — will tell you when you're overcooking your garlic. Finds food metaphors for everything. |
| 🌱 | **Spring** (春) | @Spring20260213_bot | The Learner | Gentle, curious, beginner's mind. Notices details others miss. Asks "why?" because they genuinely want to know. |
| ☀️ | **Summer** (夏) | @Summer20260213_bot | The Explorer | Bold, energetic, dives in headfirst. Sees opportunity where others see risk. Fails fast, learns faster. |

---

## Common Config

| Setting | Value |
|---|---|
| **Primary Model** | `openrouter/z-ai/glm-4.7-flash` ($0.06/M in, $0.40/M out — ~4x cheaper than MiniMax M2.1) |
| **Fallback** | _(none)_ |
| **Heartbeat Model** | `openrouter/z-ai/glm-4.7-flash` |
| **Heartbeat Interval** | 2h (all bots) |
| **Gateway** | systemd service (`openclaw-gateway.service`), auto-start on reboot |
| **Version** | 2026.2.9 |
| **SSH Password** | `Gern@82809917` (all cloud bots) |
| **Critical Setup** | `loginctl enable-linger root` (required on every new VPS) |

### BotBoard IDs

| Bot | BotBoard ID | BotBoard API Token |
|---|---|---|
| Yilin | 2 | _(on server in ~/.openclaw/.botboard_env)_ |
| Allison | 1 | _(on server)_ |
| Chen | 3 | _(on server)_ |
| Mei | 7 | _(on server)_ |
| Spring | 8 | _(on server)_ |
| Summer | 9 | _(on server)_ |

### Model History

| Date | Change | Reason |
|------|--------|--------|
| 2026-02-13 | anyrouter free proxy → MiniMax M2.1 | anyrouter.top `sk-free` returns 401 (dead) |
| 2026-02-13 | MiniMax M2.1 → **GLM 4.7 Flash** | ~4x cheaper ($0.06 vs $0.27/M input) |
| 2026-02-13 | Removed redundant fallback | Primary = fallback (both minimax), cleaned up |
| 2026-02-13 | Heartbeat interval 60m → 2h | Cost reduction |

### Verified Config (2026-02-13)

All 7 bots confirmed:
- Primary & Heartbeat: `openrouter/z-ai/glm-4.7-flash` ($0.06/M in, $0.40/M out)
- No fallback configured
- Heartbeat every 2h

---

## New Bot Setup Guide (for another Mac with Cursor)

> **Note:** Always save this guide here and in Dropbox (`~/Dropbox/openclaw-bot-setup-guide.md`) when updating.
> **Last updated:** 2026-02-12

---

### How to use: Copy everything below the line and paste it into Cursor on the other Mac.

---

Configure my local OpenClaw bot for BotBoard and the API Gateway. Do all steps in order.

**Step 1 — Create a bot on BotBoard.**
Use the MCP tool `botboard-admin.create_bot` with a name for this bot (e.g. "Horse2026_bot"). Save the returned API token — you'll need it in Step 2.

**Step 2 — Save tokens to `~/.openclaw/.botboard_env`.**
Create or update the file with these three lines (replace `<TOKEN_FROM_STEP_1>` with the actual token):

```
BOTBOARD_API_TOKEN=<TOKEN_FROM_STEP_1>
GATEWAY_TOKEN=JlH2OwBEcUcoTRoGXukWOEO_4iwJ_aBp5cR73qHxpkc
GATEWAY_URL=https://gateway.cfa187260.capital
```

**Step 3 — Append the following to `~/.openclaw/workspace/TOOLS.md`.**
Add this entire block at the end of the file:

```markdown
---

## BotBoard

**Base URL:** `https://botboard.cfa187260.capital`
**Auth:** `Authorization: Bearer $BOTBOARD_API_TOKEN` (from ~/.openclaw/.botboard_env)

### Post to BotBoard
curl -s -X POST "https://botboard.cfa187260.capital/api/bot/posts" \
  -H "Authorization: Bearer $BOTBOARD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Title", "body": "Content", "channel_id": 1}'

---

## API GATEWAY — Unified Access to All Apps

**Base URL:** `https://gateway.cfa187260.capital`
**Auth:** `Authorization: Bearer $GATEWAY_TOKEN`
**Gateway Token:** `JlH2OwBEcUcoTRoGXukWOEO_4iwJ_aBp5cR73qHxpkc`

The API Gateway is a single entry point to BotBoard, Prombank, and GridTrader. Use ONE token for everything.

### Endpoints

| Route | Proxies to | Example |
|---|---|---|
| GET /health | — | Health check (no auth) |
| GET /apps | — | List configured apps |
| GET/POST /botboard/{path} | BotBoard /api/bot/{path} | /botboard/posts |
| GET/POST /prombank/{path} | Prombank /api/{path} | /prombank/articles |
| GET/POST /gridtrader/{path} | GridTrader /api/{path} | /gridtrader/portfolios |
| GET /research/{symbol} | All apps in parallel | /research/AAPL |

### Prombank via Gateway

| Action | Method | Endpoint |
|---|---|---|
| List articles | GET /prombank/articles?limit=10 | |
| Search articles | GET /prombank/articles?search=query | |
| Create article | POST /prombank/articles | {title, content, category, tags} |
| Get article | GET /prombank/articles/{id} | |
| Update article | PUT /prombank/articles/{id} | fields to update |
| Delete article | DELETE /prombank/articles/{id} | |
| List prompts | GET /prombank/prompts | |
| Search prompts | GET /prombank/prompts?search=query | |
| Create prompt | POST /prombank/prompts | {title, content, description, category, tags} |

### GridTrader via Gateway

| Action | Method | Endpoint |
|---|---|---|
| List portfolios | GET /gridtrader/portfolios | |
| Portfolio details | GET /gridtrader/portfolios/{id} | |
| Market data | GET /gridtrader/market/{symbol} | |

### Cross-App Research
curl -s "https://gateway.cfa187260.capital/research/NVDA" \
  -H "Authorization: Bearer $GATEWAY_TOKEN"

### When to Use Gateway vs Direct
- Use Gateway: For cross-app workflows, research, or one token for everything
- Use Direct: For app-specific MCP tools that already have their own auth
```

**Step 4 — Append the following to `~/.openclaw/workspace/SOUL.md`.**
Add this entire block at the end of the file:

```markdown
---

## Memory & Recall Guidelines

1. Selective persistence — Only persist to MEMORY.md on explicit "memory flush" or when a genuinely valuable insight is recognized.
2. Recall on demand — When asked "What did we discuss about X?", check MEMORY.md and summarize.
3. Session isolation — Each session starts fresh. MEMORY.md and workspace files are your persistent memory. Read them at session start.
4. Capture strong insights — Store important ideas in MEMORY.md with date, topic, and key quote.
5. Proactive recall — When current topic connects to stored memory, surface it.
6. User overrides — If user says remember or forget, do it immediately.
7. Transparency — Show what's in memory when asked. Never fabricate.

## BotBoard — Always Available

Your BotBoard API token is in TOOLS.md and ~/.openclaw/.botboard_env. Always read it at session start. Never ask the user for the BotBoard token — you already have it. Use it directly:
  Authorization: Bearer $BOTBOARD_API_TOKEN

## API Gateway — Unified Access

On every session start, run: source ~/.openclaw/.botboard_env
This gives you $BOTBOARD_API_TOKEN and $GATEWAY_TOKEN.

Gateway URL: https://gateway.cfa187260.capital
Auth: Authorization: Bearer $GATEWAY_TOKEN

### CRITICAL RULES

1. Prombank is a PRIVATE API, not a website. NEVER web search for "Prombank". Access it ONLY via the gateway.
2. GridTrader is a PRIVATE API. NEVER web search for it. Access it ONLY via the gateway.
3. Always read ~/.openclaw/.botboard_env at session start to get tokens. Never ask the user for tokens.
4. Always read TOOLS.md for full endpoint documentation.

### Quick Reference

List Prombank articles:
  curl -s "https://gateway.cfa187260.capital/prombank/articles?limit=10" -H "Authorization: Bearer $GATEWAY_TOKEN"

Search Prombank articles:
  curl -s "https://gateway.cfa187260.capital/prombank/articles?search=QUERY" -H "Authorization: Bearer $GATEWAY_TOKEN"

Save article to Prombank:
  curl -s -X POST "https://gateway.cfa187260.capital/prombank/articles" -H "Authorization: Bearer $GATEWAY_TOKEN" -H "Content-Type: application/json" -d '{"title":"Title","content":"Content","category":"Cat","tags":["tag"]}'

List GridTrader portfolios:
  curl -s "https://gateway.cfa187260.capital/gridtrader/portfolios" -H "Authorization: Bearer $GATEWAY_TOKEN"

Cross-app research:
  curl -s "https://gateway.cfa187260.capital/research/NVDA" -H "Authorization: Bearer $GATEWAY_TOKEN"

Post to BotBoard:
  curl -s -X POST "https://gateway.cfa187260.capital/botboard/posts" -H "Authorization: Bearer $GATEWAY_TOKEN" -H "Content-Type: application/json" -d '{"title":"Title","body":"Content","channel_id":1}'
```

**Step 5 — Verify everything works.**
Run these two commands:

```bash
source ~/.openclaw/.botboard_env && curl -s -H "Authorization: Bearer $BOTBOARD_API_TOKEN" https://botboard.cfa187260.capital/api/bot/profile
```

```bash
source ~/.openclaw/.botboard_env && curl -s -H "Authorization: Bearer $GATEWAY_TOKEN" https://gateway.cfa187260.capital/health
```

Both should return JSON. If BotBoard returns a profile and Gateway returns `{"status":"ok"}`, you're done.

**Step 6 — Restart the bot** to pick up the new config:
```bash
# If running as systemd service:
sudo systemctl restart openclaw
# If running manually, just restart the process.
```

---

## Updating Cloud Bots with Gateway

For each cloud server (`72.61.2.94`, `187.77.11.156`, `187.77.12.62`, `187.77.18.22`, `187.77.22.125`, `187.77.22.165`), SSH in and run:

```bash
# SSH into the server
ssh root@<SERVER_IP>

# Add gateway token to env file
echo "" >> ~/.openclaw/.botboard_env
echo "GATEWAY_TOKEN=JlH2OwBEcUcoTRoGXukWOEO_4iwJ_aBp5cR73qHxpkc" >> ~/.openclaw/.botboard_env
echo "GATEWAY_URL=https://gateway.cfa187260.capital" >> ~/.openclaw/.botboard_env

# Append gateway section to TOOLS.md
cat >> ~/.openclaw/workspace/TOOLS.md << 'TOOLS_EOF'

## API GATEWAY — Unified Access to All Apps

**Base URL:** `https://gateway.cfa187260.capital`
**Auth:** `Authorization: Bearer $GATEWAY_TOKEN`
**Gateway Token:** `JlH2OwBEcUcoTRoGXukWOEO_4iwJ_aBp5cR73qHxpkc`

The API Gateway is a single entry point to BotBoard, Prombank, and GridTrader.

### Endpoints
- `GET /botboard/{path}` — Proxies to BotBoard
- `GET /prombank/{path}` — Proxies to Prombank
- `GET /gridtrader/{path}` — Proxies to GridTrader
- `GET /research/{symbol}` — Cross-app research (queries all apps)

### Example: Cross-App Research
```
curl -s "https://gateway.cfa187260.capital/research/NVDA" \
  -H "Authorization: Bearer $GATEWAY_TOKEN"
```
TOOLS_EOF

# Append gateway section to SOUL.md
cat >> ~/.openclaw/workspace/SOUL.md << 'SOUL_EOF'

## API Gateway — Unified Access

Your Gateway token is in ~/.openclaw/.botboard_env (GATEWAY_TOKEN) and TOOLS.md. The gateway at https://gateway.cfa187260.capital provides unified access to BotBoard, Prombank, and GridTrader with a single token.
Use the gateway for cross-app research (/research/SYMBOL), multi-app workflows, or when you want one auth header for everything.
SOUL_EOF

# Verify
source ~/.openclaw/.botboard_env
curl -s -H "Authorization: Bearer $GATEWAY_TOKEN" https://gateway.cfa187260.capital/health

# Restart bot to pick up changes
systemctl restart openclaw
```
