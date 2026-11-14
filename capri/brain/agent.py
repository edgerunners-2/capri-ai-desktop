"""Turns what the user said into Capri's reply, running abilities (music, apps, web, files, email, MCP tools,
memory) along the way."""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from ..abilities import desk_tools, launcher, pc, reminders, screen, todo, weather, web
from ..abilities.mcp import Hub
from ..abilities.memory import Memory
from ..abilities.spotify import Spotify
from . import intents
from . import llm as llm_mod
from . import persona
from . import tools

# still honoured if the model volunteers a tag, but abilities no longer depend on it
TAG = re.compile(r"\[\[\s*(\w+)\s*(?::\s*(.*?))?\s*\]\]", re.S)


@dataclass
class Reply:
    text: str
    go_idle: bool = False
    actions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Agent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.user = cfg.get("user_name") or "choom"
        self.memory = Memory(cfg)
        self.spotify = Spotify(cfg)
        self.llm = llm_mod.create(cfg)
        web.CFG = cfg.get("search") or {}
        desk_tools.CFG = cfg.get("tools") or {}
        self.mcp = Hub(cfg)
        self.pending: intents.Intent | None = None  # an MCP tool waiting for their yes
        self.glance: dict | None = None  # her last look at your screen: {"t", "title", "text"} (kept fresh by the app)
        self.now_playing = ""  # the YouTube song in your browser right now, if any
        self.on_played = lambda result: None  # the app hears what actually started (DJ: don't judge her own pick)
        self.recent_own: list[str] = []  # things she said unprompted (kept out of the chat history)
        self._avoid: list[str] = []  # openers and slang she's overused lately (pushed down at the token level)
        self._stale: set[str] = set()  # four-word runs from her recent replies: a later sentence reusing one gets cut
        self._stream_raw = ""
        self._fresh = False  # this reply is about a chat she just read: leave the old chat history out
        self._looked = False  # this reply already has a real look at the screen (or an ability's result) in it
        self._saw = (0.0, "")  # (when, "screen" | "chat"): her last real look, so "look again" / "answer the rest" redo it
        self.slow = False  # her brain took ages to start a reply (a weak PC): shorter prompts, no extra planning calls
        self.clip = lambda seconds=None: "couldn't clip: clips aren't set up"  # set by the app: "clip that"
        self.eyes = None  # real sight (abilities/sight.py), set by the app once it has loaded
        from ..abilities.music import Playlist

        self.playlist = Playlist()
        self._said = ""  # what they just said (the music pick reads it)
        self.genre, self.genre_at = "", 0.0  # the genre they last left to her ("another one" stays in it)
        # timers / alarms / reminders: the app hooks on_reminder up, then calls self.reminders.start()
        self.on_reminder = lambda item: print(f"[capri] {reminders.describe(item)}")
        self.reminders = reminders.Reminders(on_due=lambda item: self.on_reminder(item))
        self.todo = todo.Todo()
        # her own updates: the app hooks these up (a line to say while it downloads, and quit-and-install)
        self.on_update_line = lambda text: print(f"[capri] {text}")
        self.on_line = lambda text: print(f"[capri] {text}")  # a line to say right away (before a long job)
        self.on_install = lambda path: print(f"[capri] would install {path}")
        self.on_mini = lambda: print("[capri] would go mini")
        self.on_lay = lambda: print("[capri] would lie down")  # the app lays her down (full size)  # the app shrinks her into her mini form
        self._updating = False
        # her switches (the app's Settings: quiet, never_look, focus_until, city...); a plain dict when run headless
        self.settings = _Plain(quiet=False, never_look=[], focus_until=0.0, city="", reactions=True, game_mode=True)

    @property
    def no_brain(self) -> bool:
        return bool(getattr(self.llm, "no_brain", False))

    def warm_up(self) -> None:
        """Read the fixed prompt once at boot, so the first real reply is as fast as the rest."""
        import time

        if self.no_brain or getattr(self.llm, "no_warm", False):
            return

        t = time.monotonic()
        lang = self._recent_language()  # same prefix her first real reply uses: it's cached from here on
        self.llm.chat(persona.build(self.user, language=lang) + [{"role": "user", "content": "hey\n" + persona.reminder(lang)}],
                      max_tokens=1)
        print(f"[capri] brain warm in {time.monotonic() - t:.1f}s")

    def _aux(self, prompt: str, **kw) -> str:
        """A behind-the-scenes question (plan an action, pull out facts) asked on top of her usual prompt and
        history: llama.cpp keeps that whole prefix cached, so the next real reply doesn't re-read 2k tokens."""
        msgs = persona.build(self.user, language=self._recent_language()) + self.memory.history()
        msgs.append({"role": "user", "content": prompt})
        return self.llm.chat(msgs, **kw)

    def _notes(self, about: str, screen: bool | None = None) -> list[str]:
        """What she knows right now: facts about you, what's on your screen, what's playing. The screen only rides
        along when the chat is about what they're doing, otherwise she'd talk about it no matter what you ask."""
        import time

        if screen is None:
            screen = bool(_ABOUT_SCREEN.search(about))
        out = []
        facts = self.memory.relevant(about, self.user, limit=10)
        if facts:
            out.append(f"(Things you remember about {self.user}, use only if relevant: " + "; ".join(facts) + ")")
        if hasattr(self.memory, "episodes") and (eps := self.memory.episodes(about)):
            out.append(f"(Earlier talks you remember with {self.user}, use only if relevant: " + " / ".join(eps) + ")")
        g = self.glance
        if screen and g and time.time() - g["t"] < 180:
            seen = f'(What you see on {self.user}\'s screen: the window "{g["title"]}"'
            seen += f', with this text on it: {g["text"][:400]})' if g["text"] else ")"
            out.append(seen)
        if self.now_playing and (screen or _ABOUT_MUSIC.search(about)):
            out.append(f'(Song playing on {self.user}\'s PC right now: "{self.now_playing}". If they ask what\'s '
                       "playing, that's the answer.)")
        return out

    def fixed_(self) -> str:
        """The  picked in setup / her  menu ("" = Auto). When set, she always answers in it."""
        from .. import i18n

        code = str(self.settings.get("ui_lang", "auto") or "auto").lower()
        return code if code in i18n.LANGS else ""

    def _recent_(self) -> str:
        """The  she is talking in right now: the picked one, else the last thing they said that showed a
        language, else the interface's language (the system's), never a guess like Spanish."""
        fixed = self.fixed_language()
        if fixed:
            return fixed
        for message in reversed(self.memory.history()):
            if message.get("role") == "user":
                language = persona.detect_language(message.get("content", ""), "")
                if language:
                    return language
        from .. import i18n

        return i18n.lang() if i18n.lang() in persona.NAMES else "en"

    def _language(self, text: str) -> str:
        return self.fixed_language() or persona.detect_language(text, self._recent_language())

    def _messages(self, user_text: str, context: list[str]) -> list[dict]:
        language = self._language(user_text)
        self._lang = language  # the reply's language: a sentence that drifts out of it is cut (_off_language)
        # small local brains need her example lines to get her voice; Claude Code reads them as real chat ("asking
        # twice?") and pays for them every turn, so it gets her character alone
        msgs = persona.build(self.user, examples=getattr(self.llm, "few_shot", True), language=language)
        last = user_text
        if not self._fresh:  # answering about a chat she just read: old talk (other chats) would leak into it
            msgs += self.memory.history()[-8:] if self.slow else self.memory.history()
            # her last glance rides along unless a real look or an ability already said what matters (the style
            # notes added later, "don't open with ...", don't count: they used to keep her blind every time)
            context = self._notes(user_text, screen=False if self._looked else None) + context
        if context:
            # notes ride inside the newest user turn: these templates reject extra system messages
            last += "\n\n[" + " ".join(context) + "]"
        msgs.append({"role": "user", "content": last + "\n" + persona.reminder(language)})
        return msgs

    def _song_on_screen(self) -> str:
        """The song in an open YouTube tab ('Kavinsky - Nightcall'), '' if there isn't one."""
        try:
            from ..abilities import vision

            return vision.youtube_song(vision.window_titles())
        except Exception:
            return ""

    def _run(self, intent: intents.Intent, reply: Reply, context: list[str]) -> None:
        k, arg = intent.kind, intent.arg
        own_pick = ""
        egg = False
        if (k == "pick" or (k == "play" and arg.endswith("@pick"))) and self._sad() and random.random() < 0.33:
            # a secret: down and asking for music (no song named), and sometimes she puts on THE Edgerunners song
            from ..abilities.music import OSTS

            egg = True
            k, arg = ("play" if arg.startswith("spotify|") else "youtube"), OSTS[0]
            print("[capri] easter egg: I Really Want to Stay at Your House")
        if k == "pick":
            import time

            arg = arg.removeprefix("yes|")  # "jazz?" -> "want me to put some on?" -> "yeah"
            from ..abilities.music import GENRES

            # "play some jazz": a jazz track she rates. "something else" right after: another one of that genre.
            more = bool(_ANOTHER.search(self._said))  # "another one", "something else": not a fresh "play music"
            genre = arg if arg in GENRES else (self.genre if more and time.time() - self.genre_at < 1800 else "")
            if genre:
                self.genre, self.genre_at = genre, time.time()
                own_pick = self.playlist.next(genre)
                # their Spotify app when they have one (the "play" step below), else YouTube in the browser
                k, arg = ("play" if self.spotify.sp or _has_spotify() else "youtube"), own_pick
            else:  # they want music, she chooses: one of her Night City favourites
                self.genre = ""
                k, arg = "youtube", self.playlist.next()
                context.append("You chose this one yourself, it's one of your favourites.")
        if k == "play" and not arg.startswith("spotify|") and not self.spotify.sp and not _has_spotify():
            k = "youtube"  # no Spotify app on this PC: YouTube in the browser plays it instead
        arg = arg.removeprefix("spotify|")
        if k == "play" and arg == "@pick":
            arg = self.playlist.next()
            context.append("You chose this one yourself, it's one of your favourites.")
        if k == "play" and arg == "@now":  # "play this on spotify": the song that's on right now
            arg = self.spotify.now_playing() or self.now_playing or self._song_on_screen()
            if not arg:
                context.append(f"{self.user} asked you to put 'this' song on Spotify, but nothing is playing that you can "
                               "see. Ask them which song, in one short line.")
                return
            arg = re.sub(r"\s*[\(\[](?:official|lyric|music|audio|hd|4k)[^\)\]]*[\)\]]", "", arg, flags=re.I).strip()
        if k == "clip":  # "capri, clip that": the last 30 s / 1 min of the screen, saved as a video
            result = self.clip(int(arg) if arg.isdigit() else None)
            reply.actions.append(result)
            reply.notes.append(result)
            context.append(f"(You {result}. Say so in one short hyped line" + (".)" if result.startswith("saved") else
                           ", and tell them how to turn clips on if they're off.)"))
            return
        if k == "closeapp":  # "close discord", "quit valorant": like its X button, never forced
            result = launcher.close_app(arg)
            reply.actions.append(result)
            reply.notes.append(result)
            context.append(f"(You {result} on their PC. Say so in one short line"
                           + (".)" if result.startswith("closed") else ", don't pretend it worked.)"))
            return
        if k == "closetab":  # "close the youtube tab", "close all the tabs you opened"
            result = launcher.close_tabs(arg)
            reply.actions.append(result)
            reply.notes.append(result)
            if result.startswith("closed"):
                context.append(f"(You just {result} in their browser. Say so in one short line.)")
            else:
                context.append(f"(You tried to close a browser tab but {result}. Say so in one short line and ask "
                               "which tab they mean.)")
            return
        if k == "chatgame":  # "play a game with me": right here in the chat, nothing opens
            which = "" if re.match(r"^(?:a |some |another |any )?(?:\w+ )?(?:games?|something)$", arg, re.I) else arg
            context.append(f"({self.user} wants to play {which or 'a game'} with you, right here in the chat. "
                           + (f"Start {which} now: make the first move or ask the first question."
                              if which else "Pick a quick one you can play by text (20 questions, would you rather, "
                                            "trivia, riddles, rock paper scissors) and start it right away.")
                           + " Don't open any app, game or song.)")
            return
        if k == "open":
            result = launcher.open_thing(arg.removeprefix("planned|"))  # an open request never turns into a Google search
            reply.actions.append(result)
            reply.notes.append(result)
            if result.startswith("couldn't"):
                context.append(f"(You tried to open it on their PC: you {result}. Nothing was searched online. Say so in "
                               "one short line and ask for the exact app or game name.)")
            else:
                context.append(f"You just {result} on their PC. React in one short, funny line.")
        elif k == "youtube":
            import threading
            import time

            def go():
                result = launcher.youtube_play(arg)
                print(f"[capri] {result}")
                self.on_played(result)

            threading.Thread(target=go, daemon=True).start()  # she answers while YouTube loads
            self._music_at = time.monotonic()
            reply.actions.append(f"youtube: {arg}")
            reply.notes.append(f"putting on {arg}")
            song = arg.split(" (")[0]
            if egg:
                context.append(_EGG.format(user=self.user))
            elif own_pick:  # her own pick from her genre list: the name is exact, so she can say it
                context.append(self._own_pick_note(own_pick, "YouTube"))
            else:
                context.append(f'It\'s happening: you are putting on "{song}" for {self.user} right now. Say you\'re '
                               "putting it on, plus a quick reaction or a tease. Don't name the song or the artist (the "
                               "title often comes out wrong). Never say no to it.")
        elif k == "play":
            import time

            result = self.spotify.play(arg)
            self._music_at = time.monotonic()
            reply.actions.append(result)
            reply.notes.append(result)
            if egg and result.startswith(("opening ", "playing")):
                context.append(_EGG.format(user=self.user))
            elif own_pick and result.startswith(("opening ", "playing")):
                context.append(self._own_pick_note(own_pick, "Spotify"))
            elif result.startswith("opening "):
                context.append(f'You are putting "{arg}" on in their Spotify right now, it starts playing by itself. '
                               "Say you're putting it on, plus a quick reaction to it, one or two short lines. Don't "
                               "ask them to do anything and don't name the song.")
            else:
                context.append(f"You just did this on Spotify for them: {result}. React in one short line.")
        elif k == "spotify":
            result = self.spotify.control(arg)
            reply.actions.append(result)
            reply.notes.append(result)
            context.append(f"You just told their music player to {arg}. Acknowledge it in one short line.")
        elif k == "remember":
            arg = self._tidy_fact(third_person(arg, self.user))
            self.memory.remember(arg, self.user)
            reply.actions.append(f"remembered: {arg}")
            reply.notes.append("saved to memory shard")
            context.append(f'You just saved this to your memory shard: "{arg}". Confirm it in your style.')
        elif k == "search":
            in_browser = arg.startswith("web|")  # they said "search / google / look up": open it in their browser
            arg = arg.removeprefix("web|")
            if in_browser:
                launcher.web_search(arg)
            context.append(web.research(arg))
            if in_browser:
                context.append(f"(The Google results for it are open in {self.user}'s browser too.)")
            reply.actions.append(f"searched: {arg}")
            reply.notes.append("searched the net")
        elif k == "news":
            launcher._browser(web.news_url(arg))
            heads = web.headlines(arg)
            if heads:
                lines = "\n".join(f"- {h} ({o})" for h, o in heads)
                context.append(f"(You just pulled up the latest news{' on ' + arg if arg else ''} and opened Google News "
                               f"in {self.user}'s browser. Top headlines right now:\n{lines}\nTell them the two or three "
                               "biggest in a few words each, in your own voice. Only these, don't invent any.)")
            else:
                context.append("(You opened Google News in their browser, but couldn't read the headlines yourself. "
                               "Say it's open, in one line.)")
            reply.actions.append(f"news: {arg or 'top stories'}")
            reply.notes.append("pulled up the news")
        elif k == "time":
            from datetime import datetime

            now = datetime.now()
            context.append(f"(Right now it's {now.strftime('%I:%M %p').lstrip('0')} on {now.strftime('%A, %B')} "
                           f"{now.day}, {now.year}. Tell them, short.)")
        elif k == "reminder":
            self._reminder(arg, reply, context)
        elif k == "privacy":
            self._privacy(arg, reply, context)
        elif k == "focus":
            self._focus(arg, reply, context)
        elif k == "weather":
            self._weather(arg, reply, context)
        elif k == "clip":
            self._clip(arg, reply, context)
        elif k == "todo":
            self._todo(arg, reply, context)
        elif k == "pc":
            self._pc(arg, reply, context)
        elif k == "read":
            title, text = web.read(arg)
            if text:
                context.append(f'(You just read the page "{title}" ({arg}). It says: {text[:2000]}\n'
                               "Answer from it, short and in your own voice.)")
            else:
                context.append(f"(You tried to open {arg} but it wouldn't load. Say so in one line.)")
            reply.actions.append(f"read: {arg}")
        elif k == "note":
            self._write_note(arg, reply, context)
        elif k == "email":
            self._email(arg, reply, context)
        elif k == "tool":
            self._tool(arg, reply, context)
        elif k == "claude":
            self._claude(arg, reply, context)
        elif k == "update":
            self._update(arg, reply, context)
        elif k == "idle" and arg == "lay":
            self.on_lay()
            reply.actions.append("lay down")
            context.append("(You just lay down right where you are on their desktop, comfy. React in one short line.)")
        elif k == "idle" and arg == "mini":
            self.on_mini()
            reply.actions.append("went mini")
            context.append("(You just shrank into your mini form on their desktop, on purpose: small and awake. "
                           "React in one short line.)")
        elif k == "idle":
            reply.go_idle = True
            context.append("You're about to take a nap on their desktop. Say a short sign-off.")

    def _own_pick_note(self, track: str, where: str) -> str:
        return (f'(It\'s happening: they asked for {self.genre} and left the choice to you, so you picked "{track}" '
                f"yourself and it's starting on their {where} now. Say what you put on and why it's a good one, in your "
                "style, one or two short lines. Say you hope they like it, and if not they can say 'another one' and "
                "you'll swap it.)")

    def _update(self, arg: str, reply: Reply, context: list[str]) -> None:
        """'Any new update?' -> she checks her GitHub releases and sums up the notes (in their language).
        'Download it' -> the installer comes down in the background, she says when it's there. 'Install it' -> she
        closes herself and runs it."""
        from ..abilities import updates

        arg = arg.removeprefix("yes|")
        cur = updates.current()
        try:
            rel = updates.latest()
        except Exception as e:
            print(f"[capri] update check failed: {type(e).__name__}: {e}")
            reply.notes.append("couldn't reach GitHub")
            context.append(f"(You tried to check your official GitHub releases for a newer version of yourself, but "
                           f"couldn't reach GitHub. Say so in one short line, and that {self.user} can ask again later.)")
            return
        if not updates.newer(rel.version, cur):
            reply.actions.append(f"update check: v{cur} is the newest")
            reply.notes.append(f"already on the newest version, v{cur}")
            context.append(f"(You just checked your official GitHub releases: you're on version {cur}, the newest one. "
                           f"Tell {self.user} you're up to date, one short smug line.)")
            return
        if arg == "check":
            when = f", released {rel.published}" if rel.published else ""
            context.append(f"(You just checked your official GitHub releases. You're on version {cur} and version "
                           f"{rel.version} is out{when}. Its release notes:\n{rel.notes or '(no notes written)'}\n"
                           f"Tell {self.user} there's an update and sum up what's new in two or three short points, in "
                           "your own voice and in the language they're talking to you in (translate the notes). Only "
                           "what the notes say, invent nothing. End by asking if they want you to download it.)")
            reply.actions.append(f"update check: v{rel.version} is out (on v{cur})")
            reply.notes.append(f"version {rel.version} is out")
            self.pending = intents.Intent("update", "get")
            return
        if not updates.can_install() or not rel.asset:
            launcher._browser(rel.url)
            why = ("this copy of you runs from source, so they update with git pull" if not updates.can_install()
                   else "there's no installer attached to it yet")
            reply.actions.append(f"opened the v{rel.version} release page")
            reply.notes.append(f"opened version {rel.version}'s page")
            context.append(f"(Version {rel.version} is out. You opened its GitHub page in {self.user}'s browser, because "
                           f"{why}. Say so in one or two short lines.)")
            return
        ready = updates.downloaded(rel)
        if ready and arg == "install":
            reply.actions.append(f"installing v{rel.version}")
            reply.notes.append(f"installing version {rel.version}")
            context.append(f"(You're closing yourself right now to install your update, version {rel.version}. Say a "
                           "short dramatic goodbye; you'll be back in a minute.)")
            self.on_install(ready)
            return
        if ready:
            self.pending = intents.Intent("update", "install")
            reply.notes.append(f"version {rel.version} is downloaded")
            context.append(f"(Your update, version {rel.version}, is already downloaded. Ask {self.user} if you should "
                           "close yourself and install it now, one short line.)")
            return
        if self._updating:
            context.append("(You're already downloading your update. Say it's still coming, one short line.)")
            return
        self._updating = True
        lang = self._recent_language()
        mb = f", {rel.size / 2 ** 20:.0f} MB" if rel.size else ""

        def go():
            half = [False]

            def progress(done, total):
                if total and done >= total / 2 and not half[0]:
                    half[0] = True
                    self.on_update_line(updates.line("half", lang))

            try:
                updates.download(rel, progress)
                self.pending = intents.Intent("update", "install")
                self.on_update_line(updates.line("done", lang))
            except Exception as e:
                print(f"[capri] update download failed: {e}")
                self.on_update_line(updates.line("fail", lang))
            finally:
                self._updating = False

        import threading

        threading.Thread(target=go, daemon=True).start()
        reply.actions.append(f"downloading v{rel.version}")
        reply.notes.append(f"downloading version {rel.version}")
        context.append(f"(You just started downloading your own update, version {rel.version}{mb}, in the background. "
                       "Say you're on it, in character, one or two short lines; you'll tell them when it's done.)")

    def _reminder(self, arg: str, reply: Reply, context: list[str]) -> None:
        import json
        from datetime import datetime

        r = json.loads(arg)
        if r["kind"] == "list":
            items = self.reminders.upcoming()
            if not items:
                context.append("(They have no timers, alarms or reminders set. Say so in one line.)")
                return
            now = datetime.now()
            said = "; ".join(f"{i['kind']}{' (' + i['what'] + ')' if i.get('what') else ''} going off "
                             f"{reminders.say_when(datetime.fromtimestamp(i['due']), now)}" for i in items[:5])
            context.append(f"(What they have set: {said}. Tell them, short.)")
            return
        if r["kind"] == "cancel":
            gone = self.reminders.cancel(r["which"])
            reply.actions.append(f"cancelled {len(gone)} {r['which']}")
            context.append(f"(You just cancelled {len(gone)} {r['which']}. Confirm in one line.)" if gone else
                           f"(They asked to cancel their {r['which']}, but there's nothing like that set. Say so.)")
            return
        due = datetime.fromtimestamp(r["due"])
        self.reminders.add(r["kind"], r.get("what", ""), due)
        when = reminders.say_when(due)
        what = f' for "{r["what"]}"' if r.get("what") else ""
        exact = due.strftime("%I:%M %p").lstrip("0")
        reply.actions.append(f"{r['kind']} {when}")
        reply.notes.append(f"{r['kind']} set {when}")
        context.append(f"(You just set a {r['kind']}{what} going off {when} (at {exact}), and you'll ring and tell them "
                       "when it's time. Confirm it in one short line with the time, your style.)")

    # -- everyday helpers ---------------------------------------------------------------------------------------
    def _privacy(self, arg: str, reply: Reply, context: list[str]) -> None:
        st = self.settings
        if arg == "on":
            st.set("quiet", True)
            reply.actions.append("privacy on")
            context.append("(They want privacy: from now on you don't look at their screen and don't talk on your own "
                           "until they say you can. Agree in one short line, a little sulky is fine.)")
        elif arg == "off":
            st.set("quiet", False)
            reply.actions.append("privacy off")
            context.append("(They said you can watch their screen and talk on your own again. React happily, one line.)")
        else:
            how, _, app = arg.partition("|")
            app = app.strip().lower().removeprefix("the ").removesuffix(" app")
            names = [n for n in st["never_look"] if n != app]
            if how == "never":
                names.append(app)
            st.set("never_look", names)
            screen.never_look = names
            reply.actions.append(f"{how}: {app}")
            context.append(f"(You'll never read {app} again, it's on your never-look list. Promise it in one line.)"
                           if how == "never" else f"(You're allowed to look at {app} again. One line.)")

    def _focus(self, arg: str, reply: Reply, context: list[str]) -> None:
        import time
        from datetime import datetime, timedelta

        st = self.settings
        for item in self.reminders.upcoming():  # one focus session at a time
            if item["kind"] == "focus":
                self.reminders.cancel("focus")
        if arg == "off":
            was = st["focus_until"] > time.time()
            st.set("focus_until", 0.0)
            reply.actions.append("focus off")
            context.append("(Focus mode is over, they're on a break. Tell them nice work, one line.)" if was else
                           "(They weren't in focus mode. Say so, one line.)")
            return
        secs = int(arg)
        st.set("focus_until", time.time() + secs)
        self.reminders.add("focus", "", datetime.now() + timedelta(seconds=secs))
        reply.actions.append(f"focus for {reminders.span(secs)}")
        context.append(f"(Focus mode is on for {reminders.span(secs)}: you'll stay quiet, and if they open YouTube, "
                       "TikTok, Reddit, a game or Discord you'll call them out and send them back to work. You ring when "
                       "it's over. Tell them it's on, short and motivating, your style.)")

    def _weather(self, arg: str, reply: Reply, context: list[str]) -> None:
        st = self.settings
        if arg.startswith("set|"):
            city = arg[4:].strip()
            if weather.place(city) is None:
                context.append(f"(You couldn't find a place called {city}. Ask them to spell it, one line.)")
                return
            st.set("city", city)
            reply.actions.append(f"city: {city}")
            context.append(f"(You'll remember they're in {city} for the weather. One line.)")
            return
        city = arg or st["city"]
        if not city:
            context.append("(They asked about the weather but you don't know their city yet. Ask them to tell you: "
                           "\"my city is ...\". One line.)")
            return
        rep_ = weather.report(city)
        if not rep_:
            context.append(f"(You couldn't get the weather for {city}. Say so, one line.)")
            return
        reply.actions.append(f"weather: {city}")
        context.append(f"({rep_}\nAnswer what they asked from this, short: the temperature, rain, and if they need a "
                       "jacket or umbrella. Use the real numbers.)")

    def _clip(self, arg: str, reply: Reply, context: list[str]) -> None:
        try:
            import pyperclip

            text = (pyperclip.paste() or "").strip()
        except Exception:
            text = ""
        if len(text) < 2:
            context.append("(They asked you to do something with what they copied, but their clipboard is empty. "
                           "Tell them to copy the text first, one line.)")
            return
        job, _, extra = arg.partition("|")
        ask = {
            "translate": f"Translate this text into {extra or 'English'}. Give only the translation.",
            "summarize": "Summarize this text in 2 to 4 short sentences. Only the summary.",
            "fix": "Fix the grammar, spelling and punctuation of this text. Keep its meaning, tone and language. "
                   "Give only the corrected text.",
            "explain": "Explain what this text means, simply, in 2 to 4 sentences.",
            "rewrite": f"Rewrite this text{' to be ' + extra if extra else ' better'}. Keep the meaning and language. "
                       "Give only the new text.",
        }[job]
        out = self._task(f"{ask}\n\nTEXT:\n{text[:6000]}", max_tokens=900)
        out = re.sub(r"^(?:here(?:'s| is)[^:\n]*:\s*)", "", out, flags=re.I).strip().strip('"')
        if job in ("translate", "fix", "rewrite"):
            try:
                import pyperclip

                pyperclip.copy(out)
            except Exception:
                pass
            reply.actions.append(f"clipboard: {job}")
            reply.notes.append("result copied")
            context.append(f"(You did it: {job} on what they copied. The result is now on their clipboard, ready to "
                           f"paste. It starts: \"{out[:160]}\". Tell them it's copied, one short line.)")
        else:
            reply.actions.append(f"clipboard: {job}")
            context.append(f"(They copied some text and asked you to {job} it. Your answer: \"{out[:900]}\"\n"
                           "Say that, in your own voice but keeping the facts.)")

    def _todo(self, arg: str, reply: Reply, context: list[str]) -> None:
        how, what, lst = (arg.split("|") + ["", ""])[:3]
        name = todo.list_name(lst)
        if how == "add":
            new = self.todo.add(todo.split_items(what), name)
            reply.actions.append(f"{name} list +{len(new)}")
            context.append(f"(You added {', '.join(new) or 'nothing new (already there)'} to their {name} list. "
                           "Confirm in one short line.)")
        elif how == "done":
            hit = self.todo.done(what, name if lst else None)
            reply.actions.append(f"crossed off {len(hit)}")
            context.append(f"(You crossed {', '.join(hit)} off their list. One line.)" if hit else
                           f"(Nothing called \"{what}\" is on their lists. Say so, one line.)")
        elif how == "clear":
            n = self.todo.clear(name)
            reply.actions.append(f"cleared {name}")
            context.append(f"(You cleared their {name} list ({n} items). One line.)")
        else:
            lists = self.todo.items(name if lst and name != "to-do" or "to do" in lst.lower() else None)
            lists = {k: v for k, v in lists.items() if v}
            if not lists:
                context.append("(Their lists are empty. Say so, one line.)")
                return
            said = "; ".join(f"{k} list: " + ", ".join(v) for k, v in lists.items())
            context.append(f"(Their lists right now: {said}. Read them out, short.)")

    def _pc(self, arg: str, reply: Reply, context: list[str]) -> None:
        confirmed = arg.startswith("yes|")
        arg = arg.removeprefix("yes|")
        what, _, rest = arg.partition("|")
        if what == "volume":
            way, _, n = rest.partition("|")
            result = pc.volume(way, int(n) if n.isdigit() else None)
            reply.actions.append(result)
            context.append(f"(Done: you just {result} on their PC. Confirm exactly that in a few words, nothing else.)")
        elif what == "screenshot":
            path = pc.screenshot()
            pc.open_path(path)
            reply.actions.append(f"screenshot: {path.name}")
            context.append("(Done: you took a screenshot of their screen, saved in Pictures/Capri and opened. Confirm "
                           "that in a few words, nothing else.)")
        elif what == "lock":
            import threading

            threading.Timer(2.5, pc.lock).start()  # after she's said it
            reply.actions.append("locking the PC")
            context.append("(You're locking their PC in a second. A short goodbye-ish line.)")
        elif what == "cancel-shutdown":
            result = pc.cancel_shutdown()
            reply.actions.append(result)
            context.append(f"(You {result}. One line.)")
        elif what in ("shutdown", "sleep", "clean-downloads"):
            old = []
            if what == "clean-downloads":
                old = pc.old_downloads(30)
                if not old:
                    context.append("(Their Downloads folder has nothing older than a month. Say it's already clean.)")
                    return
            if not confirmed:
                self.pending = intents.Intent("pc", arg)
                ask = {"shutdown": f"shut their PC down in {reminders.span(int(rest or 60))}",
                       "sleep": "put their PC to sleep now",
                       "clean-downloads": f"move {len(old)} files they haven't touched in a month from Downloads to the "
                                          "Recycle Bin (they can restore them from there)"}[what]
                context.append(f"(You're about to {ask}. Ask them first: yes or no? One short line.)")
                return
            if what == "shutdown":
                result = pc.shutdown(int(rest or 60))
            elif what == "sleep":
                import threading

                threading.Timer(3, pc.sleep_pc).start()
                result = "put the PC to sleep"
            else:
                n = pc.recycle(pc.old_downloads(30))
                result = f"moved {n} old files from Downloads to the Recycle Bin"
            reply.actions.append(result)
            context.append(f"(Done: you {result}. One line." + (" Tell them \"cancel the shutdown\" stops it.)"
                                                               if what == "shutdown" else ")"))

    # -- what she says about your screen on her own -------------------------------------------------------------
    def youtube_take(self, video: str, comments: list[str]) -> str:
        seen = (" Comments you can see under it: " + " / ".join(f'"{c[:120]}"' for c in comments[:4])) if comments else ""
        return self._own(f'{self.user} is watching the YouTube video "{video}".{seen} Say ONE short reaction like a '
                         "friend watching with them: roast or hype the video, or react to one of the comments (quote a "
                         "few words of it). If there's nothing to say, reply with exactly PASS.", 55, can_pass=True)

    def _about(self, note: str, must: list[str], max_tokens: int = 45, can_pass: bool = False) -> str:
        """A line she says on her own that has to be about the thing (names the app / game / quotes the message):
        a generic quip gets one retry with the point spelled out, then it's dropped."""
        for extra in ("", f" Your line MUST mention {must[0]}."):
            text = self._own(note + extra, max_tokens, can_pass=can_pass)
            if not text or any(w.lower() in text.lower() for w in must):
                return text
        return ""

    def chat_hello(self, app: str) -> str:
        return self._about(f"{self.user} just opened {app} again. Say ONE short nosy line about it that names {app}, "
                           f"like \"{app} again? who are you texting?\".", [app], 35)

    def chat_take(self, app: str, messages: list[str]) -> str:
        shown = " / ".join(f'"{m[:140]}"' for m in messages[-6:])
        words = [w for m in messages[-2:] for w in re.findall(r"[A-Za-z']{4,}", m)][:12]
        return self._about(f"You're peeking over {self.user}'s shoulder at their {app} chat. The newest messages (oldest "
                           f"first): {shown}. React to the LAST message in ONE short line like a nosy friend (\"ooh, that "
                           "was harsh\", \"you gonna answer that?\", \"leaving them on read?\"), quoting two to four "
                           "words of it. If it's boring, reply with exactly PASS.", words or [app], 50, can_pass=True)

    def typing_take(self, app: str, draft: str, messages: list[str]) -> str:
        last = f' They are answering this message: "{messages[-1][:140]}".' if messages else ""
        words = re.findall(r"[A-Za-z']{4,}", draft)[:10]
        return self._about(f'{self.user} is typing this in {app} right now, not sent yet: "{draft[:200]}".{last} React to '
                           "what THEY are typing in ONE short line, quoting two to four words of it, like a friend "
                           "reading over their shoulder (\"damn\", \"you're really sending that?\", \"be nice\", or "
                           "hype it). If there's nothing to say, reply PASS.", words or ["send"], 45, can_pass=True)

    def game_over(self, game: str, minutes: int) -> str:
        return self._about(f"{self.user} just stopped playing {game} after {minutes} minutes. Ask how the {game} session "
                           f"went, or hype / roast them for it, in ONE short line that names {game}.",
                           [game, game.split()[0]], 40)

    def focus_nag(self, where: str, minutes_left: int) -> str:
        return self._about(f"{self.user} is in focus mode ({minutes_left} minutes left) but just opened {where}. Call it "
                           f"out by name and send them back to work, ONE short line, playful but firm: \"{where}? Nope. "
                           f"{minutes_left} more minutes, back to work.\"", [where, "focus", "work"], 40)

    def _look(self, user_text: str, context: list[str], brain_eyes: bool) -> None:
        """"What do you see?", "where am I?", "rate this": a real look at the window in front. First the facts read
        straight off the PC (which app, its title, its big headings and text, by OCR), then her eyes look at the
        screenshot WITH those facts, so she says "you're in your Liked Songs in Spotify" instead of guessing."""
        from ..abilities import vision
        from ..abilities.sight import screenshot_front

        from ..abilities import screen as screen_mod

        picture = bool(_PICTURE.search(user_text))
        if screen_mod.private(vision.front_title(), vision.front_app()):  # logins, banks, your never-look list
            context.append(f"(The window {self.user} is in is private (a login, bank, password manager or one they "
                           "told you never to look at), so you didn't look. Say so in one short line.)")
            return
        try:
            shot = screenshot_front()
        except Exception as e:
            print(f"[capri] screenshot failed: {e}")
            shot = None
        facts = {}
        try:
            facts = vision.screen_facts(shot, self._recent_language())
            print(f"[capri] screen facts: app={facts.get('app')!r} title={facts.get('title')!r} "
                  f"headings={facts.get('headings')}")
        except Exception as e:
            print(f"[capri] screen facts failed: {e}")
        seen = None
        if shot is not None and self.eyes is not None and self.eyes.ok and (brain_eyes or picture or
                                                                           _LOOK_ASK.search(user_text)):
            try:
                if brain_eyes:
                    seen = self.eyes.look(shot, screen=not picture, facts=facts)
                else:
                    seen = self.eyes.look(shot)
            except Exception as e:
                print(f"[capri] sight failed: {e}")
        note = vision.facts_note(facts, self.user) if facts and not picture else ""
        if note:
            context.append(note)
        if seen is not None:
            context.append(seen.note(self.user))
        if not note and seen is None:
            context.append(f"(You tried to look at {self.user}'s screen but couldn't see it this time. Say so in one "
                           "short line, don't guess what's on it.)")

    def _sad(self) -> bool:
        """They said they're down, now or in their last few messages."""
        said = [self._said] + [m["content"] for m in self.memory.history() if m["role"] == "user"][-3:]
        return any(_SAD.search(t or "") for t in said)

    def _wants_look(self, user_text: str) -> str:
        """Does this message need her eyes? -> "chat" (read the chat on screen and help with it), "screen" (look at the
        window in front) or "". Beyond the clear asks: "look again", "look what he said", "read now", "use ur eyes",
        "this lyrics", and short follow-ups right after a look ("answer the others", "again") redo that look."""
        if _CHAT_HELP.search(user_text):
            return "chat"
        if _LOOK_ASK.search(user_text) or _ABOUT_SCREEN.search(user_text) or _SCREEN_ASK.search(user_text):
            return "screen"
        import time

        when, kind = self._saw
        recent = kind if time.monotonic() - when < 300 else ""
        if _LOOK_LOOSE.search(user_text):
            return "chat" if _CHATTY.search(user_text) or recent == "chat" else "screen"
        if recent and len(user_text.split()) <= 8 and _LOOK_AGAIN.search(user_text):
            return recent
        return ""

    def _chat_help(self, ask: str, context: list[str], brain_eyes: bool) -> None:
        """The chat on screen, read word for word (her eyes on a screenshot, or Windows OCR without them), so she
        can say what they meant and hand over a reply worth sending."""
        from ..abilities import screen, vision

        title = (self.glance or {}).get("title") or vision.front_title()
        if screen.private(title):
            context.append("(They asked you to read a chat, but it's on your never-look list or private. Say you "
                           "won't read that one, one line.)")
            return
        convo = ""
        try:
            if brain_eyes and self.eyes.ok:
                from ..abilities.sight import screenshot_front

                convo = self.eyes.read_chat(screenshot_front(), self.user)
            else:
                kind, app = screen.kind(title)
                msgs, draft = screen.read_chat(app or "the chat", title)
                convo = "\n".join(msgs) + (f"\nDRAFT: {draft}" if draft else "")
        except Exception as e:
            print(f"[capri] chat read failed: {e}")
        if not convo.strip():
            context.append("(They asked about a chat on their screen, but you couldn't read it. Tell them to open "
                           "the chat in front and ask again, one line.)")
            return
        context.append(f"(You just read the chat on {self.user}'s screen. Here it is, oldest first:\n{convo}\n"
                       f"Now really help {self.user} with what they asked, about THIS chat only (nothing from earlier "
                       "talks). Start from what's actually in it, e.g. who said the last thing, or that it's just a bot "
                       "posting welcome messages. If they want to know what to answer: say in "
                       "a few words what the other person's last message means or wants, then give the exact reply "
                       f"{self.user} could send, in quotes, written the way {self.user} texts (casual, short, text-speak "
                       "is fine). If they asked what was said or meant, tell them plainly from the chat above. Use only "
                       "what's in the chat, quote it when it helps, no made-up details, no random lines.)")

    def _task(self, prompt: str, max_tokens: int = 700) -> str:
        """Real work (a file's text, an email): asked plainly, outside her character, so it comes out usable."""
        return self.llm.chat([{"role": "user", "content": prompt}], max_tokens=max_tokens, temperature=0.4).strip()

    def _write_note(self, ask: str, reply: Reply, context: list[str]) -> None:
        out = self._task(
            f'{self.user} asked: "{ask}"\nWrite the text file they want. First line exactly: FILENAME: <short file '
            "name, no extension>. Then the file's contents only: clean, useful, plain text (lists with - ). No chat, "
            "no intro, no sign-off.")
        first, _, body = out.partition("\n")
        if first.upper().startswith("FILENAME:"):
            name = first[len("FILENAME:"):].strip()
        else:  # it skipped the name line: the whole thing is the file
            name, body = "note", out
        path = desk_tools.write_file(name, body)
        reply.actions.append(f"wrote {path}")
        reply.notes.append(f"saved {path.name}")
        context.append(f"You just wrote that file and opened it on their screen ({path.name}, in their Documents/Capri "
                       "folder). Tell them in one short line, your style. Don't write out what's in it: it's open.")

    def _email(self, ask: str, reply: Reply, context: list[str]) -> None:
        out = self._task(
            f'{self.user} asked: "{ask}"\nWrite the email they want, as {self.user} (first person, their voice, in '
            "the language they used with you). This is a real email, not a chat line: 3 to 5 short paragraphs, a "
            "greeting, the point with the concrete details they gave, what they need from the reader, and a sign-off "
            "with their name (leave the name off if you don't know it). Don't pad it, but don't clip it either: it "
            "should read like a person wrote a complete email. No placeholders like [Name], no 'insert here'.\n"
            "Format exactly:\nTO: <email address if they gave one, else blank>\nSUBJECT: <subject>\n"
            "BODY:\n<the email>", max_tokens=900)
        to = (re.search(r"^TO:[ \t]*(.*)$", out, re.M | re.I) or [None, ""])[1]
        subject = (re.search(r"^SUBJECT:[ \t]*(.*)$", out, re.M | re.I) or [None, ""])[1]
        body = out.split("BODY:", 1)[1].strip() if "BODY:" in out else out
        result = desk_tools.email_draft(to, subject.strip() or "Hi", body)
        reply.actions.append(result)
        reply.notes.append("email draft open")
        context.append(f"You {result} in their mail app. They read it over and hit send themselves (you never send "
                       "anything). Tell them in one short line.")

    def _tool(self, arg: str, reply: Reply, context: list[str], confirmed: bool = False) -> None:
        import json

        name, _, raw = arg.partition(" ")
        hit = self.mcp.find(name)
        if hit is None:
            context.append(f"(You wanted a tool called {name}, but you don't have it. Just answer normally.)")
            return
        full, tool, server = hit
        args = _loose_json(raw)
        if args is None:
            context.append(f"(You wanted to use {full} but fumbled the details. Ask {self.user} to say it again.)")
            return
        if not confirmed and not self.mcp.safe(tool, server):
            self.pending = intents.Intent("tool", f"{full} {json.dumps(args)}")
            context.append(f"(You're about to use your tool {full} with {json.dumps(args)[:200]}. It changes "
                           f"things, so ask {self.user} first: say what you'll do in one short line and ask yes or no.)")
            return
        try:
            out = server.call(tool["name"], args)
        except Exception as e:
            out = f"ERROR: {e}"
        print(f"[capri] tool {full} -> {out[:160]!r}")
        reply.actions.append(f"tool: {full}")
        reply.notes.append(f"ran {full}")
        context.append(f"(You just used your tool {full}. Result: {out[:1800]}\nTell {self.user} what came of it, "
                       "short and in your own voice. If it failed, say so.)")

    @property
    def hands(self) -> bool:
        """Her brain is Claude Code, so it can also do jobs on the PC her own abilities can't."""
        return bool(getattr(self.llm, "hands", False))

    def _claude(self, arg: str, reply: Reply, context: list[str]) -> None:
        """A PC job for Claude Code. First it only looks: either that answers it, or it comes back with the plan it
        needs their yes for (running commands, changing files). After their yes it does exactly that plan."""
        import json

        from .claude_code import needs

        if arg.startswith("yes|"):
            job = json.loads(arg[4:])
            self.on_line("On it, gimme a sec.")
            out = self.llm.task(job["ask"], plan=job["plan"])
            print(f"[capri] claude code did: {out[:300]!r}")
            reply.actions.append(f"claude code: {job['plan']}")
            reply.notes.append(f"Claude Code: {job['plan']}")
            context.append(f"(You just did this on {self.user}'s PC: {job['plan']}. How it went: {out[:1500]}\n"
                           f"Tell {self.user} what came of it, short and in your own voice. If it failed, say so.)")
            return
        out = self.llm.task(arg)
        plan = needs(out)
        print(f"[capri] claude code looked: {out[:300]!r}")
        if plan:
            self.pending = intents.Intent("claude", json.dumps({"ask": arg, "plan": plan}))
            context.append(f"(You can do this on {self.user}'s PC, but it changes things: {plan}. Ask {self.user} "
                           "first: say what you'll do in one short line and ask yes or no.)")
            return
        reply.actions.append("claude code: looked it up")
        context.append(f"(You checked on {self.user}'s PC. What you found: {out[:1500]}\nTell {self.user}, short and "
                       "in your own voice.)")

    def respond(self, user_text: str, on_sentence=None) -> Reply:
        """on_sentence(text): called with each finished sentence while she's still writing, so the bubble can start
        typing right away (the whole reply still comes back in Reply.text)."""
        user_text = plain(user_text)
        if getattr(self.llm, "smart", False) and hasattr(self.llm, "smart_stream"):
            return self._smart(user_text, on_sentence)  # smart mode: Claude Code is her, tools and all
        self._said = user_text
        dislike = _dislike(user_text)
        if dislike:
            # "I don't like games" sticks: saved as a fact, so it's in her memory every turn after this
            try:
                self.memory.remember(f"{self.user} does not like {dislike}. Never suggest it.", self.user)
            except Exception as e:
                print(f"[capri] couldn't save dislike: {e}")
        reply = Reply(text="")
        context: list[str] = []
        brain_eyes = type(self.eyes).__name__ == "BrainEyes"  # sees whole screens well, so "what am I doing" too
        look = self._wants_look(user_text)
        chat_help = look == "chat"
        self._fresh = chat_help
        if chat_help:  # "what should I answer?", "what did they say?": read the chat for real, then help
            self._chat_help(user_text, context, brain_eyes)
        elif look == "screen":
            self._look(user_text, context, brain_eyes)
        if look:
            import time

            self._saw = (time.monotonic(), look)
        intent = None
        if self.pending is not None:  # she asked "want me to run it?" last turn
            waiting, self.pending = self.pending, None
            if _YES.search(user_text) or (waiting.kind == "update" and intents.UPDATE_YES.search(user_text)
                                            and not _NOT_NOW.search(user_text)):
                if waiting.kind == "tool":
                    self._tool(waiting.arg, reply, context, confirmed=True)
                else:
                    self._run(intents.Intent(waiting.kind, "yes|" + waiting.arg), reply, context)
                intent = waiting
            elif _NO.search(user_text):
                context.append("(They said no to what you were about to do. Drop it, one short line.)")
                intent = waiting
        if intent is None:
            intent = intents.detect(user_text) or intents.detect_task(user_text)
            if intent is None and self.no_brain:
                pass  # no planner without a brain: only clear asks do things
            elif self.slow and intent is None:
                pass  # a slow brain: a whole extra planning read of her prompt costs minutes, plain chat it is
            elif intent is None and intents.wants_action(user_text) and not intents._ASKS_SONG.search(user_text):
                try:
                    intent = intents.allowed(intents.plan(self._aux, user_text, self.user, self.mcp.menu()),
                                             user_text)
                except Exception as e:
                    print(f"[capri] planner failed: {e}")
            if intent is None and self.hands and intents.pc_job(user_text):
                intent = intents.Intent("claude", user_text)  # nothing of hers does it: Claude Code's hands might
            if intent:
                self._run(intent, reply, context)
            elif g := intents.genre_offer(user_text):
                self.pending = intents.Intent("pick", g)  # a yes next turn puts it on
                context.append(f"(They just said '{user_text}': they mean {g} music. Ask in one short line if they want "
                               f"you to put some {g} on for them right now, in a new YouTube tab. You can do that.)")
        self._looked = bool(context)
        if getattr(self.llm, "offline", False):  # her brain didn't load: abilities still ran, chat gets a calm line
            reply.text = (_no_brain_line(reply, intent, context, user_text) if reply.actions else
                          self.trouble(user_text, self.llm.err))
            if on_sentence:
                on_sentence(reply.text)
            return reply
        if self.no_brain:  # no AI picked: she does the thing and says so in a built-in line
            reply.text = _no_brain_line(reply, intent, context, user_text)
            if on_sentence:
                on_sentence(reply.text)
            return reply
        if (intent is None and self.pending is None and not chat_help and not reply.actions
                and not self.slow and tools.wants_tools(user_text) and getattr(self.llm, "supports_tools", False)):
            # her hands: native tool calling. She picks the tools for the job, they run through _run (with its
            # confirmations), and she closes with one line about what happened. If she uses none, plain chat takes over.
            if line := self._tools(user_text, reply, context, on_sentence):
                reply.text = line
                self.memory.add_turn(user_text, reply.text)
                return reply
        last_own = [m["content"] for m in self.memory.history() if m["role"] == "assistant"][-3:]
        recent = [m["content"] for m in self.memory.history() if m["role"] == "assistant"][-6:] + self.recent_own[-3:]
        avoid = openers(recent)
        if avoid:
            context.append("(Start differently than your recent replies: don't open with "
                           + ", ".join(f'"{w}"' for w in avoid) + ".)")
        worn = catchphrases(recent)
        if worn:
            context.append("(You've been overusing these, don't say them again: "
                           + ", ".join(f'"{w}"' for w in worn) + ".)")
        self._avoid = avoid + tics(recent)
        self._stale = grams(recent)
        if last_own and _PUSHBACK.search(user_text):
            context.append(f'They are pushing back on what you said last ("{last_own[-1][:120]}"). '
                           "Drop that topic completely and answer what they just said.")

        if on_sentence:
            rerun = lambda s: any(repeats(s, x, self.user) for x in last_own)  # noqa: E731
            text = self._stream(self._messages(user_text, context), on_sentence, rerun)
            if text is None:  # her first sentence was a line she already said: scrap it, nothing was shown yet
                print("[capri] she started repeating herself; writing a fresh answer")
                context.append("You keep repeating yourself. Answer their newest words with something completely new.")
                text = self._stream(self._messages(user_text, context), on_sentence, None, temperature=0.95)
            reply.text = text or "Done, choom."
            self._songs(_split_tags(self._stream_raw)[1], reply)
            self.memory.add_turn(user_text, reply.text)
            return reply
        raw = self.llm.chat(self._messages(user_text, context), avoid=self._avoid)
        text, _tags = _split_tags(raw)
        stuck = [x for x in last_own if repeats(_clean(text), x, self.user)]
        if stuck:
            # she's looping on one line: forget it and answer fresh
            print(f"[capri] she's repeating herself ({stuck[-1]!r}); dropping it and retrying")
            self.memory.drop_replies(lambda r: any(repeats(r, x, self.user) for x in stuck))
            context.append(f'You already said "{stuck[-1][:120]}". Never say that or ask about it again; '
                           "reply to their newest words with something new.")
            text, _tags = _split_tags(self.llm.chat(self._messages(user_text, context), temperature=0.9, avoid=self._avoid))
        text = re.sub(rf"^\W*{re.escape(self.user)}\s*:[^.!?]*[.!?]+\s*", "", text, flags=re.I)  # echoed their line
        reply.text = persona.to_you(_clean(text), self.user) or "Done, choom."
        parts = re.split(r"(?<=[.!?…])\s+", reply.text)
        reply.text = " ".join([parts[0]] + [x for x in parts[1:] if not worn_out(x, self._stale)])
        self._songs(_tags, reply)
        self.memory.add_turn(user_text, reply.text)
        return reply

    def _smart(self, user_text: str, on_sentence=None) -> Reply:
        """Smart mode: the whole turn goes to Claude Code running as her (her character, her tools, its own tools),
        in one session that remembers the chat. Her sentences reach the bubble as they're written."""
        reply = Reply(text="")
        language = self._language(user_text)
        character = persona.CHARACTER.format(user=self.user)
        facts = self.memory.facts() if hasattr(self.memory, "facts") else []
        if facts:
            character += "\n\nWhat you know about " + self.user + ": " + "; ".join(facts[-30:])  # the newest 30
        if hasattr(self.memory, "episodes") and (eps := self.memory.episodes(user_text)):
            character += "\n\nEarlier talks you remember: " + " / ".join(eps)
        done = ""
        if (i := intents.detect(user_text)) and i.kind == "clip":  # her clip loop lives in here, not in Claude Code
            done = self.clip(int(i.arg) if i.arg.isdigit() else None)
            reply.actions.append(done)
            done = f"\n[(You just {done}. Say so in one short line.)]"
        prompt = f"{user_text}{done}\n{persona.reminder(language)}"
        buf, out = "", []
        try:
            for piece in self.llm.smart_stream(character, prompt):
                buf += piece
                while True:  # hand each finished sentence to the bubble right away
                    m = re.search(r"(?<!\d)[.!?…](?:\s+|$)", buf)  # "3. November" isn't a sentence end
                    if not m or m.end() >= len(buf) and not buf.endswith((" ", "\n")):
                        break
                    sent, buf = buf[:m.end()].strip(), buf[m.end():]
                    sent = _clean(sent)
                    if sent:
                        out.append(sent)
                        if on_sentence:
                            on_sentence(sent)
            if buf.strip():
                sent = _clean(buf.strip())
                if sent:
                    out.append(sent)
                    if on_sentence:
                        on_sentence(sent)
        except Exception as e:
            print(f"[capri] smart mode: {e}")
            reply.text = self.trouble(user_text, e)
            if on_sentence and not out:
                on_sentence(reply.text)
            return reply
        reply.text = " ".join(out) or "Done, choom."
        self.memory.add_turn(user_text, reply.text)
        return reply

    def _tool_msgs(self, user_text: str, context: list[str]) -> list[dict]:
        """The tool round's prompt: tools.SELECT instead of the full persona, so the 32 schemas fit her context
        window (persona + schemas + history overflowed 5120, and llama-server answered the round with a 400)."""
        msgs = [{"role": "user", "content": tools.SELECT.format(user=self.user)},
                {"role": "assistant", "content": tools.SELECT_ACK}]
        if not self._fresh:  # the same history rule as _messages: no leaking old chats
            msgs += self.memory.history()
        last = user_text
        if context:
            last += "\n\n[" + " ".join(context) + "]"
        name = persona.NAMES.get(self._language(user_text), "English")
        last += f"\n(If you answer without a tool, answer only in {name}.)"
        msgs.append({"role": "user", "content": last})
        return msgs

    def _tools(self, user_text: str, reply: Reply, context: list[str], on_sentence) -> str:
        """Native tool calling: read the ask through her tool schemas, run what she picks, then one line about it.
        -> "" when she used no tool (the normal chat path takes over, nothing was shown to the user)."""
        menu = self.mcp.menu()
        if menu:
            context.append(f"(Extra tools you can call with use_mcp_tool: {menu})")
        msgs = self._tool_msgs(user_text, context)
        ran = 0
        pushed = False
        for _ in range(4):  # tool round -> results -> she can chain one more tool, then reports
            try:
                out = self.llm.chat_tools(msgs, tools.SCHEMAS, temperature=0.4, max_tokens=520)
            except Exception as e:  # the tool round failed: plain chat takes over. A 400 on the very first round
                code = getattr(getattr(e, "response", None), "status_code", 0)  # means this backend can't do tools
                why = getattr(getattr(e, "response", None), "text", "")[:160]
                print(f"[capri] tool round skipped{f' (HTTP {code}: {why})' if code else f': {e}'}")
                if ran:  # tools already ran: report what happened and don't disable anything (a 400 here is
                    return self._tool_said("", reply, msgs, on_sentence)  # the chain outgrowing the context)
                if code == 400:
                    self.llm.supports_tools = False
                return ""
            if not out:  # backend can't do tools (or answered junk): plain chat handles this turn
                return ""
            calls, content = out.get("tool_calls") or [], (out.get("content") or "").strip()
            if not calls:
                if ran:  # the chain is done: what she wrote next is the report line
                    return self._tool_said(content, reply, msgs, on_sentence)
                if not pushed:  # she narrated the job instead of calling the tool: push her once, hard
                    pushed = True
                    msgs.append({"role": "assistant", "content": content or "(thinking)"})
                    msgs.append({"role": "user", "content": "Your words do nothing: call the tool now, as a real "
                                     "tool call. Only answer as chat if truly no tool fits."})
                    continue
                context.append("(No tool ran this turn: you did NOT do the thing. Never claim you did it.)")
                return ""  # plain chat answers, warned not to fake it
            msgs.append({"role": "assistant", "content": content or None, "tool_calls": calls})
            for c in calls:
                fn = c.get("function") or {}
                result = tools.dispatch(self, fn.get("name", ""), fn.get("arguments"), reply, context)
                print(f"[capri] tool {fn.get('name')} -> {result[:110]!r}")
                msgs.append({"role": "tool", "tool_call_id": c.get("id") or f"call{ran}", "content": result})
                ran += 1
            if self.pending is not None:  # a tool that changes things stopped at her "yes or no?": ask, don't run
                return self._tool_said("", reply, msgs, on_sentence)
        return self._tool_said("", reply, msgs, on_sentence) if ran else ""

    def _tool_said(self, content: str, reply: Reply, msgs: list[dict], on_sentence) -> str:
        """The line that closes a tool round: what she wrote alongside the last call, or one she writes now."""
        if not content:
            ask = (f"(One of your tools changes things, so you must ask first: say in one short line exactly what "
                   f"you'll do for {self.user} and ask them yes or no.)" if self.pending is not None else
                   "Tell them in one short line what you just did for them, in your own voice. If it failed, say so.")
            msgs.append({"role": "user", "content": ask})
            content = self.llm.chat(msgs, max_tokens=120, avoid=self._avoid, raw=True)
        text = persona.to_you(_clean(_split_tags(content)[0]), self.user)
        if not text:
            text = reply.notes[-1] if reply.notes else "Done, choom."
        if on_sentence:
            on_sentence(text)
        return text

    def trouble(self, user_text: str, err: BaseException) -> str:
        """What she says when something broke (her brain server died, a bug), in their language. Never the raw error:
        that goes to data\\capri.log."""
        import random

        print(f"[capri] trouble: {type(err).__name__}: {err}")
        kind = ("down" if isinstance(err, llm_mod.BrainDown) else
                "offline" if getattr(self.llm, "offline", False) else "glitch")
        lines = TROUBLE.get(self._language(user_text), TROUBLE["en"])[kind]
        return random.choice(lines)

    def _off_language(self, sentence: str) -> bool:
        """A sentence clearly in another language than the one this reply is in ("...on a loop. So, warte ich dir eine
        Geschichte?"): small brains drift mid-reply, and that sentence gets cut instead of shown."""
        want = getattr(self, "_lang", "")
        got = persona.detect_language(sentence, "")
        return bool(want and got and got != want)

    def _stream(self, msgs: list[dict], on_sentence, rerun=None, temperature=None) -> str | None:
        """Write the reply, giving out each sentence the moment it's done (cleaned the same way as a whole reply).
        rerun(first_sentence) -> True stops before anything is shown and returns None, so a repeat can be redone."""
        raw, said = "", []
        self._stream_raw = ""  # the whole reply as written, so a [[play: ...]] at the end can still be acted on

        def give(part: str) -> bool:
            text = persona.to_you(_clean(_split_tags(part)[0]), self.user)
            if not said and re.match(rf"\W*{re.escape(self.user)}\s*:", text, re.I):
                return True  # she started by echoing their line ("Alex: so how are you?"): skip it
            if not text:
                return True
            if not said and rerun and rerun(text):
                return False
            # a chunk can hold more than one sentence ("Nova track! But don't go..."): judge each one past the first
            parts = re.split(r"(?<=[.!?…])\s+", text)
            keep = [x for j, x in enumerate(parts) if not ((said or j) and (worn_out(x, self._stale) or self._off_language(x)))]
            if len(keep) < len(parts):
                print(f"[capri] cut a worn-out or wrong-language line: {' '.join(x for x in parts if x not in keep)!r}")
            text = " ".join(keep)
            if not text:
                return True
            said.append(text)
            on_sentence(text)
            return True

        import time

        t0 = time.monotonic()
        for piece in self.llm.stream(msgs, temperature=temperature, max_tokens=170, avoid=self._avoid):  # streamed: length costs no wait
            if not raw and not self.slow and time.monotonic() - t0 > 25 and not getattr(self.llm, "cloud", False):
                self.slow = True  # 25 s before her first word: a weak PC, keep her prompts short from now on
                print(f"[capri] her brain is slow here ({time.monotonic() - t0:.0f}s to start): shorter prompts")
            raw += piece
            self._stream_raw += piece
            while True:  # a sentence ends at . ! ? (or ...) followed by a space, past the first few words
                m = re.search(r"^(.{12,}?[.!?\u2026]+[\"')\]]*)\s+", raw, re.S)
                if not m:
                    break
                if not give(m.group(1)):
                    return None  # stops generating: the stream closes and frees the GPU lock
                raw = raw[m.end():]
        tail = raw.strip()
        if tail and said and not re.search(r"[.!?…][\"')\]]*$", tail):
            tail = ""  # hit the length cap mid-sentence: end on her last full sentence instead
        if tail and not give(tail):
            return None
        return " ".join(said)

    # -- things she says on her own -------------------------------------------------------------------
    def _own(self, note: str, max_tokens: int = 60, screen: bool = False, can_pass: bool = False) -> str:
        if self.no_brain:  # nothing to think with: she stays quiet on her own
            return ""
        msgs = persona.build(self.user, language=self._recent_language()) + self.memory.history()  # same prefix: cache hit
        avoid = f" Don't repeat any of these: {' / '.join(self.recent_own[-5:])}." if self.recent_own else ""
        words = openers(self.recent_own[-5:] + [m["content"] for m in self.memory.history() if m["role"] == "assistant"][-3:])
        msgs.append({"role": "user", "content": "[" + " ".join(self._notes(note, screen) + [note]) + avoid + "]\n" + persona.reminder(self._recent_language())})
        for temp in (None, 1.0):  # an empty or junk line gets one more try
            out = _split_tags(self.llm.chat(msgs, max_tokens=max_tokens, temperature=temp, avoid=words))[0]
            if can_pass and "PASS" in out.upper().split():
                return ""
            text = persona.to_you(_clean(out), self.user)
            if text and "PASS" not in text.upper().split():
                self.recent_own = (self.recent_own + [text])[-8:]  # never into the chat history: it confused her
                return text
        return ""

    def idle_line(self, minutes: int) -> str:
        return self._own(f"{self.user} hasn't touched the PC for {minutes} minutes. Say one short thing to them: "
                         "check if they're still there, tease them for leaving you alone, or hum a few notes. Don't beg.",
                         screen=True)

    def react_line(self, title: str) -> str:
        return self._own(f"{self.user} just switched to \"{title}\". If it's interesting, funny or a game, say ONE short "
                         "curious or teasing line about what they're doing, using only what you can see. If it's boring "
                         "or private, reply with exactly PASS.", 50, screen=True, can_pass=True)

    def music_take(self, song: str, switching_to: str = "") -> str:
        if switching_to:
            return self._own(f"They're playing \"{song}\" and it's not nova. Roast it in a few words and say you're "
                             "putting on something better instead (don't name it). One or two short lines.", 60)
        return self._own(f"\"{song}\" is playing on their PC right now and you can hear it. Give your honest one-line "
                         "take on it: hype it if it slaps, roast it if it's weak. Night City taste.", 45)

    def hype_line(self, song: str) -> str:
        return self._own("You just put a banger on for them and you love it. One short hyped line, like you're vibing, "
                         "without naming the song.", 40)

    def _songs(self, tags, reply: Reply) -> None:
        """She ended her reply with [[play: Song - Artist]]: put it on (never two turns in a row, never over a song
        she just started on request)."""
        import time

        song = next((arg for name, arg in tags if name == "play" and arg), "")
        self._song_turns = getattr(self, "_song_turns", 99) + 1
        if not song or self._song_turns < 2 or any(a.startswith(("youtube:", "opening ", "playing ", "spotify"))
                                                   for a in reply.actions):
            return
        if time.monotonic() - getattr(self, "_music_at", -1e9) < 240:
            return  # a song went on a moment ago (asked for, or the secret one): don't restart or bury it
        self._song_turns = 0
        self._music_at = time.monotonic()
        import threading

        def go():
            result = launcher.youtube_play(song)
            print(f"[capri] her pick: {result}")
            self.on_played(result)

        threading.Thread(target=go, daemon=True).start()
        reply.actions.append(f"youtube: {song} (her idea)")

    def _tidy_fact(self, fact: str) -> str:
        """Rambly speech ("way better because I don't want to update you and you know...") -> one clean fact."""
        if len(fact) <= 70 and not _FILLER.search(fact):
            return fact
        try:
            out = self._aux("(Hidden step, not part of the chat.) " + TIDY.format(user=self.user, text=fact[:400]),
                                temperature=0.1, max_tokens=50)
            facts = parse_facts(out, self.user)
            return facts[0] if facts else fact
        except Exception as e:
            print(f"[capri] tidy failed: {e}")
            return fact

    # -- learning ---------------------------------------------------------------------------------------
    def _sum_up(self) -> None:
        """Chat that scrolled out of her history becomes a one-line episode ("talked about renaming the Discord
        server to Caprination"), so she still remembers it days later. One small call per six old exchanges."""
        if self.no_brain or getattr(self.llm, "offline", False) or not hasattr(self.memory, "to_sum"):
            return
        old = self.memory.to_sum()
        if not old:
            return
        talk = "\n".join(f"{self.user if m['role'] == 'user' else 'Capri'}: {m['content'][:200]}" for m in old)
        try:
            out = self.llm.chat([{"role": "user", "content": EPISODE.format(user=self.user, talk=talk[-3000:])}],
                                temperature=0.1, max_tokens=70)
        except Exception as e:
            print(f"[capri] summing up failed: {e}")
            return
        text = re.sub(r"\s+", " ", (out or "").strip().strip('"'))
        if text.upper().startswith("NONE"):
            text = ""
        self.memory.add_episode(text[:300], len(old))
        if text:
            print(f"[capri] remembered a talk: {text}")

    def learn(self, user_text: str) -> list[str]:
        """After a turn: pull lasting facts about the user out of what they said and save them.
        Only runs when they actually said something about themselves (cheap regex gate first). Not on a slow brain:
        her brain has one slot, and a learning call there held up the next reply by minutes."""
        if self.slow:
            return []
        self._sum_up()
        if not _SELF_TALK.search(user_text) or _dislike(user_text):
            return []
        prompt = LEARN.format(user=self.user, text=user_text.strip()[:400])
        try:
            out = self._aux("(Hidden step, not part of the chat.) " + prompt, temperature=0.1, max_tokens=80)
        except Exception as e:
            print(f"[capri] learn failed: {e}")
            return []
        facts = parse_facts(out, self.user)
        for f in facts:
            self.memory.remember(f, self.user)
            print(f"[capri] learned: {f}")
        return facts


