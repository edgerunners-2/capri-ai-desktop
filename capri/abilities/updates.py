"""Her own updates: compares her version with the newest release on GitHub, hands the release notes to her brain to
sum up (in whatever language you're talking), downloads the new Capri-Setup.exe when you say so, and runs it."""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from ..config import DATA, FROZEN

REPO = "Capri-cmd/capri-ai"  # exact case: the GitHub API is case-sensitive and 404s on "Capri-AI"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
FOLDER = DATA / "updates"


@dataclass
class Release:
    version: str  # "1.0.1" (no leading v)
    name: str
    notes: str  # the release description, markdown trimmed down
    url: str  # its page on GitHub
    published: str  # "2026-11-01"
    asset: str = ""  # download link of the installer ("" = none attached)
    asset_name: str = ""
    size: int = 0
    asset_time: float = 0.0  # when its installer was uploaded (unix time, 0 = unknown)
    extra: dict = field(default_factory=dict)


def parse(v: str) -> tuple[int, ...]:
    """'v1.0.1' / '1.0.1-beta' / 'Capri v0.5.7' / 'release-0.5.7' -> (1, 0, 1) / (0, 5, 7). (0,) when there's none."""
    m = re.search(r"\d+(?:\.\d+)*", str(v or ""))
    if not m:
        return (0,)
    nums = tuple(int(x) for x in m.group(0).split(".")[:4])
    while len(nums) > 1 and nums[-1] == 0:  # 0.5.7.0 == 0.5.7
        nums = nums[:-1]
    return nums


def _cmp(a: tuple, b: tuple) -> int:
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def _installed_version() -> str:
    """The version the Windows installer registered for Capri ("" when there's none, or not on Windows). A build
    whose code still carried an older __version__ (0.5.7 shipped saying 0.5.4) used to nag about the update it
    already was; the installer's own number is the truth for an installed copy."""
    if sys.platform != "win32" or not FROZEN:
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    exe = os.path.normcase(os.path.dirname(os.path.abspath(sys.executable)))
    best = ""
    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (0, getattr(winreg, "KEY_WOW64_32KEY", 0), getattr(winreg, "KEY_WOW64_64KEY", 0)):
            try:
                root = winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with root:
                for i in range(4096):
                    try:
                        sub = winreg.EnumKey(root, i)
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(root, sub) as k:
                            name = str(winreg.QueryValueEx(k, "DisplayName")[0])
                            if not re.match(r"capri\b", name, re.I):
                                continue
                            ver = str(winreg.QueryValueEx(k, "DisplayVersion")[0])
                            try:
                                where = str(winreg.QueryValueEx(k, "InstallLocation")[0])
                            except OSError:
                                where = ""
                    except OSError:
                        continue
                    # this copy's own entry (or the only Capri entry there is)
                    if where and os.path.normcase(os.path.abspath(where.rstrip("\\/"))) != exe:
                        continue
                    if not best or _cmp(parse(ver), parse(best)) > 0:
                        best = ver
    return best


_current: list[str] = []


def current() -> str:
    """Her real version: the code's __version__ or the installer's, whichever is newer."""
    if not _current:
        reg = _installed_version()
        _current.append(reg if reg and _cmp(parse(reg), parse(__version__)) > 0 else __version__)
    return _current[0]


def newer(latest: str, current_version: str | None = None) -> bool:
    """True only when `latest` is strictly newer than the version she is (same version, or an older one: no)."""
    return _cmp(parse(latest), parse(current_version or current())) > 0


def _tidy(notes: str) -> str:
    """Release notes for her prompt: no images, html, link targets or empty lines."""
    notes = re.sub(r"<!--.*?-->|<[^>]+>", "", notes or "", flags=re.S)
    notes = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", notes)
    notes = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", notes)
    lines = [ln.rstrip() for ln in notes.splitlines() if ln.strip()]
    return "\n".join(lines)[:1800]


def _pick_asset(assets: list[dict]) -> dict | None:
    """The file to download for this PC: Capri-Setup.exe on Windows (any .exe, then a .zip, as a fallback)."""
    if sys.platform != "win32":
        return None  # Linux runs from source: updating = git pull (she opens the release page)
    by = lambda rx: next((a for a in assets if re.search(rx, a.get("name", ""), re.I)), None)  # noqa: E731
    return by(r"setup.*\.exe$") or by(r"\.exe$") or by(r"\.zip$")


