"""Video + audio via ffmpeg (bundled through imageio-ffmpeg, or whatever's on PATH)."""
from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path

from ..config import settings
from .common import NO_WINDOW, Cancelled, ConvertError, noop, out_path, out_folder, which

_FF = None


def ffmpeg() -> str:
    global _FF
    if _FF:
        return _FF
    p = which("ffmpeg", "ffmpeg.exe")
    if not p:
        try:
            import imageio_ffmpeg
            p = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            p = None
    if not p:
        raise ConvertError("ffmpeg not found - run install.bat again")
    _FF = p
    return p


# ------------------------------------------------------------------ probing
class Info(dict):
    @property
    def duration(self) -> float:
        return self.get("duration", 0.0)


def probe(src: str) -> Info:
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", src], capture_output=True, creationflags=NO_WINDOW)
    err = r.stderr.decode("utf-8", errors="ignore")
    info = Info(raw=err, metadata={}, has_video=False, has_audio=False)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"bitrate: (\d+) kb/s", err)
    if m:
        info["bitrate"] = int(m.group(1))
    for line in err.splitlines():
        if "Stream #" in line and "Video:" in line and "attached pic" not in line and not info["has_video"]:
            info["has_video"] = True
            mm = re.search(r"Video: (\w+)", line)
            info["vcodec"] = mm.group(1) if mm else ""
            mm = re.search(r", (\d{2,5})x(\d{2,5})", line)
            if mm:
                info["width"], info["height"] = int(mm.group(1)), int(mm.group(2))
            mm = re.search(r"([\d.]+) fps", line)
            if mm:
                info["fps"] = float(mm.group(1))
            mm = re.search(r"rotate\s*:\s*(-?\d+)", err)
            if mm:
                info["rotate"] = int(mm.group(1))
        elif "Stream #" in line and "Audio:" in line and not info["has_audio"]:
            info["has_audio"] = True
            mm = re.search(r"Audio: (\w+)", line)
            info["acodec"] = mm.group(1) if mm else ""
            mm = re.search(r"(\d+) Hz", line)
            if mm:
                info["sample_rate"] = int(mm.group(1))
            info["channels"] = "mono" if "mono" in line else ("stereo" if "stereo" in line else
                                                               (re.search(r"Hz, ([^,]+)", line).group(1) if re.search(r"Hz, ([^,]+)", line) else ""))
    # top-level metadata block
    mblock = re.search(r"Input #0.*?Metadata:\n(.*?)(?:\n  Duration|\n  Stream)", err, re.S)
    if mblock:
        for l in mblock.group(1).splitlines():
            if ":" in l:
                k, v = l.split(":", 1)
                info["metadata"][k.strip()] = v.strip()
    return info


# ------------------------------------------------------------------ running
class Job:
    """Lets the UI cancel a running ffmpeg."""
    current: subprocess.Popen | None = None
    cancelled = False


def run_ff(args: list[str], duration: float = 0, progress=noop, label="Encoding", cancel: threading.Event | None = None):
    cmd = [ffmpeg(), "-hide_banner", "-y", "-nostdin", "-progress", "pipe:1", "-nostats"] + args
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    err_lines = []

    def drain():
        for l in p.stderr:
            err_lines.append(l.decode("utf-8", errors="ignore"))
            if len(err_lines) > 200:
                del err_lines[:100]
    t = threading.Thread(target=drain, daemon=True)
    t.start()
    for raw in p.stdout:
        if cancel is not None and cancel.is_set():
            p.kill()
            raise Cancelled()
        line = raw.decode(errors="ignore").strip()
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=")[1])
                if duration > 0:
                    progress(min(0.99, us / 1e6 / duration), label)
            except ValueError:
                pass
    p.wait()
    t.join(timeout=2)
    if p.returncode != 0:
        tail = "".join(err_lines[-6:]).strip()
        raise ConvertError("ffmpeg: " + (tail.splitlines()[-1] if tail else f"exit {p.returncode}"))


# ------------------------------------------------------------------ codec choices
AUDIO_ARGS = {
    "MP3": ["-c:a", "libmp3lame", "-b:a", None],
    "M4A": ["-c:a", "aac", "-b:a", None],
    "AAC": ["-c:a", "aac", "-b:a", None, "-f", "adts"],
    "WAV": ["-c:a", "pcm_s16le"],
    "FLAC": ["-c:a", "flac", "-compression_level", "8"],
    "OGG": ["-c:a", "libvorbis", "-q:a", "5"],
    "OPUS": ["-c:a", "libopus", "-b:a", "128k"],
    "AIFF": ["-c:a", "pcm_s16be"],
    "WMA": ["-c:a", "wmav2", "-b:a", None],
}


