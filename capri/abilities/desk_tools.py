"""Everyday jobs on your PC: text files she writes for you (in Documents/Capri, then opened), and email drafts
(your mail app opens with it filled in; you're the one who hits send)."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

CFG: dict = {}  # cfg["tools"], set by the agent


def folder() -> Path:
    if CFG.get("files_dir"):
        return Path(CFG["files_dir"]).expanduser()
    docs = Path.home() / "Documents"
    if sys.platform == "win32":  # OneDrive often moves Documents
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
                docs = Path(buf.value)
        except Exception:
            pass
    return docs / "Capri"


def open_file(path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        from .. import linux

        linux.open_path(str(path))


def write_file(name: str, text: str, open_it: bool = True) -> Path:
    """Saves text as a new file (never overwrites: 'list.txt' becomes 'list (2).txt'), then opens it."""
    stem = re.sub(r"[^\w\s.-]", "", name).strip(" .")[:60] or "note"
    stem = re.sub(r"\.(?:txt|md)$", "", stem, flags=re.I)
    ext = ".md" if name.lower().endswith(".md") else ".txt"
    d = folder()
    d.mkdir(parents=True, exist_ok=True)
    path, i = d / (stem + ext), 2
    while path.exists():
        path, i = d / f"{stem} ({i}){ext}", i + 1
    path.write_text(text.strip() + "\n", encoding="utf-8")
    if open_it:
        try:
            open_file(path)
        except Exception as e:
            print(f"[capri] couldn't open {path}: {e}")
    return path


def email_draft(to: str, subject: str, body: str) -> str:
    """Opens the default mail app with a filled-in draft. Nothing is sent by Capri."""
    to = to.strip() if re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.\w+", to.strip()) else ""
    url = f"mailto:{quote(to)}?subject={quote(subject)}&body={quote(body)}"
    try:
        open_file(url)
    except Exception as e:
        return f"couldn't open the mail app: {e}"
    return f"opened an email draft{' to ' + to if to else ''} about {subject!r}"