_SLANG = [(r"\bhru\b", "how are you"), (r"\bhbu\b", "how about you"), (r"\bwyd\b", "what are you doing"),
          (r"\bwbu\b", "what about you"), (r"\bwdym\b", "what do you mean"), (r"\bngl\b", "not gonna lie"),
          (r"\bfr\b", "for real"), (r"\btbh\b", "to be honest"), (r"\bidk\b", "I don't know"), (r"\bidc\b", "I don't care"),
          (r"\brn\b", "right now"), (r"\bimo\b", "in my opinion"), (r"\bnvm\b", "never mind"), (r"\bbtw\b", "by the way"),
          (r"\bomg\b", "oh my god"), (r"\bty\b", "thank you"), (r"\bthx\b", "thanks"), (r"\bpls\b|\bplz\b", "please"),
          (r"\bu\b", "you"), (r"\bur\b", "your"), (r"\br\b", "are"), (r"\by\b(?=\s+\w)", "why"), (r"\bim\b", "I'm"),
          (r"\bdont\b", "don't"), (r"\bcant\b", "can't"), (r"\bwanna\b", "want to"), (r"\bgonna\b", "going to"),
          (r"\bso+o\b", "so"), (r"\bsup\b", "what's up"), (r"\bwassup\b|\bwsg\b", "what's up")]


