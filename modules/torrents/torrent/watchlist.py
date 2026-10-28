"""The watchlist: titles that were not found yet, checked again later."""

from __future__ import annotations

import discord

from modules.torrents import watchlist
from utils.logger import logger
from utils.open_messages import ExpiringView


class WatchlistAddView(ExpiringView):
    """The "Watch for it" button under a search that found nothing."""

    def __init__(
        self,
        owner_id: int,
        query: str,
        category: str,
        hidden: int = 0,
    ):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.query = query
        self.category = category
        # Releases left out of the search (cam, other categories).
        self.hidden = hidden
        self.message = None

        button = discord.ui.Button(
            label="Watch for it",
            style=discord.ButtonStyle.primary,
        )
        button.callback = self._add
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id == self.owner_id:
            return True

        await interaction.response.send_message(
            "Only the person who searched can add this to their "
            "watchlist. Search for it yourself to watch for it too.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def _add(self, interaction: discord.Interaction):
        self.stop()

        added, reason = watchlist.add(
            self.query,
            self.category,
            interaction.user.id,
            getattr(interaction.channel, "id", 0),
        )

        if added:
            logger.info(
                "[Watchlist] %s is watching for '%s' (%s).",
                interaction.user,
                self.query,
                self.category,
            )
            found = (
                "Nothing usable found for"
                if self.hidden
                else "Nothing found for"
            )
            text = (
                f"{found} **{self.query}** yet. It is on "
                "your watchlist now: the bot checks Jellyfin and "
                "Jackett twice a day and mentions you when it turns "
                "up. `/torrent watchlist` shows your list."
            )
        else:
            text = reason

        await interaction.response.edit_message(content=text, view=None)


class WatchlistFoundView(ExpiringView):
    """The "Show results" button under a watchlist notification."""

    def __init__(self, bot, entry: dict):
        # Long enough to see it the next morning; a restart marks it
        # as expired like any other message.
        super().__init__(timeout=24 * 60 * 60)

        self.bot = bot
        self.entry = entry
        self.message = None

        button = discord.ui.Button(
            label="Show results",
            style=discord.ButtonStyle.success,
        )
        button.callback = self._show
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction):
        if str(interaction.user.id) == self.entry["user_id"]:
            return True

        await interaction.response.send_message(
            "This was found for someone else. Run `/torrent search` "
            "to see the results yourself.",
            ephemeral=True,
        )
        return False

    async def _show(self, interaction: discord.Interaction):
        self.stop()

        cog = self.bot.get_cog("Torrent")

        if cog is None:
            await interaction.response.send_message(
                "The torrent commands are not loaded.",
                ephemeral=True,
            )
            return

        # The normal search: Jellyfin first, then Jackett, with the
        # usual buttons. It replaces this notification.
        await cog._search(
            interaction,
            query=self.entry["query"],
            category=self.entry["category"],
        )


class WatchlistRemoveView(ExpiringView):
    """`/torrent watchlist`: a picker to remove titles."""

    def __init__(self, owner_id: int, entries: list[dict]):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.entries = {entry["id"]: entry for entry in entries[:25]}
        self.message = None

        self.select = discord.ui.Select(
            placeholder="Remove titles from your watchlist…",
            min_values=1,
            max_values=len(self.entries),
            options=[
                discord.SelectOption(
                    label=entry["query"][:100],
                    description=entry["category"],
                    value=entry["id"],
                )
                for entry in self.entries.values()
            ],
        )
        self.select.callback = self._remove
        self.add_item(self.select)

    async def _remove(self, interaction: discord.Interaction):
        self.stop()

        removed = [
            entry["query"]
            for entry_id in self.select.values
            if (entry := watchlist.remove(entry_id)) is not None
        ]

        await interaction.response.edit_message(
            content=(
                "Removed: " + ", ".join(f"**{q}**" for q in removed)
                if removed
                else "Nothing was removed."
            ),
            embed=None,
            view=None,
        )


def watchlist_embed(entries: list[dict]) -> discord.Embed:
    lines = [
        f"• **{entry['query']}** ({entry['category']}), "
        f"since <t:{entry['added']}:D>"
        for entry in entries
    ]

    return discord.Embed(
        title="Your watchlist",
        description=(
            "\n".join(lines)
            + "\n\nChecked twice a day on Jellyfin and Jackett."
        ),
        color=discord.Color.blurple(),
    )


class TorrentWatchlistMixin:
    """Mixin providing `/torrent watchlist`."""

    async def _watchlist(self, interaction: discord.Interaction):
        entries = watchlist.for_user(interaction.user.id)

        if not entries:
            await interaction.response.send_message(
                "Your watchlist is empty. When `/torrent search` finds "
                "nothing, press **Watch for it** to add the title; "
                "under a list of results, **Watch for a better "
                "release** does the same and ignores those results.",
                ephemeral=True,
            )
            return

        view = WatchlistRemoveView(interaction.user.id, entries)

        await interaction.response.send_message(
            embed=watchlist_embed(entries),
            view=view,
            ephemeral=True,
        )
