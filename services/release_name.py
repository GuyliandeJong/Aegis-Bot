"""Read title, year, season and episode from a torrent release name."""

import re

# Where the title ends: a year, a season/episode marker, or a quality tag.
_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_EPISODE = re.compile(r"\bS(\d{1,2})[ ._-]?E(\d{1,3})\b", re.IGNORECASE)
_SEASON = re.compile(r"\b(?:S(\d{1,2})|Season[ ._-]?(\d{1,2}))\b", re.IGNORECASE)
_STOP = re.compile(
    r"\b(2160p|1080p|720p|576p|480p|4k|uhd|hdr|web[ ._-]?dl|webrip|"
    r"bluray|blu[ ._-]?ray|brrip|bdrip|dvdrip|hdtv|remux|x26[45]|h[ ._]?26[45]|"
    r"hevc|complete|multi|repack|proper|extended)\b",
    re.IGNORECASE,
)


def parse(name: str) -> dict:
    """
    Split a release name such as "The.Dark.Knight.Rises.2012.1080p…" or
    "Fallout.S02E03.1080p…" into its parts.

    Returns {"title", "year", "season", "episode"}; the last three are
    None when the name does not contain them. A season pack such as
    "Fallout.S02.1080p" has a season and no episode.
    """
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", lambda m: " " + m.group(0)[1:-1] + " ", str(name or ""))
    text = re.sub(r"\.(mkv|mp4|avi)$", "", text, flags=re.IGNORECASE)

    cut = len(text)
    year = season = episode = None

    match = _EPISODE.search(text)
    if match:
        season, episode = int(match.group(1)), int(match.group(2))
        cut = min(cut, match.start())
    else:
        match = _SEASON.search(text)
        if match:
            season = int(match.group(1) or match.group(2))
            cut = min(cut, match.start())

    match = _STOP.search(text)
    if match and match.start() > 0:
        cut = min(cut, match.start())

    # The release year is the last year before the quality tags, never
    # the first word: "2001 A Space Odyssey 1968" and "Blade Runner
    # 2049 2017" keep the years that belong to their titles.
    years = [
        match
        for match in _YEAR.finditer(text)
        if 0 < match.start() < cut
    ]

    if years:
        year = int(years[-1].group(1))
        cut = years[-1].start()

    title = re.sub(r"[._]+", " ", text[:cut])
    title = re.sub(r"\s+", " ", title).strip(" -")

    return {"title": title, "year": year, "season": season, "episode": episode}


# ----------------------------------------------------------------
# PC games and music
#
# Their release names follow other conventions than movies and
# series: tags in brackets, versions, repackers and scene groups.
# These turn them into a readable title for lists such as the weekly
# summary; they never decide anything, so a leftover word is harmless.
# ----------------------------------------------------------------

_YEAR_ANYWHERE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")

_PC_STOP = re.compile(
    r"\b(v\d[\w.]*|\d+\.\d+[\w.]*|build[ ._-]?\d+|multi\d*|repack|fitgirl|dodi|elamigos|"
    r"gog|rune|codex|tenoki|flt|skidrow|empress|razor1911|plaza|cpy|"
    r"update[ ._-]?\d*|\d+[ ._-]?dlcs?|dlcs?|bonus(?:es)?|portable|"
    r"selective|incl|ost|soundtrack|crack(?:ed)?|setup|iso)\b",
    re.IGNORECASE,
)

_MUSIC_STOP = re.compile(
    r"\b(flac|mp3|alac|aac|ogg|opus|wav|320|256|192|v0|v2|\d+[ ._-]?kbps|"
    r"\d+[ ._-]?bit|\d+[ ._-]?khz|24[ ._-]?96|24[ ._-]?192|16[ ._-]?44|"
    r"hi[ ._-]?res|lossless|web|cdda|cd|vinyl)\b",
    re.IGNORECASE,
)


def _brackets_out(name: str) -> tuple[str, int | None]:
    """Drop [tags] and (tags); return the rest and a year found in them."""
    year = None

    for inside in re.findall(r"[\[(]([^\])]*)[\])]", name):
        match = _YEAR_ANYWHERE.search(inside)

        if match and year is None:
            year = int(match.group(1))

    return re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", name), year


def _spaces(text: str) -> str:
    # Dots and underscores separate words in scene names, but keep
    # the dot in a version or number such as 2.1.
    text = re.sub(r"(?<!\d)[._]|[._](?!\d)", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -:·")


def pc_title(name) -> str:
    """
    "Cyberpunk.2077.v2.1-GOG" becomes "Cyberpunk 2077",
    "[DL] Castlevania: Belmont's Curse [P] [RUS + ENG] (2026, Action)"
    becomes "Castlevania: Belmont's Curse (2026)".
    """
    text, year = _brackets_out(str(name or ""))
    text = re.sub(r"\.(iso|zip|rar|7z|exe)$", "", text.strip(), flags=re.IGNORECASE)

    # A scene group or uploader glued on with a hyphen: "-InsaneRamZes".
    text = re.sub(r"(?<=\w)-[A-Za-z0-9]+$", "", text.strip())
    text = _spaces(text)

    match = _PC_STOP.search(text)

    if match and match.start() > 0:
        text = text[:match.start()]

    # Release names lose apostrophes: "Belmont s Curse".
    text = re.sub(r"\b(\w+) s\b", r"\1's", text)
    text = re.sub(r"\s+", " ", text).strip(" -:·")

    if not text:
        return str(name or "Unknown")

    return f"{text} ({year})" if year else text


def music_title(name) -> str:
    """
    "Daft_Punk-Random_Access_Memories-WEB-2013-GROUP" becomes
    "Daft Punk - Random Access Memories (2013)",
    "Adele - 30 (2021) [FLAC 24-96]" becomes "Adele - 30 (2021)".
    """
    raw = str(name or "")
    text, year = _brackets_out(raw)
    text = text.strip()

    # Scene style: Artist-Album-Source-Year-Group, no spaces.
    if " " not in text and text.count("-") >= 2:
        parts = [part for part in text.split("-") if part]
        parts = parts[:-1]  # the group
        kept = []

        for part in parts:
            if _YEAR_ANYWHERE.fullmatch(part):
                year = year or int(part)
                continue

            if _MUSIC_STOP.fullmatch(part.replace("_", " ")):
                continue

            kept.append(_spaces(part))

        if len(kept) >= 2:
            text = f"{kept[0]} - {' '.join(kept[1:])}"
        elif kept:
            text = kept[0]
    else:
        text = _spaces(text)
        match = _MUSIC_STOP.search(text)

        if match and match.start() > 0:
            text = text[:match.start()]

        # The release year is the last one; an earlier one can be the
        # album's name ("Taylor Swift - 1989 2023"), and one at the very
        # start belongs to the artist.
        years = [m for m in _YEAR_ANYWHERE.finditer(text) if m.start() > 0]

        if years and not re.search(r"-\s*$", text[:years[-1].start()]):
            year = year or int(years[-1].group(1))
            text = text[:years[-1].start()]

        text = re.sub(r"\s+", " ", text).strip(" -:·")

    if not text:
        return raw or "Unknown"

    return f"{text} ({year})" if year else text
