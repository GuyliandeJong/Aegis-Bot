"""`/torrent cleanup`: finished torrents by how long they have seeded."""

from __future__ import annotations

import os

import discord

from modules.torrents.qbittorrent_service import QBittorrentService
from utils import theme
from utils.embeds import create_embed
from utils.formatting import format_bytes, format_compact_duration
from utils.logger import logger
from utils.open_messages import ExpiringView


def _target(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def seeding_targets() -> tuple[float, float]:
    """(ratio, days) after which a torrent counts as seeded enough."""
    return (
        _target("SEED_RATIO_TARGET", 2.0),
        _target("SEED_DAYS_TARGET", 14.0),
    )


def finished_torrents(torrents: list[dict]) -> list[dict]:
    """
    Return finished torrents, those that reached a target first.

    Each gets a "done" flag: its ratio or its seeding time reached the
    target. Within each group the highest ratio comes first.
    """
    ratio_target, days_target = seeding_targets()
    finished = []

    for torrent in torrents:
        try:
            if float(torrent.get("progress") or 0) < 1.0:
                continue

            ratio = float(torrent.get("ratio") or 0)
            seeded = int(torrent.get("seeding_time") or 0)
        except (TypeError, ValueError):
            continue

        finished.append(
            {
                **torrent,
                "done": (
                    ratio >= ratio_target
                    or seeded >= days_target * 86400
                ),
            }
        )

    finished.sort(
        key=lambda t: (not t["done"], -float(t.get("ratio") or 0)),
    )

    return finished


def seeded_enough(torrents: list[dict]) -> list[dict]:
    """
    The finished torrents that reached a seeding target, highest ratio
    first: the ones `/torrent cleanup` marks ✅. The weekly cleanup
    suggestion (`modules/torrents/seed_cleanup_monitor.py`) offers these.
    """
    return [t for t in finished_torrents(torrents) if t["done"]]


PAGE_SIZE = 25


def page_count(finished: list[dict]) -> int:
    return max(1, -(-len(finished) // PAGE_SIZE))


def cleanup_embed(finished: list[dict], page: int = 0, chosen=()) -> discord.Embed:
    ratio_target, days_target = seeding_targets()
    done = [t for t in finished if t["done"]]
    shown = finished[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    lines = []

    for torrent in shown:
        mark = "✅" if torrent["done"] else "⏳"
        picked = " ☑️" if str(torrent.get("hash")) in chosen else ""
        lines.append(
            f"{mark} **{str(torrent.get('name'))[:60]}**{picked}\n"
            f"ratio {float(torrent.get('ratio') or 0):.2f} · "
            f"seeded {format_compact_duration(torrent.get('seeding_time'))} · "
            f"{format_bytes(torrent.get('size'))}"
        )

    summary = (
        f"{len(finished)} finished torrent(s); {len(done)} reached "
        f"ratio {ratio_target:g} or {days_target:g} days of seeding "
        f"({format_bytes(sum(int(t.get('size') or 0) for t in done))})."
    )

    if chosen:
        summary += f"\n**{len(chosen)} chosen** across all pages."

    embed = discord.Embed(
        title="Seeding overview",
        description=(summary + "\n\n" + "\n".join(lines))[:4096],
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Page {page + 1} of {page_count(finished)}")

    return embed


class CleanupView(ExpiringView):
    """
    Choose finished torrents and remove them, with or without files.

    Shows 25 torrents per page. What is chosen on one page stays
    chosen when moving to another.
    """

    def __init__(self, owner_id: int, finished: list[dict]):
        super().__init__(timeout=600)

        self.owner_id = owner_id
        self.finished = finished
        self.torrents = {
            str(t.get("hash")): t
            for t in finished
            if t.get("hash")
        }
        self.chosen: list[str] = []
        self.page = 0
        self.message = None

        self.select = discord.ui.Select(
            placeholder="Choose torrents to remove…",
            min_values=0,
            row=0,
        )
        self.select.callback = self._choose
        self.add_item(self.select)

        self.keep_button = discord.ui.Button(
            label="Remove, keep files",
            style=discord.ButtonStyle.primary,
            row=1,
        )
        self.keep_button.callback = self._remove_keep
        self.add_item(self.keep_button)

        self.files_button = discord.ui.Button(
            label="Remove with files…",
            style=discord.ButtonStyle.danger,
            row=1,
        )
        self.files_button.callback = self._ask_files
        self.add_item(self.files_button)

        self.previous_button = discord.ui.Button(
            label="◀ Previous",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.previous_button.callback = self._previous
        self.add_item(self.previous_button)

        self.next_button = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        self.next_button.callback = self._next
        self.add_item(self.next_button)

        self._fill_page()

    def _page_torrents(self) -> list[dict]:
        return [
            t
            for t in self.finished[
                self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE
            ]
            if t.get("hash")
        ]

    def _fill_page(self) -> None:
        """Show the current page's torrents in the picker."""
        shown = self._page_torrents()

        self.select.options = [
            discord.SelectOption(
                label=str(t.get("name") or "Unknown")[:100],
                description=(
                    f"{'✅' if t['done'] else '⏳'} ratio "
                    f"{float(t.get('ratio') or 0):.2f} · "
                    f"{format_bytes(t.get('size'))}"
                )[:100],
                value=str(t.get("hash")),
                default=str(t.get("hash")) in self.chosen,
            )
            for t in shown
        ]
        self.select.max_values = max(1, len(shown))

        self.keep_button.disabled = not self.chosen
        self.files_button.disabled = not self.chosen
        self.previous_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= page_count(self.finished) - 1

    def embed(self) -> discord.Embed:
        return cleanup_embed(self.finished, self.page, self.chosen)

    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user.id == self.owner_id

    async def _show(self, interaction: discord.Interaction) -> None:
        self._fill_page()
        await interaction.response.edit_message(
            content=None,
            embed=self.embed(),
            view=self,
        )

    async def _choose(self, interaction: discord.Interaction):
        # Replace this page's part of the choice; keep other pages'.
        on_page = {str(t.get("hash")) for t in self._page_torrents()}
        self.chosen = [h for h in self.chosen if h not in on_page]
        self.chosen += list(self.select.values)

        await self._show(interaction)

    async def _previous(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        await self._show(interaction)

    async def _next(self, interaction: discord.Interaction):
        self.page = min(page_count(self.finished) - 1, self.page + 1)
        await self._show(interaction)

    async def _remove_keep(self, interaction: discord.Interaction):
        await self._remove(interaction, delete_files=False)

    async def _ask_files(self, interaction: discord.Interaction):
        names = ", ".join(
            f"**{str(self.torrents[h].get('name'))[:60]}**"
            for h in self.chosen[:5]
        )

        if len(self.chosen) > 5:
            names += f" and {len(self.chosen) - 5} more"

        await interaction.response.edit_message(
            content=(
                f"Delete {len(self.chosen)} torrent(s) **and their "
                f"files** for good? {names}"
            ),
            view=_ConfirmFilesView(self),
        )

    async def _remove(
        self,
        interaction: discord.Interaction,
        delete_files: bool,
    ):
        self.stop()

        await interaction.response.edit_message(
            content="Removing…",
            view=None,
        )

        service = QBittorrentService()
        removed, failed = [], []

        try:
            for torrent_hash in self.chosen:
                name = str(self.torrents[torrent_hash].get("name"))

                try:
                    await service.delete_torrent(
                        torrent_hash,
                        delete_files=delete_files,
                    )
                    removed.append(name)
                except Exception as ex:
                    logger.warning(
                        "[Cleanup] Could not remove %s: %s",
                        name,
                        ex,
                    )
                    failed.append(name)
        finally:
            await service.close()

        logger.info(
            "[Cleanup] %s removed %d torrent(s) (delete_files=%s).",
            interaction.user,
            len(removed),
            delete_files,
        )

        text = (
            f"Removed {len(removed)} torrent(s)"
            + (" and their files." if delete_files else "; files kept.")
        )

        if failed:
            text += f"\nCould not remove: {', '.join(failed)}"

        await interaction.edit_original_response(content=text, embed=None)


class _ConfirmFilesView(ExpiringView):
    def __init__(self, parent: CleanupView):
        super().__init__(timeout=60)

        self.parent = parent

        confirm = discord.ui.Button(
            label="Delete torrents and files",
            style=discord.ButtonStyle.danger,
        )
        confirm.callback = self._confirm
        self.add_item(confirm)

        back = discord.ui.Button(
            label="Back",
            style=discord.ButtonStyle.secondary,
        )
        back.callback = self._back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user.id == self.parent.owner_id

    async def _confirm(self, interaction: discord.Interaction):
        self.stop()
        await self.parent._remove(interaction, delete_files=True)

    async def _back(self, interaction: discord.Interaction):
        self.stop()
        await self.parent._show(interaction)


def _torrents_word(count: int) -> str:
    return f"{count} torrent{'s' if count != 1 else ''}"


class SeedCleanupView(ExpiringView):
    """
    The buttons under the weekly cleanup suggestion in the alert channel.

    "Remove these torrents (keep files)" removes the suggested torrents
    from qBittorrent and leaves their files on disk; "Dismiss" only
    closes the suggestion. Either way the message is edited to say what
    happened. Only server administrators may press them.
    """

    # The next suggestion comes a week later; two days is long enough
    # to see this one.
    TIMEOUT = 2 * 24 * 60 * 60

    restart_note = (
        "The bot restarted, so these buttons no longer work. "
        "Use `/torrent cleanup` to remove these torrents."
    )

    def __init__(self, torrents: list[dict]):
        super().__init__(timeout=self.TIMEOUT)

        self.torrents = [t for t in torrents if t.get("hash")]

        remove = discord.ui.Button(
            label="Remove these torrents (keep files)",
            style=discord.ButtonStyle.primary,
        )
        remove.callback = self._remove
        self.add_item(remove)

        dismiss = discord.ui.Button(
            label="Dismiss",
            style=discord.ButtonStyle.secondary,
        )
        dismiss.callback = self._dismiss
        self.add_item(dismiss)

    async def interaction_check(self, interaction: discord.Interaction):
        permissions = getattr(interaction.user, "guild_permissions", None)

        if permissions is not None and permissions.administrator:
            return True

        await interaction.response.send_message(
            "Only a server administrator can use these buttons.",
            ephemeral=True,
        )
        return False

    async def _dismiss(self, interaction: discord.Interaction):
        self.stop()

        await interaction.response.edit_message(
            content=None,
            embed=create_embed(
                interaction,
                title="Cleanup suggestion dismissed",
                description=(
                    f"{_torrents_word(len(self.torrents))} left in "
                    "qBittorrent. `/torrent cleanup` shows them again."
                ),
                color=theme.NEUTRAL_COLOR,
            ),
            view=None,
        )

    async def _remove(self, interaction: discord.Interaction):
        self.stop()

        await interaction.response.edit_message(
            content=f"Removing {_torrents_word(len(self.torrents))}…",
            view=None,
        )

        service = QBittorrentService()
        removed, failed = [], []
        gone = 0

        try:
            # Read the list again: some may have been removed since
            # the suggestion was posted.
            current = {
                str(t.get("hash"))
                for t in await service.get_torrents()
            }

            for torrent in self.torrents:
                torrent_hash = str(torrent.get("hash"))
                name = str(torrent.get("name"))

                if torrent_hash not in current:
                    gone += 1
                    continue

                try:
                    await service.delete_torrent(
                        torrent_hash,
                        delete_files=False,
                    )
                    removed.append(torrent)
                except Exception as ex:
                    logger.warning(
                        "[Cleanup] Could not remove %s: %s",
                        name,
                        ex,
                    )
                    failed.append(name)

        except Exception as ex:
            logger.exception("[Cleanup] Could not list torrents: %s", ex)
            await interaction.edit_original_response(
                content=None,
                embed=create_embed(
                    interaction,
                    title="Cleanup failed",
                    description=(
                        "Unable to read the torrent list from "
                        "qBittorrent; nothing was removed. Use "
                        "`/torrent cleanup` to try again."
                    ),
                    color=theme.ERROR_COLOR,
                ),
            )
            return

        finally:
            await service.close()

        logger.info(
            "[Cleanup] %s removed %d suggested torrent(s), files kept.",
            interaction.user,
            len(removed),
        )

        size = sum(int(t.get("size") or 0) for t in removed)
        text = (
            f"Removed {_torrents_word(len(removed))}, "
            f"{format_bytes(size)} still on disk."
        )

        if gone:
            text += (
                f"\n{_torrents_word(gone)} had already been removed "
                "from qBittorrent."
            )

        if failed:
            text += f"\nCould not remove: {', '.join(failed)}"[:1500]

        await interaction.edit_original_response(
            content=None,
            embed=create_embed(
                interaction,
                title="Cleanup done",
                description=text,
                color=(
                    theme.WARNING_COLOR
                    if failed
                    else theme.SUCCESS_COLOR
                ),
            ),
        )

    async def on_timeout(self):
        if self.message is None:
            return

        try:
            await self.message.edit(
                content=(
                    "This suggestion expired. Use `/torrent cleanup` "
                    "to remove these torrents."
                ),
                view=None,
            )
        except discord.HTTPException:
            pass


class TorrentCleanupMixin:
    """Mixin providing `/torrent cleanup`."""

    async def _cleanup(self, interaction: discord.Interaction):
        permissions = getattr(interaction.user, "guild_permissions", None)

        if permissions is None or not permissions.administrator:
            await interaction.response.send_message(
                "Only a server administrator can clean up torrents.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        service = QBittorrentService()

        try:
            torrents = await service.get_torrents()
        except Exception as ex:
            logger.exception("[Cleanup] Could not list torrents: %s", ex)
            await interaction.followup.send(
                "Unable to read the torrent list from qBittorrent.",
                ephemeral=True,
            )
            return
        finally:
            await service.close()

        finished = finished_torrents(torrents)

        if not finished:
            await interaction.followup.send(
                "No finished torrents.",
                ephemeral=True,
            )
            return

        view = CleanupView(interaction.user.id, finished)

        await interaction.followup.send(
            embed=view.embed(),
            view=view,
            ephemeral=True,
        )
