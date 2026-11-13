"""Spot ability requests in code: music (Spotify / YouTube), opening apps, web searches, text files, email drafts,
MCP tools, remembering things, and goodbyes.
Clear-cut asks are caught by regex; vague ones that sound like an action go to a tiny planner call."""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass

WAKE = r"^(?:(?:hey|yo|ok(?:ay)?|so)[,\s]+)?(?:capri|campy|capricorn)?[,\s]*"
POLITE = r"(?:(?:can|could|would|will) (?:you|u|ya)\s+|please\s+|pls\s+|go\s+(?:ahead\s+(?:and\s+)?)?|i want (?:you to\s+)?)*"


@dataclass
class Intent:
    kind: str  # play | youtube | pick (her choice of music) | spotify | open | search | read | note | email | tool
    #            | remember | idle | time | news | reminder | privacy | focus | weather | clip | todo | pc | update
    arg: str = ""


_PLAY = re.compile(WAKE + POLITE + r"(?:play|put on|queue(?: up)?|blast|throw on)\s+(?:me\s+)?(.+?)(?P<sp>\s+on spotify)?[\s.!?]*$", re.I)
# anything aimed at Spotify: "play X in spotify", "put this on spotify", "spotify, play X", "open spotify and play X"
_SP_AT = r"\s+(?:on|in|with|from|using|through|thru|via|at)\s+(?:my\s+|the\s+)?spotify(?:\s+app)?"
_SPOTIFY = re.compile(
    WAKE + POLITE + r"(?:(?:(?:open|use|go on|hop on|get on)\s+(?:up\s+)?(?:my\s+|the\s+)?)?spotify[,\s]+(?:and\s+|to\s+)?(?:play|put on|queue(?: up)?|blast|search(?: for)?|find)\s+(?:me\s+)?(?P<a>.+?)"
    r"|(?:play|put on|put|throw|queue(?: up)?|blast|throw on|search(?: for)?|find|look up|pull up|open)\s+(?:me\s+)?(?P<b>.+?)" + _SP_AT + r")"
    r"[\s.!?]*$", re.I)
# "this" in "play this on spotify": the song that's on right now
_THIS_SONG = re.compile(r"^(?:this|that|it|this (?:one|song|track|tune|banger)|that (?:one|song|track)|the (?:song|track)"
                        r"(?: (?:that'?s |that is |you'?re |thats )?playing)?|(?:the )?(?:current|same) (?:song|track|one)"
                        r"|what(?:'s| is|s)? playing(?: now| rn)?|whatever(?:'s| is) playing)$", re.I)
# "something like Ado", "songs by Ado", "I'm looking for something like, you know, like Ado": they named an artist
_HINT = re.compile(r"\b(?:(?:some(?:thing)?|some (?:music|songs?|tracks?|tunes?|stuff)|music|songs?|tracks?|tunes?|stuff|anything)"
                   r"\s+(?:(?:kinda|kind of|sort of)\s+)?(?:like|by|from|of)|(?:looking for|in the mood for|craving|vibing to)"
                   r"(?:\s+some(?:thing)?(?:\s+like)?)?)[,\s]+(?:(?:you know|y'?know|i mean|uh+|um+|like|maybe|idk)[,\s]+)*"
                   r"(?P<who>[^,.!?]+?)(?=\s*(?:[,.!?]|\s(?:on|in|with|from|using|through|via)\s+(?:my\s+|the\s+)?spotify\b|"
                   r"\s(?:for me|please|pls|plz|or something|or whatever|maybe|rn|right now|now|thanks|capri|choom)\b|$))", re.I)
_WANTS_MUSIC = re.compile(r"\b(?:play|put on|throw on|queue|blast|looking for|in the mood for|wanna hear|want to hear|"
                          r"listen to)\b", re.I)
# "I want to listen to some Kendrick", "let's hear Ado", "can we listen to Blinding Lights on spotify"
_LISTEN = re.compile(r"\b(?:i\s+(?:wanna|want to|would like to|'?d like to|feel like)|let'?s|lemme|let me|can (?:we|i)|"
                     r"could (?:we|i)|we should)\s+(?:go\s+)?(?:listen to|hear|vibe to|jam to)\s+(?:me\s+)?(?P<what>.+?)"
                     r"(?P<sp>\s+(?:on|in|with|from|using|through)\s+(?:my\s+|the\s+)?spotify)?[\s.!?]*$", re.I)
# "what time is it", "what's the date", "what day is it"
_TIME = re.compile(r"\bwhat(?:'?s| is)?\s+(?:the\s+)?time\b(?=(?:\s+(?:is it|it is|now|rn|right now|again|pls|please|"
                   r"capri))*[\s?.!]*$)|\btime is it\b|\btell me the time\b|\bcurrent time\b|"
                   r"\bwhat(?:'?s| is)\s+(?:the\s+|today'?s\s+)?date\b|\bwhat day is (?:it|today)\b|\bwhat(?:'?s| is) today\b|"
                   r"\bwhat year is it\b", re.I)
# "what's the news", "look for the news", "any news about Cyberpunk", "latest headlines"
_NEWS = re.compile(r"\b(?:what(?:'?s| is)?\s+(?:the\s+)?(?:latest\s+)?news|(?:check|look (?:for|at|up)|show me|tell me|read me|"
                   r"get me|pull up|open|search(?: for)?|find|give me|catch me up on)\s+(?:the\s+|some\s+|today'?s\s+|latest\s+)*"
                   r"(?:news|headlines)|any\s+(?:new\s+)?news|latest news|news (?:today|rn|right now)|(?:the|today'?s) "
                   r"headlines|what'?s happening in the world)\b(?:\s+(?:about|on|for|from|in|regarding)\s+(?P<topic>.+?))?"
                   r"[\s.!?]*$", re.I)
_NOT_A_NAME = re.compile(r"^(?:music|something|anything|songs?|tracks?|stuff|that|this|it|good|chill|sad|happy|new|you|me|"
                         r"else|better|different|a (?:song|banger)|the (?:song|music))$", re.I)


