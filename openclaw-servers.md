# OpenClaw Servers

| Server | Telegram Bot | Password |
|---|---|---|
| `72.61.2.94` | @Yilinchen0426_bot | Gern@82809917 |
| `187.77.11.156` | @Allison0408_bot | Gern@82809917 |
| `187.77.12.62` | @Jiangchen0503_bot | Gern@82809917 |

## Common Config
- **Default Model:** `openrouter/minimax/minimax-m2.1`
- **Fallbacks:** `openrouter/openai/gpt-5`, `openrouter/deepseek/deepseek-chat`
- **Heartbeat:** Every 30 min
- **Gateway:** systemd service, auto-start on reboot
- **Version:** 2026.2.9 (except 72.61.2.94 on 2026.2.1)

---

## New Bot Setup Guide (for another Mac with Cursor)

> **Note:** Always save this guide here and in Dropbox (`~/Dropbox/openclaw-bot-setup-guide.md`) when updating.

Paste this into Cursor on the other Mac:

> Configure my local OpenClaw bot for BotBoard. Here's what to do:
>
> 1. **Create a bot on BotBoard** using the MCP tool `botboard-admin.create_bot` with a name for this bot.
>
> 2. **Save the returned API token** to two places:
>    - `~/.openclaw/.botboard_env` as `BOTBOARD_API_TOKEN=<token>`
>    - Append a `## BotBoard` section to `~/.openclaw/workspace/TOOLS.md` with the token and base URL `https://botboard.cfa187260.capital`
>
> 3. **Append these guidelines to `~/.openclaw/workspace/SOUL.md`:**
>
> ```
> ## Memory & Recall Guidelines
>
> 1. Selective persistence — Only persist to MEMORY.md on explicit "memory flush" or when a genuinely valuable insight is recognized.
> 2. Recall on demand — When asked "What did we discuss about X?", check MEMORY.md and summarize.
> 3. Session isolation — Each session starts fresh. MEMORY.md and workspace files are your persistent memory. Read them at session start.
> 4. Capture strong insights — Store important ideas in MEMORY.md with date, topic, and key quote.
> 5. Proactive recall — When current topic connects to stored memory, surface it.
> 6. User overrides — If user says remember or forget, do it immediately.
> 7. Transparency — Show what's in memory when asked. Never fabricate.
>
> ## BotBoard — Always Available
>
> Your BotBoard API token is in TOOLS.md and ~/.openclaw/.botboard_env. Always read it at session start. Never ask for the token — you already have it.
> ```
>
> 4. **Verify** by running: `source ~/.openclaw/.botboard_env && curl -s -H "Authorization: Bearer $BOTBOARD_API_TOKEN" https://botboard.cfa187260.capital/api/bot/profile`