_cache: tuple[float, Release] | None = None


def _unix(stamp: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def has_update(rel: Release) -> bool:
    """A real update: a newer version number than hers. Her version is the code's or the installer's, whichever is
    newer (current()), so a build that shipped with a stale number (0.5.7 said 0.5.4) still knows what it is. No
    guessing from upload times: a hotfix five minutes after the last release is still an update."""
    return newer(rel.version)


def latest(max_age: float = 600) -> Release:
    """The newest release on GitHub (asked at most every 10 minutes). Raises when GitHub can't be reached."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < max_age:
        return _cache[1]
    import requests

    r = requests.get(API, timeout=10, headers={"Accept": "application/vnd.github+json", "User-Agent": "Capri"})
    r.raise_for_status()
    j = r.json()
    a = _pick_asset(j.get("assets") or [])
    rel = Release(version=(j.get("tag_name") or j.get("name") or "0").lstrip("vV"), name=j.get("name") or "",
                  notes=_tidy(j.get("body") or ""), url=j.get("html_url") or PAGE,
                  published=(j.get("published_at") or "")[:10],
                  asset=(a or {}).get("browser_download_url", ""), asset_name=(a or {}).get("name", ""),
                  size=int((a or {}).get("size") or 0), asset_time=_unix((a or {}).get("updated_at") or ""))
    _cache = (time.monotonic(), rel)
    return rel


def can_install() -> bool:
    """The installed Capri.exe on Windows updates itself; a source checkout updates with git instead."""
    return FROZEN and sys.platform == "win32"


def downloaded(rel: Release) -> Path | None:
    """The installer for `rel`, if it's already fully downloaded."""
    if not rel.asset_name:
        return None
    p = FOLDER / f"{rel.version}-{rel.asset_name}"
    return p if p.exists() and (not rel.size or p.stat().st_size == rel.size) else None


def download(rel: Release, progress=lambda done, total: None, cancelled=lambda: False) -> Path:
    """Fetch the release's installer into data\\updates (resuming a half-done one). progress(done, total) as it goes.
    Raises when it can't."""
    import requests

    if not rel.asset:
        raise RuntimeError("this release has no installer attached")
    FOLDER.mkdir(parents=True, exist_ok=True)
    dest = FOLDER / f"{rel.version}-{rel.asset_name}"
    if downloaded(rel):
        return dest
    for old in FOLDER.glob("*"):  # older updates she already installed
        if old.name != dest.name and old.name != dest.name + ".part":
            try:
                old.unlink()
            except OSError:
                pass
    part = dest.with_name(dest.name + ".part")
    last = 0.0
    for attempt in range(8):
        have = part.stat().st_size if part.exists() else 0
        if rel.size and have >= rel.size:
            break
        try:
            with requests.get(rel.asset, stream=True, timeout=30, allow_redirects=True,
                              headers={"Range": f"bytes={have}-", "User-Agent": "Capri"} if have else
                              {"User-Agent": "Capri"}) as r:
                r.raise_for_status()
                if have and r.status_code != 206:
                    have = 0
                total = rel.size or int(r.headers.get("Content-Length") or 0) + have
                with open(part, "ab" if have else "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        if cancelled():
                            raise RuntimeError("cancelled")
                        f.write(chunk)
                        have += len(chunk)
                        if time.monotonic() - last > 0.5:
                            last = time.monotonic()
                            progress(have, total)
            if not rel.size:
                break
        except requests.RequestException as e:
            print(f"[capri] update download dropped ({type(e).__name__}), resuming")
            time.sleep(2 + attempt * 2)
    if not part.exists() or (rel.size and part.stat().st_size != rel.size):
        raise RuntimeError("the download didn't finish")
    part.replace(dest)
    progress(rel.size or dest.stat().st_size, rel.size or dest.stat().st_size)
    return dest


def run_installer(path: Path) -> None:
    """Start the installer on its own (it outlives her). Capri quits right after, so her files aren't in use."""
    if os.name == "nt":
        os.startfile(str(path))  # through the shell: its own process, and Windows can ask for admin if it needs it
        return
    import subprocess

    subprocess.Popen([str(path)], cwd=str(path.parent), start_new_session=True)


# Her own lines while an update downloads (said straight away, not written by her brain), per language.
LINES = {
    "en": {"half": "Update's halfway in. Don't unplug me, choom.",
           "done": "Update's downloaded. Say \"install it\" and I'll close myself and run the installer. "
                   "I'll be right back, prettier.",
           "fail": "The update download flatlined on me. Ask me again in a bit.",
           "bye": "Installing my update. Don't miss me too much, back in a sec.",
           "news": "Psst, a new version of me is out (v{v}). Ask me \"any updates?\" and I'll tell you what's new."},
    "es": {"half": "La actualización va por la mitad. No me desenchufes, choom.",
           "done": "Actualización descargada. Di \"instálala\" y me cierro para correr el instalador. Vuelvo enseguida, "
                   "más guapa.",
           "fail": "La descarga de la actualización se cayó. Pídemelo otra vez en un rato.",
           "bye": "Instalando mi actualización. No me extrañes mucho, vuelvo en un segundo.",
           "news": "Psst, salió una nueva versión de mí (v{v}). Pregúntame \"¿hay actualizaciones?\" y te cuento qué trae."},
    "pt": {"half": "A atualização tá na metade. Não me desliga, choom.",
           "done": "Atualização baixada. Diz \"instala\" que eu fecho e rodo o instalador. Já volto, mais bonita.",
           "fail": "O download da atualização caiu. Me pede de novo daqui a pouco.",
           "bye": "Instalando minha atualização. Não sente muita saudade, já volto.",
           "news": "Psst, saiu uma versão nova de mim (v{v}). Me pergunta \"tem atualização?\" que eu te conto o que mudou."},
    "fr": {"half": "La mise à jour est à moitié là. Me débranche pas, choom.",
           "done": "Mise à jour téléchargée. Dis \"installe-la\" et je me ferme pour lancer l'installateur. "
                   "Je reviens vite, en mieux.",
           "fail": "Le téléchargement de la mise à jour a planté. Redemande-moi dans un moment.",
           "bye": "J'installe ma mise à jour. Je reviens dans une seconde.",
           "news": "Psst, une nouvelle version de moi est sortie (v{v}). Demande-moi \"y a-t-il une mise à jour ?\" et je te dis ce qui change."},
    "it": {"half": "L'aggiornamento è a metà. Non staccarmi la spina, choom.",
           "done": "Aggiornamento scaricato. Di' \"installalo\" e mi chiudo per avviare l'installer. Torno subito, più "
                   "bella.",
           "fail": "Il download dell'aggiornamento è saltato. Chiedimelo di nuovo tra poco.",
           "bye": "Installo il mio aggiornamento. Torno tra un secondo.",
           "news": "Psst, è uscita una nuova versione di me (v{v}). Chiedimi \"c'è un aggiornamento?\" e ti dico cosa cambia."},
    "de": {"half": "Das Update ist halb drin. Zieh mir nicht den Stecker, Choom.",
           "done": "Update ist geladen. Sag \"installier es\" und ich schließe mich und starte den Installer. "
                   "Bin gleich zurück, hübscher.",
           "fail": "Der Update-Download ist abgeschmiert. Frag mich gleich nochmal.",
           "bye": "Installiere mein Update. Bin in einer Sekunde zurück.",
           "news": "Psst, es gibt eine neue Version von mir (v{v}). Frag mich \"gibt es ein Update?\" und ich sag dir, was neu ist."},
    "pl": {"half": "Aktualizacja w połowie. Nie odłączaj mnie, choom.",
           "done": "Aktualizacja pobrana. Powiedz \"zainstaluj\" i zamknę się, żeby odpalić instalator. Zaraz wracam, "
                   "ładniejsza.",
           "fail": "Pobieranie aktualizacji padło. Poproś mnie znowu za chwilę.",
           "bye": "Instaluję aktualizację. Zaraz wracam.",
           "news": "Psst, wyszła nowa wersja mnie (v{v}). Zapytaj \"jest aktualizacja?\", a powiem ci, co nowego."},
    "ru": {"half": "Обновление скачано наполовину. Не выдёргивай меня, чум.",
           "done": "Обновление скачано. Скажи «установи» и я закроюсь и запущу установщик. Сейчас вернусь, ещё красивее.",
           "fail": "Загрузка обновления сорвалась. Попроси ещё раз чуть позже.",
           "bye": "Ставлю обновление. Вернусь через секунду.",
           "news": "Псс, вышла новая версия меня (v{v}). Спроси «есть обновление?» и я расскажу, что нового."},
}


def line(kind: str, language: str = "en", **kw) -> str:
    return LINES.get(language, LINES["en"])[kind].format(**kw)