def _hinted(t: str) -> str | None:
    """'...play some music in Spotify, I'm looking for something like Ado' -> 'Ado' (None if no artist is named)."""
    for m in _HINT.finditer(t):
        who = re.sub(r"^(?:(?:you know|like|the|some)[,\s]+)+", "", m.group("who").strip(), flags=re.I).strip(" '\"")
        if who and len(who.split()) <= 6 and not _NOT_A_NAME.match(who):
            return who
    return None


_YT = re.compile(WAKE + POLITE + r"(?:(?:play|watch|find|put on)\s+(.+?)\s+on youtube|(?:search\s+)?youtube(?:\s+for)?\s+(.+?))[\s.!?]*$", re.I)
_OPEN = re.compile(WAKE + POLITE + r"(?P<verb>open(?: up)?|launch|start(?: up)?|pull up|fire up|boot up|run)\s+(?:the\s+|my\s+)?(.+?)[\s.!?]*$", re.I)
# a game through its launcher, any verb: "play peak on steam", "start fortnite from epic" (not a song to play)
_GAME_AT = re.compile(WAKE + POLITE + r"(?:play|launch|start(?: up)?|open(?: up)?|run|boot(?: up)?|fire up|load(?: up)?)\s+"
                      r"(?:the\s+|my\s+)?(?:game\s+)?(?P<what>.+?\s+(?:on|in|from|via|through|using)\s+(?:my\s+|the\s+)?"
                      r"(?:steam|epic(?: games)?|battle\.?net|ea app|ubisoft(?: connect)?|gog(?: galaxy)?|xbox(?: app)?))"
                      r"[\s.!?]*$", re.I)
# "open" in her other languages: abre / öffne / ouvre / открой / apri / otwórz / abra / starte / lance / запусти / uruchom
_OPEN_INTL = re.compile(r"^(?:(?:hey|oye|hola|salut|hallo|ciao|cześć|привет|ei)[,\s]+)?(?:capri|campy)?[,\s]*"
                        r"(?:(?:puedes|podrías|peux-tu|kannst du|puoi|możesz|можешь|pode|podes|por favor|bitte|"
                        r"s'il te plaît|per favore|proszę|пожалуйста)[,\s]+)*"
                        r"(?:abre|ábreme|abrir|abra|arranca|inicia|öffne|öffnen|starte|starten|ouvre|ouvrir|lance|lancer|"
                        r"apri|aprire|avvia|otwórz|otworzyć|uruchom|włącz|открой|открыть|запусти|запустить|включи)\s+"
                        r"(?:(?:el|la|los|le|l'|den|die|das|il|lo|mi|mein(?:e|en)?|mon|ma|meu|minha|mój|moją|bitte|"
                        r"por favor|per favore|proszę|пожалуйста|mal|moi|mir|me|мне)\s+)*"
                        r"(?P<what>.+?)[\s.!?¡¿]*$", re.I)
_CTRL = [
    ("pause", re.compile(r"\b(pause|stop)\b.*\b(music|song|track|spotify|video|it|that)\b|^\W*(pause|stop the music)\W*$", re.I)),
    ("resume", re.compile(r"\b(resume|unpause|continue)\b.*\b(music|song|track|spotify|video|playing)\b|^\W*(resume|unpause)\W*$", re.I)),
    ("next", re.compile(r"\bskip\b|\bnext (?:song|track|one|video|tune|banger)\b|^\W*next\W*$", re.I)),
    ("previous", re.compile(r"\b(previous|last|go back)\b.*\b(song|track)\b", re.I)),
]
_BYE = re.compile(
    r"^\W*(?:(?:hey|ok(?:ay)?|alright)[,\s]+)?(?:capri|campy)?[,\s]*(?:(?:can|could) you\s+|you can\s+|just\s+)?"
    r"(?:go|leave|bye|bye bye|goodbye|good night|night|see ya|see you|cya|later|gotta go|i'?m out|peace)"
    r"(?:\s+(?:now|then|for now|capri|buddy|choom|later))*\W*$",
    re.I,
)
_REMEMBER = re.compile(r"\b(?:remember|don'?t forget|keep in mind|note)\s+(?:that\s+)?(.+)", re.I)
# asking what she remembers isn't telling her to: "do you remember what we talked about?", "remember when you said
# you hate music?" (a trick: saving that question as a fact put a lie in her memory)
_RECALL_ASK = re.compile(r"\?\s*$|\b(?:do|did|can|could|would|don'?t) (?:you|u|ya) (?:still )?remember\b|"
                         r"\bremember (?:when|what|who|how|where|why|if|the time|me)\b", re.I)
_SEARCH = re.compile(WAKE + POLITE + r"(?:search(?: up| for| the (?:web|net|internet) for| online for)?|google|"
                     r"look up|look into|find out(?: about)?|check (?:online|the net|the web)(?: for)?)\s+(?P<q>.{3,}?)"
                     r"[\s.!?]*$", re.I)
_SEARCH_UP = re.compile(WAKE + POLITE + r"(?:look|search|google)\s+(?P<q>.{3,}?)\s+up[\s.!?]*$", re.I)
# "open a new tab in my browser and look for green yuri": the browser named anywhere, then the last search verb
_IN_BROWSER = re.compile(r"\b(?:browser|new tab|a tab|tab|google|chrome|brave|firefox|edge|open (?:it|that) up|"
                         r"pull (?:it )?up|show me)\b", re.I)
_TAB_SEARCH = re.compile(r"\b(?:browser|new tab|tab|google|chrome|brave|firefox)\b.*\b(?:search(?: up| for)?|"
                         r"look(?: it)? (?:up|for)|google|find)\s+(?P<q>.{3,}?)[\s.!?]*$", re.I)
_FILLER = re.compile(r"^(?:(?:like|it|that|um+|uh+|for|about|in|on|so|just|me)\b[\s,]*)+", re.I)
_MUSIC_WORD = re.compile(r"\b(?:play|put on|throw on|queue|blast|listen|hear|music|songs?|tracks?|tunes?|spotify|"
                         r"playlist|beats?|album|artist)\b", re.I)


