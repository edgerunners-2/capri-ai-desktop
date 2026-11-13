"""Her clips: "capri, clip that" saves the last 30 s / 1 min / 2 min of your screen, with your PC's sound, as an
MP4. While clips are on (Settings > Clips), ffmpeg keeps recording the screen in the background into a short loop of
2-second pieces (your graphics card's own video encoder when it has one, so games barely notice), and the speakers
are kept in memory the same way. A clip glues the newest pieces together; nothing older than the loop is kept, and
the loop is wiped when clips are turned off."""
from __future__ import annotations

import collections
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np

SEG = 2  # seconds per piece of the loop (a keyframe starts each one, so pieces glue together without re-encoding)
FPS = 30


def default_dir() -> Path:
    return Path.home() / "Videos" / "Capri Clips"


def _run(cmd: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
    from ..portable import no_window

    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=timeout,
                          creationflags=no_window())


# fastest first: the graphics card's encoder (NVIDIA, AMD, Intel), Windows' own (any card), then the CPU
# each piece has to start on a keyframe (the pieces are cut there): the hardware encoders only obey that with
# forced IDR frames (Intel's QSV wrote one endless piece without it)
GOP = ["-g", str(FPS * SEG)]
ENCODERS = [
    ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p3", "-rc", "vbr", "-cq", "24", "-b:v", "0", "-forced-idr", "1"]),
    ("h264_amf", ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", "22", "-qp_p", "24",
                  "-gops_per_idr", "1"]),
    ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "24", "-forced_idr", "1"]),
    ("libx264", ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "26"]),
]


def pick_encoder(ffmpeg: str) -> tuple[str, list[str]]:
    """The first encoder that really works on this PC with these exact settings (listed isn't enough: nvenc is
    listed without an NVIDIA card). Found once, then remembered in data/clips-encoder.txt (the check takes seconds)."""
    from ..config import DATA

    memo = DATA / "clips-encoder.txt"
    try:
        name, path = memo.read_text(encoding="utf-8").split("\n")[:2]
        if path == ffmpeg:
            return next(e for e in ENCODERS if e[0] == name)
    except (OSError, ValueError, StopIteration):
        pass
    found = ENCODERS[-1]
    for name, args in ENCODERS:
        try:
            r = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                      "color=black:s=640x360:r=30:d=0.5", "-pix_fmt", "yuv420p", *args, *GOP,
                      "-force_key_frames", f"expr:gte(t,n_forced*{SEG})", "-f", "null", "-"])
            if r.returncode == 0:
                found = (name, args)
                break
        except Exception:
            continue
    try:
        DATA.mkdir(exist_ok=True)
        memo.write_text(f"{found[0]}\n{ffmpeg}", encoding="utf-8")
    except OSError:
        pass
    return found


class Speakers(threading.Thread):
    """What the PC plays (WASAPI loopback), the last `keep` seconds in memory, each block stamped with the time it
    arrived. Silence sends nothing on Windows, so a clip fills those gaps with quiet."""

    def __init__(self, keep: float):
        super().__init__(daemon=True, name="clip-audio")
        self.keep = keep
        self.rate, self.ch = 48000, 2
        self.blocks: collections.deque = collections.deque()
        self.stopped = False
        self.ok = False

    def run(self):
        try:
            import pyaudiowpatch as pa

            p = pa.PyAudio()
            lb = p.get_default_wasapi_loopback()
            self.rate, self.ch = int(lb["defaultSampleRate"]), max(1, min(2, int(lb["maxInputChannels"])))
            st = p.open(format=pa.paInt16, channels=self.ch, rate=self.rate, input=True,
                        input_device_index=lb["index"], frames_per_buffer=2048)
            self.ok = True
            while not self.stopped:
                data = st.read(2048, exception_on_overflow=False)
                now = time.time()
                self.blocks.append((now, np.frombuffer(data, dtype=np.int16).reshape(-1, self.ch)))
                while self.blocks and now - self.blocks[0][0] > self.keep:
                    self.blocks.popleft()
            st.close()
            p.terminate()
        except Exception as e:
            print(f"[capri] clips: no PC sound ({e}); clips will be silent")

    def slice(self, start: float, end: float) -> np.ndarray:
        """Samples from start to end (wall-clock seconds), quiet where nothing played."""
        n = max(0, int(round((end - start) * self.rate)))
        out = np.zeros((n, self.ch), dtype=np.int16)
        for t_end, block in list(self.blocks):
            t0 = t_end - len(block) / self.rate
            a = int(round((t0 - start) * self.rate))
            lo, hi = max(0, a), min(n, a + len(block))
            if hi > lo:
                out[lo:hi] = block[lo - a:hi - a]
        return out


