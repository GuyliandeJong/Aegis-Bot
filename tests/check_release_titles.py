"""Readable names for the weekly summary: PC games and music release names, next to the movie and series names, by torrent category."""
import sys
import types

m = types.ModuleType("utils.logger"); m.logger = types.SimpleNamespace(warning=print, error=print, info=print); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(); sys.modules["config.settings"] = c

from modules.general.weekly_summary_monitor import readable_title

CASES = [
    ("PC", "Castlevania Belmonts Curse-InsaneRamZes"),
    ("PC", "Castlevania Belmont s Curse Midnight Edition 3 DLCs Bonuses MULTi11 FitGirl Repack Selective Download from 4 1-GB"),
    ("PC", "[DL] Castlevania: Belmont’s Curse [P] [RUS + ENG + 9 / ENG + 1] (2026, Action) (25766122 + 3 DLC) [Portable]"),
    ("PC", "Cyberpunk.2077.v2.1-GOG"),
    ("PC", "Baldurs.Gate.3-RUNE"),
    ("PC", "Elden Ring [FitGirl Repack]"),
    ("PC", "Hollow Knight Silksong (v1.0.28324) [GOG]"),
    ("PC", "Stardew_Valley_1.6.15"),
    ("Music", "Daft_Punk-Random_Access_Memories-WEB-2013-GROUP"),
    ("Music", "Adele - 30 (2021) [FLAC 24-96]"),
    ("Music", "Taylor Swift - 1989 (Taylor's Version) 2023 320kbps"),
    ("Music", "Taylor Swift - 1989"),
    ("Music", "Pink Floyd - The Dark Side of the Moon (1973) [24bit Hi-Res]"),
    ("Music", "Radiohead.-.OK.Computer.1997.FLAC"),
    ("Music", "Metallica - Discography"),
    ("Movies", "Inception.2010.1080p.BluRay.x265"),
    ("Series", "Fallout.S02E03.1080p.WEB-DL"),
    (None, "Some.Unknown.Thing.2020"),
]

for category, name in CASES:
    print(f"  {str(category):6} {readable_title(name, category)}")
