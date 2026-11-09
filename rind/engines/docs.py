"""Documents: DOCX/DOC/ODT/RTF/TXT/MD/HTML/EPUB and friends."""
from __future__ import annotations

import html as htmlmod
import os
import re
import shutil
import zipfile
from pathlib import Path

from . import htmlconv as H
from .common import ConvertError, noop, out_path, out_folder, find_soffice, soffice_convert, temp_dir


def read_text_file(path: str) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            if enc == "utf-16" and not raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(raw).best()
        if best:
            return str(best)
    except Exception:
        pass
    return raw.decode("latin-1")


# ------------------------------------------------------------------ word via COM
WD = {"PDF": 17, "DOCX": 16, "RTF": 6, "TXT": 7, "HTML": 10, "ODT": 23}


def word_convert(src: str, fmt: str, dest: Path, progress=None) -> bool:
    from . import office
    if not office.available():
        return False
    if progress:
        progress(0.15, "Asking Word" if office.is_warm("Word.Application") else "Starting Word (first run is slow, then it stays warm)")
    src_abs, dest_abs = str(Path(src).resolve()), str(Path(dest).resolve())

    def job(word):
        doc = word.Documents.Open(src_abs, ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False,
                                  Visible=False, NoEncodingDialog=True)
        try:
            if fmt == "PDF":
                doc.ExportAsFixedFormat(OutputFileName=dest_abs, ExportFormat=17, OpenAfterExport=False,
                                        OptimizeFor=0, CreateBookmarks=1, DocStructureTags=True)
            elif fmt == "TXT":
                doc.SaveAs2(dest_abs, FileFormat=7, Encoding=65001)
            else:
                doc.SaveAs2(dest_abs, FileFormat=WD[fmt])
        finally:
            doc.Close(0)
        return True

    return bool(office.run("Word.Application", job)) and dest.exists()


def doc_to_docx_tmp(src: str) -> str:
    """Old .doc / .docm -> temp .docx using Word or LibreOffice."""
    tmp = temp_dir() / (Path(src).stem + ".docx")
    if word_convert(src, "DOCX", tmp):
        return str(tmp)
    if find_soffice():
        soffice_convert(src, "docx", tmp)
        return str(tmp)
    raise ConvertError(".doc files need Microsoft Word or LibreOffice installed")


# ------------------------------------------------------------------ anything -> html
def to_html(src: str) -> tuple[str, str]:
    ext = Path(src).suffix.lower().lstrip(".")
    title = Path(src).stem
    if ext in ("doc", "docm", "dotx"):
        src = doc_to_docx_tmp(src)
        ext = "docx"
    if ext == "docx":
        try:
            from .richhtml import docx_to_html
            body, _css, _bg = docx_to_html(src)      # keeps colours, highlights, shading, fonts
            return body, title
        except Exception:
            import mammoth
            with open(src, "rb") as f:
                res = mammoth.convert_to_html(f)
            return res.value, title
    if ext in ("md", "markdown"):
        import markdown
        txt = read_text_file(src)
        return markdown.markdown(txt, extensions=["tables", "fenced_code", "sane_lists", "toc"]), title
    if ext in ("txt", "log", "csv"):
        return H.text_to_html(read_text_file(src)), title
    if ext in ("html", "htm", "xhtml"):
        raw = read_text_file(src)
        m = re.search(r"<title>(.*?)</title>", raw, re.S | re.I)
        return H.body_of(raw), (m.group(1).strip() if m else title)
    if ext == "rtf":
        tmp = temp_dir() / (Path(src).stem + ".htm")
        if word_convert(src, "HTML", tmp):
            return H.body_of(read_text_file(str(tmp))), title
        from striprtf.striprtf import rtf_to_text
        return H.text_to_html(rtf_to_text(read_text_file(src), errors="ignore")), title
    if ext == "odt":
        return odt_to_html(src), title
    if ext == "epub":
        return epub_to_html(src)
    if ext in ("mobi", "fb2", "xps", "cbz", "pdf"):
        import pymupdf
        doc = pymupdf.open(src)
        parts = []
        for page in doc:
            parts.append(H.text_to_html(page.get_text("text")))
        t = doc.metadata.get("title") or title
        doc.close()
        return "\n".join(parts), t
    raise ConvertError(f"can't read .{ext}")


def odt_to_html(src: str) -> str:
    from odf.opendocument import load
    from odf import text as T, teletype
    doc = load(src)
    out = []
    for el in doc.text.childNodes:
        tag = getattr(el, "qname", (None, ""))[1]
        txt = teletype.extractText(el)
        if not txt.strip():
            continue
        if tag == "h":
            lvl = el.getAttribute("outlinelevel") or 1
            out.append(f"<h{lvl}>{htmlmod.escape(txt)}</h{lvl}>")
        elif tag == "list":
            out.append("<ul>" + "".join(f"<li>{htmlmod.escape(teletype.extractText(li))}</li>"
                                        for li in el.getElementsByType(T.ListItem)) + "</ul>")
        else:
            out.append(f"<p>{htmlmod.escape(txt)}</p>")
    return "\n".join(out)


