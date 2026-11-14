"""Everyday asks, caught in code: privacy ("leave me alone", "never look at Discord"), focus mode, weather,
clipboard jobs (translate / summarize / fix what you copied), to-do and shopping lists, and PC controls (volume,
screenshot, lock, shutdown, sleep, Downloads clean-up)."""
from __future__ import annotations

import re

from ..abilities.reminders import DURATION, seconds
from .intents import Intent

_TAIL = re.compile(r"(?:[\s,]+(?:for me|please|pls|plz|capri|campy|real quick|rn|right now|now|thanks|thank you|"
                   r"choom|bro|ok(?:ay)?))+[\s.!?]*$", re.I)

# -- privacy -----------------------------------------------------------------------------------------------
_ASK = r"^\W*(?:(?:hey|yo|ok(?:ay)?|alright)\W+)?(?:capri\W+)?(?:(?:please|pls|just|can you|could you|now)\s+)*"
_QUIET_ON = re.compile(_ASK + r"(?:leave me alone|stop (?:watching|looking at|reading) (?:me|my screen|my stuff)|"
                       r"(?:turn on |go into |enable )?privacy mode(?: on)?|stop spying(?: on me)?|don'?t (?:watch|look at) "
                       r"(?:me|my screen)|stop stalking me|give me (?:some )?privacy)"
                       r"(?:\s+(?:for (?:a (?:while|bit)|now|today|an hour)|please|pls|rn|capri))*\W*$", re.I)
_QUIET_OFF = re.compile(r"\b(?:you can (?:watch|look|talk|react|come back)(?: again)?|privacy mode off|"
                        r"(?:turn off|disable|end) privacy mode|start watching again|come back(?: out)?|"
                        r"i missed you|you can stalk me again)\b", re.I)
_NEVER = re.compile(r"\b(?:never|don'?t ever|do not|don'?t|stop) (?:look(?:ing)? at|read(?:ing)?|watch(?:ing)?) "
                    r"(?:my\s+)?(?P<app>[\w .+-]{2,30}?)(?:\s+(?:again|ever|anymore|app|window|chats?|messages?))*[\s.!?]*$"
                    r"|\bstay out of (?:my\s+)?(?P<app2>[\w .+-]{2,30}?)[\s.!?]*$", re.I)
_EVER = re.compile(r"\byou can (?:look at|read|watch) (?:my\s+)?(?P<app>[\w .+-]{2,30}?) again\b", re.I)

# -- focus -------------------------------------------------------------------------------------------------
_FOCUS_ON = re.compile(rf"\b(?:help me (?:focus|study|lock in|grind)|(?:start |turn on |begin )?focus mode(?: on)?|"
                       rf"i (?:need|have|got|gotta|wanna|want) to (?:focus|study|lock in|work|grind)|"
                       rf"(?:let'?s|time to) (?:focus|study|lock in|grind)|keep me (?:focused|on track)|pomodoro)\b"
                       rf"(?:.*?\b(?:for|in)\s+(?P<dur>{DURATION}))?", re.I)
_FOCUS_OFF = re.compile(r"\b(?:stop|end|turn off|cancel|exit) focus(?: mode)?\b|\bfocus mode off\b|"
                        r"\bi'?m done (?:focusing|studying|working)\b|\bbreak time\b", re.I)

# -- weather -----------------------------------------------------------------------------------------------
_WEATHER = re.compile(r"\b(?:weather|forecast|temperature (?:outside|today|tomorrow)|how (?:hot|cold|warm) is it|"
                      r"is it (?:gonna|going to) (?:rain|snow|be (?:hot|cold|sunny))|will it (?:rain|snow)|"
                      r"do i need (?:an? )?(?:jacket|umbrella|coat|hoodie|sweater)|should i (?:bring|wear|take) "
                      r"(?:an? )?(?:jacket|umbrella|coat|hoodie|sweater))\b", re.I)
