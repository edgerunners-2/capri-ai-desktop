"""Her tools, offered to Claude Code (smart mode). Capri runs a tiny web server on 127.0.0.1 with a secret token;
her MCP server (capri_mcp.py, started by Claude Code) forwards every tool call here, and it runs through her own
abilities exactly like when her local brain calls a tool. Only this PC can reach it, and only with the token."""
from __future__ import annotations

import base64
import io
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# never offered to Claude Code: powering the PC off, deleting downloads, and other MCP servers
HIDDEN = {"shutdown_pc", "sleep_pc", "clean_downloads", "use_mcp_tool", "describe_screen", "take_screenshot"}

LOOK = {"name": "look_at_screen",
        "description": "See the window the user is looking at right now: which app, the page or tab, its big headings, "
                       "the text on it, and a picture of it. Use it whenever they talk about their screen.",
        "inputSchema": {"type": "object", "properties": {}}}


def tool_list() -> list[dict]:
    """Her tool schemas in MCP form (name, description, inputSchema), plus look_at_screen."""
    from . import tools

    out = []
    for s in tools.SCHEMAS:
        f = s["function"]
        if f["name"] in HIDDEN:
            continue
        out.append({"name": f["name"], "description": f.get("description", ""),
                    "inputSchema": f.get("parameters") or {"type": "object", "properties": {}}})
    return out + [LOOK]


class ToolBridge:
    def __init__(self, agent):
        self.agent = agent
        self.token = secrets.token_hex(16)
        self.lock = threading.Lock()  # one tool at a time: her abilities aren't built for two at once
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if self.headers.get("X-Capri-Token") != bridge.token or self.path != "/call":
                    self.send_response(403)
                    self.end_headers()
                    return
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                    out = bridge.call(str(body.get("name", "")), body.get("arguments") or {})
                except Exception as e:  # a tool blew up: Claude Code hears why, she doesn't crash
                    out = {"text": f"The tool failed: {e}"}
                data = json.dumps(out).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/call"
        # Claude Code (and so her MCP server) inherits these from her environment
        os.environ["CAPRI_BRIDGE_URL"] = self.url
        os.environ["CAPRI_BRIDGE_TOKEN"] = self.token
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        print(f"[capri] her tools are open to Claude Code on {self.url}")

    def call(self, name: str, args: dict) -> dict:
        if name == "look_at_screen":
            return self._look()
        if name in HIDDEN:
            return {"text": f"{name} isn't available in smart mode. Do it another way or tell the user how."}
        from . import tools
        from .agent import Reply

        a = self.agent
        with self.lock:
            reply, context = Reply(text=""), []
            text = tools.dispatch(a, name, args, reply, context)
            a.pending = None  # smart mode asks in chat itself: no half-asked confirmation left hanging
        return {"text": text}

    def _look(self) -> dict:
        from ..abilities import screen, sight, vision

        title = vision.front_title()
        if screen.private(title) or a_never(self.agent, title):
            return {"text": f'The window in front ("{title}") is private (login, bank, DMs...): you must not look at it.'}
        shot = sight.screenshot_front()
        lang = self.agent._language("") if hasattr(self.agent, "_language") else "en"
        facts = vision.screen_facts(shot, lang)
        text = vision.facts_note(facts, self.agent.user) or f'Window: "{title}"'
        img = shot.convert("RGB")
        img.thumbnail((1600, 1600))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        return {"text": text, "image": base64.b64encode(buf.getvalue()).decode("ascii")}

    def close(self):
        try:
            self.server.shutdown()
        except Exception:
            pass


def a_never(agent, title: str) -> bool:
    words = (agent.settings.get("never_look") if hasattr(agent.settings, "get") else []) or []
    return any(w and w.lower() in (title or "").lower() for w in words)
