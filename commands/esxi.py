import discord
from discord import app_commands
from discord.ext import commands

from monitors.esxi_monitor import EsxiMonitor
from services import esxi_service
from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.formatting import format_bytes, usage_bar
from utils.logger import logger


POWER = {
    "poweredOn": "🟢",
    "poweredOff": "⚫",
    "suspended": "🟡",
}


class Esxi(commands.Cog):
    """`/esxi`: datastores and virtual machines on the ESXi host."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="esxi",
        description="Show ESXi datastores and virtual machines.",
    )
    @app_commands.default_permissions(administrator=True)
    async def esxi(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not esxi_service.configured():
            await interaction.followup.send(
                "ESXi is not configured. Set ESXI_HOST, ESXI_USERNAME "
                "and ESXI_PASSWORD in .env.",
                ephemeral=True,
            )
            return

        try:
            status = await esxi_service.fetch_status()
        except Exception as ex:
            logger.exception("Failed to read ESXi: %s", ex)
            await interaction.followup.send(
                embed=create_embed(
                    interaction=interaction,
                    title=f"{theme.ERROR_EMOJI} ESXi Not Reachable",
                    description=f"`{str(ex)[:200]}`",
                    color=theme.ERROR_COLOR,
                ),
                ephemeral=True,
            )
            return

        stores = []

        for d in status["datastores"]:
            if not d["accessible"]:
                stores.append(f"**{d['name']}**: not accessible")
                continue

            used = d["capacity"] - d["free"]
            percent = round(used / d["capacity"] * 100) if d["capacity"] else 0
            stores.append(
                f"**{d['name']}**\n{usage_bar(percent)} {percent}% · "
                f"{format_bytes(d['free'])} free of {format_bytes(d['capacity'])}"
            )

        # VMs in ESXI_IGNORE_VMS get no alerts; say so, so a stopped one
        # does not look like a problem.
        ignored = EsxiMonitor.ignored_vms()
        vms = [
            f"{POWER.get(v['power'], '❔')} {v['name']}"
            + (" · not watched" if v["name"].lower() in ignored else "")
            for v in status["vms"]
        ]
        running = sum(1 for v in status["vms"] if v["power"] == "poweredOn")
        not_watched = sum(1 for v in status["vms"] if v["name"].lower() in ignored)
        summary = f"{running} of {len(vms)} virtual machines running"

        if not_watched:
            summary += f" · {not_watched} not watched (ESXI_IGNORE_VMS)"

        embed = create_embed(
            interaction=interaction,
            title="🖥️ ESXi",
            description=summary,
            color=theme.INFO_COLOR,
            fields=[
                EmbedField("💾 Datastores", "\n".join(stores)[:1024] or "None", inline=False),
                EmbedField("📦 Virtual Machines", "\n".join(vms)[:1024] or "None", inline=False),
            ],
        )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Esxi(bot))
