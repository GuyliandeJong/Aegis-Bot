"""
The modules: which features are switched on.

Each folder in modules/ with a module.py is a module. Its module.py
defines MODULE, a Module saying what the feature needs and brings:

    MODULE = Module(
        name="minecraft",
        description="Minecraft server status and join messages.",
        required=("MINECRAFT_SERVERS",),
        monitors=("modules.minecraft.minecraft_monitor:MinecraftMonitor",),
    )

Its slash commands are the files in modules/<name>/commands/, loaded
as discord.py extensions. Monitors are named as "module:Class" so a
module that is off is never imported.

Which modules run is set with MODULES in .env:

    MODULES=auto                  every module whose required settings
                                  are set (the default)
    MODULES=torrents,jellyfin     only these (plus "general")
    MODULES=auto,-minecraft       auto, but never minecraft
"""

import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from utils.logger import logger


MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"


@dataclass(frozen=True)
class Module:
    name: str
    description: str

    # .env settings without which the module cannot work. With
    # MODULES=auto, a module missing any of them stays off.
    required: tuple[str, ...] = ()

    # "package.module:Class" of each background monitor. Every monitor
    # takes the notification service as its only argument.
    monitors: tuple[str, ...] = ()

    # "package.module:function" called once at startup, before the
    # commands are loaded, for things such as wrapping a client.
    setup: str | None = None

    # "package.module:function" returning {name: coroutine} of services
    # to check at startup (see core/startup_check.py).
    startup_checks: str | None = None

    # "package.module:function" awaited when the bot stops, for closing
    # shared connections.
    shutdown: str | None = None

    # The general module (help, maintenance, the weekly summary) cannot
    # be switched off.
    always_on: bool = False

    def missing(self) -> list[str]:
        """Required settings that are empty."""
        return [name for name in self.required if not os.getenv(name, "").strip()]

    def commands(self) -> list[str]:
        """Extension names of the files in modules/<name>/commands/."""
        folder = MODULES_DIR / self.name / "commands"

        return [
            f"modules.{self.name}.commands.{path.stem}"
            for path in sorted(folder.glob("*.py"))
            if path.name != "__init__.py"
        ]


@dataclass
class Status:
    module: Module
    enabled: bool
    reason: str = ""
    missing: list[str] = field(default_factory=list)


def resolve(target: str) -> object:
    """The object named by "package.module:attribute" (or "Class.attribute")."""
    path, _, attribute = target.partition(":")
    found = importlib.import_module(path)

    for part in attribute.split("."):
        found = getattr(found, part)

    return found


def discover() -> list[Module]:
    """Every module in modules/, by name."""
    found = []

    for folder in sorted(MODULES_DIR.iterdir()):
        if not (folder / "module.py").is_file():
            continue

        try:
            module = importlib.import_module(f"modules.{folder.name}.module").MODULE
        except Exception:
            logger.exception("[Modules] Could not read modules/%s/module.py", folder.name)
            continue

        if module.name != folder.name:
            logger.error(
                "[Modules] modules/%s/module.py calls itself %r; skipped.",
                folder.name,
                module.name,
            )
            continue

        found.append(module)

    return found


def selection(value: str | None = None) -> tuple[bool, set[str], set[str]]:
    """(auto, included, excluded) from MODULES."""
    value = os.getenv("MODULES", "auto") if value is None else value
    tokens = [t.strip().lower() for t in value.split(",") if t.strip()] or ["auto"]

    auto = "auto" in tokens
    excluded = {t[1:] for t in tokens if t.startswith("-")}
    included = {t for t in tokens if t != "auto" and not t.startswith("-")}

    return auto, included, excluded


def statuses(modules: list[Module], value: str | None = None) -> list[Status]:
    """Whether each module runs, and why not."""
    auto, included, excluded = selection(value)
    known = {m.name for m in modules}

    for name in sorted((included | excluded) - known):
        logger.warning("[Modules] MODULES names %r, but there is no such module.", name)

    result = []

    for module in modules:
        missing = module.missing()

        if module.always_on:
            result.append(Status(module, True))
        elif module.name in excluded or not (auto or module.name in included):
            result.append(Status(module, False, "off in MODULES"))
        elif missing:
            result.append(Status(module, False, "not configured", missing))
        else:
            result.append(Status(module, True))

    return result


# Names of the modules running in this process, set by activate(). None
# until then, which counts every module as enabled: the checks in
# tests/ build parts of the bot without starting it.
_enabled: set[str] | None = None


def activate(states: list[Status]) -> None:
    global _enabled
    _enabled = {s.module.name for s in states if s.enabled}


def enabled(name: str) -> bool:
    """Whether the module called `name` runs."""
    return _enabled is None or name in _enabled


def report(states: list[Status]) -> None:
    """Log one line per module. Settings are named, never shown."""
    auto, included, _ = selection()
    logger.info("Modules:")

    for state in states:
        if state.enabled:
            logger.info("  %-11s on", state.module.name)
        elif state.missing:
            # Someone who named the module wants to know what is missing;
            # with MODULES=auto most people simply do not use it.
            log = logger.warning if state.module.name in included else logger.info
            log(
                "  %-11s off: set %s",
                state.module.name,
                ", ".join(state.missing),
            )
        else:
            logger.info("  %-11s off (%s)", state.module.name, state.reason)