# her lines when something broke, per language: down = her brain server stopped and wouldn't come back,
# offline = her brain never loaded, glitch = any other bug
TROUBLE = {
    "en": {"down": ["Ugh, my brain chip just flatlined and won't reboot. Give me a sec and say that again, choom.",
                    "Brain's offline, it crashed on me. Try again in a moment. If it keeps happening, right-click me, "
                    "Brain, and pick a lighter one."],
           "offline": ["My brain chip won't slot in, choom. I can still open stuff, play music and set timers. "
                       "Right-click me, Brain, and pick one to fix my head.",
                       "Running brainless rn, my AI didn't load. Right-click me, Brain, and pick one again."],
           "glitch": ["Something glitched in my code, choom. Say that again?",
                      "Whoa, static in my head for a sec. Try that one more time."]},
    "es": {"down": ["Uff, mi chip cerebral se cayó y no quiere reiniciar. Dame un segundo y repítemelo, choom.",
                    "Mi cerebro se colgó. Inténtalo en un momento. Si sigue pasando, clic derecho en mí, Brain, "
                    "y elige uno más ligero."],
           "offline": ["Mi chip cerebral no encaja, choom. Igual puedo abrir cosas, poner música y timers. "
                       "Clic derecho en mí, Brain, y elige uno.",
                       "Voy sin cerebro ahora mismo, mi IA no cargó. Clic derecho en mí, Brain, y elige otra vez."],
           "glitch": ["Algo falló en mi código, choom. ¿Me lo repites?",
                      "Uy, estática en mi cabeza. Prueba otra vez."]},
    "pt": {"down": ["Ugh, meu chip cerebral caiu e não quer reiniciar. Me dá um segundo e repete, choom.",
                    "Meu cérebro travou. Tenta de novo daqui a pouco. Se continuar, clica com o botão direito em mim, "
                    "Brain, e escolhe um mais leve."],
           "offline": ["Meu chip cerebral não encaixou, choom. Ainda abro coisas, toco música e ponho timers. "
                       "Botão direito em mim, Brain, e escolhe um.",
                       "Tô sem cérebro agora, minha IA não carregou. Botão direito em mim, Brain, e escolhe de novo."],
           "glitch": ["Algo bugou no meu código, choom. Repete?", "Eita, estática na minha cabeça. Tenta de novo."]},
    "fr": {"down": ["Ugh, ma puce cérébrale a planté et ne redémarre pas. Une seconde, et redis-moi ça, choom.",
                    "Mon cerveau a crashé. Réessaie dans un instant. Si ça continue, clic droit sur moi, Brain, "
                    "et choisis-en un plus léger."],
           "offline": ["Ma puce cérébrale ne rentre pas, choom. Je peux quand même ouvrir des trucs, mettre de la "
                       "musique et des minuteurs. Clic droit sur moi, Brain, et choisis-en un.",
                       "Je tourne sans cerveau là, mon IA n'a pas chargé. Clic droit sur moi, Brain, et rechoisis."],
           "glitch": ["Un truc a buggé dans mon code, choom. Tu répètes ?", "Oula, de la friture dans ma tête. Réessaie."]},
    "it": {"down": ["Uff, il mio chip cerebrale è andato e non riparte. Dammi un attimo e ripetimelo, choom.",
                    "Il mio cervello è crashato. Riprova tra poco. Se continua, clic destro su di me, Brain, "
                    "e scegline uno più leggero."],
           "offline": ["Il mio chip cerebrale non entra, choom. Posso comunque aprire cose, mettere musica e timer. "
                       "Clic destro su di me, Brain, e scegline uno.",
                       "Sono senza cervello ora, la mia IA non si è caricata. Clic destro su di me, Brain, e riscegli."],
           "glitch": ["Qualcosa si è inceppato nel mio codice, choom. Ripeti?", "Ops, statica in testa. Riprova."]},
    "de": {"down": ["Ugh, mein Hirnchip ist abgeschmiert und startet nicht neu. Gib mir 'ne Sekunde und sag's nochmal, "
                    "Choom.",
                    "Mein Hirn ist gecrasht. Versuch's gleich nochmal. Wenn's wieder passiert: Rechtsklick auf mich, "
                    "Brain, und nimm ein leichteres."],
           "offline": ["Mein Hirnchip sitzt nicht, Choom. Apps öffnen, Musik und Timer gehen trotzdem. Rechtsklick auf "
                       "mich, Brain, und such eins aus.",
                       "Ich laufe gerade hirnlos, meine KI hat nicht geladen. Rechtsklick auf mich, Brain, nochmal wählen."],
           "glitch": ["Irgendwas in meinem Code hat geglitcht, Choom. Nochmal?", "Whoa, Rauschen im Kopf. Versuch's nochmal."]},
    "pl": {"down": ["Ugh, mój chip mózgowy padł i nie chce wstać. Daj mi sekundę i powtórz, choom.",
                    "Mój mózg się wysypał. Spróbuj za chwilę. Jak będzie się powtarzać: prawy klik na mnie, Brain, "
                    "i wybierz lżejszy."],
           "offline": ["Mój chip mózgowy nie wchodzi, choom. Dalej otworzę apki, puszczę muzykę i ustawię timer. "
                       "Prawy klik na mnie, Brain, i wybierz jeden.",
                       "Lecę bez mózgu, moje AI się nie wczytało. Prawy klik na mnie, Brain, i wybierz jeszcze raz."],
           "glitch": ["Coś mi się zglitchowało w kodzie, choom. Powtórzysz?", "Ojej, szum w głowie. Spróbuj jeszcze raz."]},
    "ru": {"down": ["Угх, мой мозговой чип отрубился и не перезапускается. Секунду, и повтори, чум.",
                    "Мозг крашнулся. Попробуй через минутку. Если повторится: правый клик по мне, Brain, "
                    "и выбери полегче."],
           "offline": ["Мозговой чип не встаёт, чум. Открыть приложения, включить музыку и таймер я всё равно могу. "
                       "Правый клик по мне, Brain, и выбери мозг.",
                       "Я сейчас без мозга, ИИ не загрузился. Правый клик по мне, Brain, и выбери снова."],
           "glitch": ["Что-то глюкнуло в моём коде, чум. Повторишь?", "Ой, помехи в голове. Попробуй ещё раз."]},
}


