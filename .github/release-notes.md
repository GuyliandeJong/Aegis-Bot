Aegis-Bot is a self-hosted Discord bot for a home server. It posts alerts and answers slash commands about hardware (Dell iDRAC), ESXi, Docker containers (Portainer), qBittorrent, Jellyfin, Minecraft and more. Every feature is a module that switches itself on when its settings are filled in.

## Install

You don't need the source code: the image is `ghcr.io/{{OWNER}}/aegis-bot:{{VERSION}}` (amd64 and arm64).

1. Create a Discord application and bot, and invite it with the `bot` and `applications.commands` scopes; the [README](https://github.com/{{REPO}}#quick-start) lists the permissions it needs.
2. Download `docker-compose.yml` and `env.example` below into an empty folder, and rename `env.example` to `.env`.
3. In `.env`, set `DISCORD_TOKEN`, `GUILD_ID` and `ALERT_CHANNEL_ID`. In `docker-compose.yml`, set `TZ` to your time zone.
4. Run `docker compose up -d` and check `docker compose logs aegis-bot`. The log lists every module and the settings each one is missing.

Fill in the settings of the modules you want in `.env`, then run `docker compose up -d` again. Details: [README](https://github.com/{{REPO}}#readme) and [docs/features.md](https://github.com/{{REPO}}/blob/{{TAG}}/docs/features.md).

## Update

```bash
docker compose pull && docker compose up -d
```

The source code archives below are only needed to build the image yourself.
