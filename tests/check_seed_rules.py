"""Seeding targets per category (SEED_CATEGORY_RULES): parsing, a category without a rule keeping the global targets, each torrent judged by its own category, the wording in the overview, and the setting being empty or damaged."""
import logging
import os
import sys
import types

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
c = types.ModuleType("config.settings"); c.settings = types.SimpleNamespace(ALERT_CHANNEL_ID=10); sys.modules["config.settings"] = c
for name in ("SEED_RATIO_TARGET", "SEED_DAYS_TARGET", "SEED_CATEGORY_RULES"):
    os.environ.pop(name, None)

from modules.torrents.torrent import cleanup as CL
import isolate  # state files go to a temporary folder

DAY = 86400


def torrent(name, category, ratio, days):
    return {"hash": name.lower(), "name": name, "category": category, "ratio": ratio, "seeding_time": days * DAY, "size": 10 ** 9, "progress": 1.0}


TORRENTS = [
    torrent("Game A", "PC", 2.5, 20),
    torrent("Game B", "PC", 3.5, 5),
    torrent("Film A", "Movies", 1.2, 3),
    torrent("Film B", "Movies", 0.5, 8),
    torrent("Show A", "Series", 2.2, 1),
    torrent("Loose", "", 0.1, 20),
]


def done(): return sorted(t["name"] for t in CL.seeded_enough(TORRENTS))


print("== without rules ==")
print("  targets:", CL.seeding_targets(), CL.seeding_targets("PC"))
print("  seeded enough:", done())
print("  wording:", CL.describe_targets())

print("\n== with rules ==")
os.environ["SEED_CATEGORY_RULES"] = "PC=3:30, movies=1:7"
print("  parsed:", CL.category_rules())
print("  targets PC / Movies / Series / none:", CL.seeding_targets("PC"), CL.seeding_targets("MOVIES"), CL.seeding_targets("Series"), CL.seeding_targets(None))
print("  seeded enough:", done())
print("  wording:", CL.describe_targets())
summary = CL.cleanup_embed(CL.finished_torrents(TORRENTS)).description.splitlines()[0]
print("  overview:", summary)

print("\n== damaged settings ==")
for value in ("", "PC", "PC=3", "PC=x:y", "=1:2", "PC=3:30,,Movies=bad,Series=0.5:2", "PC=-5:-1"):
    os.environ["SEED_CATEGORY_RULES"] = value
    print(f"  {value!r:40} -> {CL.category_rules()}")
