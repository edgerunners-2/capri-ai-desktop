"""MCP (Model Context Protocol) tools: plug any MCP server into her (files, calendar, GitHub, a database...).
Each server in config.yaml runs as a child process speaking JSON-RPC over stdio:

    mcp:
      servers:
        files:
          command: npx
          args: ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/me/Documents"]
          trusted: false      # false: tools that change things ask you first ("want me to run it?")

Her planner sees every tool's name and description and can pick one with arguments; the result goes into her
reply. Tools the server marks read-only run right away; the rest wait for your yes unless the server is trusted.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading

PROTOCOL = "2025-06-18"


class Server:
    def __init__(self, name: str, spec: dict):
        self.name, self.spec = name, spec
        self.trusted = bool(spec.get("trusted"))
        self.tools: list[dict] = []
        self._id = 0
        self._wait: dict[int, list] = {}
        self._lock = threading.Lock()
        cmd = [spec["command"], *[str(a) for a in spec.get("args", [])]]
        exe = shutil.which(cmd[0]) or cmd[0]  # npx.cmd / uvx.exe on Windows
        env = {**os.environ, **{k: str(v) for k, v in (spec.get("env") or {}).items()}}
        from ..portable import spawn

        self.proc = spawn([exe, *cmd[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env=env, cwd=spec.get("cwd") or None)
        threading.Thread(target=self._read, daemon=True, name=f"mcp-{name}").start()
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "capri", "version": "0.2"}}, timeout=60)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = self.request("tools/list", {}).get("tools", [])

    def _send(self, msg: dict) -> None:
        data = (json.dumps(msg) + "\n").encode("utf-8")
        with self._lock:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg and msg["id"] in self._wait and ("result" in msg or "error" in msg):
                box = self._wait.pop(msg["id"])
                box.append(msg)
                box[0].set()
            elif "id" in msg and "method" in msg:  # a request from the server (ping, roots...): answer politely
                answer = {"result": {}} if msg["method"] == "ping" else                     {"error": {"code": -32601, "message": "not supported"}}
                self._send({"jsonrpc": "2.0", "id": msg["id"], **answer})
        for box in list(self._wait.values()):  # it died: wake everyone waiting
            box.append({"error": {"message": f"MCP server {self.name} stopped"}})
            box[0].set()

    def request(self, method: str, params: dict, timeout: float = 60) -> dict:
        self._id += 1
        box = [threading.Event()]
        self._wait[self._id] = box
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        if not box[0].wait(timeout):
            self._wait.pop(self._id, None)
            raise TimeoutError(f"{self.name} didn't answer {method} in {timeout:.0f}s")
        msg = box[1]
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message", "error"))
        return msg["result"] or {}

    def call(self, tool: str, args: dict) -> str:
        res = self.request("tools/call", {"name": tool, "arguments": args}, timeout=120)
        parts = []
        for c in res.get("content", []):
            if c.get("type") == "text":
                parts.append(c["text"])
            elif c.get("type") == "resource" and "text" in c.get("resource", {}):
                parts.append(c["resource"]["text"])
        if not parts and res.get("structuredContent") is not None:
            parts.append(json.dumps(res["structuredContent"]))
        text = "\n".join(parts) or "(no output)"
        return ("ERROR: " if res.get("isError") else "") + text

    def close(self) -> None:
        try:
            self.proc.kill()
        except Exception:
            pass


class Hub:
    """Every configured server, started in the background so she boots as fast as before."""

    def __init__(self, cfg: dict):
        self.servers: dict[str, Server] = {}
        self.specs = {k: v for k, v in ((cfg.get("mcp") or {}).get("servers") or {}).items()
                      if v and v.get("command") and v.get("enabled", True)}
        if self.specs:
            threading.Thread(target=self._start, daemon=True, name="mcp-start").start()

    def _start(self) -> None:
        for name, spec in self.specs.items():
            try:
                s = Server(name, spec)
                self.servers[name] = s
                print(f"[capri] MCP {name}: {len(s.tools)} tools ({', '.join(t['name'] for t in s.tools[:8])})")
            except Exception as e:
                print(f"[capri] MCP {name} didn't start: {e}")

    def tools(self) -> list[tuple[str, dict, Server]]:
        return [(f"{s.name}.{t['name']}", t, s) for s in self.servers.values() for t in s.tools]

    def find(self, full: str):
        full = full.strip()
        for name, t, s in self.tools():
            if name.lower() == full.lower() or t["name"].lower() == full.lower():
                return name, t, s
        return None

    def menu(self, limit: int = 30) -> str:
        """Tool lines for her planner: name, what it does, its arguments."""
        lines = []
        for name, t, _ in self.tools()[:limit]:
            props = (t.get("inputSchema") or {}).get("properties") or {}
            args = ", ".join(f'"{k}": <{v.get("type", "any")}>' for k, v in list(props.items())[:6])
            about = (t.get("description") or "").strip().splitlines()
            lines.append(f"TOOL {name} {{{args}}}  {about[0][:110] if about else ''}")
        return "\n".join(lines)

    @staticmethod
    def safe(t: dict, s: Server) -> bool:
        """Runs without asking: read-only tools, or anything on a server you marked trusted."""
        return s.trusted or bool((t.get("annotations") or {}).get("readOnlyHint"))

    def close(self) -> None:
        for s in self.servers.values():
            s.close()
