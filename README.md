# XESXOR Bot

Free, open-source, multi-platform community bot for **Discord and Telegram**
running simultaneously from one process. AI assistant, quizzes, Coins
economy, moderation, automated news, tickets, leveling and more — with full
feature parity across both platforms.

> **The bot is 100% free.** There is no paid tier, no premium currency and
> no external billing. Coins are *earned* through activity and spent on
> cosmetic roles.

## Features

| Module | Discord | Telegram |
|---|---|---|
| **XESXOR AI** (`/ask`) | ✅ slash command | ✅ `/ask` |
| **Quizzes** (`/quiz-redteam`, `-blueteam`, `-coding`, `-it`) | ✅ button answers | ✅ inline keyboard |
| **Economy** (`/balance` `/daily` `/gift` `/shop` `/buy` `/inventory` `/thanks`) | ✅ | ✅ |
| **Contribution awards** (`/award-contribution`, staff only) | ✅ | ✅ |
| **Moderation** (`/warn` auto-escalate, `/purge`) | ✅ | ✅ |
| **Automod** (slurs, invites, token leaks, caps, repeat spam) | ✅ | ✅ |
| **Leveling** (XP, level-ups, message milestones, voice XP) | ✅ full | ✅ chat XP |
| **News briefings** (AI, twice per interval) | `#coding-news` `#cyber-news` | configured chat ids |
| **Tickets** (`/ticket`) | private channels | staff-chat relay |
| **Suggestions / Events** | ✅ | ✅ |
| **Weekly Signal** (`/question`) | `#signal-inbox` | staff-chat relay |
| **Verification panel** | `/setup-verify` button | `/setup-verify` + `/verify` |
| **Self-roles panel** | `/setup-roles` (real roles) | `/setup-roles` (interest tags) |
| **Invite tracking** | ✅ | — (no Telegram equivalent) |

## Architecture

```
main.py                     entrypoint: boots adapters, graceful shutdown
xesxor/
├── config/settings.py      env-driven settings, secret-safe
├── core/                   platform-agnostic business logic
│   ├── database.py         SQLite (schema v2, auto-migrates v1 data)
│   ├── context.py          CommandContext + platform capability contract
│   ├── registry.py         unified CommandSpec pattern
│   └── services/           ai, economy, quiz, moderation, automod,
│                           leveling, community, invites, news
├── commands/               command handlers (shared by both platforms)
├── adapters/               Discord and Telegram drivers
└── utils/                  logging (with secret redaction), helpers
```

**Rule:** adapters → commands → services → database.  Services never import
platform SDKs; every feature is declared once and compiled into Discord
slash commands *and* Telegram `/commands` automatically.

## Setup

### 1. Install

```bash
git clone https://github.com/d3v0o0nu11/XESXOR_BOT.git
cd XESXOR_BOT
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env    # then edit .env
```

### 2. Discord bot