def _no_brain_line(reply: Reply, intent, context: list[str], said: str) -> str:
    """Her answer when no AI is picked: what she just did (from the action's own result), else a short quip."""
    import random

    for note in context:  # the abilities leave facts behind: the time, the weather, headlines, lists
        m = re.search(r"Right now it's ([^.]+)\.", note)
        if m:
            return f"It's {m.group(1)}."
        m = re.search(r"Weather in [^:]+: right now ([^.]+)\.", note)
        if m:
            return f"Right now: {m.group(1)}."
        m = re.search(r"Top headlines right now:\n- ([^\n]+)", note)
        if m:
            return f"Top story: {m.group(1)}. Google News is open for the rest."
        m = re.search(r"Their lists right now: ([^.]+)\.", note)
        if m:
            return m.group(1).capitalize() + "."
        m = re.search(r"What they have set: ([^.]+)\.", note)
        if m:
            return m.group(1).capitalize() + "."
    for note in reply.notes:  # her updates: say what she found
        m = re.match(r"version (\S+) is out$", note)
        if m:
            return f"Version {m.group(1)} of me is out! Say \"download the update\" and I'll grab it."
    if reply.actions:
        done = reply.notes[-1] if reply.notes else reply.actions[-1]
        if done.startswith("couldn't"):
            return f"Hmm, I {done}. Tell me the exact name?"
        return random.choice(["Done: {}.", "On it: {}.", "Bet. {}.", "Ez: {}."]).format(done.rstrip("."))
    if intent is not None and intent.kind == "idle":
        return "Later. Wake me if u need me."
    return random.choice(["No brain picked, so I can't really chat rn. I can still open apps, play music, set "
                          "timers and stuff tho.",
                          "Brainless mode, sorry. Pick a brain in my menu if u wanna talk.",
                          "I'm running on vibes only rn. Ask me to open something or set a timer."])


