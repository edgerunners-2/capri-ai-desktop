"""LLM backends. All of them take an OpenAI-style message list and return the reply text."""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import threading
from pathlib import Path

# One CUDA user at a time: llama.cpp and faster-whisper (ctranslate2) running at once in one process is
# the most likely cause of the native crashes (0xc0000005 / 0xc0000374) seen on the PC.
GPU_LOCK = threading.RLock()


class LLM:
    supports_tools = False  # True on the servers that take OpenAI-style "tools" and answer with tool_calls

    def chat(self, messages: list[dict], temperature: float | None = None, max_tokens: int | None = None,
             avoid: list[str] | None = None, raw: bool = False) -> str:
        """avoid: words to push down (the openers she keeps reusing); backends without logit bias ignore it.
        raw=True: the messages carry tool_calls/tool turns: send them as they are, don't fold them into plain turns."""
        raise NotImplementedError

    def stream(self, messages: list[dict], temperature: float | None = None, max_tokens: int | None = None,
               avoid: list[str] | None = None, raw: bool = False):
        """Yields the reply in pieces as it's written."""
        yield self.chat(messages, temperature, max_tokens, avoid, raw)

    def chat_tools(self, messages: list[dict], tools: list[dict], temperature: float | None = None,
                   max_tokens: int | None = None):
        """OpenAI-style tool calling -> {"content": str, "tool_calls": [...]} (content kept, thinking dropped).
        None on backends that can't do tools: the agent keeps its regex-intent flow there."""
        return None


def _alternate(messages: list[dict]) -> list[dict]:
    """Mistral-style templates only take an optional leading system message, then strict user/assistant
    turns. Fold anything else in so the template never raises."""
    out: list[dict] = []
    for m in messages:
        role = m["role"] if m["role"] in ("user", "assistant") else "user"
        if out and out[-1]["role"] == role:
            out[-1] = {"role": role, "content": out[-1]["content"] + "\n\n" + m["content"]}
        else:
            out.append({"role": role, "content": m["content"]})
    if out and out[0]["role"] == "assistant":
        out.insert(0, {"role": "user", "content": "(start)"})
    return out


def _find_gguf(cfg: dict) -> str:
    if cfg["llm"].get("model_path"):
        return cfg["llm"]["model_path"]
    if cfg.get("edition") == "dev":  # the portable build keeps its brains in its own models\llm
        from ..portable import brains

        hits = brains()
        if hits:
            return str(hits[0])
    llm_dir = os.path.join(cfg["eru_ai_root"], "models", "llm")
    hits = sorted(glob.glob(os.path.join(llm_dir, "**", "*.gguf"), recursive=True))
    if not hits:
        raise FileNotFoundError(f"No .gguf model found under {llm_dir}. Set llm.model_path in config.yaml.")
    return hits[0]


_QUANT = re.compile(r"[.-](?:I?Q\d\w*|BF16|F16|F32)(?:[.-].*)?\.gguf$|\.gguf$", re.I)


def mmproj_for(model: str) -> str | None:
    """The vision projector that belongs to a GGUF: 'X.BF16-mmproj.gguf' next to 'X.Q4_K_M.gguf', or the only mmproj
    in a folder that holds just that one model."""
    m = Path(model)
    projs = [p for p in m.parent.glob("*.gguf") if "mmproj" in p.name.lower()]
    if not projs:
        return None
    base = _QUANT.sub("", m.name).lower()
    for p in projs:
        if _QUANT.sub("", p.name.lower().replace("-mmproj", "").replace("mmproj-", "")).lower() == base:
            return str(p)
    models = [p for p in m.parent.glob("*.gguf") if "mmproj" not in p.name.lower()]
    return str(projs[0]) if len(projs) == 1 and len(models) == 1 else None


def _cuda_dlls() -> None:
    """ERU AI's llama-cpp build borrows torch's CUDA DLLs; make them findable without importing torch."""
    if os.name != "nt":
        return
    import importlib.util

    spec = importlib.util.find_spec("torch")
    for loc in (spec.submodule_search_locations or []) if spec else []:
        lib = os.path.join(loc, "lib")
        if os.path.isdir(lib):
            os.add_dll_directory(lib)
            os.environ["PATH"] = lib + os.pathsep + os.environ.get("PATH", "")


