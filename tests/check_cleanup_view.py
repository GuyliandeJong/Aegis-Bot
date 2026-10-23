"""/torrent cleanup's picker: "Choose all ✅" picks every torrent that reached the seeding target on all pages, keeps hand-picked ones, and clears again."""
import asyncio
import logging
import sys
import types

logging.basicConfig(level=logging.WARNING)
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m

from modules.torrents.torrent.cleanup import CleanupView
import isolate  # state files go to a temporary folder

# 60 finished torrents over three pages: the first 40 seeded enough.
FINISHED = [
    {"hash": f"h{n:02}", "name": f"Torrent {n}", "done": n < 40, "ratio": 1, "size": 1, "seeding_time": 1}
    for n in range(60)
]


class Interaction:
    def __init__(self):
        self.response = types.SimpleNamespace(edit_message=self.edit)
        self.edits = 0

    async def edit(self, **kwargs):
        self.edits += 1


async def main():
    view = CleanupView(1, FINISHED)
    print("start:", view.all_button.label, "| remove enabled:", not view.keep_button.disabled)

    await view._toggle_all(Interaction())
    print("after choose all:", len(view.chosen), "chosen | all done:", set(view.chosen) == {t["hash"] for t in FINISHED if t["done"]},
          "| button:", view.all_button.label, "| remove enabled:", not view.keep_button.disabled)
    print("footer:", view.embed().description.splitlines()[1])

    view.page = 2
    view._fill_page()
    print("page 3 picker defaults:", sum(o.default for o in view.select.options))

    await view._toggle_all(Interaction())
    print("after clear:", len(view.chosen), "| button:", view.all_button.label)

    view.chosen = ["h55"]  # a ⏳ torrent picked by hand
    await view._toggle_all(Interaction())
    print("hand-picked kept:", "h55" in view.chosen, "| total:", len(view.chosen))

    none_done = CleanupView(1, [dict(t, done=False) for t in FINISHED[:3]])
    print("nothing done:", none_done.all_button.label, "| disabled:", none_done.all_button.disabled)

asyncio.run(main())