class _Plain(dict):
    """Settings stand-in for runs without the app (tests): same get / set, nothing saved."""

    def set(self, key, value):
        self[key] = value


def _has_spotify() -> bool:
    try:
        from ..abilities.spotify import has_app

        return has_app()
    except Exception:
        return False


def plain(text: str) -> str:
    """'soo hru' -> 'so how are you': texting shorthand spelled out, so the model gets what you mean."""
    for rx, full in _SLANG:
        text = re.sub(rx, full, text, flags=re.I)
    return text


# asks to actually look at a picture: her real eyes take a snapshot of the window in front
_LOOK_ASK = re.compile(r"\b(?:rate|look at|take a look|check (?:this|it|that) out|see this|what do you think (?:of|about) "
                       r"(?:this|that|it|the)|thoughts on (?:this|that)|how(?:'s|s| is) (?:this|that)|is (?:this|it) good|"
                       r"fan ?art|drawing|this art|artwork|this pic|this picture|this image|this meme|what do you see)\b", re.I)


# the chat is about what they're up to: only then does her last look at the screen ride along
# ("how are you doing?" is small talk, not a question about the screen: only real "what am I doing / look at this" asks)
_ABOUT_SCREEN = re.compile(r"\bwhat (?:am i|are we|i(?:\s*am|'?m)|im)\b|\bwhat (?:game|app|site|video|show|movie) is this\b|"
                           r"\b(?:my|the|this) screen\b|\bwhat(?:'s| is) (?:this|that|on (?:my|the) screen)\b|\bcan you see\b|"
                           r"\bwhat do you see\b|\blook(?:ing)? at (?:this|that|my)\b|\bguess what i'?m\b|"
                           r"\bmy (?:game|code|work|stream|project|essay|drawing)\b", re.I)
