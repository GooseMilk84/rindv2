"""What happens after you drop a file on a slice."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QLineEdit

from . import engines
from .dialogs.base import InfoDialog, ask_form, names
from .engines.common import ConvertError, out_dir_for, unique
from .jobs import Info, JobManager, Result
from .registry import Item, kind_of

ROT_CHIPS = {"cw": "90° right", "ccw": "90° left", "180": "180°", "hflip": "Flip ↔", "vflip": "Flip ↕"}


class Handler:
    def __init__(self, jobs: JobManager, shelf_factory):
        self.jobs = jobs
        self.shelf_factory = shelf_factory
        jobs.info_ready.connect(self.show_info)

    # ------------------------------------------------------------ entry
    def handle(self, item: Item, paths: list[str]):
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        try:
            if item.group == "convert":
                self.convert(item.label, paths)
            elif item.group == "tool":
                fn = getattr(self, "t_" + item.id, None)
                if fn is None:
                    raise ConvertError(f"{item.label} isn't wired up")
                groups = {}
                for p in paths:
                    groups.setdefault(kind_of(p), []).append(p)
                if len(groups) > 1 and item.id != "merge":
                    for k, ps in groups.items():   # mixed selection: run the tool once per file type
                        fn(ps, k)
                else:
                    fn(paths, kind_of(paths[0]))
            else:
                getattr(self, "a_" + item.id)(paths)
        except ConvertError as ex:
            self.show_info(Info("Hmm", str(ex)))

    def show_info(self, info: Info):
        d = InfoDialog(info.title, info.data)
        d.exec()

    def job(self, title, fn, paths, verb="Done", undo=True):
        return self.jobs.run(title, fn, paths, verb, undo=undo)

    def each(self, title, paths, per_file, verb="Done"):
        """Run per_file(path) for every file, collecting outputs."""
        def fn(progress, cancel):
            outs = []
            for i, p in enumerate(paths):
                if cancel.is_set():
                    from .engines.common import Cancelled
                    raise Cancelled()
                progress(i / len(paths), f"{Path(p).name}" if len(paths) > 1 else "")
                r = per_file(p)
                if isinstance(r, tuple):
                    r = r[0]
                if isinstance(r, list):
                    outs += r
                elif r:
                    outs.append(r)
            return outs
        return self.job(title, fn, paths, verb)

    # ------------------------------------------------------------ convert
    def convert(self, fmt: str, paths: list[str]):
        self.job(f"Converting to {fmt}", lambda pr, c: engines.convert(paths, fmt, pr, c), paths, f"Converted to {fmt}")

    # ------------------------------------------------------------ shared tools
    def t_compress(self, paths, kind):
        from .dialogs.misc import CompressDialog
        d = CompressDialog(paths)
        if not d.run():
            return
        v = d.values()
        if kind in ("image",):
            from .engines import images as IM
            val = v["value"] * 1024 if v["mode"] == "size" else v["value"]
            self.each("Compressing", paths, lambda p: IM.compress(p, v["mode"], val, v.get("fmt"), v.get("max_side")), "Compressed")
        elif kind == "pdf":
            from .engines import pdfs
            val = v["value"] * 1024 if v["mode"] == "size" else v["value"]
            self.each("Compressing PDF", paths, lambda p: pdfs.compress(p, v["mode"], val, v.get("gray", False)), "Compressed")
        else:
            from .engines import media

            def fn(pr, c):
                outs = []
                for i, p in enumerate(paths):
                    outs.append(media.compress(p, v["mode"], v["value"], v.get("max_height"), v.get("audio_kbps", 128),
                                               lambda f, m, i=i: pr((i + f) / len(paths), m), c))
                return outs
            self.job("Compressing", fn, paths, "Compressed")

    def t_metadata(self, paths, kind):
        from .dialogs.misc import MetadataDialog
        if len(paths) > 1:
            v = ask_form("Metadata", names(paths), [("what", "Remove", "chips", "strip_all",
                                                    {"options": ["strip_all", "strip_gps"] if kind == "image" else ["strip_all"],
                                                     "labels": {"strip_all": "All metadata", "strip_gps": "Location only"}})],
                         "Remove")
            if not v:
                return
            self.each("Cleaning metadata", paths, lambda p: self._meta_write(p, kind, v["what"], None), "Metadata removed")
            return
        p = paths[0]
        if kind == "image":
            from .engines import images as IM
            data = IM.read_metadata(p)
            d = MetadataDialog(p, data, list(IM.EDITABLE.keys()), data.get("GPS"), can_strip_gps=True)
        elif kind == "pdf":
            from .engines import pdfs
            d = MetadataDialog(p, pdfs.read_metadata(p), pdfs.META_KEYS)
        elif kind in ("video", "audio"):
            from .engines import media
            d = MetadataDialog(p, media.read_metadata(p), media.META_FIELDS)
        elif kind == "word":
            from .engines import docs
            d = MetadataDialog(p, docs.read_docx_meta(p), ["title", "subject", "author", "keywords", "comments", "category"])
        elif kind == "slides":
            from .engines import slides
            d = MetadataDialog(p, slides.read_metadata(p), ["title", "subject", "author", "keywords", "comments", "category"])
        else:
            raise ConvertError("no metadata tools for that type")
        if not d.run() or not d.choice:
            return
        choice, edits = d.choice, dict(d.edits)
        self.job("Writing metadata", lambda pr, c: self._meta_write(p, kind, choice, edits), paths, "Metadata saved")

    def _meta_write(self, p, kind, choice, edits):
        strip = choice == "strip_all"
        if kind == "image":
            from .engines import images as IM
            return IM.write_metadata(p, choice, edits)
        if kind == "pdf":
            from .engines import pdfs
            return pdfs.write_metadata(p, edits, strip)
        if kind in ("video", "audio"):
            from .engines import media
            return media.write_metadata(p, edits, strip)
        if kind == "word":
            from .engines import docs
            return docs.write_docx_meta(p, edits, strip)
        if kind == "slides":
            from .engines import slides
            return slides.write_metadata(p, edits, strip)
        raise ConvertError("can't write metadata for that")

    def t_resize(self, paths, kind):
        from .dialogs.misc import ResizeDialog
        if kind == "image":
            from PIL import Image
            from .engines import images as IM
            try:
                size = IM.load(paths[0]).size
            except Exception:
                size = None
            d = ResizeDialog(paths, size)
            if not d.run():
                return
            v = d.values()
            self.each("Resizing", paths, lambda p: IM.resize(p, v.get("w"), v.get("h"), v.get("percent"), v.get("keep", True), v.get("fmt")), "Resized")
        else:
            from .engines import media
            info = media.probe(paths[0]) if kind != "animated" else {}
            d = ResizeDialog(paths, (info.get("width", 0), info.get("height", 0)) if info else None, video=True)
            if not d.run():
                return
            h = d.values()["height"]
            self.each("Resizing", paths, lambda p: media.resize(p, h), "Resized")

    def t_crop(self, paths, kind):
        from .dialogs.image_tools import CropDialog, apply_crop
        from .engines import images as IM
        if kind == "image":
            for p in paths:
                d = CropDialog(IM.load(p), Path(p).name)
                if not d.run():
                    return
                res = d.result()
                self.job("Cropping", lambda pr, c, p=p, res=res: apply_crop(p, res), [p], "Cropped")
        elif kind == "animated":
            from PIL import Image
            for p in paths:
                d = CropDialog(IM.load(p).convert("RGBA"), Path(p).name, allow_rotate=False)
                if not d.run():
                    return
                rect = d.result()["rect"]
                self.job("Cropping GIF", lambda pr, c, p=p, rect=rect: crop_gif(p, rect), [p], "Cropped")
        else:
            from PIL import Image
            import io
            from .engines import media
            for p in paths:
                info = media.probe(p)
                data = media.frame_png(p, min(1.0, info.duration / 3), 1280)
                frame = Image.open(io.BytesIO(data))
                d = CropDialog(frame, Path(p).name, real_size=(info.get("width", frame.width), info.get("height", frame.height)),
                               allow_rotate=False, title="Crop Video")
                if not d.run():
                    return
                x, y, w, h = d.result()["rect"]
                self.job("Cropping video", lambda pr, c, p=p, r=(x, y, w, h): media.crop(p, *r, progress=pr, cancel=c), [p], "Cropped")

    def t_rotate(self, paths, kind):
        opts = ["cw", "ccw", "180"] + (["hflip", "vflip"] if kind == "video" else [])
        v = ask_form("Rotate", names(paths), [("how", "Direction", "chips", "cw", {"options": opts, "labels": ROT_CHIPS})], "Rotate")
        if not v:
            return
        deg = {"cw": 90, "ccw": -90, "180": 180}.get(v["how"], 0)
        if kind == "image":
            from .engines import images as IM
            self.each("Rotating", paths, lambda p: IM.rotate(p, deg), "Rotated")
        elif kind == "pdf":
            from .engines import pdfs
            self.each("Rotating pages", paths, lambda p: pdfs.rotate(p, deg % 360), "Rotated")
        else:
            from .engines import media
            self.each("Rotating video", paths, lambda p: media.rotate(p, v["how"]), "Rotated")

    def t_watermark(self, paths, kind):
        fields = [("text", "Text", "text", "CONFIDENTIAL"), ("opacity", "Opacity", "slider", 30, {"min": 5, "max": 100, "suffix": "%"})]
        if kind == "image":
            fields.append(("pos", "Placement", "chips", "tile", {"options": ["tile", "center", "br", "bl", "tr", "tl"],
                                                                 "labels": {"tile": "Tiled", "center": "Centre", "br": "↘", "bl": "↙", "tr": "↗", "tl": "↖"}}))
            fields.append(("size", "Size", "slider", 4, {"min": 1, "max": 20, "suffix": "%"}))
        else:
            fields.append(("size", "Font size", "int", 54, {"min": 8, "max": 300, "suffix": " pt"}))
        v = ask_form("Watermark", names(paths), fields, "Add watermark")
        if not v or not v["text"].strip():
            return
        if kind == "image":
            from .engines import images as IM
            self.each("Watermarking", paths, lambda p: IM.watermark(p, v["text"], v["opacity"] / 100, v["pos"], v["size"]), "Watermarked")
        else:
            from .engines import pdfs
            self.each("Watermarking", paths, lambda p: pdfs.watermark(p, v["text"], v["opacity"] / 100, int(v["size"])), "Watermarked")

    def t_redact(self, paths, kind):
        if kind == "pdf":
            from .dialogs.misc import PdfRedactDialog
            from .engines import pdfs
            for p in paths:
                d = PdfRedactDialog(p)
                if not d.run():
                    return
                terms, pats, areas = d.values()

                def fn(pr, c, p=p, terms=terms, pats=pats, areas=areas):
                    out, n = pdfs.redact(p, terms, pats, areas, progress=pr)
                    return Result([out], f"{out.name}\n{n} area(s) removed")
                self.job("Redacting", fn, [p], "Redacted")
            return
        from .dialogs.image_tools import RedactImageDialog, apply_redaction
        from .engines import images as IM
        for p in paths:
            d = RedactImageDialog(IM.load(p), Path(p).name)
            if not d.run():
                return
            boxes, style = d.boxes_full(), d.view.style
            self.job("Redacting", lambda pr, c, p=p, b=boxes, s=style: IM.save_like(p, apply_redaction(IM.load(p), b, s), "Redacted"),
                     [p], "Redacted")

    def t_split(self, paths, kind):
        if kind == "pdf":
            from .engines import pdfs
            v = ask_form("Split PDF", names(paths), [
                ("mode", "Split into", "chips", "each", {"options": ["each", "every", "half", "ranges"],
                                                         "labels": {"each": "Every page", "every": "Every N pages", "half": "Halves", "ranges": "Ranges"}}),
                ("value", "N or ranges", "text", "", {"placeholder": "e.g. 2   or   1-3, 4-7, 8-"})], "Split")
            if not v:
                return
            self.each("Splitting", paths, lambda p: pdfs.split(p, v["mode"], v["value"]), "Split")
            return
        from .dialogs.media_tools import TimelineDialog
        from .engines import media
        for p in paths:
            d = TimelineDialog(p, "split")
            if not d.run():
                return
            marks = ",".join(f"{m:.3f}" for m in d.values()["markers"])
            self.job("Splitting", lambda pr, c, p=p, marks=marks: media.split(p, "at", marks, pr, c), [p], "Split")

    def t_merge(self, paths, kind):
        from .dialogs.misc import OrderDialog
        kinds = {kind_of(p) for p in paths}
        if kinds <= {"pdf", "image"}:
            from .engines import pdfs
            d = OrderDialog(paths, "Merge PDF")
            if d.run():
                order = d.paths()
                self.job("Merging", lambda pr, c: pdfs.merge(order, pr), order, "Merged")
        elif kinds <= {"sheet"}:
            from .engines import sheets
            d = OrderDialog(paths, "Merge spreadsheets", extra_options=(["sheets", "stack"], "sheets",
                                                                        {"sheets": "One tab per file", "stack": "Stack rows"}))
            if d.run():
                order, how = d.paths(), d.opt.value()
                self.job("Merging", lambda pr, c: sheets.merge(order, how), order, "Merged")
        elif kinds <= {"subtitle"}:
            from .engines import subs
            self.job("Merging", lambda pr, c: subs.merge(paths), paths, "Merged")
        else:
            from .engines import docs
            d = OrderDialog(paths, "Merge documents")
            if d.run():
                order = d.paths()
                self.job("Merging", lambda pr, c: docs.merge(order), order, "Merged")

    def t_join(self, paths, kind):
        from .dialogs.misc import OrderDialog
        from .engines import media
        d = OrderDialog(paths, "Join", "Join")
        if d.run():
            order = d.paths()
            self.job("Joining", lambda pr, c: media.join(order, pr, c), order, "Joined")

    def t_stats(self, paths, kind):
        p = paths[0]
        if kind in ("word", "text", "ebook"):
            from .engines import docs
            self.job("Counting", lambda pr, c: Info(f"Stats · {Path(p).name}", docs.stats(p)), [p])
        elif kind == "sheet":
            from .engines import sheets
            self.job("Crunching", lambda pr, c: Info(f"Stats · {Path(p).name}", sheets.stats(p)), [p])
        elif kind == "slides":
            from .engines import slides
            self.job("Counting", lambda pr, c: Info(f"Stats · {Path(p).name}", slides.stats(p)), [p])
        elif kind == "font":
            from .engines import subs
            self.job("Reading font", lambda pr, c: Info(f"Font · {Path(p).name}", subs.font_info(p)), [p])

    def t_extractimg(self, paths, kind):
        if kind == "pdf":
            from .engines import pdfs
            self.each("Extracting images", paths, lambda p: pdfs.extract_images(p), "Images extracted")
        else:
            from .engines import docs
            self.each("Extracting images", paths, lambda p: docs.extract_media(p), "Images extracted")

    def t_protect(self, paths, kind):
        fields = [("pw", "Password", "password", ""), ("pw2", "Confirm", "password", "")]
        if kind == "pdf":
            fields += [("print", "", "check", True, {"text": "Allow printing"}), ("copy", "", "check", False, {"text": "Allow copying text"})]
        else:
            fields += [("fmt", "Format", "chips", "ZIP", {"options": ["ZIP", "7Z"], "labels": {"ZIP": "ZIP (AES-256)", "7Z": "7Z (AES-256)"}})]
        v = ask_form("Protect", names(paths), fields, "Protect", note="Don't lose it - there's no way back in without the password.")
        if not v:
            return
        if not v["pw"] or v["pw"] != v["pw2"]:
            raise ConvertError("passwords didn't match (or were empty)")
        if kind == "pdf":
            from .engines import pdfs
            self.each("Encrypting", paths, lambda p: pdfs.protect(p, v["pw"], None, v["print"], v["copy"]), "Protected")
        else:
            from .engines import archives
            self.each("Encrypting", paths, lambda p: archives.protect(p, v["pw"], v["fmt"]), "Protected")

    def t_unlock(self, paths, kind):
        from .engines import pdfs
        pw, ok = QInputDialog.getText(None, "Unlock PDF", "Password:", QLineEdit.EchoMode.Password)
        if ok:
            self.each("Unlocking", paths, lambda p: pdfs.unlock(p, pw), "Unlocked")

    def t_organize(self, paths, kind):
        from .dialogs.misc import OrganizeDialog
        from .engines import pdfs
        d = OrganizeDialog(paths[0])
        if d.run():
            plan = d.plan()
            self.job("Saving pages", lambda pr, c: pdfs.organize(paths[0], plan), paths[:1], "Organized")

    def t_pagenum(self, paths, kind):
        from .engines import pdfs
        v = ask_form("Page numbers", names(paths), [
            ("fmt", "Format", "combo", "{n} / {total}", {"options": ["{n} / {total}", "{n}", "Page {n}", "Page {n} of {total}", "- {n} -"]}),
            ("pos", "Position", "chips", "bottom-center", {"options": ["bottom-center", "bottom-right", "bottom-left", "top-center", "top-right"],
                                                           "labels": {"bottom-center": "Bottom", "bottom-right": "Bottom ↘", "bottom-left": "Bottom ↙",
                                                                      "top-center": "Top", "top-right": "Top ↗"}}),
            ("start", "Start at", "int", 1, {"min": 0, "max": 9999}), ("size", "Size", "int", 10, {"min": 6, "max": 40, "suffix": " pt"})], "Number pages")
        if v:
            self.each("Numbering", paths, lambda p: pdfs.page_numbers(p, v["fmt"], v["pos"], v["size"], v["start"]), "Numbered")

    # ------------------------------------------------------------ image-only
    def t_edit(self, paths, kind):
        from .dialogs.image_tools import EditDialog, adjust
        from .engines import images as IM
        d = EditDialog(IM.load(paths[0]), Path(paths[0]).name, len(paths))
        if not d.run():
            return
        v = d.values()
        targets = paths if (len(paths) > 1 and d.apply_all.isChecked()) else paths[:1]
        self.each("Editing", targets, lambda p: IM.save_like(p, adjust(IM.load(p), v), "Edited"), "Edited")
        if len(paths) > 1 and not d.apply_all.isChecked():
            self.t_edit(paths[1:], kind)

    def t_annotate(self, paths, kind):
        from .dialogs.image_tools import AnnotateDialog
        from .engines import images as IM
        for p in paths:
            d = AnnotateDialog(IM.load(p), Path(p).name)
            if not d.run():
                return
            img = d.result_image()
            self.job("Saving", lambda pr, c, p=p, img=img: IM.save_like(p, img, "Annotated"), [p], "Annotated")

    def t_addbg(self, paths, kind):
        from .dialogs.image_tools import AddBgDialog, apply_addbg
        from .engines import images as IM
        d = AddBgDialog(IM.load(paths[0]), Path(paths[0]).name, len(paths))
        if not d.run():
            return
        params = d.params()
        targets = paths if (len(paths) > 1 and d.apply_all.isChecked()) else paths[:1]
        self.each("Adding background", targets, lambda p: apply_addbg(p, params), "Background added")
        if len(paths) > 1 and not d.apply_all.isChecked():
            self.t_addbg(paths[1:], kind)

    def t_qr(self, paths, kind):
        from .engines import images as IM

        def fn(pr, c):
            out = []
            for p in paths:
                codes = IM.read_qr(p)
                out += [f"{Path(p).name}:  {x}" for x in codes] if len(paths) > 1 else codes
            return Info("QR / barcodes", out or ["No codes found."])
        self.job("Scanning", fn, paths)

    def t_img2pdf(self, paths, kind):
        from .engines import images as IM
        v = ask_form("Make PDF", names(paths), [("page", "Pages", "chips", "fit", {"options": ["fit", "a4"],
                                                                                   "labels": {"fit": "Fit each image", "a4": "A4 with margins"}})], "Make PDF")
        if v:
            self.job("Building PDF", lambda pr, c: IM.convert_many_to_pdf(paths, pr, v["page"]), paths, "PDF ready")

    def t_collage(self, paths, kind):
        from .engines import images as IM
        v = ask_form("Collage", names(paths), [("cols", "Columns", "int", 0, {"min": 0, "max": 20, "suffix": "  (0 = auto)"}),
                                               ("gap", "Gap", "int", 16, {"min": 0, "max": 200, "suffix": " px"}),
                                               ("cell", "Tile size", "int", 900, {"min": 100, "max": 4000, "suffix": " px"}),
                                               ("bg", "Background", "chips", "dark", {"options": ["dark", "white", "black"],
                                                                                      "labels": {"dark": "Dark", "white": "White", "black": "Black"}})], "Make collage")
        if v:
            bg = {"dark": (17, 17, 19), "white": (255, 255, 255), "black": (0, 0, 0)}[v["bg"]]
            self.job("Making collage", lambda pr, c: IM.collage(paths, v["cols"], v["gap"], bg, v["cell"]), paths, "Collage ready")

    def t_makegif(self, paths, kind):
        if kind == "video":
            from .dialogs.media_tools import TimelineDialog
            from .engines import media
            p = paths[0]
            d = TimelineDialog(p, "gif")
            if d.run():
                v = d.values()
                self.job("Making GIF", lambda pr, c: media.to_gif(p, v["start"], v["end"], v["fps"], v["width"], pr, c), [p], "GIF ready")
            return
        from .engines import images as IM
        v = ask_form("Animate", names(paths), [("fps", "Frames / sec", "float", 2, {"min": 0.2, "max": 30, "step": 0.5}),
                                               ("size", "Max size", "int", 800, {"min": 64, "max": 4000, "suffix": " px"}),
                                               ("fmt", "Format", "chips", "GIF", {"options": ["GIF", "WEBP"]})], "Make it")
        if v:
            self.job("Animating", lambda pr, c: IM.make_gif(paths, v["fps"], v["size"], v["fmt"]), paths, "Animation ready")

    # ------------------------------------------------------------ media
    def t_trim(self, paths, kind):
        from .dialogs.media_tools import TimelineDialog
        from .engines import media
        for p in paths:
            d = TimelineDialog(p, "trim")
            if not d.run():
                return
            v = d.values()
            self.job("Trimming", lambda pr, c, p=p, v=v: media.trim(p, v["start"], v["end"], v["fast"], pr, c), [p], "Trimmed")

    def t_speed(self, paths, kind):
        from .engines import media
        v = ask_form("Speed", names(paths), [
            ("f", "Speed", "chips", "2", {"options": ["0.25", "0.5", "0.75", "1.25", "1.5", "2", "3", "4"],
                                          "labels": {x: f"{x}×" for x in ["0.25", "0.5", "0.75", "1.25", "1.5", "2", "3", "4"]}}),
            ("custom", "Or exactly", "float", 0, {"min": 0, "max": 16, "step": 0.05, "suffix": " ×  (0 = use chip)"}),
            ("pitch", "", "check", True, {"text": "Keep audio pitch"})], "Change speed")
        if v:
            f = v["custom"] or float(v["f"])
            self.each("Retiming", paths, lambda p: media.speed(p, f, v["pitch"]), "Done")

    def t_snapshot(self, paths, kind):
        from .engines import media
        if kind == "animated":
            self.each("Extracting frames", paths, lambda p: media.gif_frames(p), "Frames saved")
            return
        from .dialogs.media_tools import TimelineDialog
        p = paths[0]
        d = TimelineDialog(p, "snapshot")
        if d.run():
            t = d.values()["t"]
            self.job("Grabbing frame", lambda pr, c: media.snapshot(p, t), [p], "Frame saved")

    def t_extractaudio(self, paths, kind):
        from .engines import media
        self.each("Extracting audio", paths, lambda p: media.extract_audio(p), "Audio extracted")

    def t_mute(self, paths, kind):
        from .engines import media
        self.each("Muting", paths, lambda p: media.mute(p), "Muted")

    def t_reverse(self, paths, kind):
        from .engines import media
        self.each("Reversing", paths, lambda p: media.reverse(p), "Reversed")

    def t_normalize(self, paths, kind):
        from .engines import media
        v = ask_form("Normalize", names(paths), [("lufs", "Target loudness", "chips", "-16",
                                                  {"options": ["-14", "-16", "-19", "-23"],
                                                   "labels": {"-14": "Streaming (-14)", "-16": "Podcast (-16)", "-19": "Quiet (-19)", "-23": "Broadcast (-23)"}})], "Normalize")
        if v:
            self.each("Normalizing", paths, lambda p: media.normalize(p, float(v["lufs"])), "Normalized")

    def t_bleep(self, paths, kind):
        from .dialogs.media_tools import TimelineDialog
        from .engines import media
        p = paths[0]
        d = TimelineDialog(p, "bleep")
        if d.run():
            v = d.values()
            self.job("Bleeping", lambda pr, c: media.bleep(p, v["ranges"], v["bmode"], progress=pr, cancel=c), [p], "Bleeped")

    def t_channels(self, paths, kind):
        from .engines import media
        opts = {"mono": "Mono", "stereo": "Stereo", "swap": "Swap L/R", "left_both": "Left → both", "right_both": "Right → both",
                "left": "Left only", "right": "Right only"}
        v = ask_form("Channels", names(paths), [("how", "Make it", "combo", "mono", {"options": list(opts), "labels": opts})], "Apply")
        if v:
            self.each("Remixing", paths, lambda p: media.channels(p, v["how"]), "Done")

    def t_fade(self, paths, kind):
        from .engines import media
        v = ask_form("Fade", names(paths), [("i", "Fade in", "float", 2, {"min": 0, "max": 60, "suffix": " s"}),
                                            ("o", "Fade out", "float", 3, {"min": 0, "max": 60, "suffix": " s"})], "Apply fades")
        if v:
            self.each("Fading", paths, lambda p: media.fade(p, v["i"], v["o"]), "Faded")

    # ------------------------------------------------------------ docs / data
    def t_replace(self, paths, kind):
        from .engines import docs
        v = ask_form("Find & replace", names(paths), [("find", "Find", "text", ""), ("repl", "Replace with", "text", ""),
                                                      ("case", "", "check", False, {"text": "Match case"}),
                                                      ("regex", "", "check", False, {"text": "Regular expression"})], "Replace")
        if not v or not v["find"]:
            return

        def fn(pr, c):
            outs, total = [], 0
            for p in paths:
                o, n = docs.replace(p, v["find"], v["repl"], v["case"], v["regex"])
                outs.append(o)
                total += n
            return Result(outs, f"{total} replacement{'s' if total != 1 else ''} · {outs[0].name}")
        self.job("Replacing", fn, paths, "Replaced")

    def t_encoding(self, paths, kind):
        from .engines import docs
        self.each("Re-encoding", paths, lambda p: docs.to_utf8(p), "Saved as UTF-8")

    def t_splitsheets(self, paths, kind):
        from .engines import sheets
        self.each("Splitting sheets", paths, lambda p: sheets.split_sheets(p), "Split")

    def t_dedupe(self, paths, kind):
        from .engines import sheets

        def fn(pr, c):
            outs, n = [], 0
            for p in paths:
                o, k = sheets.dedupe(p)
                outs.append(o)
                n += k
            return Result(outs, f"{n} duplicate row{'s' if n != 1 else ''} removed")
        self.job("Removing duplicates", fn, paths, "Deduped")

    def t_transpose(self, paths, kind):
        from .engines import sheets
        self.each("Transposing", paths, lambda p: sheets.transpose(p), "Transposed")

    def t_notes(self, paths, kind):
        from .engines import slides
        self.each("Pulling notes", paths, lambda p: slides.notes(p), "Notes saved")

    def t_pretty(self, paths, kind):
        from .engines import sheets
        self.each("Formatting", paths, lambda p: sheets.pretty(p), "Formatted")

    def t_minify(self, paths, kind):
        from .engines import sheets
        self.each("Minifying", paths, lambda p: sheets.pretty(p, minify=True), "Minified")

    def t_shift(self, paths, kind):
        from .engines import subs
        v = ask_form("Shift subtitles", names(paths), [("sec", "Shift by", "float", 0.5, {"min": -36000, "max": 36000, "step": 0.1, "suffix": " s"}),
                                                       ("fps", "Frame-rate fix", "combo", "1", {"options": ["1", str(25 / 23.976), str(23.976 / 25), str(24 / 25), str(25 / 24)],
                                                                                                "labels": {"1": "None", str(25 / 23.976): "23.976 → 25",
                                                                                                           str(23.976 / 25): "25 → 23.976", str(24 / 25): "25 → 24",
                                                                                                           str(25 / 24): "24 → 25"}})], "Shift")
        if v:
            self.each("Shifting", paths, lambda p: subs.shift(p, v["sec"], 1 / float(v["fps"])), "Shifted")

    def t_clean(self, paths, kind):
        from .engines import subs
        self.each("Cleaning", paths, lambda p: subs.clean(p), "Cleaned")

    # ------------------------------------------------------------ archives
    def _archive_pw(self, p) -> str | None:
        enc = False
        try:
            if p.lower().endswith(".zip"):
                with zipfile.ZipFile(p) as z:
                    enc = any(i.flag_bits & 0x1 for i in z.infolist())
            elif p.lower().endswith(".7z"):
                import py7zr
                with py7zr.SevenZipFile(p) as z:
                    enc = z.needs_password()
        except Exception:
            pass
        if not enc:
            return None
        pw, ok = QInputDialog.getText(None, "Password", f"{Path(p).name} is locked. Password:", QLineEdit.EchoMode.Password)
        return pw if ok else ""

    def t_extract(self, paths, kind):
        from .engines import archives
        for p in paths:
            pw = self._archive_pw(p)
            if pw == "":
                return
            self.job("Extracting", lambda pr, c, p=p, pw=pw: archives.extract_here(p, pw, pr), [p], "Extracted")

    def t_contents(self, paths, kind):
        from .engines import archives
        from .registry import human_size
        p = paths[0]
        self.job("Reading", lambda pr, c: Info(f"Contents · {Path(p).name}",
                                               [f"{human_size(s):>9}   {n}" for n, s in archives.contents(p)]), [p])

    def t_test(self, paths, kind):
        from .engines import archives
        p = paths[0]
        self.job("Testing", lambda pr, c: Info(f"Test · {Path(p).name}", archives.test(p)), [p])

    # ------------------------------------------------------------ actions
    def a_copy(self, paths):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        md.setData('Preferred DropEffect', (1).to_bytes(4, "little"))
        QApplication.clipboard().setMimeData(md)
        self.jobs.notify("Copied", f"{names(paths)} - paste anywhere")

    def _transfer(self, paths, move: bool):
        dest = QFileDialog.getExistingDirectory(None, "Move to…" if move else "Copy to…", str(Path(paths[0]).parent))
        if not dest:
            return

        def fn(pr, c):
            outs = []
            for i, p in enumerate(paths):
                pr(i / len(paths), Path(p).name)
                target = unique(Path(dest) / Path(p).name)
                if move:
                    shutil.move(p, target)
                elif os.path.isdir(p):
                    shutil.copytree(p, target)
                else:
                    shutil.copy2(p, target)
                outs.append(target)
            return outs
        self.job("Moving" if move else "Copying", fn, paths, "Moved" if move else "Copied", undo=not move)

    def a_moveto(self, paths):
        self._transfer(paths, True)

    def a_copyto(self, paths):
        self._transfer(paths, False)

    def a_path(self, paths):
        text = "\n".join(f'"{p}"' if " " in p and len(paths) > 1 else p for p in paths)
        QApplication.clipboard().setText(text)
        self.jobs.notify("Path copied", text if len(text) < 120 else names(paths))

    def a_terminal(self, paths):
        p = paths[0]
        folder = p if os.path.isdir(p) else os.path.dirname(p)
        if sys.platform == "win32":
            wt = shutil.which("wt")
            if wt:
                subprocess.Popen([wt, "-d", folder])
            else:
                subprocess.Popen(["powershell", "-NoExit", "-Command", f"Set-Location -LiteralPath '{folder}'"],
                                 creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
                if shutil.which(term):
                    subprocess.Popen([term], cwd=folder)
                    break

    def a_float(self, paths):
        self.shelf_factory().add(paths)

    def a_zip(self, paths):
        from .engines import archives
        self.job("Zipping", lambda pr, c: archives.zip_items(paths), paths, "Zipped")

    def a_openwith(self, paths):
        if sys.platform == "win32":
            subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", paths[0]])
        else:
            subprocess.Popen(["xdg-open", paths[0]])

    def a_rename(self, paths):
        from .dialogs.misc import RenameDialog
        d = RenameDialog(paths)
        if not d.run():
            return
        plan = [(a, b) for a, b in d.plan() if a != b]
        temps = []
        for i, (a, b) in enumerate(plan):
            t = f"{a}.rind-tmp-{i}"
            os.rename(a, t)
            temps.append((t, b))
        for t, b in temps:
            os.rename(t, b)
        self.jobs.notify("Renamed", f"{len(plan)} item{'s' if len(plan) != 1 else ''}")

    def a_delete(self, paths):
        delete_files(paths, self.jobs)

    def a_checksum(self, paths):
        from .dialogs.misc import ChecksumDialog
        ChecksumDialog(paths).exec()


def delete_files(paths: list[str], jobs: JobManager, parent=None, on_done=None) -> bool:
    """Confirm, then Recycle Bin (or permanent). Returns False if you backed out."""
    from . import trash
    from .config import settings
    paths = [p for p in paths if os.path.lexists(p)]
    if not paths:
        return False
    permanent = False
    if settings.get("confirm_delete", True):
        from .dialogs.base import DeleteDialog
        d = DeleteDialog(paths, parent)
        if not d.run():
            return False
        permanent = d.permanent
    fails = trash.delete(paths, permanent)
    gone = len(paths) - len(fails)
    if gone:
        jobs.notify("Deleted" if permanent else "Moved to Recycle Bin",
                    names([p for p in paths if p not in {f for f, _ in fails}]))
    if fails:
        jobs.info_ready.emit(Info(f"Couldn't delete {len(fails)}", "\n".join(f"{os.path.basename(p)}: {why}" for p, why in fails)))
    if on_done:
        on_done()
    return True


def crop_gif(src: str, rect) -> Path:
    from PIL import Image
    from .engines.common import out_path
    x, y, w, h = rect
    im = Image.open(src)
    frames, durs = [], []
    for i in range(getattr(im, "n_frames", 1)):
        im.seek(i)
        frames.append(im.convert("RGBA").crop((x, y, x + w, y + h)))
        durs.append(im.info.get("duration", 80))
    dest = out_path(src, "gif", "Cropped")
    pal = [f.convert("RGB").quantize(256, method=Image.Quantize.MEDIANCUT) for f in frames]
    pal[0].save(dest, "GIF", save_all=True, append_images=pal[1:], duration=durs, loop=0, optimize=True)
    return dest
