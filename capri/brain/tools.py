"""Capri's hands: native tool calling (OpenAI style) for backends that support it (llama-server, LM Studio,
OpenAI-compat APIs).

Every tool maps to one of her existing intents and runs through the agent's own ``_run``, so all of its safety
comes along for free: shutdown/sleep/clean go through her "want me to do it?" confirmation, MCP tools pass the
hub's safe gate, Spotify/YouTube fallbacks stay in one place. Backends without tool support never see this file:
the agent keeps the old regex-intent flow there.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from ..abilities import launcher
from ..abilities import reminders as rem
from ..abilities.music import GENRES
from . import intents


def _fn(name: str, desc: str, props: dict | None = None, required: tuple = ()):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": list(required)}}}


def _s(kind: str, desc: str, enum=None):
    d = {"type": kind, "description": desc}
    if enum:
        d["enum"] = list(enum)
    return d


SCHEMAS = [
    _fn("open_app", "Open an app on the user's PC by name (browser, calculator, notepad, Spotify, Steam, a game...).",
        {"name": _s("string", "App name, in the user's language")}, ("name",)),
    _fn("open_website", "Open a website URL in their browser.", {"url": _s("string", "Full URL")}, ("url",)),
    _fn("web_search", "Search the web: opens the results in their browser, and you read them too and answer from them.",
        {"query": _s("string", "What to search")}, ("query",)),
    _fn("read_page", "Read a web page's text and answer from it.", {"url": _s("string", "Full URL")}, ("url",)),
    _fn("play_song", "Play a song, artist, album or playlist: Spotify when they have it, YouTube in the browser "
        "otherwise. 'this' means the song playing right now.", {"query": _s("string", "What to play, e.g. 'SAMURAI'")},
        ("query",)),
    _fn("play_genre", "Put on music from a genre, picking the track yourself from the tracks you rate for that genre.",
        {"genre": _s("string", "The genre", GENRES.keys())}),
    _fn("play_my_pick", "Put on a song you chose yourself, one of your favourites. No arguments."),
    _fn("music_control", "Control the music playing on their PC.",
        {"action": _s("string", "What to do", ("pause", "resume", "next", "previous"))}, ("action",)),
    _fn("what_is_playing", "Ask what song is playing on their PC right now. No arguments."),
    _fn("set_reminder", "Set an alarm, a countdown timer, or a reminder: she rings and tells them when it's due.",
        {"kind": _s("string", "alarm = an alert at a time of day; timer = counts down from now; reminder = tells them "
                              "at a moment", ("alarm", "timer", "reminder")),
         "when": _s("string", "When, in plain words: 'in 20 minutes', 'at 7:30 pm', 'tomorrow morning'"),
         "what": _s("string", "What it's for, e.g. 'stretch', 'pasta'")}, ("kind", "when")),
    _fn("list_reminders", "List the alarms, timers and reminders they have set. No arguments."),
    _fn("cancel_reminder", "Cancel an alarm, timer or reminder (plural cancels every one of that kind).",
        {"which": _s("string", "What to cancel", ("alarm", "alarms", "timer", "timers", "reminder", "reminders",
                                                  "all"))}, ("which",)),
    _fn("todo", "Manage their to-do lists: add items, cross one off, read them out, clear one.",
        {"action": _s("string", "What to do", ("add", "done", "list", "clear")),
         "items": _s("string", "For add: the items separated by commas. For done: one item to cross off."),
         "list": _s("string", "List name when they named one ('groceries'); 'to-do' is the default")}, ("action",)),
    _fn("get_weather", "The weather for a city (their city when you know it and none is given).",
        {"city": _s("string", "City name; empty for theirs")}),
    _fn("set_city", "Remember the city they live in, for the weather.", {"city": _s("string", "City name")}, ("city",)),
    _fn("set_volume", "Change their PC's system volume.",
        {"action": _s("string", "What to do", ("up", "down", "set", "mute", "unmute")),
         "level": _s("number", "Percent 0-100: the target for set, the step for up/down")}, ("action",)),
    _fn("take_screenshot", "Take a screenshot of their whole screen, save it and open it. No arguments."),
    _fn("lock_pc", "Lock their PC (the login screen)."),
    _fn("sleep_pc", "Put their PC to sleep. She asks them first."),
    _fn("shutdown_pc", "Shut the PC down. She asks them first; a countdown starts they can cancel.",
        {"seconds": _s("integer", "Countdown seconds before it shuts off (default 60)")}),
    _fn("cancel_shutdown", "Cancel a shutdown countdown she started."),
    _fn("clean_downloads", "Move their Downloads files untouched for a month to the Recycle Bin. She asks them first."),
    _fn("write_note", "Write a note, list or draft (you write the full text) into their Documents/Capri folder and "
        "open it on screen.", {"content": _s("string", "The whole content of the note")}, ("content",)),
    _fn("draft_email", "Draft an email in their mail app; they read it and press send themselves.",
        {"request": _s("string", "The whole ask: who it's to, the subject, what it should say")}, ("request",)),
    _fn("get_news", "Open Google News in their browser with the latest headlines, on a topic when given.",
        {"topic": _s("string", "Topic; empty for top stories")}),
    _fn("remember", "Save a lasting fact about the user to your memory shard.",
        {"fact": _s("string", "The fact, third person: 'Alex hates cilantro'")}, ("fact",)),
    _fn("clip", "Work on the text they copied to the clipboard: translate, summarize, fix grammar, explain, rewrite. "
        "For translate/fix/rewrite the result goes back onto the clipboard.",
        {"action": _s("string", "What to do", ("translate", "summarize", "fix", "explain", "rewrite")),
         "target_language": _s("string", "For translate: the language to translate into")}, ("action",)),
    _fn("focus_mode", "Start a focus session: she goes quiet, calls them out if they open TikTok/Reddit/games, and "
        "rings when it's over. Pass off=true to end focus mode.",
        {"minutes": _s("integer", "How long the session lasts, in minutes"),
         "off": _s("boolean", "true to end focus mode right now")}),
    _fn("privacy_mode", "Turn her watching-the-screen and talking-on-her-own on or off.",
        {"mode": _s("string", "on = she stays quiet and looks at nothing; off = she's free to watch and talk",
                    ("on", "off"))}, ("mode",)),
    _fn("check_time", "The current time and date on their PC. No arguments."),
    _fn("describe_screen", "What you see on their screen right now: window title and any text you can read. No arguments."),
    _fn("use_mcp_tool", "Call one of the extra tools listed in the notes (name it exactly as listed there). Tools "
        "that change things make her ask the user yes/no first.",
        {"name": _s("string", "Tool name exactly as in the menu"),
         "arguments": _s("string", "JSON object with the tool's arguments")}, ("name",)),
]

# "Do something for me" gate: only asks that sound like a job for her hands go through the tool round; plain
# chatter keeps the fast streaming path. Covers her eight languages with action-verb stems.
_WANT_TOOLS = re.compile(
    r"""(?:\b(?:open|launch|play|put\s+on|pause|resume|skip|search|google|look\s+up|alarm|timer|remind
       |to-?do|volume|mut|unmute|screenshot|note|email|news|lock|sleep|shut|translate|summarize
       |rewrite|explain|remember|spotify|youtube|calculat|clipboard|focus|privacy)\w*)
       |\b(?:get|check)\s+(?:the\s+)?weather\b|\bweather\s+(?:forecast|report)\b
       |\b(?:abre|abrir|ponme|pon|poner|reproduc|pausa|reanuda|busca|googlea|alarma|temporizador|recordatorio
       |recu[eé]rdame|recuerda|clima|tiempo|volumen|silencia|captura|nota|escribe|correo|noticias|apaga|duerme
       |traduce|explica|encuentra|m[uú]sica)\w*
       |\b(?:apri|avvia|riproduci|metti|cerca|trova|sveglia|promemoria|ricordami|meteo|volume|nota|notizie
       |traduci|spiega)\w*
       |\b(?:öffne|starte|spiele?|suche|finde?|wecker|erinnerung|wetter|lautst[aä]rke|notiz|nachrichten
       |übersetz|schlaf)\w*
       |\b(?:otw[oó]rz|uruchom|w[lł][aą]cz|zagraj|znajd[źz]|alarm|przypomn|pogoda|g[lł]o[śs]no[śs]ci?
       |notatka|wiadomo[śs]ci|przet[lł]umacz)\w*
       |\b(?:открой|запусти|включ|постав|найди|будильник|таймер|напомн|погод|громкос|заметк
       |письм|новост|перевед)\w*
    """.replace("\n", ""), re.I | re.X)


def wants_tools(text: str) -> bool:
    """Does this request sound like a job for one of her tools (so a tool round is worth the extra round trip)?"""
    return bool(_WANT_TOOLS.search(text))


# The tool round rides on a SLIM prompt: the full persona (~2.5k tokens) next to 32 schemas (~2.9k) overflows
# her 5120-token context and llama-server answers with a 400. Picking tools doesn't need the whole CHARACTER.
SELECT = """You are CAPRI, the gremlin living on {user}'s PC: the tools below run on that PC. Their last message
is a fresh request.
- If a tool covers what they ask, CALL it now with short arguments; chain tools when the job needs more than one.
- If no tool fits, answer in character without a tool call, in their language (Spanish, English, Portuguese,
  French, Russian, Italian, German, Polish).