_IN_CITY = re.compile(
    r"\b(?:in|for|at)\s+(?P<city>[A-Za-zÀ-ÖØ-öø-ÿ][\w' -]{1,40}?)"
    r"(?=\s*(?:[?!.,]|$|\b(?:today|tomorrow|tonight)\b))",
    re.I | re.U
)
_VOL_NATURAL = re.compile(
    r"\b(?:make (?:it|the volume) (?:louder|quieter|softer)|"
    r"lower (?:my |the )?volume|raise (?:my |the )?volume|"
    r"turn (?:the )?volume all the way (?:up|down))\b",
    re.I
)
_MY_CITY = re.compile(r"\b(?:my city is|i live in|i'?m (?:from|in|based in)|my location is)\s+(?P<city>[A-Za-z][\w' -]{1,40}?)"
                      r"[\s.!?]*$", re.I)

# -- clipboard ---------------------------------------------------------------------------------------------
_CLIP_THING = r"(?:this|that|it|what i (?:just )?copied|my clipboard|the clipboard|the text|my text|the copied text)"
_CLIP = re.compile(rf"\b(?P<verb>translate|summari[sz]e|sum up|tl;?dr|fix (?:the )?(?:grammar|spelling|typos)(?: (?:in|of|on))?|"
                   rf"proofread|rewrite|reword|rephrase|explain|make)\s+{_CLIP_THING}"
                   rf"(?:\s+(?:(?:in|into|to)\s+(?P<lang>[A-Za-z]+)|(?P<how>shorter|longer|nicer|more formal|formal|"
                   rf"professional|casual|friendlier|funnier|simpler|polite)))?", re.I)

# -- lists -------------------------------------------------------------------------------------------------
_LIST_WORD = r"(?:my|the|our)\s+(?:(?:to-?\s?do|todo|shopping|grocery|groceries|[a-z]+)\s+)?list"
_TODO_ADD = re.compile(rf"\b(?:add|put|throw|stick|write down)\s+(?P<items>.+?)\s+(?:to|on|onto|in)\s+(?P<list>{_LIST_WORD})\b", re.I)
_TODO_DONE = re.compile(rf"\b(?:cross off|check off|tick off|remove|delete|take off|scratch)\s+(?P<item>.+?)\s+(?:from|off)\s+"
                        rf"(?P<list>{_LIST_WORD})\b|\b(?:cross|check|tick|scratch) (?P<item2>.+?) off\b", re.I)
_TODO_SHOW = re.compile(rf"\b(?:what(?:'?s| is) on|read(?: me)?|show(?: me)?|check|open|what do i have on|tell me)\s+(?P<list>{_LIST_WORD}s?)"
                        rf"|\bwhat(?:'?s| do i have) (?:left )?to do\b|\bwhat do i need to (?:buy|get)\b", re.I)
_TODO_CLEAR = re.compile(rf"\b(?:clear|empty|wipe|reset)\s+(?P<list>{_LIST_WORD})\b", re.I)

# -- PC controls -------------------------------------------------------------------------------------------
_VOL_SET = re.compile(r"\b(?:set|put|turn)\s+(?:the\s+)?(?:volume|sound)\s+(?:to|at)\s+(?P<n>\d{1,3})\s*(?:%|percent)?", re.I)
_VOL = re.compile(r"\b(?:(?:turn|crank|pump|bring)\s+(?:it|the (?:volume|sound|music))\s+(?P<d1>up|down)|"
                  r"(?:volume|sound)\s+(?P<d2>up|down)|(?P<d3>louder|quieter|lower the volume|raise the volume))"
                  r"(?:\s+(?:by\s+)?(?P<n>\d{1,3})\s*(?:%|percent))?", re.I)
_MUTE = re.compile(r"^\W*(?:(?:hey|yo)\W+)?(?:capri\W+)?(?:please\s+)?(?P<un>un)?mute(?:\s+(?:the|my)\s+(?:pc|sound|"
                   r"audio|computer|volume))?\W*$|\b(?P<un2>un)?mute (?:the|my) (?:pc|sound|audio|computer)\b", re.I)
