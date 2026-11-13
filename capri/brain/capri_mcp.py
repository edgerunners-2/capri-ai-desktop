"""Capri's MCP server for Claude Code (smart mode): `Capri.exe --mcp` / `python capri_dev.py --mcp`. It speaks MCP
over stdin/stdout (JSON-RPC, one message per line) and forwards each tool call to the running Capri app (her tool
bridge, toolbridge.py), so Claude Code works the PC with her own abilities: apps, Steam, music, timers, notes,
weather, her eyes on the screen..."""
from __future__ import annotations

import json
import os
import sys
import urllib.request


def _post(name: str, args: dict) -> dict:
    url, token = os.environ.get("CAPRI_BRIDGE_URL", ""), os.environ.get("CAPRI_BRIDGE_TOKEN", "")
    if not url:
        return {"text": "Capri isn't running, so her tools can't be used right now."}
    req = urllib.request.Request(url, data=json.dumps({"name": name, "arguments": args}).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "X-Capri-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"text": f"Couldn't reach Capri's tools: {e}"}


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # a notification (initialized, cancelled...): no answer
        return None
    if method == "initialize":
        want = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
        result = {"protocolVersion": want, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "capri", "version": _version()}}
    elif method == "tools/list":
        from .toolbridge import tool_list

        result = {"tools": tool_list()}
    elif method == "tools/call":
        p = msg.get("params") or {}
        out = _post(str(p.get("name", "")), p.get("arguments") or {})
        content = [{"type": "text", "text": out.get("text") or "Done."}]
        if out.get("image"):
            content.append({"type": "image", "data": out["image"], "mimeType": "image/jpeg"})
        result = {"content": content, "isError": False}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _version() -> str:
    try:
        from .. import __version__

        return __version__
    except Exception:
        return "0"


def main() -> int:
    out = sys.stdout
    sys.stdout = sys.stderr  # anything printed by her code must not land in the protocol stream
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = handle(msg)
        if reply is not None:
            out.write(json.dumps(reply) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
