"""The portable dev build: what it can download, where things go, and finding them again.

Folder layout next to Capri.exe:
    models\\llm\\      brains (.gguf). Drop your own GGUF in here and it shows up in her Brain menu.
    models\\whisper\\  ears (whisper.cpp ggml-*.bin)
    engines\\llama\\   llama.cpp's llama-server (runs any GGUF, new architectures included)
    engines\\whisper\\ whisper.cpp's whisper-server (on the CPU, so the GPU stays free for her brain)
                    (Linux: from the package manager instead, `sudo pacman -S whisper-cpp`)
    data\\            her settings and memory (starts empty)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT

MODELS = ROOT / "models"
LLM_DIR = MODELS / "llm"
WHISPER_DIR = MODELS / "whisper"
ASR_DIR = MODELS / "asr"  # Qwen3-ASR ears (a .gguf and its audio projector)
ENGINES = ROOT / "engines"

LLAMA_TAG = "b11932"  # pinned so every dev runs the same engine
_LLAMA = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/"
_WHISPER = "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/"
_HF = "https://huggingface.co/"
LINUX = sys.platform.startswith("linux")


@dataclass
class Item:
    key: str
    label: str
    note: str
    files: list[tuple[str, str, int]]  # (url, path under ROOT, bytes)
    unzip: bool = False
    system: str = ""  # Linux: a program the package manager installs (nothing to download)

    @property
    def size(self) -> int:
        return sum(f[2] for f in self.files)

    def installed(self) -> bool:
        if self.system:
            return shutil.which(self.system) is not None
        if self.unzip:
            return (ROOT / self.files[0][1]).parent.joinpath(".done-" + self.key).exists()
        # a finished file only ever exists under its real name (downloads run into .part first), so a few bytes off
        # what the catalog says (the host re-uploaded it) still counts as here
        return all((ROOT / p).exists() and abs((ROOT / p).stat().st_size - n) <= max(1 << 16, n // 50)
                   for _, p, n in self.files)


BRAINS = [
    Item("nemotron", "Nemotron Heretic E4B",
         "the best one: smart, real answers, and it can see your screen (eyes included) (~6 GB VRAM)",
         [(_HF + "Ghasty3/CapriAI2/resolve/main/nemotron-3.5-bolt-e4b-heretic/Nemotron-3.5-Bolt-E4B-Heretic.Q4_K_M.gguf",
           "models/llm/Nemotron-Heretic/Nemotron-3.5-Bolt-E4B-Heretic.Q4_K_M.gguf", 5335293696),
          (_HF + "Ghasty3/CapriAI2/resolve/main/nemotron-3.5-bolt-e4b-heretic/Nemotron-3.5-Bolt-E4B-Heretic.BF16-mmproj.gguf",
           "models/llm/Nemotron-Heretic/Nemotron-3.5-Bolt-E4B-Heretic.BF16-mmproj.gguf", 991552032)]),
    Item("conv7b", "Conversational 7B", "fast and chatty, but gets facts wrong sometimes (~5 GB VRAM)",
         [(_HF + "xiaoheiqaq/conversational-7b-v0.4-Q4_K_M-GGUF/resolve/main/conversational-7b-v0.4-q4_k_m.gguf",
           "models/llm/conversational-7b-v0.4-q4_k_m.gguf", 4372815648)]),
    Item("capriai2", "CapriAI2 (Qwen3.5 4B)", "trained to be Capri; small and new (~4 GB VRAM)",
         [(_HF + "Ghasty3/CapriAI2/resolve/main/qwen3.5-4b/Qwen3.5-4B.Q4_K_M.gguf",
           "models/llm/CapriAI2-Qwen3.5-4B.Q4_K_M.gguf",
           2783446624)]),
]
_WM = _HF + "ggerganov/whisper.cpp/resolve/main/"
EARS = [  # the multilingual models hear all eight of her languages; the .en ones only English (a bit sharper at it)
    Item("tiny", "Tiny", "fastest, makes the most mistakes, all languages",
         [(_WM + "ggml-tiny.bin", "models/whisper/ggml-tiny.bin", 77691713)]),
    Item("base", "Base", "fast, decent, all languages",
         [(_WM + "ggml-base.bin", "models/whisper/ggml-base.bin", 147951465)]),
    Item("small", "Small", "the sweet spot (what Capri uses), all languages",
         [(_WM + "ggml-small.bin", "models/whisper/ggml-small.bin", 487601967)]),
    Item("medium", "Medium", "most accurate, slower on the CPU, all languages",
         [(_WM + "ggml-medium.bin", "models/whisper/ggml-medium.bin", 1533763059)]),
    Item("small.en", "Small (English only)", "English only, a little sharper at it",
         [(_WM + "ggml-small.en.bin", "models/whisper/ggml-small.en.bin", 487614201)]),
    # Qwen3-ASR: an AI that hears, not just a transcriber: gets names and slang right (told her name and the usual
    # words), 52 languages, and says which one you spoke. Runs on her llama.cpp engine, on the CPU.
    Item("qwen", "Qwen3-ASR", "the sharpest ears: names, slang and accents, 52 languages (~2 s a line on the CPU)",
         [(_HF + "ggml-org/Qwen3-ASR-0.6B-GGUF/resolve/main/Qwen3-ASR-0.6B-Q8_0.gguf",
           "models/asr/Qwen3-ASR-0.6B-Q8_0.gguf", 804749248),
          (_HF + "ggml-org/Qwen3-ASR-0.6B-GGUF/resolve/main/mmproj-Qwen3-ASR-0.6B-Q8_0.gguf",
           "models/asr/mmproj-Qwen3-ASR-0.6B-Q8_0.gguf", 214392480)]),
]
EARS_DEFAULT = 2  # Small, all languages


def english_only(model_name: str) -> bool:
    """ggml-small.en.bin / small.en / distil-*.en: a model that only knows English."""
    name = model_name.lower().replace("\\", "/").rsplit("/", 1)[-1]
    return ".en." in name or name.endswith(".en") or name.endswith("-en") or "_en." in name


def pick_ears(models: list[Path], want: str = "") -> Path | None:
    """The whisper model to use: the one picked, else Small that hears every language, else any multilingual one,
    else whatever is there."""
    if not models:
        return None
    hit = next((m for m in models if m.name == want), None)
    if hit:
        return hit
    multi = [m for m in models if not english_only(m.name)]
    return (next((m for m in multi if "small" in m.name), None) or (multi[0] if multi else None)
            or next((m for m in models if "small" in m.name), None) or models[0])


ENGINE_GPU = [
    Item("vulkan", "Vulkan", "any GPU (NVIDIA, AMD, Intel); small download",
         [(_LLAMA + f"llama-{LLAMA_TAG}-bin-win-vulkan-x64.zip", "engines/llama/llama-vulkan.zip", 37180481)], True),
    Item("cuda", "CUDA", "NVIDIA only; a bit faster, big download",
         [(_LLAMA + f"llama-{LLAMA_TAG}-bin-win-cuda-12.4-x64.zip", "engines/llama/llama-cuda.zip", 279443090),
          (_LLAMA + "cudart-llama-bin-win-cuda-12.4-x64.zip", "engines/llama/cudart.zip", 391443627)], True),
]
ENGINE_EARS = Item("whisper", "whisper.cpp", "her ears, on the CPU",
                   [(_WHISPER + "whisper-blas-bin-x64.zip", "engines/whisper/whisper.zip", 21715607)], True)
# her clips ("capri, clip that"): ffmpeg records the screen in the background; downloaded the first time you turn
# clips on (Settings > Clips)
ENGINE_CLIPS = Item("ffmpeg", "ffmpeg", "records your screen for clips",
                    [("https://github.com/GyanD/codexffmpeg/releases/download/7.1/ffmpeg-7.1-essentials_build.zip",
                      "engines/ffmpeg/ffmpeg.zip", 92100272)], True)
if LINUX:  # llama.cpp's Ubuntu builds run on Arch too; whisper.cpp ships no Linux build, Arch packages it
    ENGINE_GPU = [
        Item("vulkan", "Vulkan", "any GPU (NVIDIA, AMD, Intel); small download",
             [(_LLAMA + f"llama-{LLAMA_TAG}-bin-ubuntu-vulkan-x64.tar.gz", "engines/llama/llama-vulkan.tar.gz",
               35281110)], True),
        Item("cuda", "CUDA", "NVIDIA only; a bit faster, big download",
             [(_LLAMA + f"llama-{LLAMA_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz", "engines/llama/llama-cuda.tar.gz",
               182241275),
              (_LLAMA + f"cudart-llama-{LLAMA_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz", "engines/llama/cudart.tar.gz",
               594371573)], True),
    ]
    ENGINE_EARS = Item("whisper", "whisper.cpp", "her ears: sudo pacman -S whisper-cpp", [], system="whisper-server")


def has_nvidia() -> bool:
    return (shutil.which("nvidia-smi") is not None or Path(r"C:\Windows\System32\nvidia-smi.exe").exists()
            or Path("/proc/driver/nvidia").exists())


def specs() -> dict:
    """This PC: {'gpu': name, 'vram': GB of the biggest graphics card, 'ram': GB}. 0 where it can't be told."""
    import subprocess

    out = {"gpu": "", "vram": 0.0, "ram": 0.0}
    flags = 0x08000000 if sys.platform == "win32" else 0  # no console window popping up
    smi = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe"
                                         if Path(r"C:\Windows\System32\nvidia-smi.exe").exists() else None)
    if smi:
        try:
            r = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=8, creationflags=flags)
            for line in r.stdout.strip().splitlines():
                name, mb = [x.strip() for x in line.rsplit(",", 1)]
                if float(mb) / 1024 > out["vram"]:
                    out["gpu"], out["vram"] = name, round(float(mb) / 1024, 1)
        except Exception:
            pass
    if not out["vram"] and sys.platform == "win32":  # AMD / Intel: the driver writes the real size in the registry
        try:
            import winreg

            base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                for i in range(32):
                    try:
                        sub = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(k, sub) as g:
                            name = str(winreg.QueryValueEx(g, "DriverDesc")[0])
                            try:
                                size = winreg.QueryValueEx(g, "HardwareInformation.qwMemorySize")[0]
                            except OSError:
                                size = 0  # integrated graphics (Intel UHD...): no memory of its own
                    except OSError:
                        continue
                    if re.search(r"Idd|Basic Display|Remote|Virtual|Parsec", name, re.I):
                        continue  # virtual displays, not a real card
                    if not out["gpu"]:
                        out["gpu"] = name
                    gb_ = int(size) / 1024 ** 3
                    if gb_ > out["vram"]:
                        out["gpu"], out["vram"] = str(name), round(gb_, 1)
        except Exception:
            pass
    if not out["vram"] and sys.platform.startswith("linux"):  # AMD / Intel on Linux: the amdgpu driver says it in sysfs
        for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
            dev = card / "device"
            try:
                gb_ = int((dev / "mem_info_vram_total").read_text()) / 1024 ** 3
            except (OSError, ValueError):
                gb_ = 0.0
            vendor = ""
            try:
                vendor = {"0x1002": "AMD", "0x8086": "Intel", "0x10de": "NVIDIA"}.get((dev / "vendor").read_text().strip(), "")
            except OSError:
                pass
            if vendor and not out["gpu"]:
                out["gpu"] = vendor + " graphics"
            if gb_ > out["vram"]:
                out["gpu"], out["vram"] = f"{vendor or 'GPU'} graphics", round(gb_, 1)
    try:
        if sys.platform == "win32":
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]

            m = _MS()
            m.len = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            out["ram"] = round(m.total / 1024 ** 3, 1)
        else:
            out["ram"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3, 1)
    except Exception:
        pass
    return out