def epub_to_html(src: str) -> tuple[str, str]:
    import base64
    from ebooklib import epub, ITEM_DOCUMENT, ITEM_IMAGE
    book = epub.read_epub(src, options={"ignore_ncx": True})
    images = {}
    for it in book.get_items_of_type(ITEM_IMAGE):
        images[os.path.basename(it.get_name())] = (it.media_type, it.get_content())
    parts = []
    for idref, _ in book.spine:
        it = book.get_item_with_id(idref)
        if not it or it.get_type() != ITEM_DOCUMENT:
            continue
        body = H.body_of(it.get_content().decode("utf-8", errors="ignore"))

        def rep(m):
            name = os.path.basename(m.group(2))
            if name in images:
                mt, data = images[name]
                return f'{m.group(1)}="data:{mt};base64,{base64.b64encode(data).decode()}"'
            return m.group(0)
        body = re.sub(r'(src|xlink:href)="([^"]+)"', rep, body)
        parts.append(body)
    titles = book.get_metadata("DC", "title")
    return "\n".join(parts), (titles[0][0] if titles else Path(src).stem)


# ------------------------------------------------------------------ convert
def convert(src: str, fmt: str, progress=noop) -> list[Path]:
    fmt = fmt.upper()
    ext = Path(src).suffix.lower().lstrip(".")
    dest = out_path(src, fmt.lower())
    progress(0.1, "Reading")
    # HTML from Word files: our own converter keeps the look and gives one self-contained file
    if fmt == "HTML" and ext in ("docx", "doc", "docm", "rtf", "odt", "dotx"):
        from .richhtml import docx_html_page
        source = src
        if ext != "docx":
            tmp = temp_dir() / (Path(src).stem + ".docx")
            if word_convert(src, "DOCX", tmp, progress) or (find_soffice() and soffice_convert(src, "docx", tmp)):
                source = str(tmp)
        if source.lower().endswith(".docx"):
            progress(0.5, "Writing HTML")
            dest.write_text(docx_html_page(source), encoding="utf-8")
            return [dest]
    # best fidelity: Office for Word-family sources
    if ext in ("docx", "doc", "docm", "rtf", "odt", "dotx") and fmt in ("PDF", "DOCX", "RTF", "ODT", "TXT"):
        if word_convert(src, fmt, dest, progress):
            return [dest]
        if fmt in ("PDF", "ODT", "DOCX", "RTF") and find_soffice():
            progress(0.3, "Using LibreOffice")
            try:
                soffice_convert(src, fmt.lower(), dest)
                return [dest]
            except ConvertError:
                pass
    if ext == "epub" and fmt == "PDF":
        import pymupdf
        progress(0.3, "Laying out pages")
        with pymupdf.open(src) as d:
            pdf_bytes = d.convert_to_pdf()
        dest.write_bytes(pdf_bytes)
        return [dest]
    if ext in ("mobi", "fb2", "xps", "cbz") and fmt == "PDF":
        import pymupdf
        with pymupdf.open(src) as d:
            dest.write_bytes(d.convert_to_pdf())
        return [dest]
    html, title = to_html(src)
    progress(0.5, f"Writing {fmt}")
    base = str(Path(src).parent)
    if fmt == "PDF":
        H.html_to_pdf(html, dest, base_dir=base)
    elif fmt == "DOCX":
        H.html_to_docx(html, dest, base_dir=base, title=title)
    elif fmt == "TXT":
        if ext in ("md", "markdown"):
            dest.write_text(H.html_to_text(html), encoding="utf-8")
        else:
            dest.write_text(H.html_to_text(html), encoding="utf-8")
    elif fmt == "MD":
        dest.write_text(H.html_to_md(html), encoding="utf-8")
    elif fmt == "HTML":
        dest.write_text(H.wrap(html, title), encoding="utf-8")
    elif fmt == "RTF":
        H.html_to_rtf(html, dest)
    elif fmt == "EPUB":
        H.html_to_epub(html, dest, title)
    elif fmt == "ODT":
        H.html_to_odt(html, dest)
    else:
        raise ConvertError(f"can't make {fmt} from .{ext}")
    progress(1, "Done")
    return [dest]


# ------------------------------------------------------------------ tools
def stats(src: str) -> dict:
    ext = Path(src).suffix.lower().lstrip(".")
    try:
        html, _ = to_html(src)
        text = H.html_to_text(html)
    except Exception:
        text = read_text_file(src)
    words = re.findall(r"\b\w[\w'’-]*\b", text)
    sentences = [s for s in re.split(r"[.!?]+\s", text) if s.strip()]
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    d = {
        "Words": f"{len(words):,}",
        "Characters": f"{len(text):,}",
        "Characters (no spaces)": f"{len(re.sub(r'\s', '', text)):,}",
        "Sentences": f"{len(sentences):,}",
        "Paragraphs": f"{len(paras):,}",
        "Lines": f"{text.count(chr(10)) + 1:,}",
        "Reading time": f"{max(1, round(len(words) / 238))} min",
        "Speaking time": f"{max(1, round(len(words) / 140))} min",
    }
    if ext == "docx":
        try:
            from docx import Document
            doc = Document(src)
            d["Tables"] = str(len(doc.tables))
            d["Images"] = str(len(doc.inline_shapes))
        except Exception:
            pass
    if words:
        from collections import Counter
        common = Counter(w.lower() for w in words if len(w) > 4).most_common(5)
        d["Top words"] = ", ".join(f"{w} ({n})" for w, n in common)
    return d


