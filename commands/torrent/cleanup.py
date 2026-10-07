"""`/torrent cleanup`: finished torrents by how long they have seeded."""

from __future__ import annotations

import os

import discord

from services.qbittorrent_service import QBittorrentService
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


def cleanup_embed(finished: list[dict]) -> discord.Embed:
    ratio_target, days_target = seeding_targets()
    done = [t for t in finished if t["done"]]

    lines = []

    for torrent in finished[:25]:
        mark = "✅" if torrent["done"] else "⏳"
        lines.append(
            f"{mark} **{str(torrent.get('name'))[:70]}**\n"
            f"ratio {float(torrent.get('ratio') or 0):.2f} · "
            f"seeded {format_compact_duration(torrent.get('seeding_time'))} · "
            f"{format_bytes(torrent.get('size'))} · "
            f"{torrent.get('category') or 'Uncategorized'}"
        )

    if len(finished) > 25:
        lines.append(f"…and {len(finished) - 25} more.")

    summary = (
        f"{len(finished)} finished torrent(s); {len(done)} reached "
        f"ratio {ratio_target:g} or {days_target:g} days of seeding "
        f"({format_bytes(sum(int(t.get('size') or 0) for t in done))})."
    )

    return discord.Embed(
        title="Seeding overview",
        description=summary + "\n\n" + "\n".join(lines),
        color=discord.Color.blurple(),
    )


class CleanupView(ExpiringView):
    """Choose finished torrents and remove them, with or without files."""

    def __init__(self, owner_id: int, finished: list[dict]):
        super().__init__(timeout=300)

        self.owner_id = owner_id
        self.torrents = {
            str(t.get("hash")): t
            for t in finished[:25]
            if t.get("hash")
        }
        self.chosen: list[str] = []
        self.message = None

        self.select = discord.ui.Select(
            placeholder="Choose torrents to remove…",
            min_values=1,
            max_values=max(1, len(self.torrents)),
            options=[
                discord.SelectOption(
                    label=str(t.get("name") or "Unknown")[:100],
                    description=(
                        f"{'✅' if t['done'] else '⏳'} ratio "
                        f"{float(t.get('ratio') or 0):.2f} · "
                        f"{format_bytes(t.get('size'))}"
                    )[:100],
                    value=torrent_hash,
                )
                for torrent_hash, t in self.torrents.items()
            ],
        )
        self.select.callback = self._choose
        self.add_item(self.select)

        self.keep_button = discord.ui.Button(
            label="Remove, keep files",
            style=discord.ButtonStyle.primary,
            disabled=True,
        )
        self.keep_button.callback = self._remove_keep
        self.add_item(self.keep_button)

        self.files_button = discord.ui.Button(
            label="Remove with files…",
            style=discord.ButtonStyle.danger,
            disabled=True,
        )
        self.files_button.callback = self._ask_files
        self.add_item(self.files_button)

    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user.id == self.owner_id

    async def _choose(self, interaction: discord.Interaction):
        self.chosen = list(self.select.values)

        for option in self.select.options:
            option.default = option.value in self.chosen

        self.keep_button.disabled = not self.chosen
        self.files_button.disabled = not self.chosen

        await interaction.response.edit_message(view=self)

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
        await interaction.response.edit_message(
            content=None,
            view=self.parent,
        )


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

        await interaction.followup.send(
            embed=cleanup_embed(finished),
            view=CleanupView(interaction.user.id, finished),
            ephemeral=True,
        )