_specs_cache: dict | None = None


def specs_cached() -> dict:
    global _specs_cache
    if _specs_cache is None:
        _specs_cache = specs()
    return _specs_cache


def best_brain(sp: dict | None = None) -> tuple[Item | None, str]:
    """The brain that fits this PC best, and why in a few words. Nemotron (smartest, and the only one with eyes)
    wants ~6 GB of VRAM; CapriAI2 fits in ~4 GB; under that a brain would crawl, so none."""
    sp = sp if sp is not None else specs_cached()
    v, ram = sp.get("vram", 0), sp.get("ram", 0)
    by = {it.key: it for it in BRAINS}
    gpu = f"{sp.get('gpu') or 'your GPU'}, {v:g} GB VRAM" if v else (
        f"{sp['gpu']} (no VRAM of its own)" if sp.get("gpu") else "no graphics card found")
    if v >= 6:
        return by["nemotron"], f"{gpu}: runs the smartest one, with eyes"
    if v >= 3.5:
        return by["capriai2"], f"{gpu}: the small one fits"
    if ram >= 6:  # an "8 GB" laptop reports ~7.7: the 2.8 GB one still fits on the CPU
        return by["capriai2"], f"{gpu}, {ram:g} GB RAM: the small one, runs on your CPU (a bit slow)"
    return None, (f"{gpu}, {ram:g} GB RAM: too light for a local brain. She works best here as a visual companion "
                  f"(roams, peeks, sits on your windows, short lines of her own); Claude Code can still be her brain")