def audio_args(fmt: str, bitrate: str | None = None) -> list[str]:
    args = list(AUDIO_ARGS.get(fmt.upper(), AUDIO_ARGS["MP3"]))
    return [bitrate or settings.get("audio_bitrate", "192k") if a is None else a for a in args]


def video_args(fmt: str, crf: int | None = None, has_audio=True) -> list[str]:
    fmt = fmt.upper()
    crf = crf if crf is not None else settings.get("video_crf", 23)
    even = ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    if fmt in ("MP4", "MOV", "M4V", "MKV", "FLV", "TS"):
        a = ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p"] + even
        a += ["-c:a", "aac", "-b:a", "160k"] if has_audio else []
        if fmt in ("MP4", "MOV", "M4V"):
            a += ["-movflags", "+faststart"]
        return a
    if fmt == "WEBM":
        a = ["-c:v", "libvpx-vp9", "-crf", str(crf + 9), "-b:v", "0", "-deadline", "good", "-cpu-used", "4",
             "-row-mt", "1", "-pix_fmt", "yuv420p"]
        return a + (["-c:a", "libopus", "-b:a", "128k"] if has_audio else [])
    if fmt == "AVI":
        return ["-c:v", "mpeg4", "-vtag", "xvid", "-q:v", "3"] + (["-c:a", "libmp3lame", "-b:a", "192k"] if has_audio else [])
    if fmt == "WMV":
        return ["-c:v", "wmv2", "-q:v", "3"] + (["-c:a", "wmav2", "-b:a", "192k"] if has_audio else [])
    if fmt in ("MPEG", "MPG"):
        return ["-c:v", "mpeg2video", "-q:v", "3", "-f", "mpeg"] + (["-c:a", "mp2", "-b:a", "224k"] if has_audio else [])
    if fmt == "3GP":
        return ["-c:v", "libx264", "-profile:v", "baseline", "-crf", "28", "-vf",
                "scale='min(640,iw)':-2", "-pix_fmt", "yuv420p"] + (["-c:a", "aac", "-b:a", "64k", "-ac", "1", "-ar", "22050"] if has_audio else [])
    if fmt == "OGV":
        return ["-c:v", "libtheora", "-q:v", "7"] + (["-c:a", "libvorbis"] if has_audio else [])
    raise ConvertError(f"can't make {fmt} video")