def _tab_search(m: re.Match) -> "Intent | None":
    """The search a _TAB_SEARCH match asks for, opened in their browser (None when it's only "it")."""
    q = _FILLER.sub("", _TAIL.sub("", m.group("q")).strip(" .!?,\"'")).strip(" .!?,\"'")
    if len(q) >= 3 and q.lower() not in ("it", "that", "this", "it up"):
        return Intent("search", "web|" + q)
    return None
_URL = re.compile(r"https?://\S+")
_NOTE = re.compile(r"\b(?:make|write|create|save|jot(?: down)?|put|type up|draft)\s+(?:me\s+|us\s+)?(?:a\s+|an\s+|the\s+)?"
                   r"(?:new\s+|quick\s+|little\s+)?(?:txt|\.txt|text file|text doc(?:ument)?|note|notes|file|list|"
                   r"to-?do(?: list)?|doc(?:ument)?)\b", re.I)
_EMAIL = re.compile(r"\b(?:write|draft|send|make|compose|type up|shoot)\b.{0,30}\b(?:e-?mail|mail)\b|"
                    r"\be-?mail\s+(?:to\s+)?\S+@\S+|\be-?mail (?:my|the|him|her|them)\b", re.I)
# "go mini", "be small", "shrink", "mini mode", in her languages too
_MINI = re.compile(r"\b(?:(?:go|turn|be|get|switch to|become|stay)\s+(?:mini|small|tiny|chibi)(?:\s+mode)?|mini[\s-]?mode|"
                   r"shrink(?: yourself)?|modo mini|ponte (?:mini|pequeña)|mode mini|deviens? (?:mini|petite)|"
                   r"mini-?modus|werde? (?:mini|klein)|modalità mini|diventa (?:mini|piccola)|tryb mini|"
                   r"мини[\s-]?режим|стань (?:мини|маленькой))\b", re.I)
# "lie down", "lay on the taskbar": her lying pose (full size)
_LAY = re.compile(r"\b(?:lie|lay|lye)(?:\s+(?:back\s+)?down|\s+on (?:the |my )?(?:taskbar|window|app)\w*)\b|"
                  r"\b(?:túmbate|acuéstate|deita|allonge-toi|leg dich hin|sdraiati|połóż się|ляг)\b", re.I)
_IDLE = re.compile(r"\b(go (?:chill|to sleep|take a nap)|take a (?:break|nap)|be quiet|hush|leave me alone)\b", re.I)

# "play some music", "change the music", "put something better on": they want music, not a specific song
_PICK = re.compile(
    r"\b(?:change|switch|swap)\b.{0,20}\b(?:music|song|track|tune|it|this|that)\b"
    r"|\b(?:something|anything|a song|some(?:thing)?)\s+(?:else|better|different|good|nova|preem|cyberpunk|new)\b"
    r"|\b(?:play|put on|throw on|queue)\s+(?:me\s+|us\s+)?(?:some\s+|a\s+|any\s+)?(?:music|something|anything|songs?|tunes?|bangers?|tracks?|beats?)\b"
    r"|\b(?:your|ur)\s+(?:fav(?:ou?rite)?|pick|choice)\b.{0,15}\b(?:song|music|track)?",
    re.I,
)
_ASKS_SONG = re.compile(r"^\W*(?:yo\W+)?(?:capri\W+)?(?:whats|what(?:'s| is)?|which|who(?:'s| is)?|do you (?:like|know)|is this|name (?:of )?this)\b"
                        r".{0,25}\b(?:song|track|music|playing|this|artist|singing)\b[^.!]*\??\W*$", re.I)
# words people put after a request that are never part of a song title
_TAIL = re.compile(r"(?:[\s,]+(?:for me|for us|please|pls|plz|capri|campy|real quick|rn|right now|now|thanks|thank you|"
                   r"choom|buddy|bro|yo))+\s*$", re.I)

# vague asks: the planner decides ("change the song", "something else")
_VAGUE = re.compile(r"\b(?:this|that one|the one|something else|another one|different|change (?:it|this|that|the song)|"
                    r"boring|switch (?:it|this)|anything else|other one|instead)\b", re.I)


# her own updates, in the languages she speaks: "any new update?", "hay alguna actualización", "download the update"
_UPD = (r"(?:updates?|upgrades?|actualizaci[oó]n(?:es)?|atualiza[cç](?:[aã]o|[oõ]es)|mises? [àa] jour|aggiornament[oi]|"
        r"aktualisierung(?:en)?|aktualizacj\w*|обновлени\w*|new(?:er)? version|nueva versi[oó]n|nova vers[aã]o|"
        r"nouvelle version|nuova versione|neue version|nowa wersja|нов\w+ верси\w*)")
# "install it": installing what she downloaded (the rest of the verbs fetch it first)
INSTALL_VERB = re.compile(r"\b(?:install|apply|run|inst[aá]la\w*|installe\w*|installa\w*|installier\w*|zainstaluj|"
                          r"установи\w*)\b", re.I)
# her answer to "want me to download / install it?" given as a verb: "download it", "instálala", "go install"
UPDATE_YES = re.compile(INSTALL_VERB.pattern + r"|\b(?:download|get it|grab it|descarga\w*|baixa\w*|t[ée]l[ée]charge\w*|"
                        r"scarica\w*|lade\w*|herunterlad\w*|pobierz|скача\w*)\b", re.I)
_UPD_GET = re.compile(r"\b(?:download|install|get|grab|pull|apply|run|descarga\w*|inst[aá]la\w*|baixa\w*|t[ée]l[ée]charge\w*|"
                      r"installe\w*|scarica\w*|installa\w*|lade\w*|herunterlad\w*|installier\w*|pobierz|zainstaluj|"
                      r"скача\w*|установи\w*|загрузи\w*)\b.{0,25}" + _UPD + r"|\bupdate (?:yourself|urself|now)\b|"
                      r"\bactual[ií]zate\b|\bupgrade yourself\b", re.I)