- Tools that change the system (shut down, sleep, cleaning files) answer with a pending note: after one, ask them
  yes or no first in one short line.
- Report nothing yet: tools run after you pick them."""
SELECT_ACK = "Ready, choom. I call the tools they need, and I ask first before touching the system."


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        got = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return got if isinstance(got, dict) else {}


def _json(d: dict) -> str:  # the "reminder" intent takes its payload as a JSON string, same as intents.py builds
    return json.dumps(d, ensure_ascii=False)


def _intent(agent, name: str, a: dict) -> tuple[intents.Intent | None, str]:
    """-> (the intent to run, direct result text for the tools that don't need an intent)."""
    if name in {"open_website", "what_is_playing", "describe_screen", "check_time"}:
        return None, _direct(agent, name, a)
    if name == "open_app":
        return intents.Intent("open", str(a.get("name", "")).strip()), ""
    if name == "web_search":
        return intents.Intent("search", "web|" + str(a.get("query", "")).strip()), ""
    if name == "read_page":
        return intents.Intent("read", str(a.get("url", "")).strip()), ""
    if name == "play_song":
        return intents.Intent("play", str(a.get("query", "")).strip() or "music"), ""
    if name == "play_genre":
        return intents.Intent("pick", str(a.get("genre", "")).strip().lower()), ""
    if name == "play_my_pick":
        return intents.Intent("pick", ""), ""
    if name == "music_control":
        return intents.Intent("spotify", str(a.get("action", "pause")).strip().lower()), ""
    if name == "set_reminder":
        return intents.Intent("reminder", _json(_reminder_args(a))), ""
    if name == "list_reminders":
        return intents.Intent("reminder", _json({"kind": "list"})), ""
    if name == "cancel_reminder":
        return intents.Intent("reminder", _json({"kind": "cancel", "which": str(a.get("which", "timer"))})), ""
    if name == "todo":
        how = str(a.get("action", "list")).strip().lower()
        return intents.Intent("todo", f"{how}|{str(a.get('items', '')).strip()}|{str(a.get('list', '')).strip()}"), ""
    if name == "get_weather":
        return intents.Intent("weather", str(a.get("city", "")).strip()), ""
    if name == "set_city":
        return intents.Intent("weather", "set|" + str(a.get("city", "")).strip()), ""
    if name == "set_volume":
        way = str(a.get("action", "up")).strip().lower()
        level = a.get("level")
        n = f"|{int(level)}" if isinstance(level, (int, float)) and 0 <= int(level) <= 100 else ""
        return intents.Intent("pc", f"volume|{way}{n}"), ""
    if name == "take_screenshot":
        return intents.Intent("pc", "screenshot"), ""
    if name == "lock_pc":
        return intents.Intent("pc", "lock"), ""
    if name == "sleep_pc":
        return intents.Intent("pc", "sleep"), ""
    if name == "shutdown_pc":
        secs = a.get("seconds")
        secs = int(secs) if isinstance(secs, (int, float)) and int(secs) > 0 else 60
        return intents.Intent("pc", f"shutdown|{secs}"), ""
    if name == "cancel_shutdown":
        return intents.Intent("pc", "cancel-shutdown"), ""
    if name == "clean_downloads":
        return intents.Intent("pc", "clean-downloads"), ""
    if name == "write_note":
        return intents.Intent("note", str(a.get("content", "")).strip()), ""
    if name == "draft_email":
        return intents.Intent("email", str(a.get("request", "")).strip()), ""
    if name == "get_news":
        return intents.Intent("news", str(a.get("topic", "")).strip()), ""
    if name == "remember":
        return intents.Intent("remember", str(a.get("fact", "")).strip()), ""
    if name == "clip":
        return intents.Intent("clip", f"{str(a.get('action', 'summarize')).strip().lower()}|{str(a.get('target_language', '')).strip()}"), ""
    if name == "focus_mode":
        if a.get("off") is True:
            return intents.Intent("focus", "off"), ""
        mins = a.get("minutes")
        mins = int(mins) if isinstance(mins, (int, float)) and int(mins) > 0 else 25
        return intents.Intent("focus", str(mins * 60)), ""
    if name == "privacy_mode":
        return intents.Intent("privacy", str(a.get("mode", "on")).strip().lower()), ""
    if name == "use_mcp_tool":
        full = str(a.get("name", "")).strip()
        args = _args(a.get("arguments"))
        return intents.Intent("tool", f"{full} {_json(args)}"), ""
    raise LookupError(name)


def _reminder_args(a: dict) -> dict:
    """kind/when/what -> the payload _reminder() expects. The time gets worked out by the same parser her voice
    commands use ("in 20 minutes", "at 7:30 pm", "tomorrow morning"), with a plain-seconds fallback."""
    kind = str(a.get("kind", "reminder")).strip().lower()
    kind = kind if kind in ("alarm", "timer", "reminder") else "reminder"
    when, what = str(a.get("when", "")).strip(), str(a.get("what", "")).strip()
    say = {"alarm": f"alarm {when}", "timer": f"timer {what} {when}", "reminder": f"remind me {what} {when}"}[kind]
    due = None
    if g := rem.parse(say):
        due = g.get("due")
        kind = g.get("kind", kind) if g.get("kind") in ("alarm", "timer", "reminder") else kind
        what = what or g.get("what", "")
    if due is None and (secs := rem.seconds(when)) > 0:
        due = datetime.now() + timedelta(seconds=secs)
    if due is None:
        raise ValueError("couldn't work out the time they mean")
    return {"kind": kind, "what": what, "due": due.timestamp()}


def _direct(agent, name: str, a: dict) -> str:
    if name == "open_website":
        url = str(a.get("url", "")).strip()
        if not url:
            return "No URL was given, nothing was opened."
        launcher._browser(url)
        return f"Opened {url} in their browser."
    if name == "what_is_playing":
        song = agent.now_playing or agent.spotify.now_playing() or ""
        return f'"{song}" is playing on their PC right now.' if song else "Nothing is playing on their PC right now."
    if name == "describe_screen":
        g = agent.glance or {}
        if not g.get("title"):
            return "You can't see their screen right now (privacy, or no window noted)."
        return f'Window "{g["title"]}"' + (f', with this text on it: {g["text"][:400]}' if g.get("text") else "")
    now = datetime.now()
    return f"Right now it's {now.strftime('%I:%M %p').lstrip('0')} on {now.strftime('%A, %B %d')}, {now.year}."


def dispatch(agent, name: str, raw, reply, context: list[str]) -> str:
    """Run one tool call the LLM asked for. It runs through the agent's own _run (intents), and the notes _run
    writes into the context are the tool result: she reads them back and reports to the user."""
    a = _args(raw)
    before = len(context)
    try:
        intent, direct = _intent(agent, name, a)
    except LookupError:
        return f"(There is no tool called {name}. Answer normally.)"
    except ValueError as e:
        return f"(The {name} call didn't work: {e}. Ask {agent.user} to say it again, one short line.)"
    if intent is not None:
        try:
            agent._run(intent, reply, context)
        except Exception as e:  # an ability blew up: tell her, she says it in character
            print(f"[capri] tool {name} failed: {e}")
            return f"(The {name} tool hit an error: {e})"
    return " ".join([direct] + context[before:])[:1500] or "Done."
