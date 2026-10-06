"""Pure torrent-list filtering, sorting, pagination, and conversion helpers."""


from utils.logger import logger


def _build_pages(
    torrents: list[dict],
    selected_category: str | None,
    allowed_categories,
    torrents_per_page: int,
) -> list[dict]:
    """Build category-aware pagination pages."""

    pages = []

    if selected_category is not None:
        categories = [selected_category]
    else:
        categories = list(allowed_categories)

    for category in categories:
        category_torrents = [
            torrent
            for torrent in torrents
            if torrent.get("category") == category
        ]

        if not category_torrents:
            continue

        for start in range(
            0,
            len(category_torrents),
            torrents_per_page,
        ):
            end = start + torrents_per_page

            pages.append(
                {
                    "category": category,
                    "torrents": category_torrents[start:end],
                }
            )

    return pages


def _filter_torrents(
    torrents: list[dict],
    filter_name: str,
    active_states,
    seeding_states,
) -> list[dict]:
    """Filter torrents based on their qBittorrent state."""

    if filter_name == "all":
        return torrents

    if filter_name == "seeding":
        return [
            torrent
            for torrent in torrents
            if str(
                torrent.get(
                    "state",
                    "",
                )
            ).lower()
            in seeding_states
        ]

    return [
        torrent
        for torrent in torrents
        if str(
            torrent.get(
                "state",
                "",
            )
        ).lower()
        in active_states
    ]


def _sort_torrents(
    torrents: list[dict],
    sort_name: str,
) -> list[dict]:
    """
    Sort torrents according to the requested sort mode.

    The default mode preserves the existing Aegis-Bot ordering:

    active torrents first, followed by state priority and name.

    Explicit metric sorts use the direction encoded in the
    selected sort name.
    """

    if sort_name == "default":
        return sorted(
            torrents,
            key=_torrent_sort_key,
        )

    if sort_name == "name_asc":
        return sorted(
            torrents,
            key=lambda torrent: str(
                torrent.get(
                    "name",
                    "",
                )
            ).casefold(),
        )

    if sort_name == "name_desc":
        return sorted(
            torrents,
            key=lambda torrent: str(
                torrent.get(
                    "name",
                    "",
                )
            ).casefold(),
            reverse=True,
        )

    if sort_name == "size_desc":
        return sorted(
            torrents,
            key=lambda torrent: _safe_float(
                torrent.get(
                    "size",
                    0,
                )
            ),
            reverse=True,
        )

    if sort_name == "size_asc":
        return sorted(
            torrents,
            key=lambda torrent: _safe_float(
                torrent.get(
                    "size",
                    0,
                )
            ),
        )

    if sort_name == "progress_desc":
        return sorted(
            torrents,
            key=lambda torrent: _safe_float(
                torrent.get(
                    "progress",
                    0,
                )
            ),
            reverse=True,
        )

    if sort_name == "progress_asc":
        return sorted(
            torrents,
            key=lambda torrent: _safe_float(
                torrent.get(
                    "progress",
                    0,
                )
            ),
        )

    if sort_name == "download_speed_desc":
        return sorted(
            torrents,
            key=lambda torrent: _safe_float(
                torrent.get(
                    "dlspeed",
                    0,
                )
            ),
            reverse=True,
        )

    if sort_name == "download_speed_asc":
        return sorted(
            torrents,
            key=lambda torrent: _safe_float(
                torrent.get(
                    "dlspeed",
                    0,
                )
            ),
        )

    if sort_name == "upload_speed_desc":
        return sorted(
            torrents,
            key=lambda torrent: _safe_float(
                torrent.get(
                    "upspeed",
                    0,
                )
            ),
            reverse=True,
        )

    if sort_name == "upload_speed_asc":
        return sorted(
            torrents,
            key=lambda torrent: _safe_float(
                torrent.get(
                    "upspeed",
                    0,
                )
            ),
        )

    logger.warning(
        "[Torrent] Unknown sort mode '%s'. "
        "Falling back to default sorting.",
        sort_name,
    )

    return sorted(
        torrents,
        key=_torrent_sort_key,
    )


def _safe_float(
    value,
    default: float = 0.0,
) -> float:
    """Safely convert a value to float."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(
    value,
    default: int = 0,
) -> int:
    """Safely convert a value to int."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _torrent_sort_key(
    torrent: dict,
):
    """Sort torrents by activity first and name second."""

    state = str(
        torrent.get(
            "state",
            "",
        )
    ).lower()

    if state in {
        "downloading",
        "forceddl",
        "metadl",
    }:
        priority = 0

    elif state == "stalleddl":
        priority = 1

    elif state == "queuedl":
        priority = 2

    elif state in {
        "checkingdl",
        "checkingresumedata",
        "allocating",
    }:
        priority = 3

    elif state in {
        "pauseddl",
        "error",
        "missingfiles",
    }:
        priority = 4

    elif state in {
        "uploading",
        "forcedup",
    }:
        priority = 5

    elif state in {
        "stalledup",
        "queueup",
    }:
        priority = 6

    elif state in {
        "pausedup",
        "completed",
        "checkingup",
    }:
        priority = 7

    else:
        priority = 8

    name = str(
        torrent.get(
            "name",
            "",
        )
    ).casefold()

    return (
        priority,
        name,
    )