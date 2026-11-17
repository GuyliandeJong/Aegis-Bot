"""
/status: one look at the bot's health.

Which modules run, whether maintenance is on, the alerts of the last
24 hours, and any background monitor that is failing or has stopped
reporting. Administrators only by default; the reply is private.
"""

import time

import discord
from discord import app_commands
from discord.ext import commands

from core import activity_log, maintenance, modules
from core import monitor_manager
from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.formatting import format_compact_duration


STARTED = time.time()

# A monitor that has not finished a check for this many of its own
# intervals (and at least a minute) counts as stopped.
STALE_INTERVALS = 3

DAY = 86400


def monitor_problems(now: float | None = None) -> tuple[int, list[str]]:
    """(monitors seen, one line per failing or silent monitor)."""
    now = time.time() if now is None else now
    problems = []

    for name, entry in sorted(monitor_manager.HEALTH.items()):
        interval = float(entry.get("interval") or 60)
        last_ok = entry.get("last_ok")
        last_error = entry.get("last_error")

        if last_error and (not last_ok or last_error > last_ok):
            problems.append(
                f"⚠️ **{name}** failed {format_compact_duration(now - last_error)} ago: "
                f"{entry.get('error') or 'unknown error'}"
            )
        elif last_ok and now - last_ok > max(60, STALE_INTERVALS * interval):
            problems.append(
                f"⚠️ **{name}** has not finished a check for "
                f"{format_compact_duration(now - last_ok)}"
            )

    return len(monitor_manager.HEALTH), problems


def module_lines() -> tuple[str, str]:
    """(modules on, modules off with the reason)."""
    on, off = [], []

    for state in modules.statuses(modules.discover()):
        if state.enabled and modules.enabled(state.module.name):
            on.append(state.module.name)
        elif state.missing:
            off.append(f"{state.module.name} (set {', '.join(state.missing)})")
        else:
            off.append(f"{state.module.name} ({state.reason})")

    return " · ".join(on) or "none", "\n".join(off) or "none"


def alert_summary(now: float | None = None) -> str:
    now = time.time() if now is None else now
    entries = activity_log.since(now - DAY, "alert")

    if not entries:
        return "No alerts in the last 24 hours."

    counts: dict[str, int] = {}

    for entry in entries:
        level = entry.get("level") or "info"
        counts[level] = counts.get(level, 0) + 1

    order = ["critical", "warning", "recovered", "success", "info"]
    line = " · ".join(
        f"{counts[level]} {level}" for level in order if level in counts
    )
    recent = [
        f"• {entry.get('title')}" for entry in reversed(entries[-3:])
    ]

    return line + "\n" + "\n".join(recent)


class StatusCog(commands.Cog):
    """The /status command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="status",
        description="The bot's health: modules, maintenance, alerts and monitors.",
    )
    @app_commands.default_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        on, off = module_lines()
        seen, problems = monitor_problems()
        state = maintenance.current()

        if state and maintenance.active():
            maintenance_text = (
                f"On until <t:{state['until']}:t> (<t:{state['until']}:R>)"
                + (f": {state['reason']}" if state.get("reason") else "")
            )
        else:
            maintenance_text = "Off"

        if not seen:
            monitors_text = "No check has finished yet."
        elif problems:
            monitors_text = "\n".join(problems)[:1000]
        else:
            monitors_text = f"All {seen} monitors are running."

        latency = getattr(self.bot, "latency", None)
        bot_text = f"Up {format_compact_duration(time.time() - STARTED)}"

        if isinstance(latency, (int, float)) and latency == latency:
            bot_text += f" · Discord latency {latency * 1000:.0f} ms"

        fields = [
            EmbedField("Bot", bot_text, inline=False),
            EmbedField("Modules on", on, inline=False),
            EmbedField("Modules off", off, inline=False),
            EmbedField("Maintenance", maintenance_text, inline=False),
            EmbedField("Monitors", monitors_text, inline=False),
            EmbedField("Alerts, last 24 hours", alert_summary(), inline=False),
        ]

        await interaction.followup.send(
            embed=create_embed(
                interaction=interaction,
                title=(
                    f"{theme.WARNING_EMOJI} Status — Needs Attention"
                    if problems
                    else f"{theme.SUCCESS_EMOJI} Status — All Good"
                ),
                description="The bot's own view of its health.",
                color=theme.WARNING_COLOR if problems else theme.SUCCESS_COLOR,
                fields=fields,
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(StatusCog(bot))