class LlamaCppLLM(LLM):
    def __init__(self, cfg: dict):
        _cuda_dlls()
        from llama_cpp import Llama

        c = cfg["llm"]
        self.c = c
        path = _find_gguf(cfg)
        # Other apps share the 12 GB card (NVIDIA Broadcast, Wallpaper Engine, browsers...). If the cache doesn't fit,
        # step down instead of booting brainless: a few layers on the CPU is slower, but she still thinks.
        layers = c["n_gpu_layers"]
        # (GPU layers, 8-bit cache): the 8-bit cache (GGML_TYPE_Q8_0) halves its memory with no noticeable
        # quality loss; plain 16-bit is the fallback in case a build can't do it
        tries = [(layers, True), (layers, False)] + [(n, True) for n in (34, 28, 20) if layers < 0 or n < layers]
        err = None
        for n, q8 in tries:
            try:
                extra = {"type_k": 8, "type_v": 8} if q8 else {}
                self.model = Llama(model_path=path, n_ctx=c["n_ctx"], n_gpu_layers=n, n_batch=512,
                                   flash_attn=True, verbose=False, **extra)
                if n != layers:
                    print(f"[capri] GPU was busy: brain loaded with {n} layers on the GPU, the rest on the CPU")
                return
            except Exception as e:  # out of VRAM for the context: try the next, smaller setup
                err = e
                print(f"[capri] brain didn't load with {n} GPU layers, 8-bit cache {q8} ({e}); trying the next setup")
        raise err

    def _bias(self, avoid: list[str] | None) -> dict | None:
        """Words she keeps opening with get pushed down (the first token of each, with and without a leading space).
        Only distinctive words are passed in, so ordinary words stay usable mid-sentence."""
        if not avoid:
            return None
        out = {}
        for word in avoid:
            for variant in (word, " " + word):
                toks = self.model.tokenize(variant.encode("utf-8"), add_bos=False, special=False)
                if not toks:
                    continue
                piece = self.model.detokenize(toks[:1]).decode("utf-8", "ignore").strip()
                # only a token that's mostly this word: "Naw" -> "N" + "aw" would push down every N-word
                if len(piece) >= 3 and len(piece) >= 0.6 * len(word):
                    out[toks[0]] = -4.0
        return out or None

    def _args(self, messages, temperature, max_tokens, avoid):
        return dict(
            messages=_alternate(messages),
            temperature=self.c["temperature"] if temperature is None else temperature,
            max_tokens=max_tokens or self.c["max_tokens"],
            top_p=0.92,
            min_p=0.05,  # drops the junk tail that makes small models say random things
            repeat_penalty=1.05,
            presence_penalty=0.3,  # within a reply: don't circle back to the same words
            frequency_penalty=0.3,
            logit_bias=self._bias(avoid),
        )

    def chat(self, messages, temperature=None, max_tokens=None, avoid=None, raw=False):
        with GPU_LOCK:
            out = self.model.create_chat_completion(**self._args(messages, temperature, max_tokens, avoid))
        return out["choices"][0]["message"]["content"].strip()

    def stream(self, messages, temperature=None, max_tokens=None, avoid=None, raw=False):
        with GPU_LOCK:
            for chunk in self.model.create_chat_completion(**self._args(messages, temperature, max_tokens, avoid), stream=True):
                piece = chunk["choices"][0]["delta"].get("content")
                if piece:
                    yield piece


class OpenAICompatLLM(LLM):
    supports_tools = True  # the API takes tools; function-calling depends on the model behind it

    def __init__(self, cfg: dict):
        import requests

        self.c = cfg["llm"]
        self.session = requests.Session()

    def _post(self, body: dict):
        r = self.session.post(self.c["base_url"].rstrip("/") + "/chat/completions",
                              headers={"Authorization": f"Bearer {self.c['api_key']}"}, json=body, timeout=120)
        r.raise_for_status()
        return r

    def chat(self, messages, temperature=None, max_tokens=None, avoid=None, raw=False):
        r = self._post({"model": self.c["model"], "messages": list(messages) if raw else _alternate(messages),
                        "temperature": self.c["temperature"] if temperature is None else temperature,
                        "max_tokens": max_tokens or self.c["max_tokens"]})
        return r.json()["choices"][0]["message"]["content"]

    def chat_tools(self, messages, tools, temperature=None, max_tokens=None):
        r = self._post({"model": self.c["model"], "messages": list(messages), "tools": tools, "tool_choice": "auto",
                        "temperature": 0.45 if temperature is None else temperature, "max_tokens": max_tokens or 400})
        msg = r.json()["choices"][0]["message"]
        return {"content": (msg.get("content") or "").strip(), "tool_calls": msg.get("tool_calls") or []}