def replace(src: str, find: str, repl: str, case: bool = False, regex: bool = False) -> tuple[Path, int]:
    flags = 0 if case else re.I
    pat = re.compile(find if regex else re.escape(find), flags)
    ext = Path(src).suffix.lower().lstrip(".")
    count = 0
    dest = out_path(src, ext, "Replaced")
    if ext == "docx":
        from docx import Document
        doc = Document(src)

        def paras():
            for p in doc.paragraphs:
                yield p
            for t in doc.tables:
                for row in t.rows:
                    for cell in row.cells:
                        yield from cell.paragraphs
            for s in doc.sections:
                for part in (s.header, s.footer):
                    yield from part.paragraphs
        for p in paras():
            if not pat.search(p.text):
                continue
            done = False
            for r in p.runs:
                if pat.search(r.text):
                    r.text, n = pat.subn(repl, r.text)
                    count += n
                    done = True
            if not done or pat.search(p.text):
                # match spans runs: collapse into first run, keeps first run's style
                new, n = pat.subn(repl, p.text)
                count += n
                for i, r in enumerate(p.runs):
                    r.text = new if i == 0 else ""
        doc.save(dest)
    else:
        text = read_text_file(src)
        text, count = pat.subn(repl, text)
        dest.write_text(text, encoding="utf-8")
    return dest, count


def merge(srcs: list[str]) -> Path:
    ext = Path(srcs[0]).suffix.lower().lstrip(".")
    if ext == "docx":
        from docx import Document
        try:
            from docxcompose.composer import Composer
            master = Document(srcs[0])
            comp = Composer(master)
            for s in srcs[1:]:
                master.add_page_break()
                comp.append(Document(s))
            dest = out_path(srcs[0], "docx", "Merged")
            comp.save(dest)
            return dest
        except ImportError:
            html = "".join(to_html(s)[0] + "<hr/>" for s in srcs)
            dest = out_path(srcs[0], "docx", "Merged")
            return H.html_to_docx(html, dest)
    if ext in ("txt", "md", "markdown", "log", "html", "htm"):
        sep = "\n\n---\n\n" if ext in ("md", "markdown") else "\n\n"
        dest = out_path(srcs[0], ext, "Merged")
        if ext in ("html", "htm"):
            dest.write_text(H.wrap("<hr/>".join(to_html(s)[0] for s in srcs)), encoding="utf-8")
        else:
            dest.write_text(sep.join(read_text_file(s).strip() for s in srcs) + "\n", encoding="utf-8")
        return dest
    html = "<hr/>".join(to_html(s)[0] for s in srcs)
    dest = out_path(srcs[0], "docx", "Merged")
    return H.html_to_docx(html, dest)


def to_utf8(src: str) -> Path:
    text = read_text_file(src)
    dest = out_path(src, Path(src).suffix.lstrip("."), "UTF-8")
    dest.write_text(text.replace("\r\n", "\n").replace("\n", "\r\n"), encoding="utf-8")
    return dest


def extract_media(src: str) -> Path:
    """Pull embedded images out of DOCX / PPTX / XLSX / EPUB / ODT (they're zips)."""
    folder = out_folder(src, "Images")
    n = 0
    with zipfile.ZipFile(src) as z:
        for name in z.namelist():
            low = name.lower()
            if re.search(r"\.(png|jpe?g|gif|bmp|tiff?|webp|svg|emf|wmf|heic|avif)$", low) and \
                    any(seg in low for seg in ("media/", "pictures/", "images/", "image/", "img/", "oebps/")):
                target = folder / os.path.basename(name)
                i = 2
                while target.exists():
                    target = folder / f"{Path(name).stem} {i}{Path(name).suffix}"
                    i += 1
                target.write_bytes(z.read(name))
                n += 1
    if n == 0:
        shutil.rmtree(folder, ignore_errors=True)
        raise ConvertError("no images in there")
    return folder


def read_docx_meta(src: str) -> dict:
    from docx import Document
    cp = Document(src).core_properties
    return {k: (str(getattr(cp, k)) if getattr(cp, k) is not None else "") for k in
            ("title", "subject", "author", "keywords", "comments", "category", "last_modified_by", "created", "modified")}


def write_docx_meta(src: str, values: dict | None, strip=False) -> Path:
    from docx import Document
    doc = Document(src)
    cp = doc.core_properties
    for k in ("title", "subject", "author", "keywords", "comments", "category", "last_modified_by"):
        if strip:
            setattr(cp, k, "")
        elif values and k in values:
            setattr(cp, k, values[k])
    dest = out_path(src, "docx", "No Metadata" if strip else "Edited")
    doc.save(dest)
    return dest