class ClipRecorder:
    """start() keeps the loop going, clip() saves the end of it, stop() ends it and wipes the loop."""

    def __init__(self, ffmpeg: str | Path, seconds: int, out_dir: str | Path, work: Path):
        self.ffmpeg = str(ffmpeg)
        self.seconds = int(seconds)
        self.out_dir = Path(out_dir) if out_dir else default_dir()
        self.work = Path(work)
        self.proc: subprocess.Popen | None = None
        self.audio: Speakers | None = None
        self.encoder = ""
        self.source = ""
        self.stopped = True
        self.lock = threading.Lock()

    # -- the loop -----------------------------------------------------------------------------------------------
    def _cmd(self, source: str, enc: list[str]) -> list[str]:
        keep = self.seconds // SEG + 4  # a few more pieces than the clip needs (one is always being written)
        if source == "ddagrab":  # Desktop Duplication: the GPU hands the frames over (fast, games included)
            src = ["-f", "lavfi", "-i", f"ddagrab=output_idx=0:framerate={FPS}:draw_mouse=1"]
            vf = "hwdownload,format=bgra,scale=-2:'min(1080,ih)':flags=fast_bilinear,format=yuv420p"
        else:  # GDI: works everywhere, a bit heavier
            import ctypes

            w, h = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
            src = ["-f", "gdigrab", "-framerate", str(FPS), "-draw_mouse", "1", "-offset_x", "0", "-offset_y", "0",
                   "-video_size", f"{w}x{h}", "-i", "desktop"]
            vf = "scale=-2:'min(1080,ih)':flags=fast_bilinear,format=yuv420p"
        return [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *src, "-vf", vf, *enc,
                *GOP, "-force_key_frames", f"expr:gte(t,n_forced*{SEG})", "-an",
                "-f", "segment", "-segment_time", str(SEG), "-segment_wrap", str(keep), "-reset_timestamps", "1",
                "-segment_format", "mpegts", str(self.work / "piece%03d.ts")]

    def _launch(self) -> bool:
        from ..portable import no_window

        for source in ("ddagrab", "gdigrab"):
            for old in self.work.glob("piece*.ts"):
                old.unlink(missing_ok=True)
            log = open(self.work / "ffmpeg.log", "w", encoding="utf-8", errors="ignore")
            self.proc = subprocess.Popen(self._cmd(source, self.enc_args), stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=log, creationflags=no_window())
            for _ in range(40):  # up: the first piece shows up after ~2 s
                if self.proc.poll() is not None:
                    break
                if any(self.work.glob("piece*.ts")):
                    self.source = source
                    return True
                time.sleep(0.1)
            if self.proc.poll() is None and source == "gdigrab":
                self.source = source
                return True
            self.proc.kill()
            print(f"[capri] clips: {source} didn't start: {(self.work / 'ffmpeg.log').read_text(errors='ignore')[-300:]}")
        return False

    def start(self) -> None:
        self.work.mkdir(parents=True, exist_ok=True)
        self.encoder, self.enc_args = pick_encoder(self.ffmpeg)
        self.stopped = False
        if not self._launch():
            self.stopped = True
            raise RuntimeError("ffmpeg couldn't record the screen (see data/clips/ffmpeg.log)")
        self.audio = Speakers(self.seconds + 3 * SEG + 5)
        self.audio.start()
        threading.Thread(target=self._watch, daemon=True, name="clip-watch").start()
        print(f"[capri] clips on: last {self.seconds} s, {self.source} + {self.encoder}, saved to {self.out_dir}")

    def _watch(self):
        """ffmpeg can stop (a game switching resolution, the screen turning off): start it again."""
        while not self.stopped:
            time.sleep(2)
            if not self.stopped and self.proc is not None and self.proc.poll() is not None:
                print("[capri] clips: the recorder stopped, starting it again")
                with self.lock:
                    if not self.stopped:
                        self._launch()

    def stop(self) -> None:
        self.stopped = True
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(5)
            except Exception:
                self.proc.kill()
        if self.audio is not None:
            self.audio.stopped = True
        shutil.rmtree(self.work, ignore_errors=True)  # the loop is gone with it: nothing kept once clips are off

    @property
    def running(self) -> bool:
        return not self.stopped and self.proc is not None and self.proc.poll() is None

    # -- "clip that" --------------------------------------------------------------------------------------------
    def clip(self, seconds: int | None = None) -> Path:
        """Save the last `seconds` (the menu's length by default) as an MP4 in the clips folder. -> its path."""
        if not self.running:
            raise RuntimeError("clips aren't recording")
        want = min(int(seconds or self.seconds), self.seconds)
        time.sleep(SEG + 0.3)  # the piece being written right now finishes, so the clip ends at "clip that"
        with self.lock:
            pieces = sorted(self.work.glob("piece*.ts"), key=lambda p: p.stat().st_mtime)
            done = [p for p in pieces[:-1] if p.stat().st_size > 0]  # the newest one is still being written
            n = -(-want // SEG)  # enough pieces to cover it (rounded up)
            take = done[-n:]
            if not take:
                raise RuntimeError("nothing recorded yet, give it a few seconds")
            end = take[-1].stat().st_mtime
            stamp = time.strftime("%Y-%m-%d %H-%M-%S")
            tmp = self.work / f"clip-{stamp}"
            tmp.mkdir(exist_ok=True)
            for i, p in enumerate(take):  # copied out first: the loop keeps overwriting the pieces
                shutil.copyfile(p, tmp / f"{i:03d}.ts")
        try:
            listing = tmp / "list.txt"
            listing.write_text("".join(f"file '{(tmp / f'{i:03d}.ts').as_posix()}'\n" for i in range(len(take))),
                               encoding="utf-8")
            video = tmp / "video.ts"
            r = _run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                      "-i", str(listing), "-c", "copy", str(video)], 60)
            if r.returncode != 0:
                raise RuntimeError(f"couldn't join the clip: {r.stderr[-200:]}")
            length = len(take) * SEG
            inputs, maps = ["-i", str(video)], ["-map", "0:v"]
            if self.audio is not None and self.audio.ok:
                pcm = self.audio.slice(end - length, end)
                with wave.open(str(tmp / "sound.wav"), "wb") as w:
                    w.setnchannels(self.audio.ch)
                    w.setsampwidth(2)
                    w.setframerate(self.audio.rate)
                    w.writeframes(pcm.tobytes())
                inputs += ["-i", str(tmp / "sound.wav")]
                maps += ["-map", "1:a", "-c:a", "aac", "-b:a", "160k"]
            elif self.audio is None:  # Linux (X11Recorder): ffmpeg recorded the sound into the pieces already
                maps += ["-map", "0:a?", "-c:a", "copy"]
            self.out_dir.mkdir(parents=True, exist_ok=True)
            out = self.out_dir / f"Capri clip {stamp}.mp4"
            r = _run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *inputs, *maps, "-c:v", "copy",
                      "-shortest", "-movflags", "+faststart", str(out)], 120)
            if r.returncode != 0 or not out.exists():
                raise RuntimeError(f"couldn't save the clip: {r.stderr[-200:]}")
            return out
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def find_ffmpeg() -> Path | None:
    """Her own ffmpeg (downloaded from her menu), else one on this PC's PATH."""
    from ..portable import find_exe

    return find_exe("ffmpeg")