LMS = Path.home() / ".lmstudio" / "bin" / ("lms.exe" if os.name == "nt" else "lms")


class BrainDown(RuntimeError):
    """Her brain's server stopped answering and couldn't be brought back. The app turns this into an in-character
    line; the real reason is in the log."""


# called (from the worker thread) when her brain server died and she's starting it again, so the app can say so
ON_REVIVE = lambda: None  # noqa: E731


def free_port(preferred: int) -> int:
    """`preferred` when nothing is listening on it, else any free port. A second Capri (or another app) on 8089 would
    otherwise answer her health check, and her replies would die with it."""
    import socket

    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred
_THINK = re.compile(r"<think>.*?(?:</think>|$)", re.S)


def _guard(lines, dead):
    """The server dying halfway through a reply: a BrainDown, not a raw connection error."""
    import requests

    try:
        yield from lines
    except (*dead, requests.exceptions.ChunkedEncodingError) as e:
        raise BrainDown(str(e)) from e


class LocalServerLLM(LLM):
    """Talks to a local OpenAI-style server that runs the model for her (LM Studio, llama.cpp's llama-server): same
    sampling as her llama-cpp brain, replies streamed, any <think> text dropped."""

    supports_tools = True  # the servers take "tools" through the model's chat template (llama-server runs with --jinja)

    def __init__(self, cfg: dict, base_url: str, name: str):
        import requests

        self.c = dict(cfg["llm"])
        self.url = base_url.rstrip("/") + "/v1/chat/completions"
        self.name = name
        self.session = requests.Session()
        self.model = self  # the brain menu closes .model when it swaps brains
        self._dead = (requests.ConnectionError, requests.Timeout)
        self._revive_lock = threading.Lock()

    def close(self):
        pass

    def _revive(self) -> bool:
        """Bring the server back after it stopped answering. -> True when it's up again."""
        return False

    def _call(self, send):
        """send() once; if the server is gone, bring it back and try one more time. A timeout is not a crash: the brain
        is just slow (a weak PC reading her long prompt), and sending it all again only doubled the wait, over and over,
        until people sat 20 minutes watching her think. So it ends the reply instead."""
        import requests

        try:
            return send()
        except requests.ReadTimeout as e:  # (a connect timeout is the server being gone: that one is revived below)
            raise BrainDown(f"her brain took too long to answer ({e})") from e
        except self._dead as e:
            print(f"[capri] her brain server isn't answering ({type(e).__name__}: {e})")
            with self._revive_lock:
                ok = self._revive()
            if not ok:
                raise BrainDown(str(e)) from e
        try:
            return send()
        except self._dead as e:
            raise BrainDown(str(e)) from e

    def _extra(self, avoid) -> dict:
        return {}

    def _body(self, messages, temperature, max_tokens, avoid, stream=False, raw=False):
        return {"model": self.name, "messages": list(messages) if raw else _alternate(messages), "stream": stream,
                "temperature": self.c["temperature"] if temperature is None else temperature,
                "max_tokens": max_tokens or self.c["max_tokens"], "top_p": 0.92, "min_p": 0.05,
                "repeat_penalty": 1.05, "presence_penalty": 0.3, "frequency_penalty": 0.3, **self._extra(avoid)}

    def chat(self, messages, temperature=None, max_tokens=None, avoid=None, raw=False):
        def send():
            r = self.session.post(self.url, timeout=120, json=self._body(messages, temperature, max_tokens, avoid,
                                                                         raw=raw))
            r.raise_for_status()
            return r

        r = self._call(send)
        return _THINK.sub("", r.json()["choices"][0]["message"].get("content") or "").strip()

    def chat_tools(self, messages, tools, temperature=None, max_tokens=None):
        """Native tool calling: llama-server / LM Studio run the model's tool template and answer with tool_calls.
        The messages ride as-is (tool turns need their ids), so one job per call, no streaming here."""
        body = {**self._body(messages, 0.45 if temperature is None else temperature, max_tokens or 400, None,
                             raw=True), "tools": tools, "tool_choice": "auto", "parallel_tool_calls": False}

        def send():
            r = self.session.post(self.url, timeout=180, json=body)
            r.raise_for_status()
            return r

        r = self._call(send)
        msg = r.json()["choices"][0]["message"]
        return {"content": _THINK.sub("", msg.get("content") or "").strip(), "tool_calls": msg.get("tool_calls") or []}

    def stream(self, messages, temperature=None, max_tokens=None, avoid=None, raw=False):
        body = self._body(messages, temperature, max_tokens, avoid, stream=True, raw=raw)

        def send():  # only the connect is retried: once words are out, a dropped stream ends the reply
            r = self.session.post(self.url, timeout=120, stream=True, json=body)
            r.raise_for_status()
            return r

        with self._call(send) as r:
            r.encoding = "utf-8"  # the stream has no charset, so requests would read it as Latin-1: ’ became â€™
            thinking = False
            for line in _guard(r.iter_lines(decode_unicode=True), self._dead):
                if not line or not line.startswith("data:") or line.strip() == "data: [DONE]":
                    continue
                choices = json.loads(line[5:]).get("choices") or [{}]
                piece = (choices[0].get("delta") or {}).get("content")
                if not piece:
                    continue
                if "<think>" in piece:
                    thinking = True
                if thinking:
                    if "</think>" in piece:
                        thinking, piece = False, piece.split("</think>", 1)[1]
                    else:
                        continue
                if piece:
                    yield piece


