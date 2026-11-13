"""Music: her Cyberpunk playlist for DJ mode, and an ear on your speakers so she can vibe to the beat.

The beat ear listens to what the PC is playing (WASAPI loopback through the `soundcard` package), finds kicks
in the low end, and exposes `pulse` (jumps to 1 on a beat, then decays) and `playing` (music has been going a
couple of seconds)."""
from __future__ import annotations

import random
import re
import threading
import time
import warnings

import numpy as np

# Cyberpunk 2077 and Edgerunners: what she reaches for when your music isn't nova enough
OSTS = [
    "I Really Want to Stay at Your House - Rosa Walton & Hallie Coggins",
    "This Fffire - Franz Ferdinand (Cyberpunk Edgerunners opening)",
    "Let You Down - Dawid Podsiadlo (Cyberpunk Edgerunners)",
    "Never Fade Away - SAMURAI (Olga Jankowska)",
    "Chippin' In - SAMURAI",
    "Black Dog - SAMURAI",
    "The Rebel Path - P.T. Adamczyk Cyberpunk 2077",
    "Resist and Disorder - Rezodrone Cyberpunk 2077",
    "Major Crimes - HEALTH Cyberpunk 2077",
    "Night City - R E L & Artemis Delta",
    "Delicate Weapon - Lizzy Wizzy Cyberpunk 2077",
    "PonPon Shit - Us Cracks Cyberpunk 2077",
    "Who's Ready for Tomorrow - Rat Boy & IBDY",
]

# a YouTube title that's already Night City music: she approves instead of switching it
CYBER = re.compile(r"cyberpunk|edgerunners|samurai|night city|rosa walton|p\.?\s*t\.?\s*adamczyk|rezodrone|lizzy wizzy|"
                   r"franz ferdinand.*fffire|dawid podsiad|health.*major crimes|us cracks|rat boy|kavinsky|perturbator|"
                   r"carpenter brut|synthwave|retrowave|darksynth", re.I)