_UPD_ASK = re.compile(r"\b(?:any|new|newer|latest|check(?:ing)?|look(?:ing)?|is there|are there|got|available|out yet|"
                      r"hay|alguna|nueva|tienes|há|tem|alguma|nova|y a-t-il|nouvelle|c'?è|nuov[oa]|gibt es|neues?|"
                      r"jest|nowa|есть|нов\w+)\b.{0,30}" + _UPD + r"|" + _UPD + r".{0,30}(?:\?|\b(?:for you|on you|"
                      r"available|out|para ti|pour toi|per te|für dich|dla ciebie|для тебя)\b)|"
                      r"\b(?:are|r) (?:you|u) (?:up to date|updated|outdated)\b|\bwhat version\b|"
                      r"\bqu[ée] versi[oó]n\b|\bwhich version\b", re.I)
_UPD_NOT_HER = re.compile(r"\b(?:update|upgrade)\s+(?:my|the|this|our|his|her)\b(?!.*\b(?:you|yourself|capri)\b)|"
                          r"\bupdates?\s+(?:on|about|for|from)\s+(?:the|my|a|this|that|his|her|their)\b", re.I)


def update_intent(t: str) -> Intent | None:
    """Questions about her own updates. -> Intent("update", "check" | "get") or None."""
    if _UPD_NOT_HER.search(t):  # "update my resume" is a chore, not her
        return None
    if m := _UPD_GET.search(t):
        return Intent("update", "install" if INSTALL_VERB.match(m.group(0)) else "get")
    if _UPD_ASK.search(t):
        return Intent("update", "check")
    return None


_GENRE_ASK = re.compile(r"\b(?:play|put on|throw on|queue(?: up)?|blast|spin|(?:i\s+)?(?:wanna|want to|would like to|"
                        r"'?d like to) (?:hear|listen to)|let'?s (?:hear|listen to)|listen to|in the mood for|pon(?:me|nos)?|"
                        r"toca(?:me)?|coloca|bota|p[õo]e|mets|joue|metti|suona|spiel|pu[śs][ćc]|w[łl][ąa]cz|включи|поставь)\s+"
                        r"(?P<what>.+?)[\s.!?]*$", re.I)


def _genre_ask(t: str) -> str | None:
    """'play some jazz' -> 'jazz'; None when they named a song or artist."""
    from ..abilities.music import genre_of

    m = _GENRE_ASK.search(_TAIL.sub("", t.rstrip(" .!?,")))
    if m and (g := genre_of(m.group("what"), whole=True)):
        return g
    who = _hinted(t) if _WANTS_MUSIC.search(t) else None  # "I'm in the mood for some jazz"
    return genre_of(who, whole=True) if who else None


# "capri clip that", "clip it", "clip the last 30 seconds", "clip that shit", "save that clip", "get a clip of that"
_CLIP = re.compile(r"\bclip+(?:\s+(?:that|this|it|dat|ts|the last|last|me|the|those))\b|\bclip+\s*$|"
                   r"\b(?:save|grab|get|make)\s+(?:a\s+|that\s+|the\s+|me\s+a\s+)?clip\b|\bclip it\b", re.I)
# YouTube the way people type it: "in yt", "on yt", "from youtube", "in you tube"
_YT_AT = re.compile(r"\s+(?:in|on|from|at|via|using|thru|through)\s+(?:the\s+)?(?:yt|ytb|youtube|you\s+tube)\b", re.I)
# "play a game", "let's play X", "wanna play X with me": the thing after "play"
_PLAY_ANY = re.compile(WAKE + POLITE + r"(?:let'?s|lets|wanna|want to|can we|could we|we should|shall we|"
                       r"i (?:wanna|want to)|do you (?:wanna|want to))?\s*play\s+(?P<what>.+?)[\s.!?]*$", re.I)
# games she can play with you right in the chat
_CHAT_GAMES = re.compile(r"^(?:a\s+|some\s+|another\s+|any\s+|one\s+more\s+)?(?:(?:little\s+|quick\s+|fun\s+)?games?$|"
                         r"(?:little\s+|quick\s+|fun\s+)?game\s+(?!on\b|in\b|from\b)|"
                         r"something$|rock\s+paper\s+scissors?|rps|20 questions|twenty questions|trivia|truth or dare|"
                         r"would you rather|wyr|riddles?|tic[\s-]?tac[\s-]?toe|word games?|hangman|never have i ever|"
                         r"(?:a\s+)?quiz|guess(?:ing)?\s+(?:the\s+|a\s+|my\s+)?\w+|i spy|two truths and a lie)\b", re.I)
_PLAY_WITH = re.compile(r"(?:^|\s+)(?:with|w/|w|wit|against|vs)\s+(?:me|you|u|us|ya)\b|\s+together\b", re.I)
_MUSIC_IN = re.compile(r"\b(?:songs?|music|tracks?|albums?|playlists?|beats?|tunes?|radio|mix|lofi|on youtube|by)\b", re.I)


# artists people shorten (searched as "mj" you get anything)
_SHORT = {"mj": "Michael Jackson", "mcr": "My Chemical Romance", "rhcp": "Red Hot Chili Peppers", "tswift": "Taylor Swift",
          "xxx": "XXXTentacion", "nf": "NF"}


def music_name(what: str) -> str:
    """'some mj' -> 'Michael Jackson', 'some mj songs' -> 'Michael Jackson songs': what to search for."""
    what = re.sub(r"^(?:some|a few|a little|a bit of)\s+(?=\S)(?!music$|songs?$|tunes?$)", "", what.strip(), flags=re.I)
    return " ".join(_SHORT.get(w.lower(), w) for w in what.split())


# "close the tab", "close that youtube tab", "close all the tabs you opened", "close the video", "close it"
_CLOSE_TAB = re.compile(
    WAKE + POLITE + r"(?:close|shut|kill|get rid of|exit|remove)\s+(?:out\s+)?(?:of\s+)?"
    r"(?P<all>all\s+(?:of\s+)?(?:the\s+|those\s+|these\s+|your\s+|ur\s+|my\s+)?|every\s+|the\s+|that\s+|this\s+|those\s+|"
    r"these\s+|your\s+|ur\s+|my\s+)?(?P<what>[\w .'&-]{0,40}?)\s*(?:tabs?|videos?|vids?|pages?|youtube|google)\b"
    r"(?P<rest>.*)$", re.I)