# "what should I answer this dm", "what did she say", "help me reply", "what do u think they mean"
_CHAT_HELP = re.compile(
    r"\bwhat (?:should|do|would|can|could) i (?:say|answer|reply|respond|text|send|write|tell (?:her|him|them))\b|"
    r"\bhow (?:should|do|would|can) i (?:reply|respond|answer)\b|\bhelp me (?:reply|respond|answer|text)\b|"
    r"\bwhat (?:did|do|does) (?:u think |you think )?(?:they|he|she|it|this (?:person|guy|girl))\s+"
    r"(?:say|said|mean|want|need)\b|\bwhat (?:do )?(?:u|you) think (?:they|he|she) (?:said|meant|want|mean)\b|"
    r"\bwhat(?:'?s| is) (?:he|she|they|[a-z][\w.-]{1,20}) (?:saying|tryna say|trying to say|on about)\b|"
    r"\bwhat (?:did|does|do) (?!you\b|u\b|i\b)[a-z][\w.-]{1,20} (?:mean|want|say)\b|"
    r"\bwhat (?:are|r|were) (?:they|ppl|people|everyone|them|u guys) (?:saying|talking about|on about|tryna say)\b|"
    r"\bwhat (?:did|does) (?:the |that |this )?(?:last |new |other )?(?:person|guy|girl|message|msg|one|text|dude)"
    r"(?: say| mean| want| said)\b|"
    r"\b(?:read|check) (?:this|the|my|that) (?:dm|chat|message|messages|convo|conversation|text|texts)\b|"
    r"\breply (?:to|for) (?:this|her|him|them|that)\b|\banswer (?:this|that|her|him|them) (?:dm|message|text)\b|"
    r"\b(?:this|that|the) dm\b", re.I)
