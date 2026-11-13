"""Her ears: the mic, hold-to-talk or always-on, transcribed by faster-whisper (ERU's "small" model, on the GPU).

Built for low latency: the model is loaded and warmed up at boot, decoding is greedy with no timestamps and no
carried-over context, and in hold mode the transcription starts the instant you let go of the key.
"""
from __future__ import annotations

import re
import threading
import time

import numpy as np

from . import hotkey

RATE = 16000
BLOCK = 512  # 32 ms


def _whisper_dir(cfg: dict) -> str | None:
    import glob
    import os

    root = os.path.join(cfg["eru_ai_root"], "LocalAIVtuber2", "backend", "models", "whisper")
    hits = glob.glob(os.path.join(root, "**", "model.bin"), recursive=True)
    return os.path.dirname(sorted(hits)[0]) if hits else None


LANGS = ("en", "es", "pt", "fr", "ru", "it", "de", "pl")  # the eight she speaks (capri/i18n.py)
_FULL = {"english": "en", "spanish": "es", "portuguese": "pt", "french": "fr", "russian": "ru", "italian": "it",
         "german": "de", "polish": "pl"}


def _code(name) -> str:
    """'russian' / 'ru' / 'RU' -> 'ru' ("" for anything else)."""
    n = str(name or "").strip().lower()
    return n if n in LANGS else _FULL.get(n, "")


def choose_language(probs: dict, hint: str, seconds: float) -> str:
    """Which language a line was in, from the model's guesses. Only her eight count, and a short or unsure line
    stays in the language you've been talking (a two-word "hey capri" sounds Spanish or Italian to Whisper, and
    used to come back as Spanish)."""
    ours = {c: p for c, p in ((_code(k), v) for k, v in probs.items()) if c}
    hint = hint if hint in LANGS else "en"
    if not ours:
        return hint
    best = max(ours, key=ours.get)
    need = 0.85 if seconds < 2.0 else 0.6
    if best != hint and ours[best] < need:
        return hint
    return best


