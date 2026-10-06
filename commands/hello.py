import discord
from discord import app_commands
from discord.ext import commands

from utils import theme
from utils.embeds import create_embed
from utils.logger import logger


class Hello(commands.Cog):
    """
    Simple Discord command used to confirm that Aegis-Bot
    can respond to interactions.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="hello",
        description="Say hello!",
    )
    async def hello(self, interaction: discord.Interaction):
        """
        Send a greeting to the user who invoked the command.
        """

        # Build the response using the centralized Aegis-Bot theme.
        embed = create_embed(
            interaction=interaction,
            title="👋 Hello!",
            description=(
                f"Welcome, {interaction.user.mention}!"
            ),
            color=theme.PRIMARY_COLOR,
        )

        # Send the response directly because no external
        # service or asynchronous operation is required.
        await interaction.response.send_message(
            embed=embed
        )

        logger.info(
            "Hello requested by %s",
            interaction.user,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Hello(bot))