# "play some jazz": she picks a real track she rates instead of YouTube's top hit for the bare word.
# genre -> (the words people use for it, in her languages, her picks)
GENRES: dict[str, tuple[str, list[str]]] = {
    "jazz": (r"jazz(?:y)?|smooth jazz|bebop", [
        "Dave Brubeck - Take Five", "Miles Davis - So What", "John Coltrane - My Favorite Things",
        "Chet Baker - Almost Blue", "Herbie Hancock - Cantaloupe Island", "Bill Evans Trio - Waltz for Debby",
        "Thelonious Monk - 'Round Midnight", "Ella Fitzgerald & Louis Armstrong - Dream a Little Dream of Me",
        "Duke Ellington - Take the A Train", "Cannonball Adderley - Autumn Leaves"]),
    "lofi": (r"lo-?fi|chillhop|study (?:music|beats)|beats to (?:study|relax)(?: to)?|música para estudiar", [
        "Nujabes - Aruarian Dance", "Nujabes - Feather", "Jinsang - Solitude", "Idealism - Controlla",
        "tomppabeats - Monday Loop", "Kupla - Kingdom in Blue", "lofi hip hop radio beats to relax/study to"]),
    "chill": (r"chill(?:out)?|relaxing|calm|tranquil[ao]?|relajante|calme|entspannt\w*", [
        "Tycho - Awake", "Bonobo - Kerala", "Khruangbin - Maria También", "Men I Trust - Show Me How",
        "Mac DeMarco - Chamber of Reflection", "Boards of Canada - Roygbiv", "Washed Out - Feel It All Around"]),
    "classical": (r"classical|cl[aá]sica|classique|classica|klassik|klasyczn\w*|классик\w*|piano music", [
        "Debussy - Clair de Lune", "Beethoven - Moonlight Sonata 3rd movement", "Vivaldi - Winter (Four Seasons)",
        "Chopin - Nocturne Op. 9 No. 2", "Bach - Cello Suite No. 1 Prelude", "Satie - Gymnopédie No. 1",
        "Tchaikovsky - Waltz of the Flowers"]),
    "rock": (r"(?:classic |hard |alt(?:ernative)? |indie )?rock(?:\s*(?:and|n|'n'|&)\s*roll)?", [
        "Queen - Bohemian Rhapsody", "AC/DC - Thunderstruck", "Arctic Monkeys - Do I Wanna Know?",
        "The White Stripes - Seven Nation Army", "Led Zeppelin - Whole Lotta Love", "Nirvana - Smells Like Teen Spirit",
        "Muse - Uprising"]),
    "metal": (r"metal|heavy metal|metalcore", [
        "Metallica - Master of Puppets", "Slipknot - Duality", "System of a Down - Chop Suey!",
        "Gojira - Amazonia", "Bring Me The Horizon - Can You Feel My Heart", "Iron Maiden - The Trooper"]),
    "hiphop": (r"hip[- ]?hop|rap|trap|boom ?bap", [
        "Kendrick Lamar - HUMBLE.", "Nas - N.Y. State of Mind", "OutKast - B.O.B.", "Travis Scott - SICKO MODE",
        "A Tribe Called Quest - Electric Relaxation", "Kanye West - Power", "Run The Jewels - Legend Has It"]),
    "electronic": (r"edm|electronic|electr[oó]nica|électro|elettronica|elektro\w*|house|techno|drum (?:and|n|&) bass|dnb", [
        "Daft Punk - Around the World", "Justice - Genesis", "Deadmau5 - Strobe", "Fred again.. - Delilah",
        "The Prodigy - Breathe", "Pendulum - Watercolour", "Disclosure - Latch"]),
    "synthwave": (r"synthwave|retrowave|outrun|darksynth|80s synth", [
        "Kavinsky - Nightcall", "Perturbator - Future Club", "Carpenter Brut - Turbo Killer", "The Midnight - Sunset",
        "FM-84 - Running in the Night", "Gunship - Tech Noir", "Timecop1983 - On the Run"]),
    "phonk": (r"phonk|drift phonk", [
        "Kordhell - Murder in My Mind", "PlayaPhonk - PHONKY TOWN", "DVRST - Close Eyes", "INTERWORLD - METAMORPHOSIS",
        "Ghostface Playa - Why Not"]),
    "pop": (r"pop(?! ?punk)|top hits|bangers", [
        "The Weeknd - Blinding Lights", "Dua Lipa - Levitating", "Harry Styles - As It Was", "Billie Eilish - bad guy",
        "Doja Cat - Say So", "Sabrina Carpenter - Espresso"]),
    "kpop": (r"k-?pop", [
        "BTS - Dynamite", "BLACKPINK - How You Like That", "Stray Kids - S-Class", "NewJeans - Super Shy",
        "aespa - Supernova", "TWICE - Fancy"]),
    "anime": (r"anime|j-?pop|j-?rock|city ?pop", [
        "Ado - Usseewa", "YOASOBI - Idol", "LiSA - Gurenge", "Kenshi Yonezu - KICK BACK", "Mariya Takeuchi - Plastic Love",
        "Miki Matsubara - Stay With Me"]),
    "rnb": (r"r ?(?:&|n|and) ?b|rnb|soul|neo-?soul", [
        "Frank Ocean - Pink + White", "SZA - Good Days", "D'Angelo - Untitled (How Does It Feel)",
        "Daniel Caesar - Get You", "Marvin Gaye - What's Going On", "Erykah Badu - On & On"]),
    "latin": (r"reggaet[oó]n|latin[ao]?|salsa|bachata|cumbia|perreo", [
        "Bad Bunny - Tití Me Preguntó", "Daddy Yankee - Gasolina", "Rosalía - DESPECHÁ", "Karol G - PROVENZA",
        "Héctor Lavoe - El Cantante", "Romeo Santos - Propuesta Indecente"]),
    "blues": (r"blues", [
        "B.B. King - The Thrill Is Gone", "Stevie Ray Vaughan - Pride and Joy", "Muddy Waters - Mannish Boy",
        "Gary Clark Jr. - Bright Lights", "John Lee Hooker - Boom Boom"]),
    "funk": (r"funk(?:y)?|disco", [
        "Earth, Wind & Fire - September", "Chic - Le Freak", "Vulfpeck - Dean Town", "Parliament - Give Up the Funk",
        "Bruno Mars - Uptown Funk"]),
    "ambient": (r"ambient|sleep(?:ing)? music|music to sleep|focus music|música para dormir", [
        "Brian Eno - An Ending (Ascent)", "Aphex Twin - #3 (Selected Ambient Works II)", "Hammock - Turn Away",
        "Marconi Union - Weightless", "Stars of the Lid - Requiem for Dying Mothers"]),
}
_GENRE_RX = {g: re.compile(rf"\b(?:{words})\b", re.I) for g, (words, _) in GENRES.items()}