_SHOT = re.compile(r"\b(?:take|grab|snap|get)\s+(?:a\s+|me\s+a\s+)?screen\s?shot\b|^\W*screen\s?shot(?: this| that)?\W*$", re.I)
_LOCK = re.compile(r"\block (?:my|the) (?:pc|computer|screen|laptop)\b", re.I)
_SHUT = re.compile(rf"\b(?:shut\s?down|turn off|power off)\s+(?:my |the )?(?:pc|computer|laptop)?\s*(?:in\s+(?P<dur>{DURATION}))?", re.I)
_SHUT_CANCEL = re.compile(r"\b(?:cancel|stop|abort)\s+(?:the\s+)?shut\s?down\b|\bdon'?t shut (?:it )?down\b", re.I)
_SLEEP = re.compile(r"\b(?:put\s+(?:my |the )?(?:pc|computer|laptop) to sleep|(?:sleep|hibernate) (?:my |the )?(?:pc|computer|laptop))\b", re.I)
_CLEAN = re.compile(r"\b(?:clean|clear|tidy|empty)\s+(?:up\s+)?(?:my\s+|the\s+)?downloads?(?: folder)?\b", re.I)


def detect(text: str) -> Intent | None:
    if not text or not text.strip():
        return None
    t = _TAIL.sub("", text.strip())
    if m := _EVER.search(t):
        return Intent("privacy", "allow|" + m.group("app").strip())
    if _QUIET_ON.search(t):
        return Intent("privacy", "on")
    if _QUIET_OFF.search(t):
        return Intent("privacy", "off")
    if (m := _NEVER.search(t)) and not re.search(r"\b(?:me|this|that|it)\b", (m.group("app") or m.group("app2") or ""), re.I):
        return Intent("privacy", "never|" + (m.group("app") or m.group("app2")).strip())
    if _FOCUS_OFF.search(t):
        return Intent("focus", "off")
    if m := _FOCUS_ON.search(t):
        return Intent("focus", str(int(seconds(m.group("dur"))) if m.group("dur") else 3600))
    if m := _MY_CITY.search(t):
        return Intent("weather", "set|" + m.group("city").strip())
    if _WEATHER.search(t):
        m = _IN_CITY.search(text)
        city = m.group("city") if m and not re.match(r"^(?:The|My|Today|Tomorrow|Here)$", m.group("city")) else ""
        return Intent("weather", city)
    if m := _CLIP.search(t):
        verb = m.group("verb").lower()
        if verb == "make" and not m.group("how"):
            return None
        job = ("translate" if verb.startswith("translate") else "summarize" if verb.startswith(("summar", "sum up", "tl"))
               else "fix" if verb.startswith(("fix", "proofread")) else "explain" if verb == "explain" else "rewrite")
        return Intent("clip", f"{job}|{m.group('lang') or m.group('how') or ''}")
    if m := _TODO_DONE.search(t):
        return Intent("todo", f"done|{m.group('item') or m.group('item2')}|{m.group('list') or ''}")
    if m := _TODO_CLEAR.search(t):
        return Intent("todo", f"clear||{m.group('list')}")
    if m := _TODO_ADD.search(t):
        return Intent("todo", f"add|{m.group('items')}|{m.group('list')}")
    if m := _TODO_SHOW.search(t):
        lst = m.group("list") or ("shopping list" if re.search(r"\bbuy|get\b", t, re.I) else "")
        return Intent("todo", f"show||{lst}")
    if _SHUT_CANCEL.search(t):
        return Intent("pc", "cancel-shutdown")
    if m := _VOL_SET.search(t):
        return Intent("pc", f"volume|set|{m.group('n')}")
    if m := _VOL_NATURAL.search(t):
        way = "up" if any(x in m.group().lower()
        for x in ("louder", "raise", "all the way up")) else "down"
        return Intent("pc", f"volume|{way}|")
    if m := _VOL.search(t):
        d = (m.group("d1") or m.group("d2") or m.group("d3") or "").lower()
        way = "up" if d in ("up", "louder", "raise the volume") else "down"
        return Intent("pc", f"volume|{way}|{m.group('n') or ''}")
    if m := _MUTE.search(t):
        return Intent("pc", "volume|" + ("unmute" if (m.group("un") or m.group("un2")) else "mute") + "|")
    if _SHOT.search(t):
        return Intent("pc", "screenshot")
    if _LOCK.search(t):
        return Intent("pc", "lock")
    if _SLEEP.search(t):
        return Intent("pc", "sleep")
    if _CLEAN.search(t):
        return Intent("pc", "clean-downloads")
    if (m := _SHUT.search(t)) and re.search(r"\b(?:pc|computer|laptop|shut\s?down)\b", t, re.I):
        return Intent("pc", "shutdown|" + str(int(seconds(m.group("dur"))) if m.group("dur") else 60))
    return None
