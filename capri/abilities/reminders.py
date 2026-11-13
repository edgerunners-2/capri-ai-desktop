"""Timers, alarms and reminders: "set a timer for 10 minutes", "wake me up at 7", "remind me to stretch in 20
minutes", "what timers do I have", "cancel my alarm". Saved to data/reminders.json so a restart doesn't lose them;
when one is due she says it and an alarm sound plays."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from datetime import datetime, timedelta

from ..config import DATA

_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
          "forty-five": 45, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "a couple of": 2, "a couple": 2,
          "a few": 3}
_N = r"(?:\d+(?:\.\d+)?|a couple(?: of)?|a few|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|" \
     r"fifteen|twenty|thirty|forty(?:[- ]five)?|fifty|sixty|ninety)"
_U = r"(?:s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?)"
# "10 minutes", "an hour and a half", "1 hour 30 minutes", "1h30m", "half an hour", "90 sec"
DURATION = rf"(?:half an? hour|{_N}\s*{_U}(?:\s*(?:and\s+)?(?:a half|{_N}\s*{_U}))?)"
# "7", "7:30", "7am", "7.30 pm", "noon", "midnight"
CLOCK = r"(?:noon|midday|midnight|\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|a\.m\.?|p\.m\.?)?)"

_TIMER = re.compile(rf"\b(?:(?:set|start|make|put on|run)\s+(?:me\s+)?(?:a|an)?\s*)?(?:timer\s+(?:for\s+|of\s+)?"
                    rf"(?P<d1>{DURATION})|(?P<d2>{DURATION})\s+timer)\b(?:\s+(?:for|to)\s+(?P<what>.+?))?[\s.!?]*$", re.I)
_ALARM = re.compile(rf"\b(?:(?:set|make|put)\s+(?:me\s+)?(?:an?\s+)?alarm|alarm|wake me(?: up)?)\s*(?:for|at|in)?\s*"
                    rf"(?:(?P<day>tomorrow|today|tonight)\s*(?:at\s+|for\s+)?)?(?:(?P<dur>{DURATION})|(?P<clock>{CLOCK}))"
                    rf"(?:\s*(?P<day2>tomorrow|today|tonight|in the morning|tonight))?", re.I)
_WHEN = rf"(?:in\s+(?P<dur>{DURATION})|(?:(?P<day>tomorrow|today|tonight)\s+)?at\s+(?P<clock>{CLOCK})" \
        rf"(?:\s+(?P<day2>tomorrow|today|tonight))?|(?P<day3>tomorrow|tonight)(?:\s+morning)?)"
_REMIND_A = re.compile(rf"\bremind me\s+(?:{_WHEN}\s+)?(?:to\s+|of\s+|about\s+|that\s+)?(?P<what>.+?)\s*,?\s*{_WHEN.replace('?P<', '?P<b_')}?[\s.!?]*$", re.I)
_REMIND_B = re.compile(rf"\b{_WHEN},?\s+remind me\s+(?:to\s+|of\s+|about\s+|that\s+)?(?P<what>.+?)[\s.!?]*$", re.I)
_LIST = re.compile(r"\b(?:what|which|any|list|show|tell me|check|how (?:long|much time))\b.{0,30}\b(?:timers?|alarms?|"
                   r"reminders?|left)\b|\btime left\b", re.I)
_CANCEL = re.compile(r"\b(?:cancel|stop|delete|remove|clear|turn off|kill|forget|snooze|dismiss)\b.{0,20}\b(?P<which>"
                     r"timers?|alarms?|reminders?|all (?:of )?(?:them|it))\b", re.I)


def _amount(word: str) -> float:
    w = word.lower().strip()
    return float(w) if re.match(r"^\d", w) else float(_WORDS.get(w, 1))


def _unit(word: str) -> int:
    w = word.lower()
    return 3600 if w.startswith("h") else 1 if w.startswith("s") else 60


def seconds(text: str) -> float:
    """'an hour and a half' -> 5400, '1h30m' -> 5400, '10 minutes' -> 600."""
    t = text.lower()
    if re.match(r"half an? hour", t):
        return 1800
    half = bool(re.search(r"\band a half\b", t))
    t = re.sub(r"\band a half\b", "", t)
    total, last = 0.0, 60
    for m in re.finditer(rf"({_N})\s*({_U})\b", t):
        last = _unit(m.group(2))
        total += _amount(m.group(1)) * last
    if half:
        total += 0.5 * last
    return total


def _clock(text: str, day: str, now: datetime) -> datetime:
    """'7' / '7:30pm' / 'noon' (+ 'tomorrow') -> the next datetime it means."""
    t = text.lower().replace(".", ":").replace(" ", "")
    if t in ("noon", "midday"):
        h, mi, ap = 12, 0, "pm"
    elif t == "midnight":
        h, mi, ap = 0, 0, "am"
    else:
        m = re.match(r"(\d{1,2})(?::(\d{2}))?(am|pm|a:m:?|p:m:?)?", t)
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(":", "")
    if ap.startswith("p") and h < 12:
        h += 12
    if ap.startswith("a") and h == 12:
        h = 0
    due = now.replace(hour=h % 24, minute=mi, second=0, microsecond=0)
    if day == "tomorrow":
        return due + timedelta(days=1)
    if day == "tonight" and not ap and h < 12:
        due += timedelta(hours=12)
    while due <= now:  # "at 5" when it's 5:30 -> 5 pm (no am/pm said), else tomorrow
        due += timedelta(hours=12) if not ap and h < 12 and due + timedelta(hours=12) > now else timedelta(days=1)
    return due


def _due(g: dict, now: datetime, prefix: str = "") -> datetime | None:
    get = lambda k: g.get(prefix + k)  # noqa: E731
    if get("dur"):
        return now + timedelta(seconds=seconds(get("dur")))
    day = (get("day") or get("day2") or get("day3") or "").lower()
    if get("clock"):
        return _clock(get("clock"), day, now)
    if day == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    if day == "tonight":
        return now.replace(hour=20, minute=0, second=0, microsecond=0)
    return None


def _tidy(what: str) -> str:
    what = re.sub(r"^(?:to|of|about|that)\s+", "", what.strip(" ,.!?"), flags=re.I)
    what = re.sub(r"\s+(?:please|pls|plz|capri|for me|real quick|ok(?:ay)?)$", "", what, flags=re.I)
    what = re.sub(r"\bmy\b", "your", what, flags=re.I)
    what = re.sub(r"\bI(?:'m| am)\b", "you're", what)
    return re.sub(r"\b(?:I|me)\b", "you", what)


def parse(text: str, now: datetime | None = None) -> dict | None:
    """-> {"kind": timer|alarm|reminder, "what", "due": datetime} | {"kind": "list"} | {"kind": "cancel", "which"} |
    None when it isn't about timers at all."""
    now = now or datetime.now()
    t = re.sub(r"(?:[\s,]+(?:please|pls|plz|capri|for me|thanks|thank you|ok(?:ay)?|real quick|rn|right now))+[\s.!?]*$",
               "", text.strip(), flags=re.I)
    if m := _CANCEL.search(t):
        return {"kind": "cancel", "which": m.group("which").lower()}
    if _LIST.search(t) and not re.search(r"\b(?:set|start|make|remind me)\b", t, re.I):
        return {"kind": "list"}
    if re.search(r"\bremind me\b", t, re.I):
        for rx, pre in ((_REMIND_B, ""), (_REMIND_A, "b_"), (_REMIND_A, "")):
            m = rx.search(t)
            if m and (due := _due(m.groupdict(), now, pre)):
                return {"kind": "reminder", "what": _tidy(m.group("what")), "due": due}
        return None
    if m := _TIMER.search(t):
        secs = seconds(m.group("d1") or m.group("d2"))
        if secs > 0:
            return {"kind": "timer", "what": _tidy(m.group("what") or ""), "due": now + timedelta(seconds=secs),
                    "length": secs}
    if (m := _ALARM.search(t)) and (due := _due(m.groupdict(), now)):
        return {"kind": "alarm", "what": "", "due": due}
    return None