def best_ears(sp: dict | None = None) -> int:
    """Index in EARS for this PC: her ears run on the CPU, and Small takes ~30 s per line on a 2-core laptop, so
    small CPUs get Base (still all languages, several times faster)."""
    sp = sp if sp is not None else specs_cached()
    cores = os.cpu_count() or 4
    if cores <= 4 or sp.get("ram", 16) < 12:
        return 1  # Base
    return next(i for i, it in enumerate(EARS) if it.key == "qwen")  # Qwen3-ASR: hears names and slang right


def finish(item: Item) -> None:
    """After an engine's zips / tarballs are downloaded: unpack them flat into the engine folder, drop the archives."""
    if not item.unzip:
        return
    folder = (ROOT / item.files[0][1]).parent
    for _, p, _ in item.files:
        z = ROOT / p
        if z.name.endswith(".tar.gz"):
            _untar(z, folder)
        else:
            with zipfile.ZipFile(z) as zf:
                for m in zf.infolist():
                    if m.is_dir():
                        continue
                    target = folder / Path(m.filename).name  # flat: the exes and their DLLs side by side
                    with zf.open(m) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        z.unlink()
    for other in ENGINE_GPU:  # one llama engine at a time
        if other is not item:
            folder.joinpath(".done-" + other.key).unlink(missing_ok=True)
    folder.joinpath(".done-" + item.key).write_text("ok")


