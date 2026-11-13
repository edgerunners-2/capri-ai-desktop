"""Her looks. A skin is a folder of sprite sheets (the names and frame counts in desk/sprites.py CLIPS).
The one you picked is kept in data/settings.json ("skin")."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DESK = Path(__file__).resolve().parents[1] / "assets" / "desk"


@dataclass(frozen=True)
class Skin:
    id: str
    name: str
    folder: Path
    can_talk: bool = True  # False: no chat box, no voice, no ears, no brain, she just sits and watches
    secret: bool = False  # shown as "???" in her Skins menu until you've picked it once
    mini: str = ""  # her mini form: one picture in assets/desk/mini ("" = Capri's own mini art, blinks and all)


SKINS = (
    Skin("capri", "Capri", DESK / "skins" / "capri"),
    Skin("watcher", "Watcher", DESK / "skins" / "watcher", can_talk=False, secret=True, mini="watcher.png"),
)
DEFAULT = "capri"


def get(skin_id: str | None) -> Skin:
    """The skin with that id; unknown or empty ids get the default."""
    return next((s for s in SKINS if s.id == skin_id), next(s for s in SKINS if s.id == DEFAULT))


def label(skin: Skin, seen) -> str:
    return "???" if skin.secret and skin.id not in seen else skin.name