# -- Linux -------------------------------------------------------------------------------------------------------
# gpu-screen-recorder has a replay buffer built in (ShadowPlay for Linux: X11, KDE / GNOME Wayland through the
# screen-share portal, Hyprland, Sway; NVIDIA / AMD / Intel encoders): it keeps the last N seconds and writes them
# out on SIGUSR1. Without it, ffmpeg does the same loop as on Windows, but only on X11 (x11grab + PulseAudio).

def _gsr_cmd() -> list[str] | None:
    """How to run gpu-screen-recorder here: the package (Arch: gpu-screen-recorder, Debian: gpu-screen-recorder-cli)
    or the Flatpak."""
    exe = shutil.which("gpu-screen-recorder")
    if exe:
        return [exe]
    if shutil.which("flatpak"):
        try:
            r = subprocess.run(["flatpak", "info", "com.dec05eba.gpu_screen_recorder"], capture_output=True, timeout=10)
            if r.returncode == 0:
                return ["flatpak", "run", "--command=gpu-screen-recorder", "com.dec05eba.gpu_screen_recorder"]
        except Exception:
            pass
    return None


def _wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


class GsrRecorder:
    """Clips on Linux with gpu-screen-recorder's own replay buffer. Same face as ClipRecorder."""

    def __init__(self, cmd: list[str], seconds: int, out_dir: str | Path, work: Path):
        self.cmd, self.seconds = cmd, int(seconds)
        self.out_dir = Path(out_dir) if out_dir else default_dir()
        self.work = Path(work)
        self.proc: subprocess.Popen | None = None
        self.stopped = True
        self.encoder = "gpu-screen-recorder"
        self.source = "portal" if _wayland() else "screen"
        self.saved: list[str] = []  # file paths it prints when a replay is written

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                self.saved.append(line)

    def start(self) -> None:
        self.work.mkdir(parents=True, exist_ok=True)
        base = [*self.cmd, "-w", self.source, "-f", str(FPS), "-r", str(self.seconds), "-c", "mp4",
                "-a", "default_output", "-o", str(self.work)]
        tries = [base + ["-restore-portal-session", "yes"], base] if self.source == "portal" else [base]
        for cmd in tries:  # (asks once to share the screen on Wayland; the portal remembers the answer)
            log = open(self.work / "gsr.log", "w", encoding="utf-8", errors="ignore")
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log,
                                         text=True, start_new_session=True)
            threading.Thread(target=self._read, daemon=True, name="clip-gsr").start()
            time.sleep(4)
            if self.proc.poll() is None:
                self.stopped = False
                print(f"[capri] clips on: last {self.seconds} s, gpu-screen-recorder ({self.source})")
                return
        raise RuntimeError("gpu-screen-recorder didn't start (see data/clips/gsr.log)")

    def stop(self) -> None:
        self.stopped = True
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except Exception:
                self.proc.kill()
        shutil.rmtree(self.work, ignore_errors=True)

    @property
    def running(self) -> bool:
        return not self.stopped and self.proc is not None and self.proc.poll() is None

    def clip(self, seconds: int | None = None) -> Path:
        import signal

        if not self.running:
            raise RuntimeError("clips aren't recording")
        n = len(self.saved)
        self.proc.send_signal(signal.SIGUSR1)
        for _ in range(150):  # it prints the file's path once it's written
            if len(self.saved) > n:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("the recorder didn't save anything")
        src = Path(self.saved[-1])
        self.out_dir.mkdir(parents=True, exist_ok=True)
        out = self.out_dir / f"Capri clip {time.strftime('%Y-%m-%d %H-%M-%S')}.mp4"
        want = int(seconds or self.seconds)
        ff = find_ffmpeg()
        if want < self.seconds and ff is not None:  # "clip the last 10 seconds": the end of the replay
            r = _run([str(ff), "-hide_banner", "-loglevel", "error", "-y", "-sseof", f"-{want}", "-i", str(src),
                      "-c", "copy", "-movflags", "+faststart", str(out)], 60)
            if r.returncode == 0 and out.exists():
                src.unlink(missing_ok=True)
                return out
        shutil.move(str(src), out)
        return out


