"""Your to-do and shopping lists: "add milk to my shopping list", "what's on my list", "cross off milk". Kept in
data/todo.json; crossed-off items move to a done list instead of disappearing."""
from __future__ import annotations

import json
import re
import threading
import time

from ..config import DATA


def list_name(raw: str) -> str:
    """'my shopping list' / 'grocery' / '' -> 'shopping' / 'shopping' / 'to-do'."""
    w = (raw or "").lower()
    if re.search(r"shop|grocer|buy", w):
        return "shopping"
    m = re.search(r"([a-z]+)\s+list", w)
    if m and m.group(1) not in ("my", "the", "to", "do", "todo", "a", "your"):
        return m.group(1)
    return "to-do"


class Todo:
    def __init__(self):
        self.path = DATA / "todo.json"
        self.lock = threading.Lock()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {"lists": {}, "done": []}

    def _save(self):
        DATA.mkdir(exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, items: list[str], name: str = "to-do") -> list[str]:
        with self.lock:
            lst = self.data["lists"].setdefault(name, [])
            new = [i for i in items if i and i.lower() not in (x.lower() for x in lst)]
            lst.extend(new)
            self._save()
        return new

    def items(self, name: str | None = None) -> dict[str, list[str]]:
        with self.lock:
            lists = self.data["lists"]
            if name:
                return {name: list(lists.get(name, []))}
            return {k: list(v) for k, v in lists.items() if v}

    def done(self, what: str, name: str | None = None) -> list[str]:
        """Cross off the items that match `what` (from one list, or any). -> what was crossed off."""
        want = what.lower().strip()
        hit = []
        with self.lock:
            for lname, lst in self.data["lists"].items():
                if name and lname != name:
                    continue
                keep = []
                for item in lst:
                    if want and (want in item.lower() or item.lower() in want):
                        hit.append(item)
                        self.data["done"].append({"item": item, "list": lname, "t": time.time()})
                    else:
                        keep.append(item)
                self.data["lists"][lname] = keep
            self.data["done"] = self.data["done"][-200:]
            self._save()
        return hit

    def clear(self, name: str) -> int:
        with self.lock:
            lst = self.data["lists"].get(name, [])
            for item in lst:
                self.data["done"].append({"item": item, "list": name, "t": time.time()})
            self.data["lists"][name] = []
            self._save()
        return len(lst)


def split_items(text: str) -> list[str]:
    """'milk, eggs and bread' -> ['milk', 'eggs', 'bread']."""
    parts = re.split(r",\s*|\s+and\s+|\s*&\s*|\s+plus\s+", text.strip(" .!?"))
    return [re.sub(r"^(?:some|a|an|the)\s+", "", p.strip(), flags=re.I) for p in parts if p.strip()]