_CLOSE_IT = re.compile(WAKE + POLITE + r"(?:close|shut)\s+(?:it|that|this)(?:\s+(?:down|up|now|pls|please))?[\s.!?]*$", re.I)


# "close discord", "quit spotify", "exit valorant", "kill notepad", "shut down steam"
_CLOSE_APP = re.compile(WAKE + POLITE + r"(?:close|quit|exit|kill|shut(?:\s+down)?|turn off|get rid of)\s+(?:out\s+of\s+)?"
                        r"(?:the\s+|my\s+|that\s+|this\s+)?(?P<app>[\w .'+&-]{2,40}?)"
                        r"(?:\s+(?:app|application|program|game|client|launcher|window))?(?:\s+(?:for me|pls|please|now|rn))*"
                        r"[\s.!?]*$", re.I)
_NOT_AN_APP = re.compile(r"^(?:it|that|this|them|yourself|urself|you|capri|campy|your \w+|ur \w+|my \w+|the door|"
                         r"the (?:pc|computer|laptop)|pc|computer|laptop|everything|all|music|the music|song|the song|"
                         r"down|up|eyes|mouth)$", re.I)


def close_app_ask(t: str) -> Intent | None:
    """'close discord' -> Intent("closeapp", "discord"), when that's an app: running now, or one she knows."""
    m = _CLOSE_APP.search(t)
    if not m:
        return None
    app = m.group("app").strip(" .,'")
    if _NOT_AN_APP.match(app):
        return None
    from ..abilities import launcher
    from ..abilities.vision import APP_NAMES

    known = {launcher._app_key(v) for v in APP_NAMES.values() if v} | set(APP_NAMES) | {
        launcher._app_key(a) for a in launcher.APPS} | {launcher._app_key(g) for g in launcher.KNOWN_GAMES}
    if launcher._app_key(app) in known or launcher.find_app_windows(app):
        return Intent("closeapp", app)
    return None


def close_tab_ask(t: str) -> Intent | None:
    """-> Intent("closetab", "" | "all" | words of the tab) for an ask to close browser tabs."""
    if m := _CLOSE_TAB.search(t):
        site = re.search(r"\b(youtube|google)\b", m.group(0), re.I)
        if (m.group("all") or "").lower().startswith(("all", "every")) or re.search(
                r"\b(?:you|u) (?:opened|open|put on|made)\b", m.group("rest") or "", re.I):
            return Intent("closetab", "all")
        what = (m.group("what") or "").strip(" .,'")
        what = music_name(re.sub(r"^(?:the|that|this|my|your|ur)\s+", "", what, flags=re.I))  # "mj" -> Michael Jackson
        return Intent("closetab", " ".join(x for x in (what, site.group(1) if site else "") if x).strip())
    if _CLOSE_IT.search(t):
        return Intent("closetab", "it")  # only a tab she just opened: "close it" could mean anything else
    return None


def game_ask(t: str) -> Intent | None:
    """'play a game with me' / 'let's play 20 questions' -> a game right in the chat; 'play valorant' (a game on this
    PC, or a well-known one) -> open it. Not a song search for "a game with me" on Spotify."""
    m = _PLAY_ANY.search(_TAIL.sub("", t.rstrip(" .!?,")))
    if not m or re.match(r"^\W*(?:do|did|does|have|has|what|whats|what's|who|when|why|how|which|is|are)\b"
                         r"(?!.*\b(?:wanna|want to)\b)", t, re.I):
        return None  # "do you play valorant?" is a question, not "start it"
    what = m.group("what").strip(" .!?,")
    bare = _PLAY_WITH.sub("", what).strip(" .!?,")
    if _MUSIC_IN.search(what):
        return None
    if not bare:  # "play with me"
        return Intent("chatgame", "a game") if _PLAY_WITH.search(what) else None
    if _CHAT_GAMES.match(bare) and not re.search(r"\bon (?:steam|epic)\b", bare, re.I):
        return Intent("chatgame", bare)
    from ..abilities.launcher import game_named

    if game_named(re.sub(r"^(?:the|some|a)\s+", "", bare, flags=re.I)):
        return Intent("open", re.sub(r"^(?:the|some|a)\s+", "", bare, flags=re.I))
    if _PLAY_WITH.search(what):  # "play minecraft with me" was a game above; "play X with me": still not a song
        return Intent("chatgame", bare)
    return None