class X11Recorder(ClipRecorder):
    """Clips on Linux without gpu-screen-recorder: ffmpeg's own loop, on X11 (Wayland hides other apps' windows
    from it). The PC's sound comes from PulseAudio / PipeWire's monitor of your speakers, straight into ffmpeg."""

    ENCODERS = [
        ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p3", "-rc", "vbr", "-cq", "24", "-b:v", "0", "-forced-idr", "1"]),
        ("h264_vaapi", ["-vaapi_device", "/dev/dri/renderD128", "-c:v", "h264_vaapi", "-qp", "24"]),
        ("libx264", ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "26"]),
    ]

    def _monitor(self) -> str:
        """The speakers' monitor source ('alsa_output....monitor'), so the clip has what you heard."""
        try:
            sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=5).stdout.strip()
            if sink:
                return sink + ".monitor"
        except Exception:
            pass
        return "@DEFAULT_MONITOR@"  # PulseAudio / PipeWire's name for it ("default" would be the mic)

    def _cmd(self, source: str, enc: list[str]) -> list[str]:
        keep = self.seconds // SEG + 4
        size = "1920x1080"
        try:
            from .. import linux

            d = linux._display()
            if d is not None:
                s = d.screen()
                size = f"{s.width_in_pixels}x{s.height_in_pixels}"
        except Exception:
            pass
        vaapi = "h264_vaapi" in enc
        pre = enc[:2] if vaapi else []
        enc = enc[2:] if vaapi else enc
        vf = "scale=-2:'min(1080,ih)':flags=fast_bilinear," + ("format=nv12,hwupload" if vaapi else "format=yuv420p")
        return [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *pre,
                "-f", "x11grab", "-framerate", str(FPS), "-video_size", size, "-draw_mouse", "1",
                "-i", os.environ.get("DISPLAY", ":0"),
                "-f", "pulse", "-i", self._monitor(), "-vf", vf, *enc, *GOP,
                "-force_key_frames", f"expr:gte(t,n_forced*{SEG})", "-c:a", "aac", "-b:a", "160k",
                "-f", "segment", "-segment_time", str(SEG), "-segment_wrap", str(keep), "-reset_timestamps", "1",
                "-segment_format", "mpegts", str(self.work / "piece%03d.ts")]

    def start(self) -> None:
        self.work.mkdir(parents=True, exist_ok=True)
        self.encoder, self.enc_args = self._pick()
        self.stopped = False
        if not self._launch():
            self.stopped = True
            raise RuntimeError("ffmpeg couldn't record the screen (see data/clips/ffmpeg.log)")
        self.audio = None  # the sound is already in the pieces
        threading.Thread(target=self._watch, daemon=True, name="clip-watch").start()
        print(f"[capri] clips on: last {self.seconds} s, x11grab + {self.encoder}, saved to {self.out_dir}")

    def _pick(self) -> tuple[str, list[str]]:
        for name, args in self.ENCODERS:
            vaapi = name == "h264_vaapi"
            try:
                r = _run([self.ffmpeg, "-hide_banner", "-loglevel", "error", *(args[:2] if vaapi else []),
                          "-f", "lavfi", "-i", "color=black:s=640x360:r=30:d=0.5",
                          "-vf", "format=nv12,hwupload" if vaapi else "format=yuv420p",
                          *(args[2:] if vaapi else args), *GOP, "-f", "null", "-"])
                if r.returncode == 0:
                    return name, args
            except Exception:
                continue
        return self.ENCODERS[-1]

    def _launch(self) -> bool:
        for old in self.work.glob("piece*.ts"):
            old.unlink(missing_ok=True)
        log = open(self.work / "ffmpeg.log", "w", encoding="utf-8", errors="ignore")
        self.proc = subprocess.Popen(self._cmd("x11grab", self.enc_args), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
        for _ in range(40):
            if self.proc.poll() is not None:
                return False
            if any(self.work.glob("piece*.ts")):
                self.source = "x11grab"
                return True
            time.sleep(0.1)
        return self.proc.poll() is None


def make_recorder(seconds: int, out_dir: str | Path, work: Path):
    """The clip recorder for this PC: ffmpeg's loop on Windows; on Linux gpu-screen-recorder, else ffmpeg on X11.
    Raises with what to install when there's nothing that can record here."""
    import sys

    if sys.platform == "win32":
        ff = find_ffmpeg()
        if ff is None:
            raise FileNotFoundError("ffmpeg")
        return ClipRecorder(ff, seconds, out_dir, work)
    if cmd := _gsr_cmd():
        return GsrRecorder(cmd, seconds, out_dir, work)
    ff = find_ffmpeg()
    if ff is not None and not _wayland():
        return X11Recorder(ff, seconds, out_dir, work)
    raise RuntimeError(linux_hint())


def linux_hint() -> str:
    """What to install for clips on this Linux."""
    if shutil.which("pacman"):
        return "install gpu-screen-recorder: sudo pacman -S gpu-screen-recorder"
    if shutil.which("apt-get"):
        return ("install gpu-screen-recorder: sudo apt install gpu-screen-recorder-cli (or the Flatpak "
                "com.dec05eba.gpu_screen_recorder)" + ("" if _wayland() else ", or ffmpeg on X11: sudo apt install ffmpeg"))
    return "install gpu-screen-recorder (or ffmpeg on an X11 session)"


def wanted_seconds(text: str) -> int | None:
    """'clip the last minute' -> 60, 'clip the last 30 seconds' -> 30; None = the menu's length."""
    t = (text or "").lower()
    if m := re.search(r"(\d+)\s*(?:s\b|sec|secs|second|seconds)", t):
        return int(m.group(1))
    if m := re.search(r"(\d+)\s*(?:m\b|min|mins|minute|minutes)", t):
        return int(m.group(1)) * 60
    if re.search(r"\b(?:a|one|last|the last|1)\s+min(?:ute)?\b", t):
        return 60
    if re.search(r"\btwo minutes\b", t):
        return 120
    if re.search(r"\b(?:half a minute|thirty seconds)\b", t):
        return 30
    return None
