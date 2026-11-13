"""Switches you flip from her menu (ears mode, talk key, mic, DJ, vibing, eyes, chatter), kept in data/settings.json
so they survive restarts. config.yaml only gives the first-run defaults."""
from __future__ import annotations

import json

from ..config import DATA


class Settings(dict):
    def __init__(self, cfg: dict):
        d = cfg["desk"]
        super().__init__(
            ears=cfg["stt"]["mode"], talk_key=cfg["stt"]["talk_key"], mic="",
            dj=d["dj"], vibe=d["vibe"], eyes=d["eyes"], chatter=d["chatter"],
            # leave me alone (no screen reading, no talking on her own), apps she never reads, reacting to videos /
            # chats / what you type, staying quiet in games, focus mode's end time, your city for the weather
            quiet=False, never_look=[], reactions=True, game_mode=True, focus_until=0.0, city="",
            brain="",  # a .gguf picked from her Brain menu ("" = config.yaml's, "none" = no AI, just her)
            my_brains=[],  # .gguf files on this PC you picked for her (shown in her Brain menu)
            size=0,  # her height in pixels, from her Size menu (0 = config.yaml's)
            whisper_model="",  # portable build: which ggml whisper model her ears use ("" = small)
            skin="capri",  # her look, from her Skins menu (capri/skins.py)
            # her language, for everything: menu, chat replies and ears. "auto" = menu follows the system, replies
            # follow whatever you talk in; one of capri/i18n.py's eight = she sticks to it
            ui_lang="auto",
            lang_set=False,  # the first-run language question was answered (setup or the picker)
            always_mini=False,  # she stays in her mini form, also after a restart (menu > Always mini)
            cc_smart=False,  # Claude Code as her brain in smart mode (her tools + its own, talks as her)
            lay=True,  # her mini form lies down on the taskbar and on top of windows
            skins_seen=[],  # secret skins you've picked once (named in the menu from then on)
            update_told="",  # the newest version she has already told you about (she mentions each one once)
            claude_told=False,  # she has told you once that Claude Code is on this PC and can be her brain
            clips=0,  # "clip that": seconds she keeps recording in the background (0 = off, 30, 60, 120)
            clips_dir="",  # where clips are saved ("" = Videos\Capri Clips)
        )
        self.path = DATA / "settings.json"
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.update({k: v for k, v in saved.items() if k in self})
            if "lang_set" not in saved and saved.get("ui_lang", "auto") != "auto":
                self["lang_set"] = True  # picked a language before 0.5.8: don't ask again
        except (OSError, ValueError, AttributeError):
            pass

    def set(self, key, value):
        self[key] = value
        try:
            DATA.mkdir(exist_ok=True)
            self.path.write_text(json.dumps(dict(self), indent=2), encoding="utf-8")
        except OSError as e:
            print(f"[capri] couldn't save settings: {e}")
