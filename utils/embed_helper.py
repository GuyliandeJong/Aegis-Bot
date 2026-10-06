import discord


def get_health_colour(health: str) -> discord.Color:
    """Return a Discord colour based on the health state."""

    health = health.upper()

    if health == "OK":
        return discord.Color.green()

    if health in ("WARNING", "WARN"):
        return discord.Color.orange()

    return discord.Color.red()


def get_health_icon(health: str) -> str:
    """Return an emoji based on the health state."""

    health = health.upper()

    if health == "OK":
        return "🟢"

    if health in ("WARNING", "WARN"):
        return "🟠"

    return "🔴"


def create_status_embed(title: str, health: str) -> discord.Embed:
    """Create a standard status embed."""

    embed = discord.Embed(
        title=title,
        colour=get_health_colour(health),
    )

    embed.add_field(
        name="Health",
        value=f"{get_health_icon(health)} {health}",
        inline=False,
    )

    return embed