REMUX_OK = {
    "MP4": ({"h264", "hevc", "av1", "mpeg4"}, {"aac", "mp3", "ac3", "alac", "opus"}),
    "MOV": ({"h264", "hevc", "prores", "mpeg4"}, {"aac", "mp3", "alac", "pcm_s16le"}),
    "M4V": ({"h264", "hevc"}, {"aac"}),
    "MKV": ({"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "mpeg2video"}, {"aac", "mp3", "opus", "vorbis", "flac", "ac3", "pcm_s16le"}),
    "WEBM": ({"vp8", "vp9", "av1"}, {"opus", "vorbis"}),
}


def _ext(fmt: str) -> str:
    return {"MPEG": "mpg"}.get(fmt.upper(), fmt.lower())


# ------------------------------------------------------------------ conversion
def gif_filter(fps=12, width=640) -> str:
    return (f"fps={fps},scale='min({width},iw)':-1:flags=lanczos,split[s0][s1];"
            f"[s0]palettegen=max_colors=256:stats_mode=diff[p];[s1][p]paletteuse=dither=sierra2_4a")


def convert(src: str, fmt: str, progress=noop, cancel=None) -> list[Path]:
    fmt = fmt.upper()
    info = probe(src)
    dur = info.duration
    dest = out_path(src, _ext(fmt))
    if fmt == "GIF":
        run_ff(["-i", src, "-vf", gif_filter(), "-loop", "0", str(dest)], dur, progress, "Making GIF", cancel)
        return [dest]
    if fmt in AUDIO_ARGS:
        if not info["has_audio"]:
            raise ConvertError("that file has no audio track")
        run_ff(["-i", src, "-vn", "-map", "0:a:0"] + audio_args(fmt) + ["-map_metadata", "0", str(dest)],
               dur, progress, f"Encoding {fmt}", cancel)
        return [dest]
    if not info["has_video"]:
        raise ConvertError("no video stream in there")
    ok = REMUX_OK.get(fmt)
    if ok and info.get("vcodec") in ok[0] and (not info["has_audio"] or info.get("acodec") in ok[1]):
        try:
            progress(0.1, "Remuxing (lossless)")
            run_ff(["-i", src, "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-map_metadata", "0"]
                   + (["-movflags", "+faststart"] if fmt in ("MP4", "MOV", "M4V") else []) + [str(dest)],
                   dur, progress, "Remuxing", cancel)
            return [dest]
        except ConvertError:
            pass
    run_ff(["-i", src, "-map", "0:v:0", "-map", "0:a:0?"] + video_args(fmt, has_audio=info["has_audio"]) + [str(dest)],
           dur, progress, f"Encoding {fmt}", cancel)
    return [dest]


# ------------------------------------------------------------------ tools
def is_audio_only(src: str, info: Info | None = None) -> bool:
    info = info or probe(src)
    return not info["has_video"]


def same_args(src: str, info: Info, crf=None) -> list[str]:
    ext = Path(src).suffix.lstrip(".").upper()
    if is_audio_only(src, info):
        return audio_args({"OGA": "OGG", "AIF": "AIFF"}.get(ext, ext))
    try:
        return video_args({"MPG": "MPEG", "MTS": "MP4", "M2TS": "MP4", "VOB": "MPEG"}.get(ext, ext), crf, info["has_audio"])
    except ConvertError:
        return video_args("MP4", crf, info["has_audio"])


def _dest_same(src: str, suffix: str) -> Path:
    ext = Path(src).suffix.lstrip(".").lower()
    if ext in ("mts", "m2ts", "vob", "ts"):
        ext = "mp4"
    return out_path(src, ext, suffix)


def _strip_vf(args: list[str]) -> list[str]:
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "-vf":
            skip = True
            continue
        out.append(a)
    return out


def trim(src: str, start: float, end: float, fast=False, progress=noop, cancel=None) -> Path:
    info = probe(src)
    dest = _dest_same(src, "Trimmed")
    if fast:
        run_ff(["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", src, "-map", "0", "-c", "copy",
                "-avoid_negative_ts", "make_zero", str(dest)], end - start, progress, "Cutting", cancel)
    else:
        run_ff(["-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}", "-map", "0:v:0?", "-map", "0:a:0?"]
               + same_args(src, info) + [str(dest)], end - start, progress, "Trimming", cancel)
    return dest


def crop(src: str, x: int, y: int, w: int, h: int, progress=noop, cancel=None) -> Path:
    info = probe(src)
    w, h = w - w % 2, h - h % 2
    dest = _dest_same(src, "Cropped")
    run_ff(["-i", src, "-vf", f"crop={w}:{h}:{x}:{y}", "-map", "0:v:0", "-map", "0:a:0?"]
           + _strip_vf(same_args(src, info)) + [str(dest)], info.duration, progress, "Cropping", cancel)
    return dest


def _atempo(f: float) -> str:
    parts = []
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        parts.append("atempo=0.5")
        f /= 0.5
    parts.append(f"atempo={f:.4f}")
    return ",".join(parts)


def speed(src: str, factor: float, keep_pitch=True, progress=noop, cancel=None) -> Path:
    info = probe(src)
    dest = _dest_same(src, f"{factor:g}x")
    if src.lower().endswith(".gif"):
        run_ff(["-i", src, "-filter_complex", f"setpts=PTS/{factor}," + gif_filter(fps=15), str(dest)],
               info.duration / factor, progress, "Retiming", cancel)
        return dest
    a = _atempo(factor) if keep_pitch else f"asetrate={info.get('sample_rate', 44100)}*{factor},aresample={info.get('sample_rate', 44100)}"
    args = ["-i", src]
    if info["has_video"]:
        fc = f"[0:v]setpts=PTS/{factor},scale=trunc(iw/2)*2:trunc(ih/2)*2[v]"
        if info["has_audio"]:
            fc += f";[0:a]{a}[a]"
        args += ["-filter_complex", fc, "-map", "[v]"] + (["-map", "[a]"] if info["has_audio"] else [])
        args += _strip_vf(same_args(src, info))
    else:
        args += ["-filter:a", a] + same_args(src, info)
    run_ff(args + [str(dest)], info.duration / factor, progress, "Retiming", cancel)
    return dest


def split(src: str, mode: str, value: float | str, progress=noop, cancel=None) -> Path:
    """mode: parts (N equal) | every (seconds) | at ('0:30, 1:15')"""
    info = probe(src)
    dur = info.duration
    if mode == "parts":
        n = max(2, int(value))
        cuts = [dur * i / n for i in range(1, n)]
    elif mode == "every":
        step = max(1.0, float(value))
        cuts = [t for t in frange(step, dur, step)]
    else:
        cuts = sorted(parse_time(t) for t in str(value).split(",") if t.strip())
    bounds = [0.0] + [c for c in cuts if 0 < c < dur] + [dur]
    folder = out_folder(src, "Parts")
    ext = Path(_dest_same(src, "x")).suffix
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        progress(i / (len(bounds) - 1), f"Part {i + 1} of {len(bounds) - 1}")
        dest = folder / f"{Path(src).stem} - part {i + 1:02d}{ext}"
        run_ff(["-ss", f"{a:.3f}", "-i", src, "-t", f"{b - a:.3f}", "-map", "0:v:0?", "-map", "0:a:0?"]
               + same_args(src, info) + [str(dest)], 0, noop, "", cancel)
    return folder


def frange(a, b, s):
    while a < b - 0.5:
        yield a
        a += s


def parse_time(t: str) -> float:
    t = t.strip()
    parts = [float(p) for p in t.split(":")]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


def fmt_time(s: float) -> str:
    s = max(0.0, s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{sec:05.2f}" if h else f"{int(m)}:{sec:05.2f}"


def join(srcs: list[str], progress=noop, cancel=None) -> Path:
    infos = [probe(s) for s in srcs]
    total = sum(i.duration for i in infos)
    dest = _dest_same(srcs[0], "Joined")
    args = []
    for s in srcs:
        args += ["-i", s]
    n = len(srcs)
    if all(not i["has_video"] for i in infos):
        fc = "".join(f"[{k}:a:0]" for k in range(n)) + f"concat=n={n}:v=0:a=1[a]"
        args += ["-filter_complex", fc, "-map", "[a]"] + same_args(srcs[0], infos[0])
    else:
        W = infos[0].get("width", 1280) // 2 * 2
        Hh = infos[0].get("height", 720) // 2 * 2
        fps = infos[0].get("fps", 30) or 30
        parts = []
        all_audio = all(i["has_audio"] for i in infos)
        for k in range(n):
            parts.append(f"[{k}:v:0]scale={W}:{Hh}:force_original_aspect_ratio=decrease,"
                         f"pad={W}:{Hh}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{k}]")
            if all_audio:
                parts.append(f"[{k}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{k}]")
        chain = "".join(f"[v{k}]" + (f"[a{k}]" if all_audio else "") for k in range(n))
        parts.append(f"{chain}concat=n={n}:v=1:a={1 if all_audio else 0}[v]" + ("[a]" if all_audio else ""))
        args += ["-filter_complex", ";".join(parts), "-map", "[v]"] + (["-map", "[a]"] if all_audio else [])
        args += _strip_vf(same_args(srcs[0], infos[0]))
    run_ff(args + [str(dest)], total, progress, "Joining", cancel)
    return dest


def snapshot(src: str, t: float, fmt="png") -> Path:
    dest = out_path(src, fmt, f"at {fmt_time(t).replace(':', '.')}")
    subprocess.run([ffmpeg(), "-hide_banner", "-y", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1", "-q:v", "2", str(dest)],
                   capture_output=True, creationflags=NO_WINDOW)
    if not dest.exists():
        raise ConvertError("couldn't grab that frame")
    return dest


def frame_png(src: str, t: float, width: int = 960) -> bytes:
    r = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0, t):.3f}", "-i", src,
                        "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2", "-f", "image2pipe", "-c:v", "png", "-"],
                       capture_output=True, creationflags=NO_WINDOW)
    return r.stdout