_FILL = re.compile(r"\b(?:some|a little|a bit of|any|good|nice|real|old|new|chill|smooth|relaxing|music|songs?|tracks?|tunes?|"
                   r"stuff|vibes?|me|us|for|the|a|de|del|la|el|algo|un poco|música|musica|musique|musik|muzyk\w*|"
                   r"музык\w*|please|pls|plz|rn|now)\b|[\s,.!?¡¿'\"]+", re.I)


def genre_of(text: str, whole: bool = False) -> str | None:
    """'play some jazz' -> 'jazz' (None if no genre is named). whole: only when the genre is all they asked for
    ('jazz', 'some lofi music'), so 'House of the Rising Sun' stays a song."""
    for g, rx in _GENRE_RX.items():
        m = rx.search(text)
        if m and (not whole or not _FILL.sub("", text[:m.start()] + " " + text[m.end():]).strip()):
            return g
    return None


class Playlist:
    """Hands out OSTs (or a genre's picks) without repeating until it has gone through them all."""

    def __init__(self):
        self.left: dict[str, list[str]] = {}

    def next(self, genre: str | None = None) -> str:
        pool = GENRES[genre][1] if genre in GENRES else OSTS
        key = genre if genre in GENRES else ""
        if not self.left.get(key):
            self.left[key] = random.sample(pool, len(pool))
        return self.left[key].pop()


# The loopback recording runs in its own little Python process: `soundcard` can corrupt the heap on some Pythons
# (3.12 from the Microsoft Store does it within seconds), and a crash over there must never take her down with it.
# It prints one "rms low" line per block of what your speakers play.
_EAR = r"""
import sys, warnings
if sys.prefix == sys.base_prefix:  # run.bat's Python: her extra packages live in .deps (a venv has its own)
    sys.path[:0] = [sys.argv[1] + '/.deps']
import numpy as np
warnings.filterwarnings('ignore')
win = np.hanning(1024)

def out(x):
    spec = np.abs(np.fft.rfft(x * win))
    print('%.6f %.5f' % (float(np.sqrt(np.mean(x ** 2))), float(spec[1:8].sum())), flush=True)

try:  # PyAudioWPatch: WASAPI loopback that holds up on every Python
    import pyaudiowpatch as pa
except ImportError:
    pa = None
if pa is not None:
    p = pa.PyAudio()
    lb = p.get_default_wasapi_loopback()
    ch = lb['maxInputChannels']
    st = p.open(format=pa.paFloat32, channels=ch, rate=int(lb['defaultSampleRate']), input=True,
                input_device_index=lb['index'], frames_per_buffer=1024)
    while True:
        out(np.frombuffer(st.read(1024, exception_on_overflow=False), dtype=np.float32).reshape(-1, ch).mean(axis=1))
import soundcard as sc
spk = sc.default_speaker()
mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
with mic.recorder(samplerate=44100, channels=1, blocksize=1024) as rec:
    while True:
        out(rec.record(numframes=1024)[:, 0])
"""


