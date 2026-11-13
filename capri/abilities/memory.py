"""Long-term facts plus recent chat history, saved to a JSON "shard"."""
from __future__ import annotations

import difflib
import json
import re
import threading
import time

from ..config import ROOT


class Memory:
    def __init__(self, cfg: dict):
        self.path = ROOT / cfg["memory"]["file"]
        self.turns = cfg["memory"]["history_turns"]
        self.lock = threading.Lock()
        self.data = {"facts": [], "history": []}
        self.eru = None
        if cfg["eru"]["memory"]:
            try:
                from ..eru_bridge import EruMemory

                self.eru = EruMemory(cfg)
                print("[capri] memory: ERU AI vector memory (Capri's own store)")
            except Exception as e:
                print(f"[capri] ERU memory unavailable, using JSON facts only: {e}")
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        self.data.setdefault("facts", [])
        self.data.setdefault("history", [])
        self.data.setdefault("episodes", [])  # old talks, one short summary each: {"text", "t"}
        self.data.setdefault("unsummed", [])  # chat that rolled out of the history, waiting to be summed up

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def facts(self) -> list[str]:
        return [f["text"] for f in self.data["facts"]]

    def relevant(self, query: str, speaker: str, limit: int = 14) -> list[str]:
        """Facts worth putting in the prompt for this turn: ones that share words with what was just said, then the
        vector store's closest matches. Small talk gets none: unrelated facts make her bring up random stuff."""
        facts = self.facts()
        hard_no = [f for f in facts if "never suggest" in f.lower()][-4:]  # things you said you hate: always on
        out: list[str] = []
        if self.eru and _words(query):
            try:
                known = set(facts)
                out += [f for f in self.eru.recall(query, speaker) if f in known and f not in out][:2]
            except Exception as e:
                print(f"[capri] recall failed: {e}")
        words = _stems(query) - {speaker.lower()[:4]}  # their own name matches every fact about them
        if words:  # "songs" finds "song", "playing" finds "plays"; the newer fact wins a tie
            scored = sorted(((len(words & _stems(f)), i, f) for i, f in enumerate(facts)), reverse=True)
            out = list(dict.fromkeys([f for n, _, f in scored if n][:6] + out))
        out = list(dict.fromkeys(hard_no + out))
        # "what's my...", "do you remember...": give her the lot. A question about them with no word in common
        # ("what's my dog's name?" vs "has a corgi named Biscuit") gets them all too, or she draws a blank.
        if _ASKS_ABOUT_ME.search(query) and (len(out) > len(hard_no) or _QUESTION.search(query)):
            out += [f for f in reversed(facts) if f not in out]
        return out[:limit]

    def remember(self, text: str, subject: str = "user") -> None:
        text = text.strip()
        if self.eru and text:
            try:
                self.eru.add(subject, text)
            except Exception as e:
                print(f"[capri] vector memory add failed: {e}")
        with self.lock:
            if not text:
                return
            for f in self.data["facts"]:
                # same fact said again (or updated: "likes X" -> "really likes X"): keep one, the newest wording
                if difflib.SequenceMatcher(None, f["text"].lower(), text.lower()).ratio() > 0.85:
                    f["text"], f["t"] = text, time.time()
                    self._save()
                    return
            self.data["facts"].append({"text": text, "t": time.time()})
            self._save()

    def history(self) -> list[dict]:
        return self.data["history"][-self.turns * 2 :]

    def add_turn(self, user: str, assistant: str) -> None:
        with self.lock:
            self.data["history"] += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
            keep = self.turns * 4
            if len(self.data["history"]) > keep:  # rolled out of the chat: kept until it's summed up as an episode
                self.data["unsummed"] = (self.data["unsummed"] + self.data["history"][:-keep])[-60:]
                self.data["history"] = self.data["history"][-keep:]
            self._save()

    # -- episodes: what you talked about before, long after the chat itself has scrolled away ------------------
    def to_sum(self, at_least: int = 12) -> list[dict]:
        """The old chat waiting to become an episode, once there's enough of it (6 exchanges)."""
        with self.lock:
            return list(self.data["unsummed"]) if len(self.data["unsummed"]) >= at_least else []

    def add_episode(self, text: str, done: int) -> None:
        """Save one talk's summary and drop the `done` old messages it covers."""
        with self.lock:
            text = text.strip()
            if text:
                self.data["episodes"] = (self.data["episodes"] + [{"text": text, "t": time.time()}])[-200:]
            self.data["unsummed"] = self.data["unsummed"][done:]
            self._save()

    def episodes(self, query: str, limit: int = 3) -> list[str]:
        """Past talks worth bringing back for this message: ones sharing words with it, or the latest few when they
        ask about before ("remember when...", "what did we talk about yesterday"). Dated, so she knows how long ago."""
        eps = self.data["episodes"]
        if not eps:
            return []
        if _ASKS_BEFORE.search(query):
            picked = eps[-limit:]
        else:
            words = _stems(query)
            need = 1 if _ASKS_ABOUT_ME.search(query) and _QUESTION.search(query) else 2  # "what was i scared about?"
            scored = sorted(((len(words & _stems(e["text"])), i) for i, e in enumerate(eps)), reverse=True)
            picked = [eps[i] for n, i in scored[:limit] if n >= need]
            picked.sort(key=lambda e: e["t"])
        return [f"{_ago(e['t'])}: {e['text']}" for e in picked]

    def drop_replies(self, bad) -> int:
        """Forget exchanges whose reply bad(text) flags, so a line she got stuck on stops feeding back into her."""
        with self.lock:
            h, keep, i = self.data["history"], [], 0
            while i < len(h):
                pair = h[i : i + 2]
                if len(pair) == 2 and pair[1]["role"] == "assistant" and bad(pair[1]["content"]):
                    i += 2
                    continue
                keep.append(h[i])
                i += 1
            n = (len(h) - len(keep)) // 2
            if n:
                self.data["history"] = keep
                self._save()
            return n


