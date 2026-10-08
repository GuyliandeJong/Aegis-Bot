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
