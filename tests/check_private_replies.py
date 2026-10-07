"""/wan, /server, /idrac and /network answer only the person who ran them: every reply call passes ephemeral=True."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPLIES = {"defer", "send_message", "send"}

for name in ("wan", "server", "idrac", "network"):
    tree = ast.parse((ROOT / "commands" / f"{name}.py").read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in REPLIES:
            owner = ast.unparse(node.func.value)
            if owner.endswith(("response", "followup")):
                private = any(k.arg == "ephemeral" and getattr(k.value, "value", None) is True for k in node.keywords)
                calls.append(f"{owner.split('.')[-1]}.{node.func.attr}:{'private' if private else 'PUBLIC'}")
    print(f"/{name:8} {'all private' if calls and all(c.endswith('private') for c in calls) else 'NOT all private'} {calls}")