def waveform(src: str, points: int = 800) -> list[float]:
    """Peak envelope for drawing, decoded at 4 kHz mono."""
    import array
    r = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", src, "-vn", "-ac", "1", "-ar", "4000",
                        "-f", "s16le", "-"], capture_output=True, creationflags=NO_WINDOW)
    a = array.array("h")
    a.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    if not a:
        return [0.0] * points
    step = max(1, len(a) // points)
    out = []
    for i in range(0, len(a), step):
        chunk = a[i:i + step]
        out.append(max(abs(min(chunk)), abs(max(chunk))) / 32768)
    return out[:points]


def extract_audio(src: str, progress=noop, cancel=None) -> Path:
    info = probe(src)
    if not info["has_audio"]:
        raise ConvertError("no audio in that video")
    codec = info.get("acodec", "")
    copy_map = {"aac": "m4a", "mp3": "mp3", "opus": "opus", "vorbis": "ogg", "flac": "flac"}
    if codec in copy_map:
        dest = out_path(src, copy_map[codec])
        try:
            run_ff(["-i", src, "-vn", "-map", "0:a:0", "-c:a", "copy", str(dest)], info.duration, progress, "Extracting", cancel)
            return dest
        except ConvertError:
            pass
    dest = out_path(src, "mp3")
    run_ff(["-i", src, "-vn", "-map", "0:a:0"] + audio_args("MP3", "256k") + [str(dest)], info.duration, progress, "Extracting", cancel)
    return dest


def mute(src: str, progress=noop, cancel=None) -> Path:
    info = probe(src)
    dest = _dest_same(src, "Muted")
    run_ff(["-i", src, "-map", "0:v", "-c:v", "copy", "-an", str(dest)], info.duration, progress, "Removing audio", cancel)
    return dest


def rotate(src: str, how: str = "cw", progress=noop, cancel=None) -> Path:
    info = probe(src)
    vf = {"cw": "transpose=1", "ccw": "transpose=2", "180": "transpose=1,transpose=1", "hflip": "hflip", "vflip": "vflip"}[how]
    dest = _dest_same(src, "Rotated")
    run_ff(["-i", src, "-vf", vf, "-map", "0:v:0", "-map", "0:a:0?"] + _strip_vf(same_args(src, info))
           + (["-c:a", "copy"] if info["has_audio"] and Path(src).suffix.lower() in (".mp4", ".mov", ".mkv") else [])
           + [str(dest)], info.duration, progress, "Rotating", cancel)
    return dest


def reverse(src: str, progress=noop, cancel=None) -> Path:
    info = probe(src)
    dest = _dest_same(src, "Reversed")
    if src.lower().endswith(".gif"):
        run_ff(["-i", src, "-filter_complex", "reverse," + gif_filter(fps=15), str(dest)], info.duration, progress, "Reversing", cancel)
        return dest
    args = ["-i", src]
    if info["has_video"]:
        args += ["-vf", "reverse"] + (["-af", "areverse"] if info["has_audio"] else [])
        args += _strip_vf(same_args(src, info))
    else:
        args += ["-af", "areverse"] + same_args(src, info)
    run_ff(args + [str(dest)], info.duration, progress, "Reversing", cancel)
    return dest


def resize(src: str, height: int, progress=noop, cancel=None) -> Path:
    info = probe(src)
    dest = _dest_same(src, f"{height}p")
    if src.lower().endswith(".gif"):
        run_ff(["-i", src, "-filter_complex", f"scale=-1:{height}:flags=lanczos," + gif_filter(fps=15, width=9999), str(dest)],
               info.duration, progress, "Resizing", cancel)
        return dest
    run_ff(["-i", src, "-vf", f"scale=-2:{height}:flags=lanczos", "-map", "0:v:0", "-map", "0:a:0?"]
           + _strip_vf(same_args(src, info)) + [str(dest)], info.duration, progress, "Resizing", cancel)
    return dest


def to_gif(src: str, start: float, end: float, fps: int = 12, width: int = 640, progress=noop, cancel=None) -> Path:
    dest = out_path(src, "gif")
    run_ff(["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", src, "-vf", gif_filter(fps, width), "-loop", "0", str(dest)],
           end - start, progress, "Making GIF", cancel)
    return dest


def gif_frames(src: str) -> Path:
    folder = out_folder(src, "Frames")
    subprocess.run([ffmpeg(), "-hide_banner", "-y", "-i", src, str(folder / "frame %04d.png")],
                   capture_output=True, creationflags=NO_WINDOW)
    return folder


def compress(src: str, mode: str = "quality", value: float = 28, max_height: int | None = None,
             audio_kbps: int = 128, progress=noop, cancel=None) -> Path:
    """Video: quality=CRF, size=target MB, percent=% of original. Audio: kbps / size / percent."""
    info = probe(src)
    dur = max(info.duration, 0.1)
    orig = os.path.getsize(src)
    if src.lower().endswith(".gif"):
        dest = out_path(src, "gif", "Compressed")
        width = 480 if mode == "quality" and value > 30 else 640
        run_ff(["-i", src, "-vf", gif_filter(fps=10, width=width).replace("max_colors=256", "max_colors=128"), str(dest)],
               dur, progress, "Compressing", cancel)
        return dest
    if is_audio_only(src, info):
        ext = Path(src).suffix.lstrip(".").upper()
        fmt = ext if ext in ("MP3", "M4A", "OGG", "OPUS", "AAC", "WMA") else "MP3"
        if mode == "quality":
            kbps = int(value)
        else:
            target_bytes = value * 1024 * 1024 if mode == "size" else orig * value / 100
            kbps = max(24, int(target_bytes * 8 / dur / 1000 * 0.97))
        dest = out_path(src, fmt.lower(), "Compressed")
        run_ff(["-i", src, "-vn"] + audio_args(fmt, f"{kbps}k") + [str(dest)], dur, progress, "Compressing", cancel)
        return dest
    dest = out_path(src, "mp4", "Compressed")
    vf = []
    if max_height and info.get("height", 0) > max_height:
        vf = ["-vf", f"scale=-2:{max_height}"]
    else:
        vf = ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    base = ["-i", src, "-map", "0:v:0", "-map", "0:a:0?"] + vf + ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
                                                                   "-movflags", "+faststart"]
    aud = ["-c:a", "aac", "-b:a", f"{audio_kbps}k"] if info["has_audio"] else []
    if mode == "quality":
        run_ff(base + ["-crf", str(int(value))] + aud + [str(dest)], dur, progress, "Compressing", cancel)
    else:
        target_bytes = value * 1024 * 1024 if mode == "size" else orig * value / 100
        total_kbps = target_bytes * 8 / dur / 1000 * 0.96
        a_k = audio_kbps if info["has_audio"] else 0
        if total_kbps - a_k < 80 and info["has_audio"]:
            a_k = 64
            aud = ["-c:a", "aac", "-b:a", "64k"]
        v_k = max(50, int(total_kbps - a_k))
        if not max_height and info.get("height", 0) > 720 and v_k < 1200:
            base[base.index("-vf") + 1] = "scale=-2:720"
        if not max_height and info.get("height", 0) > 480 and v_k < 500:
            base[base.index("-vf") + 1] = "scale=-2:480"
        passlog = str(dest.with_suffix("")) + "_pass"
        run_ff(base + ["-b:v", f"{v_k}k", "-pass", "1", "-passlogfile", passlog, "-an", "-f", "null", os.devnull],
               dur, lambda p, m: progress(p / 2, "Analysing (pass 1)"), "", cancel)
        run_ff(base + ["-b:v", f"{v_k}k", "-pass", "2", "-passlogfile", passlog] + aud + [str(dest)],
               dur, lambda p, m: progress(0.5 + p / 2, "Encoding (pass 2)"), "", cancel)
        for f in Path(passlog).parent.glob(Path(passlog).name + "*"):
            try:
                f.unlink()
            except OSError:
                pass
    return dest