class BeatEar(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.pulse = 0.0  # 0..1, 1 right on a beat
        self.level = 0.0  # smoothed loudness
        self.playing = False
        self.beats: list[float] = []  # recent beat times
        self.stopped = False
        self.ok = False
        self._loud_since = None
        self._quiet_since = time.monotonic()

    @property
    def rhythmic(self) -> bool:
        """A steady beat (music), not one-off bangs from a game or a video."""
        now = time.monotonic()
        recent = [b for b in self.beats if now - b < 4.0]
        if len(recent) < 6:
            return False
        gaps = np.diff(recent)
        return float(np.std(gaps)) < 0.5 * float(np.mean(gaps))

    def _blocks(self):
        """(rms, low) per block from the helper process; restarts it if it dies (crash, device change)."""
        import subprocess
        import sys

        from ..config import ROOT
        from ..portable import spawn

        if getattr(sys, "frozen", False):  # the exe can't run a script: listen in here instead
            yield from self._blocks_here()
            return
        py = sys.executable.replace("pythonw.exe", "python.exe")
        fails = 0
        while not self.stopped and fails < 5:
            proc = spawn([py, "-c", _EAR, str(ROOT)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            got = False
            for line in proc.stdout:
                if self.stopped:
                    break
                try:
                    rms, low = map(float, line.split())
                except ValueError:
                    continue
                got = True
                yield rms, low
            proc.kill()
            fails = 0 if got else fails + 1
            if not self.stopped:
                print("[capri] beat ear restarting")
                time.sleep(3)
        if fails >= 5:
            print("[capri] beat ear unavailable (couldn't listen to the speakers)")

    def _blocks_here(self):
        win = np.hanning(1024)
        try:  # PyAudioWPatch first: soundcard can corrupt the heap, and in here that would take her down too
            import pyaudiowpatch as pa

            p = pa.PyAudio()
            lb = p.get_default_wasapi_loopback()
            ch = lb["maxInputChannels"]
            st = p.open(format=pa.paFloat32, channels=ch, rate=int(lb["defaultSampleRate"]), input=True,
                        input_device_index=lb["index"], frames_per_buffer=1024)
            while not self.stopped:
                x = np.frombuffer(st.read(1024, exception_on_overflow=False), dtype=np.float32).reshape(-1, ch).mean(1)
                yield float(np.sqrt(np.mean(x ** 2))), float(np.abs(np.fft.rfft(x * win))[1:8].sum())
            return
        except ImportError:
            pass
        import soundcard as sc

        warnings.filterwarnings("ignore", message="data discontinuity")
        spk = sc.default_speaker()
        mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
        with mic.recorder(samplerate=44100, channels=1, blocksize=1024) as rec:
            while not self.stopped:
                x = rec.record(numframes=1024)[:, 0]
                spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
                yield float(np.sqrt(np.mean(x ** 2))), float(spec[1:8].sum())

    def run(self):
        avg = 1e-4
        last_beat = 0.0
        self.ok = True
        for rms, low in self._blocks():  # low: ~40-300 Hz, kicks and bass
            now = time.monotonic()
            self.level = self.level * 0.9 + rms * 0.1
            avg = avg * 0.96 + low * 0.04
            if low > avg * 1.6 and low > 0.6 and now - last_beat > 0.22:
                self.pulse, last_beat = 1.0, now
                self.beats = [b for b in self.beats if now - b < 4.0] + [now]
            else:
                self.pulse *= 0.86
            if rms > 0.01:
                self._loud_since = self._loud_since or now
                self._quiet_since = None
            else:
                self._quiet_since = self._quiet_since or now
                if now - self._quiet_since > 1.5:
                    self._loud_since = None
            self.playing = bool(self._loud_since and now - self._loud_since > 2.0)
