import discord
from discord import app_commands
from discord.ext import commands

from modules.minecraft import minecraft_service
from utils import theme
from utils.embeds import create_embed


def server_line(server: dict) -> str:
    """One server as shown by /minecraft: status, name, version, players, latency."""
    name = discord.utils.escape_markdown(server["name"])

    if not server["up"]:
        return f"🔴 **{name}** · offline"

    line = (
        f"🟢 **{name}** · {discord.utils.escape_markdown(server['version'])} · "
        f"{server['online']}/{server['max']} players · {server['latency']} ms"
    )

    if server["players"]:
        names = ", ".join(discord.utils.escape_markdown(p) for p in server["players"])

        # Vanilla lists at most 12 names; say how many are left out.
        hidden = server["online"] - len(server["players"])

        if hidden > 0:
            names += f" and {hidden} more"

        line += f"\n└ {names}"

    return line


class Minecraft(commands.Cog):
    """`/minecraft`: status of the Minecraft servers in MINECRAFT_SERVERS."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="minecraft",
        description="Show the Minecraft servers and who is playing.",
    )
    async def minecraft(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not minecraft_service.configured():
            await interaction.followup.send(
                "No Minecraft servers are configured. Set MINECRAFT_SERVERS "
                "in .env.",
                ephemeral=True,
            )
            return

        servers = await minecraft_service.fetch_all()
        up = sum(1 for s in servers if s["up"])
        players = sum(s["online"] for s in servers if s["up"])
        lines = [server_line(s) for s in servers]

        embed = create_embed(
            interaction=interaction,
            title="⛏️ Minecraft",
            description=(
                f"{up} of {len(servers)} servers online · "
                f"{players} player{'s' if players != 1 else ''}\n\n"
                + "\n\n".join(lines)
            )[:4096],
            color=theme.INFO_COLOR if up else theme.ERROR_COLOR,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Minecraft(bot))