class Transcriber:
    def __init__(self, cfg: dict):
        from faster_whisper import WhisperModel

        c = cfg["stt"]
        name = _whisper_dir(cfg) or c["model"]
        device = "cuda"
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() == 0:
                device = "cpu"
        except Exception:
            device = "cpu"
        self.model = WhisperModel(name, device=device, compute_type="int8_float16" if device == "cuda" else "int8")
        from ..portable import english_only

        self.multilingual = bool(getattr(getattr(self.model, "model", None), "is_multilingual", not english_only(name)))
        self.language = "en"  # the language of the last line it heard
        self.hotwords = c.get("hotwords") or None
        t = time.monotonic()
        self(np.zeros(RATE, dtype=np.float32))  # warm-up: the first real line shouldn't pay for CUDA init
        print(f"[capri] ears: whisper {name.split(chr(92))[-1]} on {device}, warm in {time.monotonic() - t:.1f}s")

    def _detect(self, audio: np.ndarray, hint: str) -> str:
        try:
            _, _, probs = self.model.detect_language(audio)  # faster-whisper 1.1+
            probs = dict(probs)
        except Exception:
            try:
                _, info = self.model.transcribe(audio[: RATE * 8], language=None, beam_size=1, without_timestamps=True,
                                                max_new_tokens=1)
                probs = dict(info.all_language_probs or [(info.language, info.language_probability)])
            except Exception:
                return hint
        return choose_language(probs, hint, len(audio) / RATE)

    def __call__(self, audio: np.ndarray, language: str = "", hint: str = "") -> str:
        """language: the one picked in her Language menu ("" = Auto: she works it out, sticking to `hint`, the
        language you've been talking, when a line is short or unclear)."""
        from ..brain.llm import GPU_LOCK

        with GPU_LOCK:  # segments are lazy: consume them inside the lock too
            if not self.multilingual:
                lang = "en"
            elif _code(language):
                lang = _code(language)
            else:
                lang = self._detect(audio, _code(hint) or self.language)
            self.language = lang
            segs, _ = self.model.transcribe(
                audio, language=lang, beam_size=1, best_of=1, temperature=0, without_timestamps=True,
                condition_on_previous_text=False, vad_filter=False, hotwords=self.hotwords, max_new_tokens=120,
            )
            keep = [s.text.strip() for s in segs
                    if s.compression_ratio <= 2.4 and not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        text = " ".join(keep).strip()
        if hallucination(text, len(audio) / RATE, self.hotwords):
            return ""
        return text


class WhisperCpp:
    """The portable build's ears: whisper.cpp's whisper-server on the CPU (the GPU stays free for her brain), with
    the ggml model picked in setup (or the Ears > Model menu)."""

    PORT = 8090

    def __init__(self, cfg: dict):
        import io
        import os
        import subprocess
        import wave

        import requests

        from ..config import DATA
        from ..portable import ears_models, find_exe, spawn

        self.io, self.wave = io, wave
        exe = find_exe("whisper-server")
        models = [m for m in ears_models() if not m.name.lower().endswith(".gguf")]  # whisper's own, not Qwen's
        if exe is None or not models:
            raise RuntimeError("her ears (whisper.cpp) aren't downloaded yet: run Capri's setup"
                               + (" (Linux: sudo pacman -S whisper-cpp)" if exe is None and os.name != "nt" else ""))
        from ..portable import english_only, pick_ears

        model = pick_ears(models, cfg["stt"].get("whisper_model") or "")
        self.model_name = model.name
        self.multilingual = not english_only(model.name)
        self.language = "en"
        self.hotwords = cfg["stt"].get("hotwords") or ""
        self.session = requests.Session()
        self.base = f"http://127.0.0.1:{self.PORT}"
        DATA.mkdir(exist_ok=True)
        threads = str(max(2, min(8, (os.cpu_count() or 4) - 2)))
        with open(DATA / "whisper-server.log", "w", encoding="utf-8", errors="ignore") as log:
            self.proc = spawn(
                [str(exe), "-m", str(model), "--host", "127.0.0.1", "--port", str(self.PORT), "-l", "auto" if self.multilingual else "en", "-t", threads,
                 "-nt", "-ng"], cwd=str(exe.parent), stdout=log, stderr=subprocess.STDOUT)
        t = time.monotonic()
        while time.monotonic() - t < 60:
            if self.proc.poll() is not None:
                raise RuntimeError("whisper-server stopped right away (see data/whisper-server.log)")
            try:
                self.session.get(self.base + "/", timeout=1)
                break
            except Exception:
                time.sleep(0.3)
        self(np.zeros(RATE, dtype=np.float32))  # warm-up
        print(f"[capri] ears: whisper.cpp {model.name} on the CPU, ready in {time.monotonic() - t:.1f}s")

    def _post(self, wav: bytes, lang: str) -> dict:
        r = self.session.post(self.base + "/inference", timeout=60,
                              files={"file": ("speech.wav", wav, "audio/wav")},
                              data={"temperature": "0", "response_format": "verbose_json", "prompt": self.hotwords,
                                    "language": lang})
        try:
            return r.json()
        except ValueError:
            return {}

    def __call__(self, audio: np.ndarray, language: str = "", hint: str = "") -> str:
        """Same as Transcriber: `language` = the picked one ("" = Auto, leaning on `hint` for short lines)."""
        buf = self.io.BytesIO()
        with self.wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        wav = buf.getvalue()
        hint = _code(hint) or self.language
        if not self.multilingual:
            j = self._post(wav, "en")
        elif _code(language):
            j = self._post(wav, _code(language))
            self.language = _code(language)
        else:  # Auto: let it guess, and redo the line in your usual language when the guess is weak or not hers
            j = self._post(wav, "auto")
            heard = _code(j.get("detected_language") or j.get("language"))
            prob = j.get("detected_language_probability")
            probs = {heard: float(prob) if prob is not None else 0.9} if heard else {}
            lang = choose_language(probs, hint, len(audio) / RATE)
            if lang != heard:
                j = self._post(wav, lang)
            self.language = lang
        text = " ".join((j.get("text") or "").split())
        if hallucination(text, len(audio) / RATE, self.hotwords or None):
            return ""
        return text

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()


class QwenASR:
    """Ears on Qwen3-ASR: an audio model on llama.cpp's llama-server (her brain engine), on the CPU so the GPU stays
    free for her brain. It's told her name and the usual words first, so "Capri" and "Ado" don't come back as
    "Coppery" and "Adele", and it says which language you spoke ("language English<asr_text>...")."""

    PORT = 8091
    CONTEXT = "Capri, choom, gonk, preem, Night City, Edgerunners, Arasaka, Discord, Spotify, YouTube, Steam"

    def __init__(self, cfg: dict, model):
        import base64
        import io
        import os
        import subprocess
        import wave

        import requests

        from ..brain.llm import free_port
        from ..config import DATA
        from ..portable import find_exe, spawn

        self.io, self.wave, self.b64 = io, wave, base64
        exe = find_exe("llama-server")
        proj = next((p for p in model.parent.glob("*.gguf") if "mmproj" in p.name.lower()), None)
        if exe is None:
            raise RuntimeError("Qwen ears run on her brain engine (llama.cpp), and it isn't downloaded yet: get it "
                               "from her Brain menu, or pick other ears")
        if proj is None:
            raise RuntimeError(f"Qwen ears are missing their audio part (mmproj) next to {model.name}")
        self.model_name = model.name
        self.multilingual = True
        self.language = "en"
        hot = (cfg["stt"].get("hotwords") or "").strip()
        self.context = self.CONTEXT + (", " + hot if hot else "")
        self.session = requests.Session()
        self.port = free_port(self.PORT)
        self.url = f"http://127.0.0.1:{self.port}/v1/chat/completions"
        DATA.mkdir(exist_ok=True)
        threads = str(max(2, min(8, (os.cpu_count() or 4) - 2)))
        with open(DATA / "qwen-asr.log", "w", encoding="utf-8", errors="ignore") as log:
            self.proc = spawn([str(exe), "-m", str(model), "--mmproj", str(proj), "--host", "127.0.0.1",
                               "--port", str(self.port), "-c", "4096", "-np", "1", "-ngl", "0", "-dev", "none",
                               "-t", threads],
                              cwd=str(exe.parent), stdout=log, stderr=subprocess.STDOUT)
        t = time.monotonic()
        while time.monotonic() - t < 120:
            if self.proc.poll() is not None:
                raise RuntimeError("Qwen ears stopped right away (see data/qwen-asr.log)")
            try:
                if self.session.get(f"http://127.0.0.1:{self.port}/health", timeout=1).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.3)
        else:
            self.close()
            raise RuntimeError("Qwen ears took too long to start (see data/qwen-asr.log)")
        self(np.zeros(RATE, dtype=np.float32))  # warm-up: the first line reads the projector in
        print(f"[capri] ears: Qwen3-ASR {model.name} on the CPU, ready in {time.monotonic() - t:.1f}s")

    def __call__(self, audio: np.ndarray, language: str = "", hint: str = "") -> str:
        """Same as Transcriber. Qwen picks the language itself; `language` / `hint` aren't needed to hear it right."""
        buf = self.io.BytesIO()
        with self.wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        clip = self.b64.b64encode(buf.getvalue()).decode("ascii")
        seconds = len(audio) / RATE
        try:
            r = self.session.post(self.url, timeout=60, json={
                "messages": [{"role": "system", "content": self.context},
                             {"role": "user", "content": [{"type": "input_audio",
                                                           "input_audio": {"data": clip, "format": "wav"}}]}],
                "temperature": 0, "max_tokens": int(48 + 12 * seconds)})
            out = r.json()["choices"][0]["message"].get("content") or ""
        except Exception as e:
            print(f"[capri] Qwen ears: {e}")
            return ""
        heard, text = qwen_text(out)
        if heard:
            self.language = heard
        if hallucination(text, seconds):
            return ""
        return text

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()