def say_when(due: datetime, now: datetime | None = None) -> str:
    """'at 7:30 AM tomorrow', 'in 10 minutes', 'at 5:00 PM'."""
    now = now or datetime.now()
    left = (due - now).total_seconds()
    if left < 3600:
        return "in " + span(left)
    clock = due.strftime("%I:%M %p").lstrip("0")
    if due.date() == now.date():
        return f"at {clock}"
    if due.date() == (now + timedelta(days=1)).date():
        return f"at {clock} tomorrow"
    return due.strftime(f"at {clock} on %A, %B %d").replace(" 0", " ")


def span(secs: float) -> str:
    secs = max(0, int(round(secs)))
    h, rest = divmod(secs, 3600)
    m, s = divmod(rest, 60)
    parts = [f"{h} hour{'s' * (h != 1)}"] * bool(h) + [f"{m} minute{'s' * (m != 1)}"] * bool(m)
    if not h and (s or not m):
        parts.append(f"{s} second{'s' * (s != 1)}")
    return " and ".join(parts)


class Reminders:
    """The saved list plus a little clock thread. on_due(item) is called (from that thread) when one goes off."""

    def __init__(self, on_due=None):
        self.path = DATA / "reminders.json"
        self.lock = threading.Lock()
        self.on_due = on_due or (lambda item: None)
        self.items: list[dict] = []
        try:
            self.items = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = []
        self._started = False

    def start(self):
        """Start ringing (once whoever shows them is ready; ones that came due while she was off go off now)."""
        if not self._started:
            self._started = True
            threading.Thread(target=self._loop, daemon=True).start()

    def _save(self):
        try:
            DATA.mkdir(exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.items, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            print(f"[capri] couldn't save reminders: {e}")

    def add(self, kind: str, what: str, due: datetime) -> dict:
        item = {"kind": kind, "what": what, "due": due.timestamp(), "set": time.time()}
        with self.lock:
            self.items.append(item)
            self.items.sort(key=lambda i: i["due"])
            self._save()
        print(f"[capri] {kind} set {say_when(due)}: {what!r}")
        return item

    def upcoming(self) -> list[dict]:
        with self.lock:
            return list(self.items)

    def cancel(self, which: str) -> list[dict]:
        """'timer' / 'alarms' / 'reminder' / 'all of them': the soonest one of that kind, or every one for plurals."""
        kind = re.sub(r"s$", "", which) if which.startswith(("timer", "alarm", "reminder", "focus")) else ""
        every = which.endswith("s") or which.startswith("all")
        with self.lock:
            hits = [i for i in self.items if not kind or i["kind"] == kind]
            if not every:
                hits = hits[:1]
            self.items = [i for i in self.items if i not in hits]
            self._save()
        stop_sound()
        return hits

    def _loop(self):
        while True:
            time.sleep(1)
            now = time.time()
            with self.lock:
                due = [i for i in self.items if i["due"] <= now]
                if due:
                    self.items = [i for i in self.items if i["due"] > now]
                    self._save()
            for item in due:
                late = now - item["due"]
                if late > 6 * 3600 and item["kind"] != "reminder":
                    continue  # a timer from before the PC slept all night: not worth ringing for
                try:
                    ring()
                    self.on_due(item)
                except Exception as e:
                    print(f"[capri] reminder failed: {e}")


def describe(item: dict) -> str:
    """How she announces it going off."""
    what = item.get("what") or ""
    if item["kind"] == "timer":
        return f"Timer's done{': ' + what if what else ''}!"
    if item["kind"] == "focus":
        return "Focus time's over! Nice work, take a break."
    if item["kind"] == "alarm":
        return "Wake up, it's " + datetime.now().strftime("%I:%M %p").lstrip("0") + "! That's your alarm."
    return f"Reminder: {what}."


_ringing = threading.Event()


def ring(times: int = 4) -> None:
    """The Windows alarm sound a few times (or the default beep), without blocking anything."""
    if sys.platform != "win32":
        print("\a", end="", flush=True)
        return
    _ringing.set()

    def go():
        import os
        import winsound

        wav = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Media", "Alarm01.wav")
        for _ in range(times):
            if not _ringing.is_set():
                break
            try:
                if os.path.exists(wav):
                    winsound.PlaySound(wav, winsound.SND_FILENAME)
                else:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                    time.sleep(1)
            except RuntimeError:
                break
        _ringing.clear()

    threading.Thread(target=go, daemon=True).start()


def stop_sound() -> None:
    _ringing.clear()
    if sys.platform == "win32":
        try:
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
