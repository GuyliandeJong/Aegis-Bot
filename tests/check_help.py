"""/help: who sees which commands, the grouping, one command in detail, autocomplete, and Discord's embed limits, on a fake command tree built from real discord.py objects."""
import asyncio
import logging
import os
import sys
import types

logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
m = types.ModuleType("utils.logger"); m.logger = logging.getLogger("aegis"); sys.modules["utils.logger"] = m
ADMIN, MEMBER, CONTAINER_MEMBER = 111, 222, 333
os.environ["PORTAINER_ALLOWED_USER_IDS"] = f"{ADMIN}, {CONTAINER_MEMBER}"

import discord
from discord import app_commands
import modules.general.commands.help as H
import isolate  # state files go to a temporary folder


async def noop(interaction: discord.Interaction):
    pass


async def with_mode(interaction: discord.Interaction, mode: str | None = None):
    pass


async def with_title(interaction: discord.Interaction, title: str):
    pass


async def inspect(interaction: discord.Interaction, message: discord.Message):
    pass


async def titles(interaction, current):
    return []


def build_tree():
    """A tree shaped like the bot's: groups, admin defaults, /container."""
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)

    torrent = app_commands.Group(name="torrent", description="Manage and view qBittorrent torrents.")
    torrent.command(name="search", description="Search Jackett.")(noop)
    torrent.command(name="cleanup", description="Finished torrents by seeding; remove them (admins).")(noop)
    speed = app_commands.Command(name="speed", description="Show qBittorrent's speed, or switch normal and slow.", callback=with_mode)
    speed = app_commands.describe(mode="Switch to this speed (administrators).")(speed)
    speed = app_commands.choices(mode=[app_commands.Choice(name="Normal", value="normal"), app_commands.Choice(name="Slow", value="slow")])(speed)
    torrent.add_command(speed)
    tree.add_command(torrent)

    jellyfin = app_commands.Group(name="jellyfin", description="Browse the Jellyfin libraries.")
    movies = app_commands.Command(name="movies", description="Look up a movie on all Jellyfin servers.", callback=with_title)
    movies = app_commands.describe(title="Movie title, or words from it.")(movies)
    movies.autocomplete("title")(titles)
    jellyfin.add_command(movies)
    tree.add_command(jellyfin)

    container = app_commands.Group(name="container", description="Manage Docker containers through Portainer.")
    container.command(name="list", description="List containers and their state.")(noop)
    container.command(name="stop", description="Stop a container.")(noop)
    tree.add_command(container)

    for name, admin in (("server", True), ("wol", True), ("cpu", False), ("ping", False), ("newthing", False)):
        command = app_commands.Command(name=name, description=f"The /{name} command.", callback=noop)
        if admin:
            command = app_commands.default_permissions(administrator=True)(command)
        tree.add_command(command)

    # A context menu must not be listed.
    tree.add_command(app_commands.ContextMenu(name="Inspect", callback=inspect))

    cog = H.Help(bot=None)
    for command in cog.__cog_app_commands__:
        tree.add_command(command)

    return tree, cog


def user(uid, admin):
    permissions = discord.Permissions(administrator=True) if admin else discord.Permissions(send_messages=True)
    return types.SimpleNamespace(id=uid, guild_permissions=permissions, display_name=f"user{uid}",
                                 display_avatar=types.SimpleNamespace(url="https://example.invalid/a.png"))


class Response:
    def __init__(s): s.sent = []
    def is_done(s): return bool(s.sent)
    async def send_message(s, content=None, **k): s.sent.append((content, k))


def interaction(tree, who):
    return types.SimpleNamespace(client=types.SimpleNamespace(tree=tree), user=who, guild=None, response=Response())


tree, cog = build_tree()
people = {"admin": user(ADMIN, True), "member": user(MEMBER, False), "container member": user(CONTAINER_MEMBER, False)}

print("visible commands")
for label, who in people.items():
    names = [c.qualified_name for _a, c in H.visible_commands(tree, who)]
    print(f"  {label:16}: {names}")

print("\noverview for the admin and the member")
for label in ("admin", "member"):
    i = interaction(tree, people[label])
    asyncio.run(cog.help_command.callback(cog, i, None))
    content, kw = i.response.sent[-1]
    embed = kw["embed"]
    print(f"  {label}: private: {kw.get('ephemeral')} | fields: {[f.name for f in embed.fields]}")
    for field in embed.fields:
        for line in field.value.splitlines():
            print(f"    | {line}")

print("\none command in detail")
for label, name in (("member", "torrent speed"), ("member", "/jellyfin movies"), ("member", "torrent cleanup"), ("member", "container list"), ("container member", "container stop"), ("admin", "torrent cleanup"), ("member", "tor"), ("admin", "cleanup"), ("member", "cleanup"), ("member", "zzz")):
    i = interaction(tree, people[label])
    asyncio.run(cog.help_command.callback(cog, i, name))
    content, kw = i.response.sent[-1]
    embed = kw.get("embed")
    if embed is None:
        print(f"  {label} {name!r}: {content} | private: {kw.get('ephemeral')}")
    else:
        options = [(f.name, f.value.replace(chr(10), ' / ')) for f in embed.fields]
        print(f"  {label} {name!r}: {embed.title} | {embed.description.replace(chr(10), ' ')} | {options}")

print("\nautocomplete")
for label, current in (("member", "tor"), ("admin", "tor"), ("member", "cont"), ("container member", "/cont")):
    i = interaction(tree, people[label])
    choices = asyncio.run(cog._command_names(i, current))
    print(f"  {label} {current!r}: {[c.name for c in choices]}")

# ---- a tree far larger than one embed can hold
big = app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))
for g in range(4):
    group = app_commands.Group(name=f"group{g}", description="A group.")
    for n in range(25):
        group.command(name=f"sub{n:02}", description=("x" * 99) + "!")(noop)
    big.add_command(group)
for n in range(40):
    big.add_command(app_commands.Command(name=f"cmd{n:02}", description=("y" * 99) + "!", callback=noop))
i = interaction(big, people["admin"])
asyncio.run(cog.help_command.callback(cog, i, None))
embed = i.response.sent[-1][1]["embed"]
print("\nlarge tree (140 commands)")
print(f"  total {len(embed)} <= 6000: {len(embed) <= 6000} | fields {len(embed.fields)} <= 25: {len(embed.fields) <= 25}"
      f" | longest value <= 1024: {max(len(f.value) for f in embed.fields) <= 1024}")
print(f"  last field: {embed.fields[-1].name}: {embed.fields[-1].value}")