1. Open <https://discord.com/developers/applications> → **New Application**.
2. **Bot** tab → **Add Bot** → copy the **Token** into `DISCORD_BOT_TOKEN`.
3. Enable **Message Content Intent** (Privileged Gateway Intents).
4. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`;
   permissions: *Manage Roles, Kick Members, Moderate Members, Manage
   Channels, Manage Messages, Send Messages, Read Message History*.
5. Open the generated URL and invite the bot to your server.

### 3. Telegram bot

1. In Telegram, open **@BotFather** → send `/newbot`.
2. Choose a display name and a unique username ending in `bot`.
3. Copy the API token BotFather gives you into `TELEGRAM_BOT_TOKEN`.
4. **Groups:** add the bot to a group and promote it to **administrator**
   (it needs *Delete messages* for automod/purge and *Restrict members*
   for `/warn` timeouts). Send `/setcommands` to BotFather to publish the
   command menu (optional).
5. **Optional verify gate:** set `TELEGRAM_REQUIRE_VERIFY=true` and grant
   the bot the *Restrict members* permission — new members stay muted
   until they press **✅ Verify** or send `/verify`.
6. **Optional staff relay:** add the bot to a staff group, forward a
   message from it to @getidsbot to learn the chat id, and set
   `TELEGRAM_STAFF_CHAT_ID`. Tickets, suggestions and Signal questions
   will be delivered there.
7. **Optional news:** add the bot to a channel or group, learn the chat
   id the same way, and set `TELEGRAM_NEWS_CHAT_IDS=<id>`.

### 4. AI key

Create a key at <https://aistudio.google.com/apikey> and set `AI_API_KEY`.
Without it, `/ask`, quizzes (fallback pool still works) and news degrade
gracefully.

### 5. Run

```bash
./venv/bin/python main.py
```

Configure **at least one** of the two platform tokens; both may run
simultaneously from the same process and share one economy database.

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `DISCORD_BOT_TOKEN` | — | Discord credentials (secret) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram credentials (secret) |
| `AI_API_KEY` | — | Gemini API key for XESXOR AI (secret) |
| `DATABASE_PATH` | `bot_database.db` | SQLite file location |
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `PORT` | `10000` | Keep-alive HTTP port |
| `KEEP_ALIVE_ENABLED` | `true` | Self-ping server for free-tier hosts |
| `DISCORD_AUTO_SETUP` | `false` | Bootstrap full server layout in every joined guild |
| `TELEGRAM_ADMIN_IDS` | — | Extra Telegram user ids treated as admins |
| `TELEGRAM_NEWS_CHAT_IDS` | — | Chats receiving AI news briefings |
| `TELEGRAM_STAFF_CHAT_ID` | — | Staff relay for tickets/suggestions/questions |
| `TELEGRAM_REQUIRE_VERIFY` | `false` | Mute new group members until verified |
| `NEWS_INTERVAL_HOURS` | `8` | Hours between news editions |

Secrets are only ever read from the environment; log output automatically
redacts anything token-shaped.  `DISCORD_AUTO_SETUP=true` is **not**
recommended for public deployments — it rewrites channel permissions in
every guild the bot joins.

## Deployment

### Docker (recommended)

```bash
cp .env.example .env   # fill in tokens
docker compose up -d --build
docker compose logs -f
```

The SQLite database persists in `./data/`.

### systemd (AWS EC2)

```bash
ssh -i key.pem ec2-user@<EC2_PUBLIC_IP>
sudo dnf install -y git
git clone https://github.com/d3v0o0nu11/XESXOR_BOT.git /opt/xesxor-bot
cd /opt/xesxor-bot
printf 'DISCORD_BOT_TOKEN=...\nTELEGRAM_BOT_TOKEN=...\nAI_API_KEY=...\n' > .env
chmod 600 .env
bash deploy/setup_ec2.sh
sudo systemctl start xesxor-bot
journalctl -u xesxor-bot -f
```

The service auto-starts on boot and restarts on crash (`Restart=always`).

### Render / Railway

Deploy as a Docker web service (or plain Python service running
`python main.py`), set the environment variables in the dashboard, and
keep `KEEP_ALIVE_ENABLED=true` so the free tier does not idle-sleep.

## Upgrading from v1 (monolithic `bot.py`)

* Point `DATABASE_PATH` at the old `bot_database.db` — the schema
  migrates automatically on first start (balances, inventory, warnings
  and quiz history are preserved).
* `DISCORD_TOKEN` is still accepted as an alias of `DISCORD_BOT_TOKEN`.
* Pro subscriptions and the PWN currency were removed: the bot is now
  completely free.  Remaining balances are Coins only.
* Server lockdown/bootstrap is now opt-in (`DISCORD_AUTO_SETUP`).

## Development

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env
./venv/bin/python main.py
python -m compileall xesxor main.py   # syntax check
```

`tools/test_speed.py` benchmarks AI model latency.

Adding a feature = writing one handler in `xesxor/commands/` and
registering a `CommandSpec`; it appears on both platforms automatically.

## License

MIT — use it in your own community.