class LMStudioLLM(LocalServerLLM):
    """A GGUF her own llama-cpp is too old for (Qwen3.5 and newer), run on LM Studio's newer engine instead: it gets
    hard-linked into LM Studio's models (no copy), loaded under the name capri-brain, and she talks to LM Studio's
    local server."""

    NAME = "capri-brain"

    def __init__(self, cfg: dict, path: str):
        super().__init__(cfg, "http://127.0.0.1:1234", self.NAME)
        if not LMS.exists():
            raise RuntimeError("this model needs LM Studio (not found)")
        self._lms("server", "start")
        self._lms("unload", self.NAME)
        repo = "capri/" + Path(path).parent.name
        self._import(path, repo)
        self._eyes(path, repo)
        key = self.key = self._key(path)
        out = self._load()
        if self.NAME not in self._lms("ps"):
            raise RuntimeError(f"LM Studio couldn't load {Path(path).name}: {out[-300:]}")
        self.vision = self._info(key).get("vision") is True
        if self.vision:
            print(f"[capri] her brain can see (LM Studio): {Path(path).name}")

    def _load(self) -> str:
        return self._lms("load", self.key, "--gpu", "max", "-c", str(self.c["n_ctx"]), "--identifier", self.NAME, "-y")

    def _revive(self) -> bool:
        """LM Studio was closed or unloaded her: start its server and load her brain again."""
        print("[capri] LM Studio stopped answering; starting its server and loading her brain again")
        try:
            ON_REVIVE()
        except Exception:
            pass
        self._lms("server", "start")
        if self.NAME not in self._lms("ps"):
            self._load()
        return self.NAME in self._lms("ps")

    def _import(self, path: str, repo: str) -> str:
        """Links the file into LM Studio's models (never a copy): a hard link when it's on the same drive, else a
        symbolic one, so a model kept on a fast SSD is still read from there."""
        out = self._lms("import", path, "--hard-link", "-y", "--user-repo", repo)
        if re.search(r"error|fail|cross-device|EXDEV", out, re.I):
            out = self._lms("import", path, "--symbolic-link", "-y", "--user-repo", repo)
        return out

    def _eyes(self, path: str, repo: str):
        """Her vision projector goes next to the brain in LM Studio too. LM Studio only knows it's one when the name
        starts with 'mmproj', so the link it makes gets that name (the original file is never touched)."""
        proj = mmproj_for(path)
        if not proj:
            return
        out = self._import(proj, repo)
        m = re.search(r"(?:link|copied|moved)[^\n]*? (?:at|to) (.+\.gguf)", out, re.I)
        link = Path(m.group(1).strip()) if m else None
        if link and link.exists() and not link.name.lower().startswith("mmproj"):
            stem = _QUANT.sub("", link.name.lower().replace("-mmproj", "").replace("mmproj-", ""))
            target = link.with_name("mmproj-" + link.name[:len(stem)] + ".gguf")
            if not target.exists():
                link.rename(target)
            elif link != Path(proj):  # already set up last time: drop the fresh duplicate link (the original stays)
                link.unlink()

    def _info(self, key: str) -> dict:
        try:
            return next((m for m in json.loads(self._lms("ls", "--json")) if m.get("modelKey") == key), {})
        except ValueError:
            return {}

    def _extra(self, avoid) -> dict:
        # thinking models (Gemma 4, Nemotron, Qwen3.5) would spend her whole reply budget thinking: switch it off
        return {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none"}

    @staticmethod
    def _lms(*args) -> str:
        r = subprocess.run([str(LMS), *args], capture_output=True, text=True, encoding="utf-8", errors="ignore",
                           timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or "") + (r.stderr or "")

    def _key(self, path: str) -> str:
        """LM Studio's key for the imported file."""
        import time

        name = Path(path).stem.lower()
        for _ in range(5):  # a model imported a moment ago can take a second to show up in the list
            try:
                for m in json.loads(self._lms("ls", "--json")):
                    if name in (m.get("path", "") + " " + m.get("modelKey", "")).lower():
                        return m["modelKey"]
            except ValueError:
                pass
            time.sleep(1)
        return name

    def close(self):
        self._lms("unload", self.NAME)


class LMStudioServer(LocalServerLLM):
    """LM Studio as her brain, picked from her Brain menu or in setup: whatever model you loaded in LM Studio (any
    size, any family, your GPU or someone's server), through its local server. Nothing to download for Capri."""

    ID = "lm-studio"  # what her Brain menu saves as the brain (like "claude-code")

    def __init__(self, cfg: dict):
        base = ((cfg.get("lmstudio") or {}).get("url") or "http://127.0.0.1:1234").rstrip("/")
        super().__init__(cfg, base, "")
        self.base = base
        if not self._models() and LMS.exists():  # installed but its server is off: switch it on
            LMStudioLLM._lms("server", "start")
        self.name = self._pick()

    def _models(self) -> list[dict]:
        """The models LM Studio has, loaded first. [] when its server isn't answering."""
        for path in ("/api/v0/models", "/v1/models"):
            try:
                r = self.session.get(self.base + path, timeout=5)
                if r.ok:
                    ms = [m for m in r.json().get("data", []) if m.get("type", "llm") in ("llm", "vlm")]
                    return sorted(ms, key=lambda m: m.get("state") != "loaded")
            except Exception:
                continue
        return []

    def _pick(self) -> str:
        """The model she talks to: the one loaded in LM Studio (else the first it has, which it loads on the first
        message)."""
        ms = self._models()
        if not ms:
            raise RuntimeError("LM Studio isn't answering: open LM Studio, load a model and switch its local server "
                               "on (Developer tab), then pick LM Studio in her Brain menu again")
        pick = (self.c.get("lmstudio_model") or "").lower()
        m = next((m for m in ms if pick and pick in m.get("id", "").lower()), ms[0])
        self.vision = m.get("type") == "vlm"
        print(f"[capri] her brain is LM Studio's {m.get('id')} ({m.get('state', 'ready')})"
              + (", and it can see" if self.vision else ""))
        return m["id"]

    def _revive(self) -> bool:
        """LM Studio was closed or the model unloaded: start its server and talk to whatever is loaded now."""
        try:
            ON_REVIVE()
        except Exception:
            pass
        if LMS.exists():
            LMStudioLLM._lms("server", "start")
        try:
            self.name = self._pick()
            return True
        except RuntimeError:
            return False

    def _extra(self, avoid) -> dict:
        # thinking models would spend her whole reply budget thinking (the 20-minute "thinking" hang): switch it off
        return {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none"}


class LlamaServerLLM(LocalServerLLM):
    """The portable build's brain: llama.cpp's own llama-server running the GGUF (any architecture its release knows,
    on Vulkan or CUDA), started and stopped with her. Thinking is switched off for models that do it. If the GGUF
    came with a vision projector (an mmproj file next to it), that loads too and she can see screenshots."""

    PORT = 8089  # the one she asks for; another free port when something else already sits on it

    def __init__(self, cfg: dict):
        from ..config import DATA
        from ..portable import find_exe

        self.port = free_port(self.PORT)
        super().__init__(cfg, f"http://127.0.0.1:{self.port}", "capri-brain")
        self.exe = find_exe("llama-server")
        if self.exe is None:
            raise RuntimeError("her brain engine (llama.cpp) isn't downloaded yet: run Capri's setup")
        self.path = _find_gguf(cfg)
        DATA.mkdir(exist_ok=True)
        self.log = DATA / "llama-server.log"
        self.base = f"http://127.0.0.1:{self.port}"
        self.vision = False
        proj = mmproj_for(self.path)
        # all on the GPU, or as much as fits, or the CPU; with her eyes first, then without (a broken or half-downloaded
        # mmproj never keeps her brain from starting)
        self.setups = ([("999", proj), ("24", proj)] if proj else []) + [("999", None), ("24", None), ("0", None)]
        self.step = 0
        err = self._start(0)
        if err is not None:
            raise RuntimeError(f"llama-server couldn't load {Path(self.path).name}: {err}")

    def _start(self, first: int) -> str | None:
        """Start llama-server with setups[first], else the next ones down. -> None once it answers, else the end of
        its log."""
        import time

        err = ""
        for i in range(first, len(self.setups)):
            gpu_layers, mm = self.setups[i]
            if i > first or getattr(self, "proc", None) is not None:
                self.port = free_port(self.port)  # the old one can linger on its port for a moment
                self.base = f"http://127.0.0.1:{self.port}"
                self.url = self.base + "/v1/chat/completions"
            from ..portable import spawn

            with open(self.log, "w", encoding="utf-8", errors="ignore") as log:
                self.proc = spawn(
                    [str(self.exe), "-m", self.path, "--host", "127.0.0.1", "--port", str(self.port),
                     "-c", str(self.c["n_ctx"]), "-ngl", gpu_layers, "--jinja", "--reasoning-budget", "0",
                     # one slot: her whole context and the cache of her long prompt stay together (4 slots split the
                     # context and each re-read her prompt); cache-reuse keeps it when old chat scrolls off the top
                     "-np", "1", "--cache-reuse", "256",
                     # the CPU for real: with 0 layers, llama.cpp still put its work buffers on the GPU, so a weak one
                     # (Intel UHD) ran out of memory and even the CPU fallback never started
                     *(["-dev", "none"] if gpu_layers == "0" else []),
                     "--alias", "capri-brain", *(["--mmproj", mm] if mm else [])],
                    cwd=str(self.exe.parent), stdout=log, stderr=subprocess.STDOUT)
            t = time.monotonic()
            while time.monotonic() - t < 240:
                if self.proc.poll() is not None:
                    break
                if self._healthy():
                    if gpu_layers != "999":
                        print(f"[capri] GPU was busy: brain running with {gpu_layers} layers on the GPU")
                    self.vision = mm is not None
                    caps = self._caps()
                    if caps:  # the server's own word on this template: tools on or off (jinja, template support)
                        self.supports_tools = bool(caps.get("supports_tools"))
                        if not self.supports_tools:
                            print("[capri] this brain template can't do tool calls: her hands stay on the old flow")
                    if mm:
                        print(f"[capri] her brain can see: {Path(mm).name}")
                    self.step = i
                    self.started = time.monotonic()
                    return None
                time.sleep(0.5)
            self.close()
            err = self._log_tail()
            print(f"[capri] llama-server didn't start with {gpu_layers} GPU layers{' + eyes' if mm else ''}; "
                  "trying the next setup")
        return err

    def _healthy(self) -> bool:
        try:
            return self.session.get(self.base + "/health", timeout=2).status_code == 200
        except Exception:
            return False

    def _caps(self) -> dict:
        """The server's chat_template_caps from /props: {'supports_tools': True, ...} when it reports them.
        llama-server (with --jinja) tells the truth about the loaded template; LM Studio just 404s here."""
        try:
            return self.session.get(self.base + "/props", timeout=3).json().get("chat_template_caps") or {}
        except Exception:
            return {}

    def _log_tail(self, n: int = 400) -> str:
        try:
            return self.log.read_text(encoding="utf-8", errors="ignore")[-n:]
        except OSError:
            return ""

    def _revive(self) -> bool:
        """llama-server stopped (crashed on the GPU, killed by an antivirus, ...): log why and start it again. A crash
        steps one setup down (fewer GPU layers, then without eyes, then the CPU), so it doesn't fall over again."""
        import time

        proc = getattr(self, "proc", None)
        if proc is not None and proc.poll() is None and self._healthy():
            return True  # another reply already brought it back
        code = proc.poll() if proc is not None else None
        print(f"[capri] her brain server stopped (exit code {code}). Its last words:\n{self._log_tail(1200)}")
        crashed_fast = time.monotonic() - getattr(self, "started", 0) < 600
        first = min(self.step + 1, len(self.setups) - 1) if crashed_fast else self.step
        try:
            ON_REVIVE()
        except Exception:
            pass
        self.close()
        err = self._start(first)
        if err is None:
            print(f"[capri] brain server back up on port {self.port}")
            return True
        print(f"[capri] couldn't bring her brain server back: {err}")
        return False

    def _extra(self, avoid) -> dict:
        """Words she keeps opening with get pushed down (the first token of each, if it's mostly that word). Thinking
        is switched off in the chat template too: Gemma 4 / Nemotron otherwise write their thinking into the reply."""
        off = {"chat_template_kwargs": {"enable_thinking": False}}
        if not avoid:
            return off
        bias = []
        for word in avoid:
            for variant in (word, " " + word):
                try:
                    toks = self.session.post(self.base + "/tokenize", json={"content": variant}, timeout=5).json()["tokens"]
                    if not toks:
                        continue
                    piece = self.session.post(self.base + "/detokenize", json={"tokens": toks[:1]},
                                              timeout=5).json()["content"].strip()
                except Exception:
                    continue
                if len(piece) >= 3 and len(piece) >= 0.6 * len(word):
                    bias.append([toks[0], -4.0])
        return {**off, "logit_bias": bias} if bias else off

    def close(self):
        proc = getattr(self, "proc", None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except Exception:
                proc.kill()


class NoBrain(LLM):
    """No AI picked ("just her"): nothing loads, nothing thinks. Her abilities still work; the agent answers
    with short built-in lines instead of asking a model."""

    no_brain = True
    vision = False

    def chat(self, messages, temperature=None, max_tokens=None, avoid=None, raw=False):
        return ""

    def close(self):
        pass


class OfflineLLM(LLM):
    """Used when nothing else loads, so the UI still runs. Her abilities still work; the agent answers chat with an
    in-character line (agent.trouble) instead of the raw error, which goes to the log."""

    offline = True
    vision = False

    def __init__(self, err: Exception):
        self.err = err

    def chat(self, messages, temperature=None, max_tokens=None, avoid=None, raw=False):
        return ""

    def close(self):
        pass


def create(cfg: dict) -> LLM:
    pick = (cfg["llm"].get("model_path") or "").lower()
    if pick == "none":
        return NoBrain()
    backend = cfg["llm"]["backend"]
    if backend == "llama_cpp":  # no llama-cpp-python (run from source, setup wrote a config.yaml without a backend)
        import importlib.util

        from ..portable import find_exe

        if importlib.util.find_spec("llama_cpp") is None and find_exe("llama-server"):
            backend = "server"
    try:
        if pick == "claude-code":  # picked from her Brain menu: Claude Code's CLI thinks for her
            from .claude_code import ClaudeCodeLLM

            return ClaudeCodeLLM(cfg)
        if pick == LMStudioServer.ID:  # picked from her Brain menu: any model running in LM Studio
            return LMStudioServer(cfg)
        if backend == "llama_cpp":
            from ..config import DATA

            # models her llama-cpp already failed on go straight to LM Studio (no minutes of retries every start)
            seen = DATA / "lmstudio_models.json"
            try:
                needs_lms = set(json.loads(seen.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                needs_lms = set()
            path = cfg["llm"].get("model_path", "")
            if path in needs_lms and LMS.exists():
                return LMStudioLLM(cfg, _find_gguf(cfg))
            try:
                return LlamaCppLLM(cfg)
            except Exception as e:
                # too new for her llama-cpp (e.g. Qwen3.5: "unknown model architecture"): LM Studio's engine can run it
                if not LMS.exists():
                    raise
                print(f"[capri] her llama-cpp can't run this model ({e}); loading it in LM Studio instead")
                brain = LMStudioLLM(cfg, _find_gguf(cfg))
                try:
                    DATA.mkdir(exist_ok=True)
                    seen.write_text(json.dumps(sorted(needs_lms | {path})), encoding="utf-8")
                except OSError:
                    pass
                return brain
        if backend == "openai":
            return OpenAICompatLLM(cfg)
        if backend == "server":  # the portable build: llama.cpp's llama-server from its engines folder
            return LlamaServerLLM(cfg)
        raise ValueError(f"unknown llm backend {backend!r}")
    except Exception as e:  # keep the overlay alive even without a brain
        print(f"[capri] LLM load failed: {e}")
        return OfflineLLM(e)
