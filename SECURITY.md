# Security

Aegis-Bot holds credentials for the services it controls (qBittorrent,
Portainer, iDRAC, ESXi, Jellyfin) and can stop containers and remove
torrents. Please report a security problem privately.

## Reporting

Use **Report a vulnerability** on the repository's **Security** tab on
GitHub. Do not open a public issue for it.

Include what an attacker could do, the steps to reproduce it, and the
version or commit you tested.

## Scope

In scope: anything in this repository, such as a Discord user running
a command they should not be allowed to, a setting or token appearing
in a message or log, or a path or file name from another server
escaping the media folder.

Out of scope: the security of the services the bot talks to, and a
Discord server whose administrators grant the bot's commands to
everyone.

## Running it safely

- Keep `.env` readable only by you; it holds every password the bot
  uses.
- Give the bot accounts with the least rights that work: a read-only
  role on ESXi, a Portainer access token for one environment, and only
  the people who need it in `PORTAINER_ALLOWED_USER_IDS`.
- Keep administrator commands limited to administrators in Discord
  (Server Settings > Integrations).
