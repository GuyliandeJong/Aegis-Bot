"""qBittorrent torrent information command implementation."""

import discord

from commands.torrent.views import TorrentControlView
from services.qbittorrent_service import QBittorrentService
from utils import theme
from utils.embed_models import EmbedField
from utils.embeds import create_embed
from utils.logger import logger


class TorrentMatchView(discord.ui.View):
    """
    Dropdown used to select one torrent from multiple search results.
    """

    MAX_OPTIONS = 25

    def __init__(
        self,
        interaction: discord.Interaction,
        matches: list[dict],
        query: str,
        torrent_cog,
    ):
        super().__init__(timeout=180)

        self.owner_id = interaction.user.id
        self.matches = matches
        self.query = query
        self.torrent_cog = torrent_cog
        self.message: discord.Message | None = None

        options = []

        for torrent in matches[: self.MAX_OPTIONS]:
            torrent_hash = str(
                torrent.get(
                    "hash",
                    "",
                )
            )

            if not torrent_hash:
                continue

            name = str(
                torrent.get(
                    "name",
                    "Unknown torrent",
                )
            )

            category = str(
                torrent.get(
                    "category",
                    "Unknown",
                )
            )

            state = torrent_cog._format_state(
                torrent.get(
                    "state",
                    "unknown",
                )
            )

            progress = float(
                torrent.get(
                    "progress",
                    0,
                )
            )

            label = torrent_cog._truncate_name(
                name,
                85,
            )

            if category:
                label = f"{label} [{category}]"

            description = (
                f"{progress * 100:.0f}% • "
                f"{state}"
            )

            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=torrent_hash,
                    description=description[:100],
                )
            )

        self.select = discord.ui.Select(
            placeholder="Select a torrent...",
            min_values=1,
            max_values=1,
            options=options,
        )

        self.select.callback = self._select_torrent
        self.add_item(self.select)

    async def _check_owner(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        """Ensure only the original requester can use the selector."""

        if interaction.user.id == self.owner_id:
            return True

        await interaction.response.send_message(
            "Only the person who requested this torrent "
            "search can select a torrent.",
            ephemeral=True,
        )

        return False

    async def _select_torrent(
        self,
        interaction: discord.Interaction,
    ):
        """Display the exact torrent selected from the dropdown."""

        if not await self._check_owner(interaction):
            return

        selected_hash = self.select.values[0]

        await interaction.response.defer()

        try:
            torrent = await self.torrent_cog._fetch_torrent_details(
                selected_hash
            )

            if torrent is None:
                await interaction.edit_original_response(
                    content=(
                        "That torrent could no longer be found "
                        "in qBittorrent."
                    ),
                    embed=None,
                    view=None,
                )
                return

            embed = self.torrent_cog._build_info_embed(
                interaction,
                torrent,
            )

            control_view = TorrentControlView(
                owner_id=interaction.user.id,
                torrent_hash=selected_hash,
                torrent_name=torrent.get(
                    "name",
                    "Unknown torrent",
                ),
                torrent=torrent,
                torrent_cog=self.torrent_cog,
            )

            message = await interaction.edit_original_response(
                content=None,
                embed=embed,
                view=control_view,
            )

            control_view.message = message
            control_view.start_refresh()

            self.stop()

            logger.info(
                "[Torrent] Selected torrent '%s' "
                "(hash=%s) from multiple matches for %s.",
                torrent.get(
                    "name",
                    "Unknown torrent",
                ),
                selected_hash,
                interaction.user,
            )

        except Exception as ex:
            logger.exception(
                "[TorrentMatchView] Failed to select torrent: %s",
                ex,
            )

            await interaction.followup.send(
                "Unable to retrieve the selected torrent.",
                ephemeral=True,
            )

    async def on_timeout(self):
        """Disable the selector after it expires."""

        self.select.disabled = True

        try:
            if self.message is not None:
                await self.message.edit(
                    view=self,
                )
        except discord.HTTPException:
            pass


class TorrentInfoMixin:
    """Mixin providing detailed torrent information."""

    async def _autocomplete_info_query(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[discord.app_commands.Choice[str]]:
        """
        Return torrent-name autocomplete suggestions.

        Only Aegis-Bot managed categories are exposed.
        """

        service = QBittorrentService()

        try:
            torrents = await service.get_torrents()

        except Exception as ex:
            logger.warning(
                "[Torrent] Autocomplete search failed: %s",
                ex,
            )
            return []

        finally:
            await service.close()

        torrents = [
            torrent
            for torrent in torrents
            if torrent.get("category")
            in self.ALLOWED_CATEGORIES
        ]

        selected_category = getattr(
            interaction.namespace,
            "category",
            None,
        )

        if selected_category is not None:
            if isinstance(
                selected_category,
                discord.app_commands.Choice,
            ):
                selected_category = selected_category.value

            if selected_category in self.ALLOWED_CATEGORIES:
                torrents = [
                    torrent
                    for torrent in torrents
                    if torrent.get("category")
                    == selected_category
                ]

        search = (
            current or ""
        ).strip().casefold()

        if search:
            matches = [
                torrent
                for torrent in torrents
                if search
                in str(
                    torrent.get(
                        "name",
                        "",
                    )
                ).casefold()
            ]

        else:
            matches = torrents

        matches.sort(
            key=lambda torrent: (
                not str(
                    torrent.get(
                        "name",
                        "",
                    )
                ).casefold().startswith(search),
                str(
                    torrent.get(
                        "name",
                        "",
                    )
                ).casefold(),
            )
        )

        suggestions = []

        for torrent in matches[:25]:
            name = str(
                torrent.get(
                    "name",
                    "Unknown torrent",
                )
            )

            category = str(
                torrent.get(
                    "category",
                    "Unknown",
                )
            )

            display_name = self._truncate_name(
                name,
                82,
            )

            display = f"{display_name} [{category}]"

            # Discord limits choice values to 100 characters.
            #
            # Normal torrent names are returned directly so selecting
            # one keeps the friendly torrent name in the query field.
            #
            # Extremely long names use the torrent hash instead. This
            # allows those torrents to still be resolved exactly.

            torrent_hash = str(
                torrent.get(
                    "hash",
                    "",
                )
            )

            if len(name) <= 100:
                value = name

            elif torrent_hash:
                value = f"hash:{torrent_hash}"

            else:
                value = name[:100]

            suggestions.append(
                discord.app_commands.Choice(
                    name=display[:100],
                    value=value,
                )
            )

        return suggestions

    async def _fetch_torrent_details(
        self,
        torrent_hash: str,
    ) -> dict | None:
        """Retrieve one torrent directly by qBittorrent hash."""

        service = QBittorrentService()

        try:
            details = await service.get_torrent_info(
                torrent_hash
            )

        finally:
            await service.close()

        if not details:
            return None

        return details[0]

    async def _info(
        self,
        interaction: discord.Interaction,
        category: str | None,
        query: str | None,
    ):
        """Find a torrent and display detailed information."""

        await interaction.response.defer()

        if not query or not query.strip():
            await interaction.followup.send(
                "Please provide a torrent name or part of a "
                "torrent name using the **Query** field."
            )
            return

        if (
            category is not None
            and category not in self.ALLOWED_CATEGORIES
        ):
            await interaction.followup.send(
                "That qBittorrent category is not available "
                "through Aegis-Bot."
            )
            return

        search_query = query.strip().lower()

        # Autocomplete can use the hash directly for exceptionally
        # long torrent names.
        if search_query.startswith("hash:"):
            torrent_hash = query.strip()[5:].strip()

            if not torrent_hash:
                await interaction.followup.send(
                    "The selected torrent did not contain a valid hash."
                )
                return

            try:
                torrent = await self._fetch_torrent_details(
                    torrent_hash
                )

            except Exception as ex:
                logger.exception(
                    "[Torrent] Failed to retrieve selected torrent: %s",
                    ex,
                )

                await interaction.followup.send(
                    "Unable to retrieve the selected torrent."
                )
                return

            if torrent is None:
                await interaction.followup.send(
                    "That torrent could no longer be found in qBittorrent."
                )
                return

            await self._send_info_result(
                interaction,
                torrent,
            )

            return

        service = QBittorrentService()

        try:
            # Retrieve all torrents and filter the category locally.
            #
            # This keeps Aegis-Bot's category behavior consistent
            # with /torrent list.

            torrents = await service.get_torrents()

        except Exception as ex:
            logger.exception(
                "[Torrent] Failed to search torrents: %s",
                ex,
            )

            await interaction.followup.send(
                "Unable to search the qBittorrent torrent list."
            )

            return

        finally:
            await service.close()

        torrents = [
            torrent
            for torrent in torrents
            if torrent.get("category")
            in self.ALLOWED_CATEGORIES
        ]

        if category is not None:
            torrents = [
                torrent
                for torrent in torrents
                if torrent.get("category")
                == category
            ]

        matches = [
            torrent
            for torrent in torrents
            if search_query
            in str(
                torrent.get(
                    "name",
                    "",
                )
            ).lower()
        ]

        if not matches:
            await interaction.followup.send(
                f"No torrent matching `{query}` was found."
            )

            logger.info(
                "[Torrent] No torrent matched info query '%s' "
                "from %s.",
                query,
                interaction.user,
            )

            return

        # Exactly one match: display it immediately.
        if len(matches) == 1:
            torrent_hash = matches[0].get(
                "hash"
            )

            if not torrent_hash:
                await interaction.followup.send(
                    "The matching torrent did not contain a valid "
                    "qBittorrent hash."
                )
                return

            try:
                torrent = await self._fetch_torrent_details(
                    torrent_hash
                )

            except Exception as ex:
                logger.exception(
                    "[Torrent] Failed to retrieve torrent details: %s",
                    ex,
                )

                await interaction.followup.send(
                    "Unable to retrieve the torrent details from "
                    "qBittorrent."
                )

                return

            if torrent is None:
                await interaction.followup.send(
                    "qBittorrent could not find the torrent anymore."
                )
                return

            await self._send_info_result(
                interaction,
                torrent,
            )

            return

        # Multiple matches: let the user select the exact torrent.
        matches.sort(
            key=lambda torrent: (
                not str(
                    torrent.get(
                        "name",
                        "",
                    )
                ).lower().startswith(search_query),
                str(
                    torrent.get(
                        "name",
                        "",
                    )
                ).lower(),
            )
        )

        embed = self._build_match_embed(
            interaction,
            matches,
            query,
        )

        view = TorrentMatchView(
            interaction=interaction,
            matches=matches,
            query=query,
            torrent_cog=self,
        )

        message = await interaction.followup.send(
            embed=embed,
            view=view,
            wait=True,
        )

        view.message = message

        logger.info(
            "[Torrent] Info query '%s' returned %s matches "
            "for %s. Selection view displayed.",
            query,
            len(matches),
            interaction.user,
        )

    async def _send_info_result(
        self,
        interaction: discord.Interaction,
        torrent: dict,
    ):
        """Display detailed information for one exact torrent."""

        torrent_hash = torrent.get(
            "hash"
        )

        if not torrent_hash:
            await interaction.followup.send(
                "The torrent did not contain a valid "
                "qBittorrent hash."
            )
            return

        embed = self._build_info_embed(
            interaction,
            torrent,
        )

        view = TorrentControlView(
            owner_id=interaction.user.id,
            torrent_hash=torrent_hash,
            torrent_name=torrent.get(
                "name",
                "Unknown torrent",
            ),
            torrent=torrent,
            torrent_cog=self,
        )

        message = await interaction.followup.send(
            embed=embed,
            view=view,
            wait=True,
        )

        view.message = message
        view.start_refresh()

        logger.info(
            "[Torrent] Detailed information requested for '%s' "
            "by %s.",
            torrent.get(
                "name",
                "Unknown",
            ),
            interaction.user,
        )

    def _build_info_embed(
        self,
        interaction: discord.Interaction | None,
        torrent: dict,
    ) -> discord.Embed:
        """Build the detailed torrent information embed."""

        name = torrent.get(
            "name",
            "Unknown torrent",
        )

        state = self._format_state(
            torrent.get(
                "state",
                "unknown",
            )
        )

        progress = float(
            torrent.get(
                "progress",
                0,
            )
        )

        category = torrent.get(
            "category",
            "Uncategorized",
        )

        size = self._format_bytes(
            torrent.get(
                "size",
                0,
            )
        )

        downloaded = self._format_bytes(
            torrent.get(
                "downloaded",
                0,
            )
        )

        uploaded = self._format_bytes(
            torrent.get(
                "uploaded",
                0,
            )
        )

        remaining = self._format_bytes(
            torrent.get(
                "amount_left",
                torrent.get(
                    "amountLeft",
                    0,
                ),
            )
        )

        download_speed = self._format_speed(
            torrent.get(
                "dlspeed",
                0,
            )
        )

        upload_speed = self._format_speed(
            torrent.get(
                "upspeed",
                0,
            )
        )

        ratio = float(
            torrent.get(
                "ratio",
                0,
            )
        )

        seeds = torrent.get(
            "num_seeds",
            torrent.get(
                "seeds",
                0,
            ),
        )

        peers = torrent.get(
            "num_leechs",
            torrent.get(
                "peers",
                0,
            ),
        )

        eta = self._format_eta(
            torrent.get(
                "eta",
                0,
            )
        )

        save_path = torrent.get(
            "save_path",
            torrent.get(
                "savePath",
                "Unknown",
            ),
        )

        torrent_hash = torrent.get(
            "hash",
            "Unknown",
        )

        tracker = torrent.get(
            "tracker",
            "",
        )

        added_time = torrent.get(
            "added_on",
            torrent.get(
                "addedOn",
                0,
            ),
        )

        fields = [
            EmbedField(
                "Category",
                category,
                True,
            ),
            EmbedField(
                "Status",
                state,
                True,
            ),
            EmbedField(
                "Progress",
                self._format_progress_bar(
                    progress
                ),
                False,
            ),
            EmbedField(
                "Size",
                size,
                True,
            ),
            EmbedField(
                "Remaining",
                remaining,
                True,
            ),
            EmbedField(
                "Download Speed",
                download_speed,
                True,
            ),
            EmbedField(
                "Upload Speed",
                upload_speed,
                True,
            ),
            EmbedField(
                "Downloaded",
                downloaded,
                True,
            ),
            EmbedField(
                "Uploaded",
                uploaded,
                True,
            ),
            EmbedField(
                "Ratio",
                f"{ratio:.2f}",
                True,
            ),
            EmbedField(
                "Seeds",
                str(seeds),
                True,
            ),
            EmbedField(
                "Peers",
                str(peers),
                True,
            ),
            EmbedField(
                "ETA",
                eta,
                True,
            ),
        ]

        if added_time:
            fields.append(
                EmbedField(
                    "Added",
                    self._format_added_time_absolute(
                        added_time
                    ),
                    True,
                )
            )

        fields.extend(
            [
                EmbedField(
                    "Save Path",
                    f"`{save_path}`",
                    False,
                ),
                EmbedField(
                    "Hash",
                    f"`{torrent_hash}`",
                    False,
                ),
            ]
        )

        if tracker:
            fields.append(
                EmbedField(
                    "Tracker",
                    self._truncate_name(
                        tracker,
                        200,
                    ),
                    False,
                )
            )

        if interaction is None:
            embed = discord.Embed(
                title=(
                    f"{theme.STORAGE_EMOJI} "
                    "qBittorrent — Torrent Info"
                ),
                description=name,
                color=discord.Color(
                    theme.PRIMARY_COLOR
                ),
            )

            embed.timestamp = discord.utils.utcnow()

            for field in fields:
                embed.add_field(
                    name=field.name,
                    value=field.value,
                    inline=field.inline,
                )

            return embed

        return create_embed(
            interaction=interaction,
            title=(
                f"{theme.STORAGE_EMOJI} "
                "qBittorrent — Torrent Info"
            ),
            description=name,
            fields=fields,
        )

    def _build_match_embed(
        self,
        interaction: discord.Interaction,
        matches: list[dict],
        query: str,
    ) -> discord.Embed:
        """Build the multiple-match selection embed."""

        display_matches = matches[:10]
        lines = []

        for torrent in display_matches:
            name = self._truncate_name(
                torrent.get(
                    "name",
                    "Unknown torrent",
                ),
                70,
            )

            category = torrent.get(
                "category",
                "Unknown",
            )

            progress = float(
                torrent.get(
                    "progress",
                    0,
                )
            )

            state = self._format_state(
                torrent.get(
                    "state",
                    "unknown",
                )
            )

            lines.append(
                f"**{name}**\n"
                f"{category} • "
                f"{progress * 100:.0f}% • "
                f"{state}"
            )

        if len(matches) > 10:
            lines.append(
                f"...and {len(matches) - 10} more."
            )

        description = (
            f"Found **{len(matches)}** torrents matching "
            f"`{query}`.\n\n"
            + "\n\n".join(lines)
            + "\n\n"
            "Select the exact torrent from the dropdown below."
        )

        if len(matches) > TorrentMatchView.MAX_OPTIONS:
            description += (
                "\n\n"
                f"The dropdown contains the first "
                f"{TorrentMatchView.MAX_OPTIONS} matches. "
                "Use a more specific query if the torrent "
                "you want is not listed."
            )

        return create_embed(
            interaction=interaction,
            title=(
                f"{theme.STORAGE_EMOJI} "
                "qBittorrent — Multiple Matches"
            ),
            description=description,
        )