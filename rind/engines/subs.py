"""Subtitles (SRT/VTT/ASS/SSA/SBV) and fonts (TTF/OTF/WOFF/WOFF2)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .common import ConvertError, noop, out_path
from .docs import read_text_file


@dataclass
class Cue:
    start: float
    end: float
    text: str


def _t(s: str) -> float:
    s = s.strip().replace(",", ".")
    parts = s.split(":")
    v = 0.0
    for p in parts:
        v = v * 60 + float(p)
    return v


def _srt_time(t: float) -> str:
    t = max(0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_time(t: float) -> str:
    return _srt_time(t).replace(",", ".")


def _ass_time(t: float) -> str:
    t = max(0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def parse(src: str) -> list[Cue]:
    text = read_text_file(src).replace("\r\n", "\n").replace("\r", "\n")
    ext = Path(src).suffix.lower().lstrip(".")
    cues = []
    if ext in ("ass", "ssa"):
        fmt = None
        for line in text.splitlines():
            if line.startswith("Format:") and fmt is None and "Text" in line:
                fmt = [f.strip().lower() for f in line[7:].split(",")]
            elif line.startswith("Dialogue:") and fmt:
                vals = line[9:].split(",", len(fmt) - 1)
                d = dict(zip(fmt, vals))
                t = re.sub(r"\{[^}]*\}", "", d.get("text", "")).replace("\\N", "\n").replace("\\n", "\n")
                cues.append(Cue(_t(d["start"]), _t(d["end"]), t.strip()))
        return cues
    if ext == "sbv":
        for block in re.split(r"\n\s*\n", text.strip()):
            lines = block.split("\n")
            if lines and "," in lines[0]:
                a, b = lines[0].split(",", 1)
                cues.append(Cue(_t(a), _t(b), "\n".join(lines[1:]).strip()))
        return cues
    # srt / vtt
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.split("\n")]
        for i, l in enumerate(lines):
            m = re.match(r"\s*([\d:.,]+)\s*-->\s*([\d:.,]+)", l)
            if m:
                cues.append(Cue(_t(m.group(1)), _t(m.group(2)), "\n".join(lines[i + 1:]).strip()))
                break
    return cues


def write(cues: list[Cue], fmt: str, dest: Path) -> Path:
    fmt = fmt.upper()
    if fmt == "SRT":
        out = [f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1)]
        dest.write_text("\n".join(out), encoding="utf-8")
    elif fmt == "VTT":
        out = ["WEBVTT\n"] + [f"{_vtt_time(c.start)} --> {_vtt_time(c.end)}\n{c.text}\n" for c in cues]
        dest.write_text("\n".join(out), encoding="utf-8")
    elif fmt == "ASS":
        head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
                "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                "Style: Default,Arial,64,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,1,2,40,40,50,1\n\n"
                "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        lines = [f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,{c.text.replace(chr(10), chr(92) + 'N')}" for c in cues]
        dest.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    elif fmt == "TXT":
        dest.write_text("\n".join(re.sub(r"<[^>]+>", "", c.text) for c in cues) + "\n", encoding="utf-8")
    else:
        raise ConvertError(f"can't write {fmt}")
    return dest


def convert(src: str, fmt: str, progress=noop) -> list[Path]:
    cues = parse(src)
    if not cues:
        raise ConvertError("couldn't find any subtitles in there")
    return [write(cues, fmt, out_path(src, fmt.lower()))]


def _same(src: str) -> str:
    e = Path(src).suffix.lstrip(".").upper()
    return {"SSA": "ASS", "SBV": "SRT"}.get(e, e)


def shift(src: str, seconds: float, speed: float = 1.0) -> Path:
    cues = parse(src)
    for c in cues:
        c.start = max(0, c.start * speed + seconds)
        c.end = max(0, c.end * speed + seconds)
    fmt = _same(src)
    return write(cues, fmt, out_path(src, fmt.lower(), "Shifted"))


def clean(src: str) -> Path:
    cues = parse(src)
    out = []
    for c in cues:
        t = re.sub(r"<[^>]+>|\{[^}]*\}", "", c.text)
        t = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", t)         # [music] (laughs)
        t = re.sub(r"^[A-Z][A-Z .'-]{1,20}:\s*", "", t, flags=re.M)  # SPEAKER:
        t = re.sub(r"[♪♫]+", "", t)
        t = "\n".join(l.strip(" -") for l in t.splitlines() if l.strip(" -"))
        if t.strip():
            out.append(Cue(c.start, c.end, t.strip()))
    fmt = _same(src)
    return write(out, fmt, out_path(src, fmt.lower(), "Clean"))


def merge(srcs: list[str]) -> Path:
    cues = []
    for s in srcs:
        cues += parse(s)
    cues.sort(key=lambda c: c.start)
    fmt = _same(srcs[0])
    return write(cues, fmt, out_path(srcs[0], fmt.lower(), "Merged"))


# ------------------------------------------------------------------ fonts
def font_convert(src: str, fmt: str, progress=noop) -> list[Path]:
    from fontTools.ttLib import TTFont
    fmt = fmt.upper()
    f = TTFont(src)
    is_cff = "CFF " in f or "CFF2" in f
    if fmt in ("TTF", "OTF"):
        f.flavor = None
        if fmt == "TTF" and is_cff:
            try:
                from fontTools.pens.cu2quPen import Cu2QuPen  # noqa
                _otf_to_ttf(f)
            except Exception as ex:
                raise ConvertError(f"CFF->TrueType outline conversion failed: {ex}")
        if fmt == "OTF" and not is_cff:
            # TrueType outlines in an .otf container is valid OpenType
            pass
    elif fmt == "WOFF":
        f.flavor = "woff"
    elif fmt == "WOFF2":
        try:
            import brotli  # noqa
        except ImportError:
            raise ConvertError("WOFF2 needs the 'brotli' package")
        f.flavor = "woff2"
    dest = out_path(src, fmt.lower())
    f.save(dest)
    return [dest]


def _otf_to_ttf(font):
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import newTable
    gs = font.getGlyphSet()
    glyf = newTable("glyf")
    glyf.glyphOrder = font.getGlyphOrder()
    glyf.glyphs = {}
    for name in font.getGlyphOrder():
        pen = TTGlyphPen(gs)
        gs[name].draw(Cu2QuPen(pen, 1.0, reverse_direction=True))
        glyf.glyphs[name] = pen.glyph()
    font["glyf"] = glyf
    font["loca"] = newTable("loca")
    del font["CFF "]
    font["maxp"].tableVersion = 0x00010000
    for attr in ("maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs", "maxInstructionDefs",
                 "maxStackElements", "maxSizeOfInstructions", "maxComponentElements", "maxPoints", "maxContours",
                 "maxCompositePoints", "maxCompositeContours", "maxComponentDepth"):
        setattr(font["maxp"], attr, 0)
    font["maxp"].maxZones = 1
    font["head"].indexToLocFormat = 0
    font["head"].glyphDataFormat = 0
    font.sfntVersion = "\x00\x01\x00\x00"
    if "post" in font:
        font["post"].formatType = 2.0
        font["post"].extraNames = []
        font["post"].mapping = {}


def font_info(src: str) -> dict:
    from fontTools.ttLib import TTFont
    f = TTFont(src)
    n = f["name"]

    def g(i):
        r = n.getDebugName(i)
        return r or ""
    return {"Family": g(1), "Style": g(2), "Full name": g(4), "Version": g(5), "Designer": g(9),
            "License": g(13)[:200], "Glyphs": str(len(f.getGlyphOrder())),
            "Outlines": "CFF (PostScript)" if ("CFF " in f or "CFF2" in f) else "TrueType",
            "Variable": "yes" if "fvar" in f else "no", "Container": f.flavor or "sfnt"}
