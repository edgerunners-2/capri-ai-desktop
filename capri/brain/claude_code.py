"""Claude Code as her brain (an extra pick in her Brain menu, never the default). She talks through the `claude` CLI in
print mode (`claude -p`, streamed JSON), with her own character as the system prompt, so she still sounds like
herself, and her bubble still types it out with blips. It can also be her hands on the PC: a job her own abilities
can't do goes to Claude Code, read-only first, and anything that runs commands or changes files waits for their yes."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from .llm import LLM, BrainDown

ID = "claude-code"  # what her Brain menu saves as the brain (like "none" for no AI)
SETUP_URL = "https://code.claude.com/docs/en/setup"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# chat: no tools at all, only words
HEADER = ("You are the brain inside a desktop companion character. Below is the chat so far; its first part tells you "
          "who you are. Write only your next reply, fully in that character: plain text, no markdown, no name label, "
          "no quotes around it. Keep it short like a speech bubble: one line, usually one sentence, two at most (under "
          "20 words), no filler, no repeating their question, no follow-up question unless it really matters; go "
          "longer only when they ask for details, an explanation or a list. Bracketed notes in the newest message are hidden directions for you: follow them, "
          "never read them out.")

# her eyes: what's in a screenshot, told plainly (her own brain turns it into her reply afterwards)
LOOKER = ("You are the eyes of a desktop companion. Answer what you're asked about the picture plainly and "
          "accurately, quoting any text that matters. No character voice, no questions back, no markdown.")

# her hands: what Claude Code may use before they said yes, and after
LOOK = ["Read", "Glob", "Grep", "WebSearch", "WebFetch"]
DO = LOOK + ["Bash", "PowerShell", "Edit", "Write"]
# never, even after a yes: permanent deletes, power and disk commands, the registry, pushing code
DENY = ["Bash(rm *)", "Bash(rmdir *)", "Bash(del *)", "Bash(erase *)", "Bash(rd *)", "Bash(format *)",
        "Bash(shutdown *)", "Bash(reg *)", "Bash(diskpart *)", "Bash(mkfs *)", "Bash(dd *)", "Bash(git push *)",
        "Bash(git reset *)", "Bash(git clean *)", "PowerShell(Remove-Item *)", "PowerShell(Clear-RecycleBin *)",
        "PowerShell(Format-Volume *)", "PowerShell(Stop-Computer *)", "PowerShell(Restart-Computer *)",
        "PowerShell(Remove-ItemProperty *)", "PowerShell(Set-ItemProperty *)", "PowerShell(Set-MpPreference *)"]

ASK_FIRST = ("You are the hands of a desktop companion on {user}'s {os} PC (home folder: {home}). Their request is "
             "below. You may only look right now (read files, search). If the request can be fully answered by "
             "looking, do it and end with a short plain summary of the answer. If it needs running a command, "
             "opening, creating, changing, moving or deleting anything, or changing a setting, do NOT do it: answer "
             "with exactly one line that starts with 'NEEDS:' followed by one plain sentence saying exactly what you "
             "would do (say clearly if anything would be deleted or overwritten). Never ask for passwords or keys.")
GO = ("You are the hands of a desktop companion on {user}'s {os} PC (home folder: {home}). {user} asked for the "
      "request below and said yes to this plan: {plan}\nDo exactly that plan, nothing more. Never delete anything "
      "permanently: send files to the Recycle Bin / trash instead. Never shut down, restart or log off the PC, "
      "change security or antivirus settings, edit the registry, touch passwords, keys or browser data, or push "
      "code. Never touch {protect}. If the job turns out to need anything beyond the plan, stop and say what "
      "you'd need. End with one short plain sentence about what you did.")


def find_cli(cfg: dict | None = None) -> str | None:
    """Where the Claude Code CLI is: the one set in config.yaml, on PATH, or where its installers put it."""
    want = ((cfg or {}).get("claude_code") or {}).get("exe") or ""
    if want and Path(want).is_file():
        return want
    hit = shutil.which("claude")
    if hit:
        return hit
    home = Path.home()
    spots = [home / ".local" / "bin" / "claude", home / ".claude" / "local" / "claude",
             home / ".npm-global" / "bin" / "claude", Path("/usr/local/bin/claude"), Path("/opt/homebrew/bin/claude")]
    if os.name == "nt":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        spots = [home / ".local" / "bin" / "claude.exe", appdata / "npm" / "claude.cmd",
                 home / ".claude" / "local" / "claude.cmd", home / ".claude" / "local" / "claude.exe"]
    hit = next((str(p) for p in spots if p.is_file()), None)
    return hit or desktop_cli()


def desktop_cli() -> str | None:
    """The Claude Code that comes with the Claude desktop app on Windows (newest version it has). The Store version
    of the app keeps it under its package folder, the regular installer under %APPDATA%\\Claude."""
    if os.name != "nt":
        return None
    import glob

    local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    roaming = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    hits = glob.glob(os.path.join(roaming, "Claude", "claude-code", "*", "*", "claude.exe"))
    hits += glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "claude-code",
                                   "*", "*", "claude.exe"))
    hits += glob.glob(os.path.join(local, "Claude-Data", "claude_*", "claude-code", "*", "*", "claude.exe"))

    def version(path: str) -> tuple:
        name = Path(path).parent.parent.name  # .../claude-code/2.1.309/<build>/claude.exe
        return tuple(int(x) if x.isdigit() else 0 for x in name.split("."))

    return max(hits, key=version) if hits else None


def _env() -> dict:
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)  # started from inside a Claude Code session (testing her): don't look nested
    return env


def signed_in(exe: str) -> bool:
    """`claude auth status` says loggedIn (a Claude account or an API key)."""
    try:
        r = subprocess.run([exe, "auth", "status", "--json"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, env=_env(), creationflags=_NO_WINDOW)
        return bool(json.loads(r.stdout or "{}").get("loggedIn"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def open_sign_in(exe: str) -> bool:
    """A terminal window running `claude auth login`, for them to sign in (they asked for it from her menu).
    -> False when there's no terminal to open (then she tells them the command)."""
    try:
        if os.name == "nt":
            subprocess.Popen(["cmd", "/k", exe, "auth", "login"], env=_env(),
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            return True
        for term in (["konsole", "-e"], ["gnome-terminal", "--"], ["x-terminal-emulator", "-e"], ["xterm", "-e"]):
            if shutil.which(term[0]):
                subprocess.Popen(term + [exe, "auth", "login"], env=_env())
                return True
    except OSError as e:
        print(f"[capri] couldn't open the Claude Code sign-in: {e}")
    return False


def pictures(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """OpenAI-style picture parts (a data: URL in image_url, how her eyes send a screenshot) -> (the messages with
    only their words, Claude image blocks for the pictures)."""
    out, images = [], []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            words = []
            for part in content:
                if part.get("type") == "text":
                    words.append(part.get("text") or "")
                elif part.get("type") == "image_url":
                    url = (part.get("image_url") or {}).get("url") or ""
                    hit = re.match(r"data:(image/[\w.+-]+);base64,(.+)", url, re.S)
                    if hit:
                        images.append({"type": "image", "source": {"type": "base64", "media_type": hit.group(1),
                                                                   "data": hit.group(2)}})
            m = {**m, "content": "\n".join(words)}
        out.append(m)
    return out, images


def user_line(prompt: str, images: list[dict]) -> str:
    """One user turn for `--input-format stream-json`: the pictures, then the words."""
    return json.dumps({"type": "user", "message": {"role": "user", "content": images + [{"type": "text",
                                                                                          "text": prompt}]}}) + "\n"


def render(messages: list[dict], words: int | None = None) -> tuple[str, str]:
    """An OpenAI-style message list -> (system prompt, prompt). A primed chat (her character, then turns) puts the
    character in the system prompt and the turns in a transcript; a lone question goes as it is."""
    msgs = list(messages)
    system = HEADER
    if msgs and (msgs[0]["role"] == "system" or (len(msgs) > 2 and msgs[1]["role"] == "assistant")):
        system += "\n\n" + msgs.pop(0)["content"]
    limit = (f"\n(Hard limit: {words} words, one or two short sentences. Only ask something back if you really need "
             f"to know.)" if words and words <= 20 else f"\n(Keep it under about {words} words.)" if words else "")
    if len(msgs) == 1:
        return system, msgs[0]["content"] + limit
    lines = []
    for m in msgs[:-1]:
        who = "YOU" if m["role"] == "assistant" else "THEM"
        lines.append(f"{who}: {m['content']}")
    last = msgs[-1]["content"] if msgs else ""
    return system, ("The chat so far:\n\n" + "\n\n".join(lines) + f"\n\nTHEM: {last}\n\n"
                    "Write YOUR next reply only." + limit)


def text_of(event: dict) -> str:
    """The new words in one streamed event, '' for anything else."""
    if event.get("type") == "stream_event":
        e = event.get("event") or {}
        if e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
            return e["delta"].get("text") or ""
    return ""


class ClaudeCodeLLM(LLM):
    """Her brain is Claude Code. Every reply is one `claude -p` run with no tools; temperature and logit bias don't
    exist there, so they're ignored, and the length cap becomes a word limit in the prompt."""

    hands = True  # the agent can hand PC jobs to task()
    few_shot = False
    cloud = True  # a slow first word here is the network, not a weak PC: her prompts don't get cut for it  # her character alone: her example lines would read as real chat and cost tokens every turn
    vision = True  # Claude reads screenshots itself: her eyes go through it (abilities/sight.py BrainEyes)
    no_warm = True  # nothing to warm: each reply is a fresh run, and a warm-up would only cost a call

    def __init__(self, cfg: dict):
        from ..config import DATA

        self.c = cfg.get("claude_code") or {}
        self.user = cfg.get("user_name") or "you"
        self.protect = cfg.get("eru_ai_root") or ""
        self.exe = find_cli(cfg)
        if self.exe is None:
            raise RuntimeError("Claude Code isn't installed")
        if not signed_in(self.exe):
            raise RuntimeError("Claude Code isn't signed in")
        self.dir = DATA / "claude-code"  # its working folder: nothing of hers (no CLAUDE.md, no repo) leaks in
        self.dir.mkdir(parents=True, exist_ok=True)
        self.model = self  # the brain menu closes .model when it swaps brains
        print(f"[capri] her brain is Claude Code ({self.exe})")

        self.smart = bool(self.c.get("smart"))  # smart mode: Claude Code with her tools, talking as her
        self.session = ""  # smart mode keeps one Claude Code session per run of Capri: a new page each start
        self.mode_ok = None  # does this CLI take --permission-mode auto? (found out on the first smart turn)

    def close(self):
        pass

    def new_chat(self):
        self.session = ""

    def _mcp_config(self) -> str:
        """Her own tool server, started by Claude Code: Capri.exe --mcp (built) or python capri_dev.py --mcp."""
        import sys

        if getattr(sys, "frozen", False):
            cmd, args = sys.executable, ["--mcp"]
        else:
            cmd, args = sys.executable, [str(Path(__file__).resolve().parents[2] / "capri_dev.py"), "--mcp"]
        env = {k: os.environ[k] for k in ("CAPRI_BRIDGE_URL", "CAPRI_BRIDGE_TOKEN", "CAPRI_HOME") if k in os.environ}
        return json.dumps({"mcpServers": {"capri": {"type": "stdio", "command": cmd, "args": args, "env": env}}})

    def smart_stream(self, character: str, prompt: str):
        """One smart-mode turn: Claude Code as Capri, her tools plus its own, in a session that remembers the chat
        (it shows up in Claude Code's session list). Yields her words as they come."""
        import platform
        import uuid

        home = str(Path.home())
        system = HEADER + "\n\n" + character + SMART.format(user=self.user, os=platform.system() or "Windows",
                                                             home=home, protect=self.protect or "Capri's own files")
        deny = list(DENY)
        if self.protect:
            p = self.protect.replace("\\", "/")
            deny += [f"Edit({p}/**)", f"Write({p}/**)"]
        allowed = ["mcp__capri"] + DO
        for attempt in range(2):
            mode = self.c.get("smart_permissions", "auto") if self.mode_ok is not False else "dontAsk"
            cmd = [self.exe, "-p", "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
                   "--mcp-config", self._mcp_config(), "--allowedTools", ",".join(allowed),
                   "--disallowedTools", ",".join(deny), "--permission-mode", mode, "--add-dir", home,
                   "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
            if self.c.get("model"):
                cmd += ["--model", self.c["model"]]
            if self.c.get("smart_effort", "medium"):
                cmd += ["--effort", self.c.get("smart_effort", "medium")]
            fresh = not self.session
            if fresh:
                self.session = str(uuid.uuid4())
                cmd += ["--session-id", self.session]
            else:
                cmd += ["--resume", self.session]
            said, done = False, None
            for event in self._run(cmd, prompt, system, int(self.c.get("task_timeout", 300))):
                piece = text_of(event)
                if piece:
                    said = True
                    yield piece
                elif event.get("type") == "result":
                    done = event
            if done is None and not said and mode == "auto" and attempt == 0 and "permission" in self._stderr().lower():
                print(f"[capri] this Claude Code has no auto mode, using dontAsk: {self._stderr()}")
                self.mode_ok = False
                if fresh:
                    self.session = ""
                continue
            if mode == "auto" and done is not None:
                self.mode_ok = True
            if done is None:
                if fresh:
                    self.session = ""
                raise BrainDown(f"Claude Code stopped without an answer: {self._stderr()}")
            if done.get("is_error"):
                raise BrainDown(f"Claude Code: {done.get('result') or done.get('subtype')}")
            if not said and done.get("result"):
                yield done["result"]
            return

    def _base(self, effort: str) -> list[str]:
        cmd = [self.exe, "-p", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
               "--disable-slash-commands"]
        if self.c.get("model"):
            cmd += ["--model", self.c["model"]]
        if effort:
            cmd += ["--effort", effort]
        return cmd

    def _run(self, cmd: list[str], prompt: str, system: str, timeout: int):
        """Start one run, prompt on stdin (no command-line length limit), system prompt from a file. Yields the
        streamed JSON events; the run is killed if it takes too long or the reader stops early."""
        sp = self.dir / f"system-{threading.get_ident()}.txt"  # one per thread: a chat and a background job at once
        sp.write_text(system, encoding="utf-8")
        err = open(self.dir / f"stderr-{threading.get_ident()}.log", "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(cmd + ["--system-prompt-file", str(sp)], cwd=str(self.dir), env=_env(),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, text=True,
                                encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        timer = threading.Timer(timeout, proc.kill)
        timer.start()
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("{"):
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            err.close()

    def _stderr(self) -> str:
        try:
            return (self.dir / f"stderr-{threading.get_ident()}.log").read_text(encoding="utf-8", errors="replace")[-300:].strip()
        except OSError:
            return ""

    def stream(self, messages, temperature=None, max_tokens=None, avoid=None, raw=False):
        words = int(max_tokens * 0.7) if max_tokens and max_tokens > 1 else None
        if words and max_tokens <= 200:  # a chat line (not an email or a summary): a speech bubble, not a paragraph
            words = min(words, 20)
        messages, images = pictures(messages)
        system, prompt = render(messages, words)
        cmd = self._base(self.c.get("chat_effort", "low")) + [
            "--tools", "", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
        if images:  # a screenshot to look at: the turn goes in as JSON, pictures and all
            cmd += ["--input-format", "stream-json"]
            prompt = user_line(prompt, images)
            system = LOOKER  # her eyes, not her mouth: a plain account of the picture, she reacts to it after
        said = False
        done = None
        for event in self._run(cmd, prompt, system, int(self.c.get("chat_timeout", 120))):
            piece = text_of(event)
            if piece:
                said = True
                yield piece
            elif event.get("type") == "result":
                done = event
        if done is None:
            raise BrainDown(f"Claude Code stopped without an answer: {self._stderr()}")
        if done.get("is_error"):
            raise BrainDown(f"Claude Code: {done.get('result') or done.get('subtype')}")
        if not said and done.get("result"):  # a CLI without partial messages: the whole reply at the end
            yield done["result"]

    def chat(self, messages, temperature=None, max_tokens=None, avoid=None, raw=False):
        return "".join(self.stream(messages, temperature, max_tokens, avoid)).strip()

    def task(self, request: str, plan: str = "") -> str:
        """A job on their PC. No plan: look only, and come back with either the answer or 'NEEDS: <what I'd do>'.
        With the plan they said yes to: do it (commands and file changes allowed, the DENY list never)."""
        import platform

        home = str(Path.home())
        fill = dict(user=self.user, os=platform.system() or "Windows", home=home, plan=plan,
                    protect=self.protect or "Capri's own files")
        tools = DO if plan else LOOK
        system = (GO if plan else ASK_FIRST).format(**fill)
        deny = list(DENY)
        if self.protect:
            p = self.protect.replace("\\", "/")
            deny += [f"Edit({p}/**)", f"Write({p}/**)"]
        cmd = self._base(self.c.get("task_effort", "medium")) + [
            "--tools", ",".join(tools), "--allowedTools", ",".join(tools), "--disallowedTools", ",".join(deny),
            "--permission-mode", "dontAsk", "--add-dir", home, "--output-format", "stream-json", "--verbose"]
        done = None
        for event in self._run(cmd, f"Request from {self.user}: {request}", system,
                               int(self.c.get("task_timeout", 300))):
            if event.get("type") == "result":
                done = event
        if done is None:
            raise BrainDown(f"Claude Code stopped without an answer: {self._stderr()}")
        if done.get("is_error"):
            raise BrainDown(f"Claude Code: {done.get('result') or done.get('subtype')}")
        return (done.get("result") or "").strip()


# smart mode: Claude Code IS her (her character as its system prompt), with her tools and its own to work the PC
SMART = ("\n\nSMART MODE. You are running as Claude Code on {user}'s {os} PC (home folder: {home}), and you are Capri: "
         "every word you write goes into her speech bubble, so write only what Capri says, in her voice, short "
         "(one line, usually one sentence, two at most, under 20 words, unless they ask for more; after a job, just say "
         "what you did in a few words, no recap of the steps), plain text, no markdown, no lists, no code blocks, never "
         "narrate your tool use ('let me check...'). Use the capri tools for what they cover (open apps and Steam games, "
         "music, timers and reminders, notes, weather, news, volume, look_at_screen to see their screen) and your own "
         "tools for anything else on the PC (files, settings, commands). Just do what they ask, then say in her voice "
         "what you did. Never delete anything permanently (Recycle Bin / trash only), never shut down or restart the "
         "PC, never touch the registry, security settings, passwords, keys or browser data, never push code, never "
         "touch {protect}. If something risky or irreversible is needed, ask them in one short line first and wait.")


NEEDS = re.compile(r"^\W*NEEDS:\s*(.+)", re.I | re.S | re.M)


def needs(answer: str) -> str:
    """'NEEDS: move 12 screenshots into Pictures/Old' -> that plan; '' when the job was already done by looking."""
    m = NEEDS.search(answer)
    return m.group(1).strip().splitlines()[0].strip() if m else ""