def detect(text: str) -> Intent | None:
    """Clear-cut asks only. Returns None for anything vague so plan() can think it through."""
    t = _YT_AT.sub(" on youtube", text.strip())  # "play some mj in yt" -> "... on youtube"
    if _BYE.match(t):
        return Intent("idle", "bye")
    if _CLIP.search(t):  # "capri clip that", "clip the last minute"
        from ..abilities.clips import wanted_seconds

        return Intent("clip", str(wanted_seconds(t) or ""))
    if (u := update_intent(t)) and not re.search(r"\b(?:remind me|timer|alarm)\b", t, re.I):
        return u
    from ..abilities import reminders

    if re.search(r"\b(?:timers?|alarms?|remind me|reminders?|wake me)\b", t, re.I) and (r := reminders.parse(t)):
        import json

        return Intent("reminder", json.dumps({**r, "due": r["due"].timestamp() if "due" in r else 0}))
    # "play some jazz", "ponme reggaeton": a genre, not a song. She picks a real track she rates
    if not re.search(r"\bspotify\b", t, re.I) and (g := _genre_ask(t)):
        return Intent("pick", g)
    from . import daily

    if d := daily.detect(t):  # privacy, focus, weather, clipboard, lists, PC controls
        return d
    if _TIME.search(t):
        return Intent("time")
    if m := _NEWS.search(t):
        return Intent("news", _TAIL.sub("", m.group("topic") or "").strip(" .!?,\"'"))
    if (m := _REMEMBER.search(t)) and not _RECALL_ASK.search(t):
        return Intent("remember", m.group(1).strip(" .!?"))
    for action, rx in _CTRL:  # pause / skip / next first: "skip this song" is clear, not vague
        if rx.search(t) and re.search(r"\b(music|song|track|spotify|video|skip|next|pause|resume|unpause|previous)\b", t, re.I):
            return Intent("spotify", action)
    if _ASKS_SONG.search(t):
        return None  # answered from what's playing, nothing gets changed
    on_spotify = bool(re.search(r"\bspotify\b", t, re.I))
    if not on_spotify and (g := game_ask(t)):  # "play a game with me", "play valorant": a game, not a song
        return g
    if c := close_tab_ask(t):  # "close the youtube tab", "close all the tabs you opened"
        return c
    if c := close_app_ask(t):  # "close discord", "quit valorant"
        return c
    if (m := _TAB_SEARCH.search(t)) and (hit := _tab_search(m)):  # before "open": "open a new tab and look for X"
        return hit
    # "play me something like Ado (on Spotify)"; "looking for X" alone is a job hunt, not a song, without a music word
    if _WANTS_MUSIC.search(t) and _MUSIC_WORD.search(t) and (who := _hinted(t)):
        return Intent("play", ("spotify|" if on_spotify else "") + who)
    if m := _LISTEN.search(_TAIL.sub("", t.rstrip(" .!?,"))):
        what = re.sub(r"^(?:some|a little|a bit of)\s+", "", _TAIL.sub("", m.group("what")).strip(" .!?,\"'"), flags=re.I)
        if what and not _NOT_A_NAME.match(what) and not re.match(r"^(?:you|me|this|that|it|music|something)\b", what, re.I):
            return Intent("play", ("spotify|" if m.group("sp") or on_spotify else "") + what)
    if m := _SPOTIFY.search(_TAIL.sub("", t.rstrip(" .!?,"))):
        what = _TAIL.sub("", m.group("a") or m.group("b")).strip(" .!?,\"'")
        what = re.sub(r"^some\s+(?=\S)(?!music$|songs?$|tunes?$)", "", what, flags=re.I)  # "play some Ado on spotify"
        if _THIS_SONG.match(what):
            return Intent("play", "spotify|@now")
        if what.lower() in ("", "music", "something", "some music", "a song", "anything", "some songs", "a banger",
                            "something good", "your favourite", "your favorite", "ur fav"):
            return Intent("play", "spotify|@pick")
        return Intent("play", "spotify|" + music_name(what))
    if _PICK.search(t) and not re.search(r"\b(?:called|named|by)\b", t, re.I):
        return Intent("play", "spotify|@pick") if on_spotify else Intent("pick")
    if _VAGUE.search(t) and not re.search(r"\b(?:spotify|on youtube)\b", t, re.I):
        return None
    if m := _YT.search(t):
        return Intent("youtube", music_name(_TAIL.sub("", (m.group(1) or m.group(2))).strip(" .!?,")))
    if m := _GAME_AT.search(t):
        return Intent("open", _TAIL.sub("", m.group("what")).strip(" .!?"))
    if (m := _OPEN_INTL.search(t)) and len(m.group("what").split()) <= 6:
        what = re.sub(r"[\s,]+(?:por favor|bitte|s'il te plaît|stp|per favore|proszę|пожалуйста|please|pls|ya|jetzt|"
                      r"ahora|maintenant|adesso|teraz|сейчас)\s*$", "", m.group("what").strip(" .!?"), flags=re.I)
        return Intent("open", _TAIL.sub("", what).strip(" .!?"))
    if m := _OPEN.search(t):
        what = m.group(2)
        if pm := re.search(r"\band (?:play|put on)\s+(.+)", what, re.I):
            return Intent("play", pm.group(1).strip(" .!?"))
        target = re.split(r"\s+(?:and|for me|please|real quick|rn|now)\b", what, maxsplit=1, flags=re.I)[0].strip(" .!?")
        loose_verb = m.group("verb").lower().split()[0] in ("start", "run", "boot", "fire")
        from ..abilities.launcher import APPS

        known = target.lower().removeprefix("the ").removesuffix(" app") in APPS
        # "start talking", "run away": only treat loose verbs as launches for apps we know
        if len(target.split()) <= 4 and (known or not loose_verb):
            return Intent("open", target)
    if m := _PLAY.search(t):
        what = _TAIL.sub("", m.group(1)).strip(" .!?,")
        what = re.sub(r"^(?:that|the) song (?:that )?(?:is |goes |that goes )?(?:like\s+)?", "", what, flags=re.I).strip(" ,")
        what = re.sub(r"^some\s+(?=\S)(?!music$|songs?$|tunes?$)", "", what, flags=re.I)  # "play some Ado"
        if what.lower() in ("", "music", "something", "some music", "a song", "anything"):
            return Intent("pick")
        if what.lower() not in ("it", "that"):
            return Intent("play", ("spotify|" if m.group("sp") else "") + music_name(what))
        return Intent("spotify", "resume")
    for action, rx in _CTRL:
        if rx.search(t) and re.search(r"\b(music|song|track|spotify|video|skip|next|pause|resume|unpause|previous)\b", t, re.I):
            return Intent("spotify", action)
    if _MINI.search(t) and not re.match(r"^\W*(?:what|how|why|is|does|qu[eé]|was|wie|che|co|что|как)\b", t, re.I):
        return Intent("idle", "mini")
    if _LAY.search(t):
        return Intent("idle", "lay")
    if _IDLE.search(t):
        return Intent("idle")
    if g := genre_request(t):  # "i need uhh some jazz ig?", "no play some jazz like for me"
        return Intent("pick", g)
    return None


# loose music asks, the way people actually type them: fillers, typos, no "play" at the start
_UMS = re.compile(r"\b(?:u+h+m*|u+m+|e+r+m*|hm+|ig|i guess|idk|lol|lmao|bro|pls|plz|please|ya know|you know|kinda|maybe|"
                  r"like|for (?:me|,e|em|mw|ne))\b|[,?!.]+", re.I)