def download(items: list[Item], progress=lambda done, total, what: None, cancelled=lambda: False) -> bool:
    """Fetch the items' files into the folder (resuming .part files, retrying a dropped connection), then unpack the
    engines. progress(bytes done, bytes total, what) as it goes. -> False if cancelled() stopped it (it carries on
    where it stopped next time); raises when a download can't be finished."""
    import time

    import requests

    total = sum(it.size for it in items)
    done = 0
    t0, last = time.monotonic(), 0.0
    for it in items:
        for url, rel, size in it.files:
            dest = ROOT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and abs(dest.stat().st_size - size) <= max(1 << 16, size // 50):
                done += size
                continue
            part = dest.with_name(dest.name + ".part")
            for attempt in range(100):  # flaky connections: keep resuming
                have = part.stat().st_size if part.exists() else 0
                if have >= size:
                    break
                try:
                    with requests.get(url, stream=True, timeout=(15, 120), allow_redirects=True,
                                      headers={"Range": f"bytes={have}-"} if have else {}) as r:
                        
                        if r.status_code == 429 or r.status_code >= 500:
                            raise requests.HTTPError(f"Temporary HTTP error: {r.status_code}")
                        if r.status_code in (401, 403, 404, 410):  # moved or gone: retrying won't help
                            raise RuntimeError(f"{it.label}: dead download link ({r.status_code}) {url}")
                        r.raise_for_status()
                        if have and r.status_code != 206:  # server ignored the range: start over
                            have = 0
                        real = _full_size(r)  # the host's own size wins over the catalog's (re-uploaded files)
                        if real and abs(real - size) <= max(1 << 20, size // 20):
                            size = real
                        with open(part, "ab" if have else "wb") as f:
                            for chunk in r.iter_content(1 << 20):
                                if cancelled():
                                    return False
                                f.write(chunk)
                                have += len(chunk)
                                now = time.monotonic()
                                if now - last > 0.25:
                                    last = now
                                    speed = (done + have) / max(1.0, now - t0)
                                    progress(done + have, total, f"{it.label}: {gb(have)} of {gb(size)}   "
                                                                 f"({speed / 2 ** 20:.1f} MB/s)")
                except requests.RequestException as e:
                   retry_after = getattr(getattr(e, "response", None), "headers", {}).get("Retry-After")

                   try:
                        delay = min(300, max(1, int(retry_after))) if retry_after else min(60, 5 + attempt * 3)
                    except (ValueError, TypeError):
                    delay = min(60, 5 + attempt * 3)

progress(done, total, f"connection dropped ({type(e).__name__}), retrying...")
time.sleep(delay)
            if not part.exists() or part.stat().st_size != size:
                progress(done, total, f"{it.label}: download incomplete, skipping")
                continue
            part.replace(dest)
            done += size
        try:
            finish(it)
        except (OSError, zipfile.BadZipFile, tarfile.TarError) as e:
            progress(done, total, f"{it.label}: extraction failed: {e}")
            raise
    return True


def _full_size(r) -> int:
    """The whole file's size from a download response (Content-Range on a resume, Content-Length otherwise)."""
    try:
        if r.status_code == 206:
            return int(r.headers.get("Content-Range", "").rsplit("/", 1)[-1])
        return int(r.headers.get("Content-Length") or 0)
    except ValueError:
        return 0


def gb(n: int) -> str:
    return f"{n / 2 ** 30:.1f} GB" if n >= 2 ** 30 * 0.95 else f"{n / 2 ** 20:.0f} MB"


def config_name() -> str:
    """Your name from config.yaml ("" when it isn't set)."""
    import re

    try:
        m = re.search(r"^user_name:\s*['\"]?([^'\"#\n]*?)['\"]?\s*(?:#.*)?$",
                      (ROOT / "config.yaml").read_text(encoding="utf-8"), re.M)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


def set_config_name(name: str) -> None:
    """Your name into config.yaml (the rest of the file, comments and all, stays as it is)."""
    import json
    import re

    p = ROOT / "config.yaml"
    if not p.exists():  # start from the dev build's settings (backend: server), not a file with only the name in it
        template = Path(__file__).resolve().parents[1] / "portable" / "config.yaml"
        if template.exists():
            shutil.copy(template, p)
    try:
        s = p.read_text(encoding="utf-8")
    except OSError:
        s = ""
    line = "user_name: " + json.dumps(name)  # quoted: any name is valid YAML
    s = re.sub(r"^user_name:.*$", line, s, flags=re.M) if re.search(r"^user_name:", s, re.M) else s + "\n" + line + "\n"
    p.write_text(s, encoding="utf-8")


def _untar(z: Path, folder: Path) -> None:
    """Linux engines: flat like the zips, keeping the .so symlinks and the executable bits."""
    with tarfile.open(z) as tf:
        for m in tf.getmembers():
            target = folder / Path(m.name).name
            if m.issym() or m.islnk():
                target.unlink(missing_ok=True)
                os.symlink(Path(m.linkname).name, target)
            elif m.isfile():
                with tf.extractfile(m) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.chmod(target, 0o755 if m.mode & 0o111 else 0o644)


def find_exe(name: str) -> Path | None:
    """An engine program: the one setup downloaded, else one on PATH (Linux packages: whisper-cpp, llama.cpp)."""
    name = name.removesuffix(".exe") + (".exe" if os.name == "nt" else "")
    for p in ENGINES.rglob(name):
        return p
    found = shutil.which(name)
    return Path(found) if found else None


def model_dirs() -> list[Path]:
    """Folders where people usually keep .gguf models (LM Studio, GPT4All, Jan, text-generation-webui, ERU AI,
    Downloads...), the ones that exist."""
    import os

    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    cands = [home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models", local / "nomic.ai" / "GPT4All",
             home / "jan" / "models", home / "AppData" / "Roaming" / "Jan" / "data" / "models",
             home / "text-generation-webui" / "models", Path(r"D:\ERU AI\models\llm"), home / "CapriModels",
             home / "Downloads", LLM_DIR]
    return [d for d in cands if d.is_dir()]


def local_models(limit: int = 60) -> list[Path]:
    """.gguf brains already on this PC (vision add-ons and tiny files left out), biggest folders first."""
    found, seen = [], set()
    for d in model_dirs():
        try:
            for p in d.rglob("*.gguf"):
                low = p.name.lower()
                if "mmproj" in low or p.stat().st_size < 300 * 2 ** 20:
                    continue
                key = (p.name, p.stat().st_size)
                if key not in seen:
                    seen.add(key)
                    found.append(p)
                if len(found) >= limit:
                    return found
        except OSError:
            continue
    return found


def brains() -> list[Path]:
    """Every .gguf in models\\llm (the catalog's and any a dev dropped in), minus vision add-on files."""
    if not LLM_DIR.exists():
        return []
    return sorted(p for p in LLM_DIR.rglob("*.gguf") if "mmproj" not in p.name.lower())


def ears_models() -> list[Path]:
    """Every ears model on this PC: whisper's ggml-*.bin files, then Qwen3-ASR (its .gguf, not the projector)."""
    whisper = sorted(WHISPER_DIR.glob("ggml-*.bin")) if WHISPER_DIR.exists() else []
    qwen = sorted(p for p in ASR_DIR.glob("*.gguf") if "mmproj" not in p.name.lower()) if ASR_DIR.exists() else []
    return whisper + qwen


def qwen_ears(name: str) -> bool:
    """Is this ears model Qwen3-ASR (runs on llama-server) rather than whisper?"""
    return str(name).lower().endswith(".gguf")


def needs_setup() -> bool:
    """Nothing to think with yet? Not when a brain was picked (downloaded, your own .gguf anywhere, or "none")."""
    import json

    from .config import DATA

    try:
        picked = json.loads((DATA / "settings.json").read_text(encoding="utf-8")).get("brain", "")
    except (OSError, ValueError):
        picked = ""
    if picked in ("none", "claude-code", "lm-studio"):
        return False  # no AI, or Claude Code / LM Studio thinking for her: no brain of hers to run
    if picked and Path(picked).exists():
        return find_exe("llama-server") is None
    return not brains() or find_exe("llama-server") is None


def no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


_job = None


def tie_to_me(proc) -> None:
    """Put a helper process (llama-server, whisper-server) in a Windows job that dies with Capri, so a crash or a
    killed Capri never leaves them running in the background."""
    global _job
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    if _job is None:
        class Limits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Limits), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        info = Extended()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
        _job = job
    k32.AssignProcessToJobObject(wintypes.HANDLE(_job), wintypes.HANDLE(int(proc._handle)))


def spawn(cmd: list[str], **kw) -> subprocess.Popen:
    """Start a helper (llama-server, whisper-server) that can't outlive Capri, with no console window."""
    if os.name == "nt":
        proc = subprocess.Popen(cmd, creationflags=no_window(), **kw)
        tie_to_me(proc)
        return proc
    env = {**os.environ, **(kw.pop("env", None) or {})}  # caller's env (MCP servers) wins over ours
    env["LD_LIBRARY_PATH"] = str(Path(cmd[0]).parent) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    import ctypes
    import signal

    prctl = ctypes.CDLL(None).prctl  # looked up here: the child, between fork and exec, should only make the call

    def die_with_capri():  # SIGTERM once the thread that started it is gone (see _on_spawner)
        prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG

    return _on_spawner(lambda: subprocess.Popen(cmd, env=env, preexec_fn=die_with_capri, **kw))


_jobs = None


def _on_spawner(start):
    """Linux ties a child's death signal to the *thread* that forked it, so every helper is forked from one thread
    that lives exactly as long as Capri does: when she dies, for any reason, they get SIGTERM."""
    global _jobs
    import queue

    if _jobs is None:
        _jobs = queue.Queue()

        def run():
            while True:
                fn, box, done = _jobs.get()
                try:
                    box.append(fn())
                except BaseException as e:
                    box.append(e)
                done.set()

        threading.Thread(target=run, daemon=True, name="capri-spawner").start()
    box, done = [], threading.Event()
    _jobs.put((start, box, done))
    done.wait()
    if isinstance(box[0], BaseException):
        raise box[0]
    return box[0]