def normalize(src: str, target_lufs: float = -16, progress=noop, cancel=None) -> Path:
    info = probe(src)
    dest = _dest_same(src, "Normalized")
    af = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
    args = ["-i", src, "-af", af]
    if info["has_video"]:
        args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    else:
        ext = Path(src).suffix.lstrip(".").upper()
        args += same_args(src, info)
        if ext in ("WAV", "FLAC", "AIFF", "AIF"):
            args += ["-ar", str(info.get("sample_rate", 48000))]
    run_ff(args + [str(dest)], info.duration, progress, "Normalizing loudness", cancel)
    return dest


def bleep(src: str, ranges: list[tuple[float, float]], mode="beep", freq=1000, progress=noop, cancel=None) -> Path:
    info = probe(src)
    if not ranges:
        raise ConvertError("add at least one range to bleep")
    cond = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in ranges)
    dur = info.duration
    sr = info.get("sample_rate", 44100)
    if mode == "beep":
        fc = (f"[0:a]volume=0:enable='{cond}'[m];"
              f"sine=frequency={freq}:sample_rate={sr}:duration={dur:.3f},volume=0.25,volume=0:enable='eq({cond},0)'[s];"
              f"[m][s]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        fc = f"[0:a]volume=0:enable='{cond}'[a]"
    dest = _dest_same(src, "Bleeped")
    args = ["-i", src, "-filter_complex", fc, "-map", "[a]"]
    if info["has_video"]:
        args = ["-i", src, "-filter_complex", fc, "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    else:
        args += same_args(src, info)
    run_ff(args + [str(dest)], dur, progress, "Bleeping", cancel)
    return dest


def channels(src: str, how: str, progress=noop, cancel=None) -> Path:
    info = probe(src)
    af = {"mono": "pan=mono|c0=0.5*c0+0.5*c1", "stereo": "aformat=channel_layouts=stereo",
          "swap": "pan=stereo|c0=c1|c1=c0", "left": "pan=mono|c0=c0", "right": "pan=mono|c0=c1",
          "left_both": "pan=stereo|c0=c0|c1=c0", "right_both": "pan=stereo|c0=c1|c1=c1"}[how]
    if info.get("channels") == "mono" and how in ("mono", "swap", "left", "right"):
        if how != "mono":
            raise ConvertError("that file is already mono")
    if how == "stereo" and info.get("channels") == "mono":
        af = "pan=stereo|c0=c0|c1=c0"
    dest = _dest_same(src, how.replace("_", " ").title())
    args = ["-i", src, "-af", af]
    if info["has_video"]:
        args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    else:
        args += same_args(src, info)
    run_ff(args + [str(dest)], info.duration, progress, "Remixing channels", cancel)
    return dest


def fade(src: str, fade_in: float, fade_out: float, progress=noop, cancel=None) -> Path:
    info = probe(src)
    dur = info.duration
    parts = []
    if fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        parts.append(f"afade=t=out:st={max(0, dur - fade_out):.3f}:d={fade_out}")
    if not parts:
        raise ConvertError("set a fade length")
    dest = _dest_same(src, "Faded")
    args = ["-i", src, "-af", ",".join(parts)]
    if info["has_video"]:
        vf = []
        if fade_in > 0:
            vf.append(f"fade=t=in:st=0:d={fade_in}")
        if fade_out > 0:
            vf.append(f"fade=t=out:st={max(0, dur - fade_out):.3f}:d={fade_out}")
        args += ["-vf", ",".join(vf)] + _strip_vf(same_args(src, info))
    else:
        args += same_args(src, info)
    run_ff(args + [str(dest)], dur, progress, "Fading", cancel)
    return dest


META_FIELDS = ["title", "artist", "album", "album_artist", "date", "genre", "track", "comment", "composer", "copyright"]


def read_metadata(src: str) -> dict:
    info = probe(src)
    out = {}
    out["Duration"] = fmt_time(info.duration)
    if info.get("bitrate"):
        out["Bitrate"] = f"{info['bitrate']} kb/s"
    if info["has_video"]:
        out["Video"] = f"{info.get('vcodec', '')} {info.get('width', '?')}×{info.get('height', '?')} @ {info.get('fps', '?')} fps"
    if info["has_audio"]:
        out["Audio"] = f"{info.get('acodec', '')} {info.get('sample_rate', '')} Hz {info.get('channels', '')}"
    out["File size"] = f"{os.path.getsize(src) / 1024 / 1024:.2f} MB"
    for k, v in info["metadata"].items():
        out[k] = v
    return out


def write_metadata(src: str, values: dict | None, strip=False, progress=noop, cancel=None) -> Path:
    info = probe(src)
    dest = _dest_same(src, "No Metadata" if strip else "Edited")
    args = ["-i", src, "-map", "0", "-c", "copy"]
    if strip:
        args += ["-map_metadata", "-1", "-map_chapters", "-1"]
        if Path(src).suffix.lower() == ".mp3":
            args += ["-id3v2_version", "0", "-write_id3v1", "0"]
    else:
        args += ["-map_metadata", "0"]
        for k, v in (values or {}).items():
            args += ["-metadata", f"{k}={v}"]
        if Path(src).suffix.lower() == ".mp3":
            args += ["-id3v2_version", "3"]
    run_ff(args + [str(dest)], info.duration, progress, "Writing tags", cancel)
    return dest
