"""
Optional admin approval before a torrent is added.

Off by default. With REQUEST_APPROVAL=true in .env, a torrent chosen or
added by someone without administrator permission is not sent to
qBittorrent straight away: the message shows it as waiting, and an
administrator approves or rejects it there. Administrators' own
torrents are added as usual.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs, unquote_plus, urlparse

import discord

from config.settings import settings
from utils.logger import logger
from utils.open_messages import ExpiringView


def approval_required() -> bool:
    value = os.getenv("REQUEST_APPROVAL", "false")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_admin(user) -> bool:
    permissions = getattr(user, "guild_permissions", None)
    return bool(permissions is not None and permissions.administrator)


def magnet_name(source_url: str) -> str:
    """The display name in a magnet link, or a short placeholder."""
    try:
        query = parse_qs(urlparse(source_url).query)
        name = query.get("dn", [""])[0]
    except ValueError:
        name = ""

    return unquote_plus(name).strip() or "Unnamed torrent"


def _embed(title: str, description: str, color) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)


class ApprovalView(ExpiringView):
    """Approve / Reject for administrators, Cancel for the requester."""

    # A request nobody answers within a day expires.
    TIMEOUT = 24 * 60 * 60

    def __init__(self, cog, requester, category: str, source_url: str):
        super().__init__(timeout=self.TIMEOUT)

        self.cog = cog
        self.requester = requester
        self.category = category
        self.source_url = source_url
        self.name = magnet_name(source_url)
        self.message = None

        for label, style, callback in (
            ("Approve", discord.ButtonStyle.success, self._approve),
            ("Reject", discord.ButtonStyle.danger, self._reject),
            ("Cancel request", discord.ButtonStyle.secondary, self._cancel),
        ):
            button = discord.ui.Button(label=label, style=style)
            button.callback = callback
            self.add_item(button)

    def embed(self) -> discord.Embed:
        return _embed(
            "Waiting for approval",
            f"**{self.name}**\n"
            f"Category: **{self.category}**\n"
            f"Requested by {self.requester.mention}\n\n"
            "An administrator can approve or reject it below.",
            discord.Color.gold(),
        )

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=_embed(
                        "Request expired",
                        f"**{self.name}** was not approved within a day "
                        "and was not added.",
                        discord.Color.dark_grey(),
                    ),
                    view=None,
                )
            except discord.HTTPException:
                pass

    async def _approve(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "Only an administrator can approve requests.",
                ephemeral=True,
            )
            return

        self.stop()

        logger.info(
            "[Approval] %s approved '%s' for %s.",
            interaction.user,
            self.name,
            self.requester,
        )

        # The normal add, on behalf of the requester: they are
        # mentioned when it completes and own its controls.
        await self.cog._add(
            interaction,
            category=self.category,
            source_url=self.source_url,
            requester=self.requester,
            approved=True,
        )

    async def _reject(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "Only an administrator can reject requests.",
                ephemeral=True,
            )
            return

        self.stop()

        logger.info(
            "[Approval] %s rejected '%s' for %s.",
            interaction.user,
            self.name,
            self.requester,
        )

        await interaction.response.edit_message(
            content=self.requester.mention,
            embed=_embed(
                "Request rejected",
                f"**{self.name}** was not added. Rejected by "
                f"{interaction.user.mention}.",
                discord.Color.red(),
            ),
            view=None,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def _cancel(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                "Only the person who asked can cancel this request.",
                ephemeral=True,
            )
            return

        self.stop()

        await interaction.response.edit_message(
            embed=_embed(
                "Request cancelled",
                f"**{self.name}** was not added.",
                discord.Color.dark_grey(),
            ),
            view=None,
        )


async def ask_approval(cog, interaction: discord.Interaction, category: str, source_url: str) -> None:
    """Show the request as waiting and tell the administrators."""
    view = ApprovalView(cog, interaction.user, category, source_url)

    view.message = await interaction.edit_original_response(
        content=None,
        embed=view.embed(),
        view=view,
    )

    logger.info(
        "[Approval] %s asked for '%s' (%s).",
        interaction.user,
        view.name,
        category,
    )

    channel = interaction.client.get_channel(
        getattr(settings, "ALERT_CHANNEL_ID", 0)
    )

    if channel is not None and view.message is not None:
        try:
            await channel.send(
                embed=_embed(
                    "Torrent request waiting for approval",
                    f"{interaction.user.mention} asked for "
                    f"**{view.name}** ({category}).\n"
                    f"[Open the request]({view.message.jump_url})",
                    discord.Color.gold(),
                ),
            )
        except discord.HTTPException as ex:
            logger.warning("[Approval] Could not notify the alert channel: %s", ex)