def qwen_text(out: str) -> tuple[str, str]:
    """'language English<asr_text>Hey Capri.' -> ('en', 'Hey Capri.'). ('', '') for silence ('language None')."""
    m = re.match(r"\s*language\s+(\w+)\s*<asr_text>(.*)", out or "", re.S)
    if not m:
        return "", " ".join((out or "").replace("<asr_text>", " ").split())
    if m.group(1).lower() == "none":
        return "", ""
    return _code(m.group(1)), " ".join(m.group(2).split())


def hallucination(text: str, seconds: float, hotwords: str | None = None) -> bool:
    """Whisper on noise invents text: the hotword list on repeat, one word over and over, more words than anyone
    can say in that time, or its favourite silence filler."""
    words = re.findall(r"[\w']+", text.lower())  # any alphabet: Russian lines count as words too
    if not words:
        return False
    if len(words) / max(seconds, 0.3) > 6.5:
        return True
    if re.fullmatch(r"\W*(?:thanks? (?:you )?for watching|you|bye|\.+)\W*", text.lower()):
        return True
    run = best = 1
    for a, b in zip(words, words[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    if best >= 5 or (len(words) >= 6 and len(set(words)) / len(words) < 0.3):
        return True
    hot = set(re.findall(r"[\w']+", (hotwords or "").lower()))
    return bool(hot) and len(words) >= 2 and set(words) <= hot


def list_inputs() -> list[str]:
    """Real microphones (MME names, like Windows' Sound settings), without the mapper / virtual duplicates."""
    import sounddevice as sd

    try:
        mme = next((i for i, a in enumerate(sd.query_hostapis()) if a["name"] == "MME"), None)
        out = []
        for d in sd.query_devices():
            if d["max_input_channels"] > 0 and (mme is None or d["hostapi"] == mme) and "Sound Mapper" not in d["name"]:
                if d["name"] not in out:
                    out.append(d["name"])
        return out
    except Exception as e:
        print(f"[capri] can't list mics: {e}")
        return []


def resolve_input(name: str):
    if not name:
        return None
    import sounddevice as sd

    try:
        mme = next((i for i, a in enumerate(sd.query_hostapis()) if a["name"] == "MME"), None)
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and (mme is None or d["hostapi"] == mme) and name.lower() in d["name"].lower():
                return i
    except Exception as e:
        print(f"[capri] can't look up mic {name!r}: {e}")
    print(f"[capri] mic {name!r} not found, using the Windows default")
    return None


class _Vad:
    """Silero VAD per 512-sample block (works at any mic volume), or a loudness gate if it won't load."""

    def __init__(self, thresh: float):
        self.thresh = thresh
        self.model = None
        try:
            import torch
            from silero_vad import load_silero_vad

            self.model = load_silero_vad(onnx=True)
            self.torch = torch
        except Exception as e:
            print(f"[capri] silero VAD unavailable ({e}), using the loudness gate")

    def __call__(self, block: np.ndarray) -> bool:
        if self.model is not None:
            return float(self.model(self.torch.from_numpy(block.copy()), RATE).item()) > 0.5
        return float(np.sqrt(np.mean(block ** 2))) > self.thresh


class Listener(threading.Thread):
    """mode: "hold" (record while the talk key is held), "auto" (voice activity), "off".
    Callbacks run on this thread: on_text(str), on_hearing(bool), on_level(float), on_ready(state)."""

    def __init__(self, cfg: dict, settings, on_text, on_hearing, on_level=None, on_ready=None):
        super().__init__(daemon=True)
        self.cfg, self.s = cfg, settings
        self.on_text, self.on_hearing = on_text, on_hearing
        self.on_level = on_level or (lambda v: None)
        self.on_ready = on_ready or (lambda state: None)
        self.muted = False  # set while she talks in auto mode, so she doesn't hear herself
        self.stopped = False
        self.reopen = False
        self.stt = None
        self.hint = ""  # the language you've been talking lately (the app keeps it fresh); helps short lines

    @property
    def mode(self) -> str:
        return self.s["ears"]

    @property
    def language(self) -> str:
        """The language picked in setup / her Language menu ("" = Auto)."""
        code = str(self.s.get("ui_lang", "auto") or "").lower()
        return code if code in LANGS else ""

    @property
    def multilingual(self) -> bool:
        return bool(getattr(self.stt, "multilingual", True))

    def run(self):
        try:
            import importlib.util

            from ..portable import find_exe

            from ..portable import ears_models, pick_ears, qwen_ears

            picked = pick_ears(ears_models(), self.cfg["stt"].get("whisper_model") or "")
            if picked is not None and qwen_ears(picked.name):  # Qwen3-ASR picked: it runs on llama-server
                self.stt = QwenASR(self.cfg, picked)
            else:
                engine = WhisperCpp if self.cfg["stt"].get("engine") == "whisper_cpp" else Transcriber
                if engine is Transcriber:
                    if importlib.util.find_spec("faster_whisper") is None:  # start.bat's Python: whisper.cpp instead
                        engine = WhisperCpp
                elif find_exe("whisper-server") is None and importlib.util.find_spec("faster_whisper") is not None:
                    engine = Transcriber  # no whisper.cpp package (Kali / Debian): faster-whisper from pip instead
                self.stt = engine(self.cfg)
        except Exception as e:
            print(f"[capri] ears failed to load: {e}")
            self.on_ready("fail")
            return
        vad = _Vad(self.cfg["stt"]["energy_threshold"])
        while not self.stopped:
            self.reopen = False
            if not self._listen(vad) and not self.reopen:
                time.sleep(2)

    def _listen(self, vad) -> bool:
        import sounddevice as sd

        mic = self.s["mic"] or self.cfg["stt"].get("input_device") or ""
        try:
            stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK, device=resolve_input(mic))
        except Exception as e:
            print(f"[capri] mic {mic or 'default'} failed to open: {e}")
            self.on_ready("fail")
            return False
        print(f"[capri] mic open ({mic or 'system default'})")
        self.on_ready("ok")
        silence = self.cfg["stt"]["silence_seconds"]
        buf: list[np.ndarray] = []
        speaking = False
        last_voice = 0.0
        tail = 0
        n = 0
        with stream:
            while not self.stopped and not self.reopen:
                try:
                    block, _ = stream.read(BLOCK)
                except Exception as e:  # the device went away (PipeWire restart, unplugged mic...)
                    print(f"[capri] mic {mic or 'default'} read failed: {e}")
                    self.on_ready("fail")
                    return False
                block = block[:, 0]
                n += 1
                if n % 3 == 0:
                    self.on_level(min(1.0, float(np.sqrt(np.mean(block ** 2))) * 12))
                mode = self.mode
                if mode == "off" or (self.muted and mode == "auto"):
                    if speaking:
                        self.on_hearing(False)
                    buf, speaking = [], False
                    continue
                done = False
                if mode == "hold":
                    try:
                        keys = hotkey.parse(self.s["talk_key"])
                    except ValueError:
                        keys = hotkey.parse("ctrl+1")
                    if hotkey.held(keys):
                        if not speaking:
                            self.on_hearing(True)
                            buf = buf[-4:]  # ~0.1 s from just before the press, so the first word isn't clipped
                        speaking, tail = True, 0
                        buf.append(block)
                        continue
                    if speaking:
                        buf.append(block)
                        tail += 1
                        done = tail >= 4  # ~0.13 s after letting go: the last syllable
                    else:
                        buf = (buf + [block])[-4:]
                else:
                    now = time.monotonic()
                    if vad(block):
                        if not speaking:
                            self.on_hearing(True)
                            buf = buf[-6:]
                        speaking, last_voice = True, now
                    if speaking:
                        buf.append(block)
                    else:
                        buf = (buf + [block])[-6:]
                    done = speaking and now - last_voice > silence
                if done:
                    speaking = False
                    self.on_hearing(False)
                    audio = np.concatenate(buf)
                    buf = []
                    if len(audio) < RATE * 0.35:
                        continue
                    t = time.monotonic()
                    text = self.stt(audio, language=self.language, hint=self.hint)
                    print(f"[capri] heard ({time.monotonic() - t:.2f}s for {len(audio) / RATE:.1f}s): {text!r}")
                    if text and len(text.strip(" .")) > 1:
                        self.on_text(text)
        return True