_MUSIC_VERB = re.compile(r"\b(?:play|put on|put|throw on|blast|spin|queue|listen|hear|music|songs?|tunes?|tracks?|vibes?|"
                         r"beats?|playlist)\b", re.I)
_WANT = re.compile(r"\b(?:need|want|wanna|gimme|give me|i'?d like|feel(?:ing)? like|craving|in the mood for|some|"
                   r"how about|what about|let'?s)\b", re.I)
# genre words that can't mean anything else ("rock", "chill", "house", "soul" can, so those need a music word too)
_PLAIN_GENRE = re.compile(r"\b(?:jazz\w*|bebop|lo-?fi|chillhop|classical|synthwave|retrowave|phonk|k-?pop|hip[- ]?hop|edm|"
                          r"techno|reggaet[oó]n|rnb|r ?& ?b|bachata|salsa|cumbia|dnb|j-?pop|metalcore)\b", re.I)
_REFUSE = re.compile(r"\b(?:don'?t|do not|never|stop|hate|no more|not in the mood)\b", re.I)


def genre_request(t: str) -> str | None:
    """A genre asked for loosely: 'i need uhh some jazz ig?' -> 'jazz'. None for 'I hate jazz' or 'I hit a rock'."""
    from ..abilities.music import genre_of

    if len(t.split()) > 14 or _REFUSE.search(t):
        return None
    g = genre_of(t)
    if not g:
        return None
    if _MUSIC_VERB.search(t) or (_PLAIN_GENRE.search(t) and _WANT.search(t)):
        return g
    return None


def genre_offer(t: str) -> str | None:
    """Just a genre named ('jazz?', 'hmm lofi'): she asks if they want her to put it on."""
    from ..abilities.music import genre_of

    rest = _UMS.sub(" ", t).strip()
    if len(rest.split()) > 3 or _REFUSE.search(t):
        return None
    return genre_of(rest, whole=True) if _PLAIN_GENRE.search(rest) else None


def detect_task(text: str) -> Intent | None:
    """Jobs on the PC and the net: clear asks only (vague ones go to the planner)."""
    t = text.strip()
    if _EMAIL.search(t):
        return Intent("email", t)
    if _NOTE.search(t):
        return Intent("note", t)
    if (m := _URL.search(t)) and re.search(r"\b(?:read|summar|check|look at|open|what'?s (?:on|in))", t, re.I):
        return Intent("read", m.group(0).rstrip(").,!?"))
    if (m := _TAB_SEARCH.search(t)) and (hit := _tab_search(m)):
        return hit
    if m := (_SEARCH.search(t) or _SEARCH_UP.search(t)):
        q = _TAIL.sub("", m.group("q")).strip(" .!?,\"'")
        q = re.sub(r"^(?:for|about|on)\s+", "", q, flags=re.I)
        if len(q) >= 3:
            return Intent("search", "web|" + q)
    return None


# -- planner: the "think before acting" step for vague asks -------------------------------------
PLANNER = """You are the brain of Capri, a desktop companion on {user}'s PC. Decide what to DO about their
latest message.

{user} says: "{text}"

Actions:
PLAY <song or artist>          play a specific song or artist they named (or quoted lyrics from)
PICK                           they want music but didn't name a real song: you choose
SPOTIFY next|previous|pause|resume
OPEN <app or website>
SEARCH <search query>          look it up on the web: prices, weather, scores, guides, anything current or
                               anything you're not sure of
NEWS <topic, or blank>         today's headlines (on a topic if they named one)
NOTE <what goes in the file>   write a text file for them (a list, notes, a plan)
EMAIL <who> | <what about>     draft an email in their mail app (they send it)
{tools}NONE                           just talk, no action (the default: small talk, questions about you, feelings)
Only choose an action if THIS message asks for one, and only PLAY a real song or artist name.

Format exactly:
ACTION: <one action line>"""

_ACTION = re.compile(r"ACTION:\s*([A-Z_]+)\s*(.*)", re.I)
_FILLER_TARGET = re.compile(
    r"^(?:something|anything|some(?:thing)? else|another(?: one| song| track)?|a song|a different (?:one|song)|"
    r"music|some music|it|that|this|the (?:next|other) one|whatever|random)$",
    re.I,
)


def parse_plan(out: str) -> Intent | None:
    m = _ACTION.search(out) or re.search(r"^\s*([A-Z_]{3,})\s*(.*)$", out.strip(), re.M)
    if not m:
        return None
    verb, arg = m.group(1).upper(), m.group(2)
    if verb not in ("TOOL", "EMAIL", "NOTE"):
        arg = re.sub(r"\s*\(.*$|\s+\bif\b.*$|\s+on (?:spotify|youtube)\b.*$", "", arg, flags=re.I)
    arg = arg.strip().strip("<>\"' .")
    if verb == "PICK" or (verb == "PLAY" and arg and _FILLER_TARGET.match(arg)):
        return Intent("pick")
    if verb == "PLAY" and arg:
        return Intent("play", arg)
    if verb == "OPEN" and arg:
        return Intent("open", "planned|" + arg)  # planner guesses never turn into surprise Google searches
    if verb == "SPOTIFY" and arg.lower() in ("next", "previous", "pause", "resume"):
        return Intent("spotify", arg.lower())
    if verb == "SEARCH" and len(arg) >= 3:
        return Intent("search", arg)
    if verb == "NEWS":
        return Intent("news", "" if arg.lower() in ("blank", "none", "") else arg)
    if verb == "NOTE" and arg:
        return Intent("note", arg)
    if verb == "EMAIL" and arg:
        return Intent("email", arg)
    if verb == "TOOL" and arg:
        return Intent("tool", m.group(2).strip())  # "name {json}": keep the braces and quotes
    return None