_ASKS_ABOUT_ME = re.compile(r"\b(?:my|me|i|remember|about me)\b", re.I)
_RULE = re.compile(r"\b(?:does not|doesn't|don't|never|hates?|dislikes?|wants? Capri to|call (?:me|him|her|them))\b", re.I)
_STOP = set("the a an and or but to of in on for with is are was were be it this that i you my your me he she they "
            "what how why when who do does did can could would should just like really so very not no yes".split())


_ASKS_BEFORE = re.compile(r"\b(?:remember|recall|last time|yesterday|the other day|earlier|before|last week|"
                          r"we (?:talked|spoke|chatted)|(?:talk|talked|spoke) about|did i (?:tell|say)|i told you)\b", re.I)


_QUESTION = re.compile(r"\?|^\W*(?:what|who|whats|what's|where|when|which|how|do you|did i|do i|am i|tell me)\b", re.I)


def _words(text: str) -> set[str]:
    text = re.sub(r"'s\b|’s\b", "", text.lower())  # "dog's" -> "dog"
    return {w for w in re.findall(r"[a-z0-9']{3,}", text) if w not in _STOP}


def _stems(text: str) -> set[str]:
    """Words cut to their first four letters, so "songs" / "song", "named" / "name" and "scared" / "scary" match."""
    return {w[:4] for w in _words(text)}


def _ago(t: float) -> str:
    """When a talk happened, the way you'd say it: 'earlier today', 'yesterday', '3 days ago', 'on 2 Nov'."""
    days = (time.time() - t) / 86400
    if days < 0.75:
        return "Earlier today"
    if days < 1.75:
        return "Yesterday"
    if days < 14:
        return f"{round(days)} days ago"
    return time.strftime("On %d %b", time.localtime(t)).replace("On 0", "On ")