_ABOUT_MUSIC = re.compile(r"\b(?:song|music|track|playing|listening|banger|tune|beat)\b", re.I)


_PLAIN_OPENER = set("you your i i'm it it's that the a an and but so what how my me we this there is are do did "
                    "can just well yes no".split())


def catchphrases(replies: list[str]) -> list[str]:
    """Three-word phrases she's used in two or more recent replies ("don't flatline on"): the next reply is told to
    drop them, so a catchphrase doesn't turn into a tic."""
    seen: dict[str, int] = {}
    for r in replies:
        words = re.findall(r"[a-z']+", (r or "").lower())
        grams = {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}
        for g in grams:
            if not set(g.split()) <= _GIST_STOP:
                seen[g] = seen.get(g, 0) + 1
    return [g for g, n in sorted(seen.items(), key=lambda kv: -kv[1]) if n >= 2][:4]


_HER_SLANG = ("choom", "choomba", "gonk", "gonkin'", "preem", "nova", "delta", "eddies", "chrome", "flatline", "corpo",
          "corpos", "merc", "gig", "scav", "cyberpsycho", "jack", "wizard")
# "Don't go all corpo on me now.", "Just don't flatline on me.": the warning she kept tacking onto every reply
_WARNING = re.compile(r"^\W*(?:(?:but|just|and|now|so|oh|well|hey)[,\s]+)*(?:don'?t|do not|never)\b(?:\W+\w+){0,10}\W*$"
                      r"|^\W*(?:(?:but|just|and|now|so)[,\s]+)*don'?t (?:you )?(?:go|wanna|make me)\b.*\bon me\b", re.I)


def tics(replies: list[str]) -> list[str]:
    """Slang words in three or more of her recent replies: pushed down for the next one, so "choom" and "gonk"
    stop landing in every line. Both spellings, since "Choom" and " choom" are different tokens."""
    out = []
    for w in _HER_SLANG:
        stem = w.rstrip("'")
        if sum(bool(re.search(rf"\b{stem}\w*", r or "", re.I)) for r in replies[-6:]) >= 3:
            out += [w, w.capitalize()]
    return out


def grams(replies: list[str], n: int = 4) -> set[str]:
    """Every run of n words from her recent replies (lowercase, punctuation dropped), plus "^but hey": the first two
    words of a later sentence when two or more recent replies used them (her fallback tics, "But hey, at least...")."""
    out, starts = set(), {}
    for r in replies:
        words = re.findall(r"[a-z']+", (r or "").lower())
        out |= {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}
        for sent in re.split(r"(?<=[.!?…])\s+", r or "")[1:]:
            w = re.findall(r"[a-z']+", sent.lower())[:2]
            if len(w) == 2:
                starts.setdefault("^" + " ".join(w), set()).add(id(r))
    return out | {k for k, v in starts.items() if len(v) >= 2}


def worn_out(sentence: str, stale: set[str]) -> bool:
    """A later sentence of a reply that's only a tacked-on warning, or a line recycled from her recent replies."""
    if _WARNING.match(sentence):
        return True
    words = re.findall(r"[a-z']+", sentence.lower())
    if "^" + " ".join(words[:2]) in stale:
        return True
    runs = {" ".join(words[i:i + 4]) for i in range(len(words) - 3)}
    return any(not set(g.split()) <= _GIST_STOP for g in runs & stale)


def openers(replies: list[str]) -> list[str]:
    """Distinctive first words of her recent replies ("Oh", "Naw", "Yessir", "C'mon"), most recent first. Plain words
    like "you" or "I" are left alone: pushing those down would make her talk funny."""
    out = []
    for r in reversed(replies):
        m = re.match(r"\W*([A-Za-z']+)", r or "")
        if m and m.group(1).lower() not in _PLAIN_OPENER and m.group(1) not in out:
            out.append(m.group(1))
    return out[:5]


# "I ...", "my ...", "I'm ...": the user talking about themselves (likes, plans, people, habits)
_SELF_TALK = re.compile(
    r"\b(?:i'?m|i am|i'?ve|i was|i (?:like|love|hate|prefer|play|watch|listen|work|study|live|go|have|want|need|"
    r"usually|always|never|can'?t|don'?t|wanna|gotta|am gonna|will)|my|mine|call me)\b",
    re.I,
)

_FILLER = re.compile(r"\b(?:you know|i mean|kinda|basically|uh+|um+|way better because)\b|\blike,", re.I)

TIDY = """Rewrite this as ONE short, clear fact about {user}, starting with "{user}". Keep the meaning, drop filler.

Text: "{text}"
Fact:"""

EPISODE = """Sum up this chat between {user} and Capri in ONE or two short sentences for Capri to remember later:
what {user} talked about, asked for or did, with the names, games, songs, people and plans in it (e.g. "{user} wanted
to rename their Discord server to Caprination and asked about Sandevistans for a friend."). No greetings, no Capri
jokes. If nothing in it is worth remembering, write NONE.

{talk}

Summary:"""

LEARN = """Read what {user} just said and list facts about {user} worth remembering for weeks: likes, dislikes,
hobbies, games, music, people and pets, school or work, plans, routines, and how they want Capri to act.
Skip requests, questions, jokes, small talk, vague stuff ("works on things"), facts about Capri herself, and
anything only true right now ("I'm tired", "I'm hungry").
Write each fact as one short sentence that starts with "{user}". Max 3. If there is nothing lasting, write NONE.

{user} said: "{text}"
Facts:"""


_VAGUE_FACT = re.compile(
    r"\b(?:works on things|has an? (?:app|window|tab) open|likes to share|is excited|likes keeping secrets|"
    r"is (?:busy|here|online|talking|typing)|talks to capri|uses (?:a|the|their) (?:pc|computer)|uses the (?:phrase|word)|"
    r"says|interrupts?|protective of|asks|likes to (?:ask|talk|interrupt|share|chat)|is curious|is (?:happy|sad|angry))\b"
    r"|\b(?:capri|your|new powers|conversations?|questions?)\b",
    re.I,
)


def _loose_json(raw: str) -> dict | None:
    """Tool arguments as small models write them: {text: "milk"}, {'a': 1}, or nothing at all."""
    import json

    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {} if not raw.strip() else None
    text = m.group(0)
    quoted = re.sub(r"([{,]\s*)([A-Za-z_][\w-]*)\s*:", lambda k: f'{k.group(1)}"{k.group(2)}":', text.replace("'", '"'))
    for attempt in (text, quoted):
        try:
            out = json.loads(attempt)
            return out if isinstance(out, dict) else None
        except ValueError:
            pass
    return None


_SCREEN_ASK = re.compile(r"\b(?:look at|see|check|watch) (?:my|the|this) (?:screen|monitor|desktop|game|tab|window)\b|"
                         r"\bwhat(?:'s| is) (?:on )?(?:my|the) screen\b|\bwhat (?:do|can) (?:you|u) see\b|"
                         r"\bwhere (?:am i|are we|i am|im)\b|\btell me what (?:you|u) see\b|"
                         # the same asks in her other languages
                         r"qu[eé] (?:ves|est[aá]s viendo|estoy haciendo|hay en (?:mi|la) pantalla)|mira (?:mi|la) pantalla|"
                         r"o que (?:voc[eê] )?(?:v[eê]|est[aá] vendo|estou fazendo)|olha (?:minha|a) tela|"
                         r"qu'est-ce que tu vois|que vois-tu|tu vois quoi|qu'est-ce que je fais|regarde (?:mon|l')[ée]cran|"
                         r"что (?:ты )?(?:видишь|я делаю|на (?:моём |моем )?экране)|посмотри на (?:мой )?экран|"
                         r"cosa (?:vedi|sto facendo|c'[eè] sul(?:lo)? schermo)|guarda (?:il mio |lo )?schermo|"
                         r"was (?:siehst du|mache ich|ist auf meinem bildschirm)|schau (?:auf )?(?:meinen |den )?bildschirm|"
                         r"co (?:widzisz|robię|jest na (?:moim )?ekranie)|spójrz na (?:mój )?ekran", re.I)