_ACTIONISH = re.compile(
    r"\b(?:play|put on|turn on|queue|start|change|switch|skip|next|another|something else|different|youtube|spotify|"
    r"song|music|video|boring|open|launch|search|google|look up|look into|find out|news|weather|price|prices|cost|"
    r"how much|latest|today|tonight|score|who won|release date|recommend|best|worth it|buy|any good|should i get|"
    r"write|note|notes|txt|file|"
    r"list|email|e-mail|mail|draft|remind|reminder|calendar|schedule|check|send|create|read|use your|tool|"
    r"calculat\w*|save)\b",
    re.I,
)
# what the user's own words must contain before a planner guess may DO something on the PC
_ASKS = {
    "music": re.compile(r"\b(?:play|put on|queue|throw on|blast|song|songs|music|track|spotify|youtube|skip|next|listen|"
                        r"change (?:it|this|that|the song)|switch (?:it|this|songs?)|another|something else|boring|"
                        r"different (?:song|one|track)|pause|resume)\b", re.I),
    "open": re.compile(r"\b(?:open|launch|start|pull up|fire up|go to|bring up|load)\b", re.I),
    # looking things up changes nothing on the PC: any question or info ask will do
    "search": re.compile(r"\?|\b(?:search|google|look (?:up|into|for)|look it up|find|check|what|who|when|where|how|"
                         r"which|why|is there|are there|any|news|weather|price|cost|recommend|best|latest|tell me|"
                         r"browser|tab|details|info)\b", re.I),
    "note": re.compile(r"\b(?:write|make|create|save|jot|note|notes|txt|text file|file|list|doc|document)\b", re.I),
    "email": re.compile(r"\b(?:e-?mail|mail)\b", re.I),
}
_KIND_ASK = {"play": "music", "pick": "music", "spotify": "music", "youtube": "music", "open": "open",
             "search": "search", "read": "search", "news": "search", "note": "note", "email": "email"}
# "we're NOT listening to anything", "don't play that": the action word is there, but they're saying no to it
_NEGATED = re.compile(r"(?:\b(?:not|no|never|stop|quit)|n't)\s+(?:\w+\s+){0,2}?(?:play|playing|listen|listening|"
                      r"open|opening|music|songs?)\b", re.I)


_QUESTION_ONLY = re.compile(r"^\W*(?:what|which|who|when|where|how|why|are|were|is|do|did|does|have|wait)\b[^.!]*\??\W*$", re.I)
_ASK_TO_PLAY = re.compile(r"\b(?:can|could|would|will|why (?:don'?t|not)) you (?:play|put on|queue)\b|\b(?:play|put on) (?:me |us )?(?:some|a|the)\b", re.I)


def allowed(intent: Intent | None, text: str) -> Intent | None:
    """A planner guess only becomes an action if the user actually asked for that kind of thing."""
    if intent is None:
        return None
    if _NEGATED.search(re.sub(r"\b(?:why do|can|could|would|wo)(?:n'?t| not) you\b", "", text, flags=re.I)):
        return None
    need = _KIND_ASK.get(intent.kind)
    if need and not _ASKS[need].search(text):
        print(f"[capri] planner wanted {intent.kind} {intent.arg!r} but they didn't ask for it; just chatting")
        return None
    if intent.kind in ("play", "youtube", "pick") and _QUESTION_ONLY.match(text) and not _ASK_TO_PLAY.search(text):
        return None  # "what game do I play?" is a question, not a request
    if intent.kind == "search" and not intent.arg.startswith("web|") and _IN_BROWSER.search(text):
        intent = Intent("search", "web|" + intent.arg.replace('"', "").strip(" '"))  # they want to see it: in their browser
    if intent.kind in ("play", "youtube") and intent.arg.lower().strip(" ?!.") in text.lower().strip(" ?!."):
        if len(intent.arg.split()) >= len(text.split()) - 1:
            return None  # the planner echoed their whole sentence back as a "song"
    return intent


# jobs on the PC itself (files, folders, programs, settings): with Claude Code as her brain, these go to its hands
_PC_JOB = re.compile(
    r"\b(?:move|rename|copy|delete|remove|clean ?up|tidy|organi[sz]e|folders?|files?|downloads|desktop|"
    r"documents|pictures|install|uninstall|zip|unzip|extract|compress|convert|resize|backup|back up|script|"
    r"command|terminal|powershell|cmd|disk|storage|disk space|biggest|largest|duplicates?|process(?:es)?|"
    r"task manager|startup apps?|drivers?|wifi|wi-fi|ip address|battery|ram|cpu|gpu)\b",
    re.I,
)


def pc_job(text: str) -> bool:
    """A request to do something on their PC (not small talk, not a question about her)."""
    t = re.sub(r"\b(?:why do|can|could|would|wo)(?:n'?t| not) you\b", "", text, flags=re.I)
    return bool(_PC_JOB.search(t)) and not re.search(r"\b(?:don'?t|do not|never|stop)\b", t, re.I)


def wants_action(text: str) -> bool:
    """Small talk never reaches the planner, so it can't decide to do things on a whim. Neither does a plain question
    ("what game do i play every night?"): on Claude Code the planner is a whole extra run, 10+ seconds for nothing."""
    return bool(_ACTIONISH.search(text)) and not _JUST_ASKING.search(text)


# a question, not a job: "what game do i play", "who do i play with", "when is my exam" (unless it asks her to do it)
_JUST_ASKING = re.compile(r"(?:^|[,.;!?]\s+)\W*(?:so\s+|ok\s+|and\s+|but\s+)?(?:what|whats|what's|who|whos|who's|when|where|why|"
                          r"which|do i|did i|am i|was i|do you|did you|are you)\b"
                          r"(?!.*\b(?:can you|could you|would you|will you|pls|please|for me)\b)", re.I)


def plan(ask, text: str, user: str, tools: str = "") -> Intent | None:
    """ask(prompt, **kw) -> reply. The agent runs it on her cached prompt, so planning costs ~0.2 s, not a re-read.
    tools: MCP tool lines ("TOOL name {args}  what it does"), offered as extra actions."""
    extra = (tools + "\n" + "TOOL <name> <JSON arguments>   use one of the tools above\n") if tools else ""
    out = ask(PLANNER.format(user=user, text=text, tools=extra)
              + "\n(This is a hidden system step: answer only with the ACTION line.)",
              temperature=0.0, max_tokens=60 if tools else 32)
    print(f"[capri] plan: {out.strip()!r}")
    return parse_plan(out)
