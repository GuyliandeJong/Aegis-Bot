"""/wol behaviour and the administrator check on deleting files."""
import asyncio
import sys
import types

import stubs
from stubs import mod, Any_

S = stubs.settings
S.GPTWOL_HOST = "h"; S.GPTWOL_PORT = 1; S.GPTWOL_TIMEOUT = 5
S.GPTWOL_TARGET_IP = "10.0.0.5"; S.GPTWOL_TARGET_MAC = "aa:bb"; S.GPTWOL_TARGET_TEST_TYPE = "icmp"; S.GPTWOL_TARGET_NAME = "Gaming PC"

# decorators that leave the function untouched, recording what was applied
applied = {}


def passthrough(tag):
    def deco(*a, **k):
        def wrap(fn):
            applied.setdefault(fn.__name__, []).append((tag, k)); return fn
        return wrap
    return deco


ac = mod('discord.app_commands', command=passthrough('command'), default_permissions=passthrough('default_permissions'))
sys.modules['discord'].app_commands = ac
mod('discord.ext', commands=mod('discord.ext.commands', Cog=object, Bot=object))


class Response:
    def __init__(s): s.done = False; s.kinds = []
    def is_done(s): return s.done
    async def defer(s, **k): assert not s.done; s.done = True; s.kinds.append('defer')
    async def send_message(s, msg=None, **k): assert not s.done; s.done = True; s.kinds.append('send_message'); s.msg = msg
    async def edit_message(s, **k): assert not s.done; s.done = True; s.kinds.append('edit_message'); s.kw = k


class Inter:
    def __init__(s, uid=1, admin=True):
        s.response = Response(); s.sent = []
        s.user = types.SimpleNamespace(id=uid, guild_permissions=types.SimpleNamespace(administrator=admin))
        s.followup = types.SimpleNamespace(send=s._fu)
    async def _fu(s, msg=None, **k): assert s.response.done; s.sent.append(k.get('embed') or msg)
    async def edit_original_response(s, **k): s.sent.append(k.get('content'))


import commands.wol as W
calls = []


class FakeGPT:
    status = "asleep"; fail = None
    async def check_status(s, ip_address, test_type):
        calls.append('status')
        if FakeGPT.fail == 'status': raise RuntimeError('x')
        return FakeGPT.status
    async def send_wol_or_sol(s, mac_address):
        calls.append('toggle:' + mac_address)
        if FakeGPT.fail == 'send': raise RuntimeError('x')


W.GPTWOLService = FakeGPT
cog = W.WOL(bot=None)
print("/wol decorators:", [(t, k) for t, k in applied['wol'] if t == 'default_permissions'], "| takes arguments:", W.WOL.wol.__code__.co_varnames[:W.WOL.wol.__code__.co_argcount])
for status, fail in (("asleep", None), ("awake", None), ("asleep", "status"), ("asleep", "send")):
    FakeGPT.status = status; FakeGPT.fail = fail; calls.clear(); i = Inter()
    asyncio.run(cog.wol(i)); r = i.sent[-1]
    print(f"  pc={status:6} fail={str(fail):6} -> gptwol calls={calls} reply={(r.title if hasattr(r, 'title') else r)!r}")

# ---- delete-files permission
import services.qbittorrent_service as QS
deleted = []


async def fake_delete(self, torrent_hash, delete_files=False): deleted.append((torrent_hash, delete_files))


QS.QBittorrentService.delete_torrent = fake_delete
from commands.torrent.views.delete_view import TorrentDeleteConfirmationView as V1, TorrentDeleteFilesConfirmationView as V2
import isolate  # state files go to a temporary folder
parent = types.SimpleNamespace(owner_id=1, torrent={"hash": "abc", "name": "T"}, message=None, stop=lambda: None)
print("\ndelete buttons (user is the requester in every case)")
for admin in (True, False):
    i = Inter(admin=admin); asyncio.run(V1(parent)._delete_files(i))
    print(f"  admin={admin!s:5} 'Remove torrent + files' ->", i.response.kinds, getattr(i.response, 'msg', '') or 'opens confirmation')
    deleted.clear(); i = Inter(admin=admin); asyncio.run(V2(parent)._confirm(i))
    print(f"  admin={admin!s:5} 'Delete files' confirm    ->", i.response.kinds, "| qBittorrent delete calls:", deleted)
    deleted.clear(); i = Inter(admin=admin); asyncio.run(V1(parent)._keep_files(i))
    print(f"  admin={admin!s:5} 'Remove torrent' (keep)    ->", i.response.kinds, "| qBittorrent delete calls:", deleted)