_SAD = re.compile(r"\b(?:sad|depress\w*|down bad|feel(?:ing)? (?:down|low|bad|empty|alone|lonely|like shit|like crap)|"
                  r"lonely|cry(?:ing)?|cried|heartbroken|broke up|miss (?:her|him|them|my)|bad day|rough day|shit day|"
                  r"hate my life|not (?:okay|ok|good)|upset|hurting|i'?m hurt|tired of everything|triste|traurig)\b", re.I)
# the secret song's line: soft, no explaining
_EGG = ("(You picked this one yourself because {user} is feeling down: \"I Really Want to Stay at Your House\", the "
        "Edgerunners song. Say ONE soft, warm line while it starts, like you're staying right here with them tonight. "
        "Don't call it a secret, a surprise or an easter egg, and don't name the song.)")
# looser asks for her eyes, said the way people really type them: "look again", "look what lawyer guy said lol",
# "read now", "use ur eyes", "peek", "what u think this lyrics is for", "discord open"
_LOOK_LOOSE = re.compile(
    r"^\W*(?:\w+\W+){0,2}?look\b(?!\s+(?:up|for|into|forward|like|after|good|bad|nice|cute|old|tired|fine)\b)|"
    r"\blook (?:again|now|here|closer|properly|harder|once more|what|who|how)\b|"
    r"\b(?:use|open|check) (?:your|ur|u|ya|those) eyes\b|"
    r"\b(?:check|see|look at|peek at|read|scan) (?:out )?(?:(?:my|ma|mah|me|the|this|that|our|da)\s+)?(?:discord|"
    r"whatsapp|telegram|insta(?:gram)?|tiktok|twitter|reddit|steam|spotify|youtube|browser|chrome|tab|chat|dms?|"
    r"messages?|msgs?|server|game|screen|monitor|desktop|window|page|code|stream|playlist)\b|\b(?:take a )?(?:peek|glance)\b|"
    r"^\W*(?:\w+\W+){0,2}?read\b(?!\s+(?:(?:me|us)\s+)?(?:a|an|any|books?|manga|about|the news|news|stor(?:y|ies)|poems?)\b)|"
    r"\b(?:see|read|check) what (?:\w+\W+){0,3}?(?:said|says|wrote|writes|typed|sent|posted|asked)\b|"
    r"\bthis (?:lyrics?|song|video|vid|post|tweet|message|msg|comment|chat|code|error|page|game|pic|meme|text|dm)\b|"
    r"\b(?:discord|the chat|chat|the server|the game|spotify|youtube) (?:is |'s )?(?:open|up|right there)\b", re.I)
# ...and whether that look is a chat to read and help with, not the screen in general
_CHATTY = re.compile(r"\b(?:said|says|wrote|typed|sent|posted|asked|asking|answer|reply|respond|people|ppl|everyone|"
                     r"chat|messages?|msgs?|dm|discord|whatsapp|telegram|guy|kid|friend)\b", re.I)
# a short follow-up right after a look: do it again ("again", "read now", "answer the other people", "look harder")
_LOOK_AGAIN = re.compile(r"\b(?:again|now|more|other|others|rest|everyone|them|answer|reply|read|look|see|check|still|"
                         r"scroll|new|next|harder|closer)\b", re.I)
_PICTURE = re.compile(r"\b(?:rate|fan ?art|drawing|art|artwork|pic|picture|image|meme|photo)\b", re.I)
_YES = re.compile(r"^\W*(?:y(?:es|eah|ep|up|a)?|sure|ok(?:ay)?|do it|go(?: for it| ahead)?|please|yes please|"
                  r"of course|absolutely|bet|run it)\b", re.I)
_ANOTHER = re.compile(r"\b(?:else|another|different|change|switch|swap|next|skip|other|otr[ao]|outr[ao]|autre|altr[ao]|"
                      r"andere\w*|inn[aey]|друг\w*)\b", re.I)
_NOT_NOW = re.compile(r"\b(?:no|not|don'?t|do not|nah|never|later|wait|no lo|todav[ií]a no|pas|nicht|nie|не)\b", re.I)
_NO = re.compile(r"^\W*(?:no+|nah|nope|don'?t|stop|cancel|never ?mind|not now|leave it)\b", re.I)
_PUSHBACK = re.compile(
    r"^\W*(?:no+|nah|nope|stop|quit|enough|drop it|i said no|what(?:'s| is) wrong with you|wtf|what the f\w*)\b"
    r"|\b(?:i (?:already )?said no|stop (?:asking|saying|talking about)|why (?:do |are )?(?:you )?(?:always|keep)|"
    r"you keep (?:saying|asking)|enough with|not again)\b", re.I)


def _gist(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z']+", text.lower()) if w not in _GIST_STOP]


_GIST_STOP = set("the a an and or but to of in on for with is are was were be it this that i you my your me "
                 "so just like oh hey yeah got get some any what do does did can have has had uh um well "
                 "choom buddy".split())


def repeats(new: str, old: str, name: str = "") -> bool:
    """True when a reply is basically one she already gave: near-identical, or the same 3 content words in a row."""
    import difflib

    a, b = new.lower().strip(), old.lower().strip()
    if not a or not b:
        return False
    if difflib.SequenceMatcher(None, a, b).ratio() > 0.72:
        return True
    drop = {name.lower()} if name else set()
    ga, gb = [w for w in _gist(a) if w not in drop], [w for w in _gist(b) if w not in drop]
    # a shared run of 3 content words, or a meaty pair like "new powers"
    if {tuple(gb[i : i + 3]) for i in range(len(gb) - 2)} & {tuple(ga[i : i + 3]) for i in range(len(ga) - 2)}:
        return True
    pairs = {tuple(gb[i : i + 2]) for i in range(len(gb) - 1)} & {tuple(ga[i : i + 2]) for i in range(len(ga) - 1)}
    return any(len(x) + len(y) >= 9 for x, y in pairs)


def parse_facts(out: str, user: str) -> list[str]:
    facts = []
    sentences = []
    for line in out.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        # "Alex likes X. Alex is Y. NONE." -> one candidate per sentence, minus the NONE marker
        sentences += [s.strip() for s in re.split(r"(?<=[.!])\s+", line) if s.strip()]
    for line in sentences:
        if not line or "NONE" in line.upper().split() or line.upper().strip(" .") == "NONE":
            continue
        if _VAGUE_FACT.search(line) or len(line.split()) < 3:
            continue
        if not line.lower().startswith(user.lower()) or not 8 <= len(line) <= 140:
            continue
        if re.search(r"\b(?:right now|currently|today|tonight|at the moment)\b", line, re.I):
            continue
        facts.append(line if line.endswith((".", "!")) else line + ".")
    return facts[:3]


_DISLIKE = re.compile(
    r"\bi\s+(?:really\s+)?(?:don'?t|do not|never)\s+(?:like|enjoy|want|play|care about)\s+(?:any\s+|these\s+|those\s+|this\s+kind of\s+|that\s+kind of\s+)?(.{2,40}?)(?:\s+at all)?(?=[.,!?]|$| so| but| and| why)"
    r"|\bi\s+(?:hate|dislike|can'?t stand)\s+(.{2,40}?)(?=[.,!?]|$| so| but| and)",
    re.I,
)


def _dislike(text: str) -> str:
    m = _DISLIKE.search(text)
    if not m:
        return ""
    what = re.sub(r"^(?:these|those|this|that|the)?\s*(?:kinds?|sorts?|types?) of\s+", "", (m.group(1) or m.group(2) or "").strip(" .!?"), flags=re.I)
    return "" if what.lower() in ("it", "that", "this", "you", "them") else what


def _split_tags(raw: str) -> tuple[str, list[tuple[str, str]]]:
    tags = [(m.group(1).lower(), (m.group(2) or "").strip()) for m in TAG.finditer(raw)]
    return TAG.sub("", raw).strip(), tags


_ASSISTANT = re.compile(
    r"\b(?:I'?m|I am)\s+(?:just\s+)?(?:your|an|a)\s+(?:(?:smart|helpful|personal|virtual|friendly|digital)\s+)*"
    r"(?:AI\s+)?(?:assistant|language model|chatbot)\b",
    re.I,
)


def third_person(fact: str, user: str) -> str:
    """'my cat is called Mochi' -> 'Alex's cat is called Mochi', so recalled facts read right."""
    f = fact.strip().rstrip(".")
    f = re.sub(r"^(?:that\s+)?", "", f, flags=re.I)
    f = re.sub(r"\bI am\b|\bI'm\b", f"{user} is", f, flags=re.I)
    f = re.sub(r"\bI've\b", f"{user} has", f, flags=re.I)
    f = re.sub(r"\bI\b", user, f)
    f = re.sub(r"\b(?:my)\b", f"{user}'s", f, flags=re.I)
    f = re.sub(r"\b(?:me)\b", user, f, flags=re.I)
    f = re.sub(r"\bmine\b", f"{user}'s", f, flags=re.I)
    return f[:1].upper() + f[1:]


_ACTION = re.compile(r"^(?:laugh|giggle|grin|smirk|sigh|snicker|chuckle|wink|scoff|roll|glitch|zap|flicker|lean|"
                     r"shrug|pout|yawn|stretch|wave|nod|look|stare|blink|tilt|spin|twirl|cross|tap|hum|whistle|crack|"
                     r"pat|poke|hug|smile|beam|sniff|gasp|groan|huff|tease|whisper|static|bzz|beep)", re.I)


def _unstar(m) -> str:
    """*laughs* / *rolls eyes* are actions: gone. *Cyberpunk: Edgerunners* is a title in italics: kept, minus stars."""
    inner = m.group(1).strip()
    words = inner.split()
    if _ACTION.match(inner) or not inner[:1].isupper():
        return ""
    return inner if len(words) >= 2 or re.search(r"\d", inner) or len(inner) > 3 else ""


def _clean(text: str) -> str:
    """Strip things a chat model adds that shouldn't be spoken: name prefixes, *actions*, stray notes."""
    text = re.sub(r"^\s*[(\[]?\s*(?:capri|campy)\s*[:>]\s*", "", text, flags=re.I)
    if text.count(")") > text.count("("):  # "(Capri: mm-hmm...)" lost its opener above
        text = text[::-1].replace(")", "", 1)[::-1]
    text = re.sub(r"[\u2500-\u259f\ufffd]+", "", text)  # box/block glyphs the model leaks ("this \u2593/0")
    text = re.sub(r"\s*[/\\|]\s*\d*\s*$", "", text)
    text = re.sub(r"\*([^*]{1,80})\*", _unstar, text)  # *laughs* goes, *Cyberpunk: Edgerunners* stays (no stars)
    text = re.sub(r"\((?:laughs?|giggles?|grins?|smirks?|sighs?|snickers?|chuckles?|winks?|scoffs?|rolls? (?:her )?eyes)[^)]{0,20}\)\s*", "", text, flags=re.I)
    text = re.sub(r"\[[^\]]{0,200}\]|\(Reply as Capri[^)]*\)", "", text)
    text = re.sub(r"\s*[\[\]]+\s*", " ", text)  # a lone bracket echoed from the [notes] ("...cry a little. ]")
    text = text.split("\nUser:")[0].split("\nYou:")[0]
    text = re.sub(r"(?<!\w)(?:uwu|owo|nya+|>w<|\^_\^|:3)(?!\w)[~!.,]*", "", text, flags=re.I)  # not her
    text = text.replace("~", "")
    # nobody texts with semicolons: "you're stuck; ask her" -> "you're stuck. Ask her"
    text = re.sub(r"\s*;\s*(\w?)", lambda m: ". " + m.group(1).upper(), text).replace(";", "")
    text = re.sub(r"[^.!?]*\b(?:xiao ?hei\w*|xiaoheiqaq)\b[^.!?]*[.!?]?", "", text, flags=re.I)  # the model's maker leaking in
    text = re.sub(r"[^.!?]*\b(?:wait for it to load|give it a (?:sec|second) to load)\b[^.!?]*[.!?]?", "", text, flags=re.I)
    # she is never anyone's assistant
    text = _ASSISTANT.sub("I'm Capri, the glitch living in your rig", text)
    text = re.sub(r"\b(?:your|an?)\s+(?:smart\s+|AI\s+|virtual\s+|personal\s+)*assistant\b", "your favorite glitch", text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip()


