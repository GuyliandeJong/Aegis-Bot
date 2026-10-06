import discord

from utils.embed_models import EmbedField
from utils.theme import PRIMARY_COLOR


def create_embed(
    interaction: discord.Interaction,
    title: str,
    description: str = "",
    color: int = PRIMARY_COLOR,
    fields: list[EmbedField] | None = None,
) -> discord.Embed:
    """
    Create a standardized Aegis-Bot Discord embed.

    All commands use this function so that embeds share the same
    timestamp, footer, field formatting, and general structure.
    """

    # Create the base embed using the supplied theme color.
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color(color),
    )

    # Show when the information was generated.
    embed.timestamp = discord.utils.utcnow()

    # Identify the user who requested the information.
    embed.set_footer(
        text=f"Requested by {interaction.user.display_name}",
        icon_url=interaction.user.display_avatar.url,
    )

    # Add command-specific information fields.
    if fields:
        for field in fields:
            embed.add_field(
                name=field.name,
                value=field.value,
                inline=field.inline,
            )

    return embed