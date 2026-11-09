"""What can each file become, and which tools/actions apply to it."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- file kinds
EXT_KIND = {}


def _reg(kind, exts):
    for e in exts.split():
        EXT_KIND[e] = kind


_reg("image", "jpg jpeg jfif jpe png webp heic heif avif tif tiff bmp dib gif ico svg tga psd jp2 j2k dds pcx ppm pgm pbm qoi")
_reg("raw", "cr2 cr3 nef arw dng orf rw2 raf srw pef")
_reg("pdf", "pdf")
_reg("word", "docx docm doc odt dotx")
_reg("text", "txt md markdown html htm rtf log")
_reg("ebook", "epub mobi fb2 xps cbz")
_reg("sheet", "xlsx xlsm xls ods csv tsv")
_reg("data", "json jsonl xml yaml yml")
_reg("slides", "pptx ppt pptm odp")
_reg("video", "mp4 mov mkv webm avi wmv flv m4v mpeg mpg 3gp ts mts m2ts ogv vob")
_reg("audio", "mp3 m4a aac wav flac ogg oga opus aiff aif wma alac amr ac3 caf")
_reg("subtitle", "srt vtt ass ssa sbv")
_reg("archive", "zip 7z rar tar gz tgz bz2 tbz2 xz txz zst cab iso")
_reg("font", "ttf otf woff woff2")

KIND_LABEL = {
    "image": "Image", "raw": "RAW photo", "pdf": "PDF", "word": "Document", "text": "Text",
    "ebook": "eBook", "sheet": "Spreadsheet", "data": "Data", "slides": "Presentation",
    "video": "Video", "audio": "Audio", "subtitle": "Subtitles", "archive": "Archive",
    "font": "Font", "folder": "Folder", "other": "File", "animated": "Animated GIF",
}


def ext_of(path: str) -> str:
    name = os.path.basename(path).lower()
    for multi in ("tar.gz", "tar.bz2", "tar.xz", "tar.zst"):
        if name.endswith("." + multi):
            return multi
    return Path(name).suffix.lstrip(".")


def kind_of(path: str) -> str:
    if os.path.isdir(path):
        return "folder"
    e = ext_of(path)
    if e.startswith("tar."):
        return "archive"
    k = EXT_KIND.get(e, "other")
    if k == "image" and e == "gif" and is_animated(path):
        return "animated"
    return k


def is_animated(path: str) -> bool:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return getattr(im, "is_animated", False) and im.n_frames > 1
    except Exception:
        return False


# ---------------------------------------------------------------- items
@dataclass
class Item:
    id: str
    label: str
    icon: str | None = None
    group: str = "convert"           # convert | tool | action
    min_files: int = 1
    hint: str = ""
    kinds: tuple = field(default_factory=tuple)

    def available(self, n: int) -> bool:
        return n >= self.min_files


IMG_OUT = ["JPG", "PNG", "WEBP", "HEIC", "AVIF", "TIFF", "BMP", "GIF", "ICO", "SVG", "PDF", "DOCX"]
VIDEO_OUT = ["MP4", "MOV", "MKV", "WEBM", "AVI", "WMV", "GIF", "MP3", "M4A", "WAV", "FLV", "M4V", "MPEG", "3GP"]
AUDIO_OUT = ["MP3", "M4A", "WAV", "FLAC", "OGG", "OPUS", "AIFF", "AAC", "WMA"]
DOC_OUT = ["PDF", "DOCX", "TXT", "MD", "HTML", "RTF", "EPUB", "ODT"]

CONVERT = {
    "image": IMG_OUT,
    "raw": ["JPG", "PNG", "TIFF", "WEBP", "HEIC", "AVIF"],
    "animated": ["MP4", "WEBM", "WEBP", "PNG", "JPG", "AVIF", "PDF"],
    "pdf": ["DOCX", "JPG", "PNG", "TXT", "MD", "HTML", "EPUB", "XLSX", "PPTX", "TIFF", "WEBP", "SVG"],
    "word": DOC_OUT,
    "text": DOC_OUT,
    "ebook": ["PDF", "EPUB", "TXT", "DOCX", "HTML", "MD"],
    "sheet": ["XLSX", "CSV", "ODS", "TSV", "PDF", "HTML", "JSON", "MD"],
    "data": ["JSON", "CSV", "XLSX", "YAML", "XML", "TXT"],
    "slides": ["PDF", "PPTX", "JPG", "PNG", "TXT", "MD"],
    "video": VIDEO_OUT,
    "audio": AUDIO_OUT,
    "subtitle": ["SRT", "VTT", "ASS", "TXT"],
    "archive": ["ZIP", "7Z", "TAR", "TAR.GZ", "TAR.BZ2", "TAR.XZ"],
    "font": ["TTF", "OTF", "WOFF", "WOFF2"],
}

# formats each ext should NOT offer (same format)
SAME = {
    "jpeg": "JPG", "jpg": "JPG", "jfif": "JPG", "jpe": "JPG", "tif": "TIFF", "htm": "HTML", "markdown": "MD",
    "heif": "HEIC", "yml": "YAML", "aif": "AIFF", "tgz": "TAR.GZ", "tbz2": "TAR.BZ2", "txz": "TAR.XZ", "oga": "OGG",
}

T = Item
TOOLS = {
    "image": [
        T("compress", "Compress", "compress", "tool"), T("metadata", "Metadata", "metadata", "tool"),
        T("edit", "Edit", "edit", "tool"), T("annotate", "Annotate", "annotate", "tool"),
        T("addbg", "Add BG", "addbg", "tool"), T("crop", "Crop", "crop", "tool"),
        T("redact", "Redact", "redact", "tool"), T("resize", "Resize", "resize", "tool"),
        T("rotate", "Rotate", "rotate", "tool"), T("watermark", "Watermark", "watermark", "tool"),
        T("qr", "Read QR", "qr", "tool"), T("img2pdf", "Make PDF", "pdf", "tool"),
        T("collage", "Collage", "collage", "tool", 2), T("makegif", "Make GIF", "gif", "tool", 2),
    ],
    "pdf": [
        T("compress", "Compress", "compress", "tool"), T("merge", "Merge", "merge", "tool", 2),
        T("split", "Split", "split", "tool"), T("organize", "Organize", "organize", "tool"),
        T("rotate", "Rotate", "rotate", "tool"), T("protect", "Protect", "lock", "tool"),
        T("unlock", "Unlock", "unlock", "tool"), T("extractimg", "Images", "extract", "tool"),
        T("metadata", "Metadata", "metadata", "tool"), T("redact", "Redact", "redact", "tool"),
        T("watermark", "Watermark", "watermark", "tool"), T("pagenum", "Page No.", "hash", "tool"),
    ],
    "video": [
        T("compress", "Compress", "compress", "tool"), T("trim", "Trim", "trim", "tool"),
        T("crop", "Crop", "crop", "tool"), T("speed", "Speed", "speed", "tool"),
        T("split", "Split", "split", "tool"), T("join", "Join", "merge", "tool", 2),
        T("snapshot", "Snapshot", "snapshot", "tool"), T("extractaudio", "Audio", "audio", "tool"),
        T("mute", "Mute", "mute", "tool"), T("rotate", "Rotate", "rotate", "tool"),
        T("reverse", "Reverse", "reverse", "tool"), T("resize", "Resize", "resize", "tool"),
        T("makegif", "To GIF", "gif", "tool"), T("metadata", "Metadata", "metadata", "tool"),
    ],
    "audio": [
        T("compress", "Compress", "compress", "tool"), T("trim", "Trim", "trim", "tool"),
        T("normalize", "Normalize", "normalize", "tool"), T("bleep", "Bleep", "bleep", "tool"),
        T("channels", "Channels", "channels", "tool"), T("speed", "Speed", "speed", "tool"),
        T("split", "Split", "split", "tool"), T("join", "Join", "merge", "tool", 2),
        T("fade", "Fade", "wave", "tool"), T("reverse", "Reverse", "reverse", "tool"),
        T("metadata", "Metadata", "metadata", "tool"),
    ],
    "animated": [
        T("compress", "Compress", "compress", "tool"), T("resize", "Resize", "resize", "tool"),
        T("crop", "Crop", "crop", "tool"), T("speed", "Speed", "speed", "tool"),
        T("reverse", "Reverse", "reverse", "tool"), T("snapshot", "Frames", "snapshot", "tool"),
    ],
    "word": [
        T("stats", "Stats", "info", "tool"), T("metadata", "Metadata", "metadata", "tool"),
        T("merge", "Merge", "merge", "tool", 2), T("replace", "Replace", "text", "tool"),
        T("extractimg", "Images", "extract", "tool"),
    ],
    "text": [
        T("stats", "Stats", "info", "tool"), T("replace", "Replace", "text", "tool"),
        T("merge", "Merge", "merge", "tool", 2), T("encoding", "Encoding", "settings", "tool"),
    ],
    "sheet": [
        T("merge", "Merge", "merge", "tool", 2), T("splitsheets", "Split", "split", "tool"),
        T("dedupe", "Dedupe", "list", "tool"), T("transpose", "Transpose", "rotate", "tool"),
        T("stats", "Stats", "info", "tool"),
    ],
    "slides": [
        T("extractimg", "Images", "extract", "tool"), T("notes", "Notes", "text", "tool"),
        T("stats", "Stats", "info", "tool"), T("metadata", "Metadata", "metadata", "tool"),
    ],
    "ebook": [T("stats", "Stats", "info", "tool"), T("extractimg", "Images", "extract", "tool")],
    "subtitle": [T("shift", "Shift", "clock", "tool"), T("clean", "Clean", "erase", "tool"),
                 T("merge", "Merge", "merge", "tool", 2)],
    "archive": [T("extract", "Extract", "extract", "tool"), T("contents", "Contents", "list", "tool"),
                T("protect", "Protect", "lock", "tool"), T("test", "Test", "check", "tool")],
    "data": [T("pretty", "Format", "text", "tool"), T("minify", "Minify", "compress", "tool")],
    "font": [T("stats", "Info", "info", "tool")],
}

ACTIONS = [
    T("copy", "Copy", "copy", "action"), T("moveto", "Move to", "move", "action"),
    T("copyto", "Copy to", "folder", "action"), T("path", "Copy Path", "path", "action"),
    T("terminal", "Terminal", "terminal", "action"), T("float", "Float", "float", "action"),
    T("zip", "Zip", "zip", "action"), T("openwith", "Open with", "openwith", "action"),
    T("rename", "Rename", "text", "action"), T("checksum", "Checksum", "hash", "action"),
    T("delete", "Delete", "trash", "action"),
]


def convert_items(paths: list[str]) -> list[Item]:
    kinds = {kind_of(p) for p in paths}
    lists = []
    exts = {ext_of(p) for p in paths}
    for k in kinds:
        lists.append(CONVERT.get(k, []))
    if not lists:
        return []
    common = [f for f in lists[0] if all(f in l for l in lists[1:])]
    if len(kinds) > 1:
        # mixed images + pdfs can always be bundled into a PDF
        if kinds <= {"image", "pdf", "raw"} and "PDF" not in common:
            common.append("PDF")
    same = set()
    if len(exts) == 1:
        e = next(iter(exts))
        same.add(SAME.get(e, e.upper()))
    out = []
    for f in common:
        if f in same and not (f == "PDF" and len(paths) > 1):
            continue
        out.append(Item("to:" + f, f, None, "convert"))
    # merging many images into one PDF is a big deal: label it
    if len(paths) > 1 and any(i.label == "PDF" for i in out) and kinds <= {"image", "pdf", "raw"}:
        for i in out:
            if i.label == "PDF":
                i.hint = "Combine into one PDF"
    return out


def tool_items(paths: list[str]) -> list[Item]:
    kinds = {kind_of(p) for p in paths}
    n = len(paths)
    if not kinds:
        return []
    lists = [TOOLS.get(k, []) for k in kinds]
    ids0 = [t.id for t in lists[0]]
    common = [i for i in ids0 if all(any(t.id == i for t in l) for l in lists[1:])]
    out = []
    for t in lists[0]:
        if t.id in common and t.available(n):
            out.append(t)
    # mixed image/pdf: allow merge into pdf
    if kinds <= {"image", "pdf"} and len(kinds) == 2 and n >= 2:
        out.append(T("merge", "Merge PDF", "merge", "tool", 2))
    return out


def action_items(paths: list[str]) -> list[Item]:
    return list(ACTIONS)


def describe(paths: list[str]) -> str:
    if len(paths) == 1:
        return KIND_LABEL.get(kind_of(paths[0]), "File")
    kinds = {kind_of(p) for p in paths}
    if len(kinds) == 1:
        return f"{len(paths)} {KIND_LABEL.get(next(iter(kinds)), 'File').lower()}s"
    return f"{len(paths)} files"


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def total_size(paths: list[str]) -> int:
    s = 0
    for p in paths:
        try:
            if os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for f in files:
                        try:
                            s += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
            else:
                s += os.path.getsize(p)
        except OSError:
            pass
    return s
