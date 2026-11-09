"""HTML <-> everything. HTML is the hub format for documents."""
from __future__ import annotations

import base64
import html as htmlmod
import io
import re
from html.parser import HTMLParser
from pathlib import Path

DOC_CSS = """
body { font-family: sans-serif; font-size: 11pt; line-height: 1.45; color: #111; }
h1 { font-size: 20pt; margin: 14pt 0 8pt; } h2 { font-size: 16pt; margin: 12pt 0 6pt; }
h3 { font-size: 13pt; margin: 10pt 0 4pt; } h4,h5,h6 { font-size: 11pt; }
p { margin: 0 0 7pt; } pre, code { font-family: monospace; font-size: 9.5pt; }
pre { background: #f3f3f3; padding: 6pt; white-space: pre-wrap; }
table { border-collapse: collapse; margin: 6pt 0; } td, th { border: 1px solid #999; padding: 3pt 5pt; font-size: 9.5pt; }
th { background: #eee; } blockquote { margin-left: 16pt; color: #444; } img { max-width: 100%; }
"""


def text_to_html(text: str) -> str:
    parts = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    out = []
    for p in parts:
        if not p.strip():
            continue
        out.append("<p>" + "<br/>".join(htmlmod.escape(l) for l in p.split("\n")) + "</p>")
    return "\n".join(out)


def wrap(body: str, title: str = "") -> str:
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{htmlmod.escape(title)}</title>"
            f"<style>{DOC_CSS} body {{ max-width: 820px; margin: 32px auto; padding: 0 16px; }}</style></head>"
            f"<body>{body}</body></html>")


def body_of(html: str) -> str:
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.S | re.I)
    body = m.group(1) if m else html
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S | re.I)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S | re.I)
    return body


def html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style"]):
            t.decompose()
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for li in soup.find_all("li"):
            li.insert_before("• ")
        blocks = []
        for el in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "tr", "blockquote"]):
            if el.find_parent(["p", "li", "pre", "tr", "blockquote"]) and el.name not in ("tr",):
                continue
            if el.name == "tr":
                blocks.append("\t".join(c.get_text(" ", strip=True) for c in el.find_all(["td", "th"])))
            else:
                blocks.append(el.get_text("", strip=False).strip())
        txt = "\n\n".join(b for b in blocks if b)
        return txt or soup.get_text("\n").strip()
    except ImportError:
        return re.sub(r"<[^>]+>", "", html)


def html_to_md(html: str) -> str:
    try:
        from markdownify import markdownify
        md = markdownify(body_of(html), heading_style="ATX", bullets="-")
        md = re.sub(r"!\[([^\]]*)\]\(data:[^)]+\)", r"![\1](image)", md)
        return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    except ImportError:
        return html_to_text(html)


def split_data_uris(html: str):
    """Pull data: URIs out so MuPDF Story/EPUB can reference them by name."""
    assets = {}

    def rep(m):
        mime, data = m.group(1), m.group(2)
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/svg+xml": "svg",
               "image/webp": "webp"}.get(mime, "bin")
        name = f"img{len(assets) + 1}.{ext}"
        try:
            assets[name] = base64.b64decode(data)
        except Exception:
            return m.group(0)
        return f'src="{name}"'
    html = re.sub(r'src="data:([^;"]+);base64,([^"]+)"', rep, html)
    return html, assets


def _fix_img_widths(html: str, assets: dict, maxw: int = 480) -> str:
    from PIL import Image

    def rep(m):
        tag, name = m.group(0), m.group(1)
        if name in assets and "width=" not in tag:
            try:
                im = Image.open(io.BytesIO(assets[name]))
                w = min(maxw, im.width)
                return tag.replace("<img", f'<img width="{w}"', 1)
            except Exception:
                pass
        return tag
    return re.sub(r'<img[^>]*src="([^"]+)"[^>]*>', rep, html)


def html_to_pdf(html: str, dest: Path, base_dir: str | None = None, paper="a4", landscape=False) -> Path:
    import pymupdf
    body, assets = split_data_uris(body_of(html))
    body = _fix_img_widths(body, assets)
    arch = pymupdf.Archive()
    if base_dir:
        try:
            arch.add(base_dir)
        except Exception:
            pass
    for name, data in assets.items():
        arch.add((data, name))
    story = pymupdf.Story(html=body, user_css=DOC_CSS, archive=arch)
    writer = pymupdf.DocumentWriter(str(dest))
    mediabox = pymupdf.paper_rect(paper + ("-l" if landscape else ""))
    where = mediabox + (54, 54, -54, -54)
    more = 1
    pages = 0
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
        pages += 1
        if pages > 5000:
            break
    writer.close()
    return dest


# ------------------------------------------------------------------ html -> docx
class _DocxBuilder(HTMLParser):
    def __init__(self, doc, base_dir=None):
        super().__init__(convert_charrefs=True)
        self.doc = doc
        self.base = base_dir
        self.para = None
        self.fmt = {"b": 0, "i": 0, "u": 0, "code": 0, "s": 0}
        self.lists = []
        self.pre = 0
        self.table = None
        self.row = None
        self.cell = None
        self.skip = 0
        self.heading = None

    def _p(self, style=None):
        self.para = self.doc.add_paragraph()
        if style:
            try:
                self.para.style = style
            except Exception:
                pass
        return self.para

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.cell == "pending" and tag not in ("td", "th", "tr", "table", "b", "strong", "i", "em"):
            if tag == "br":
                self._cells[-1].append(("\n", {}))
            return
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._p(f"Heading {min(int(tag[1]), 9)}")
            self.heading = tag
        elif tag in ("p", "div", "section", "article") and not self.pre:
            if self.para is None or self.para.text or tag == "p":
                self._p("Quote" if self.fmt.get("bq") else None)
        elif tag in ("ul", "ol"):
            self.lists.append(tag)
        elif tag == "li":
            kind = self.lists[-1] if self.lists else "ul"
            self._p("List Number" if kind == "ol" else "List Bullet")
        elif tag in ("b", "strong", "th"):
            self.fmt["b"] += 1
            if tag == "th":
                self._cell_start()
        elif tag in ("i", "em"):
            self.fmt["i"] += 1
        elif tag == "u":
            self.fmt["u"] += 1
        elif tag in ("s", "del", "strike"):
            self.fmt["s"] += 1
        elif tag == "code":
            self.fmt["code"] += 1
        elif tag == "pre":
            self.pre += 1
            self._p()
        elif tag == "blockquote":
            self.fmt["bq"] = self.fmt.get("bq", 0) + 1
            self._p("Quote")
        elif tag == "br":
            if self.para is not None:
                self.para.add_run().add_break()
        elif tag == "hr":
            self._p().add_run("―" * 30)
            self.para = None
        elif tag == "table":
            self.table = self.doc.add_table(rows=0, cols=0)
            try:
                self.table.style = "Table Grid"
            except Exception:
                pass
            self._ncols = 0
        elif tag == "tr" and self.table is not None:
            self.row = []
            self._cells = []
        elif tag == "td":
            self._cell_start()
        elif tag == "img":
            self._img(a.get("src", ""))

    def _cell_start(self):
        if self.table is None:
            return
        self._cells.append([])
        self.cell = "pending"

    def _img(self, src):
        from docx.shared import Inches
        data = None
        if src.startswith("data:"):
            try:
                data = base64.b64decode(src.split(",", 1)[1])
            except Exception:
                data = None
        elif self.base:
            p = Path(self.base) / src
            if p.exists():
                data = p.read_bytes()
        if not data:
            return
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            buf = io.BytesIO()
            if im.format not in ("PNG", "JPEG", "GIF", "BMP"):
                im.convert("RGBA").save(buf, "PNG")
                data = buf.getvalue()
            w_in = min(6.0, im.width / 96)
            par = self.para if self.para is not None else self._p()
            par.add_run().add_picture(io.BytesIO(data), width=Inches(w_in))
        except Exception:
            pass

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "div"):
            self.para = None
            self.heading = None
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
        elif tag in ("b", "strong"):
            self.fmt["b"] = max(0, self.fmt["b"] - 1)
        elif tag in ("i", "em"):
            self.fmt["i"] = max(0, self.fmt["i"] - 1)
        elif tag == "u":
            self.fmt["u"] = max(0, self.fmt["u"] - 1)
        elif tag in ("s", "del", "strike"):
            self.fmt["s"] = max(0, self.fmt["s"] - 1)
        elif tag == "code":
            self.fmt["code"] = max(0, self.fmt["code"] - 1)
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self.para = None
        elif tag == "blockquote":
            self.fmt["bq"] = max(0, self.fmt.get("bq", 1) - 1)
            self.para = None
        elif tag in ("td", "th"):
            if tag == "th":
                self.fmt["b"] = max(0, self.fmt["b"] - 1)
            self.cell = None
        elif tag == "tr" and self.table is not None:
            cells = getattr(self, "_cells", [])
            if cells:
                ncols = max(len(self.table.columns), len(cells))
                from docx.shared import Inches
                while len(self.table.columns) < ncols:
                    self.table.add_column(Inches(1.3))
                r = self.table.add_row()
                for i, runs in enumerate(cells):
                    par = r.cells[i].paragraphs[0]
                    for text, f in runs:
                        run = par.add_run(text)
                        run.bold = bool(f.get("b"))
                        run.italic = bool(f.get("i"))
            self._cells = []
        elif tag == "table":
            self.table = None
            self.para = None

    def handle_data(self, data):
        if self.skip:
            return
        if not self.pre:
            data = re.sub(r"\s+", " ", data)
            if not data.strip() and (self.para is None or not self.para.text):
                return
        if self.cell == "pending" and self.table is not None:
            self._cells[-1].append((data, dict(self.fmt)))
            return
        if self.para is None:
            self._p()
        if self.para is not None and not self.para.text:
            data = data.lstrip() if not self.pre else data
        run = self.para.add_run(data)
        run.bold = bool(self.fmt["b"]) or None
        run.italic = bool(self.fmt["i"]) or None
        run.underline = bool(self.fmt["u"]) or None
        if self.fmt["s"]:
            run.font.strike = True
        if self.fmt["code"] or self.pre:
            run.font.name = "Consolas"


def html_to_docx(html: str, dest: Path, base_dir: str | None = None, title: str = "") -> Path:
    from docx import Document
    doc = Document()
    if title:
        doc.core_properties.title = title
    b = _DocxBuilder(doc, base_dir)
    b.feed(body_of(html))
    b.close()
    # drop leading empty paragraph python-docx sometimes leaves
    doc.save(dest)
    return dest


# ------------------------------------------------------------------ html -> rtf
def _rtf_escape(s: str) -> str:
    out = []
    for ch in s:
        c = ord(ch)
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\line ")
        elif c > 127:
            if c > 32767:
                c -= 65536
            out.append(f"\\u{c}?")
        else:
            out.append(ch)
    return "".join(out)


class _RtfBuilder(HTMLParser):
    SIZES = {"h1": 40, "h2": 32, "h3": 28, "h4": 24, "h5": 22, "h6": 22}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0
        self.pre = 0
        self.lists = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in self.SIZES:
            self.out.append(f"\\pard\\sb200\\sa100\\b\\fs{self.SIZES[tag]} ")
        elif tag == "p":
            self.out.append("\\pard\\sa120\\fs22 ")
        elif tag in ("b", "strong", "th"):
            self.out.append("{\\b ")
        elif tag in ("i", "em"):
            self.out.append("{\\i ")
        elif tag == "u":
            self.out.append("{\\ul ")
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
        elif tag == "li":
            mark = "\\bullet"
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                mark = f"{self.lists[-1][1]}."
            self.out.append(f"\\pard\\li{360 * len(self.lists)}\\fi-240\\sa60\\fs22 {mark}\\tab ")
        elif tag == "br":
            self.out.append("\\line ")
        elif tag == "pre":
            self.pre += 1
            self.out.append("\\pard\\sa120\\f1\\fs19 ")
        elif tag in ("td", "th"):
            pass
        elif tag == "tr":
            self.out.append("\\pard\\fs20 ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.SIZES:
            self.out.append("\\b0\\par\n")
        elif tag in ("p", "li", "tr"):
            self.out.append("\\par\n")
        elif tag in ("b", "strong", "i", "em", "u", "th"):
            self.out.append("}")
            if tag == "th":
                self.out.append("\\tab ")
        elif tag == "td":
            self.out.append("\\tab ")
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self.out.append("\\f0\\par\n")

    def handle_data(self, data):
        if self.skip:
            return
        if not self.pre:
            data = re.sub(r"\s+", " ", data)
        self.out.append(_rtf_escape(data))


def html_to_rtf(html: str, dest: Path) -> Path:
    b = _RtfBuilder()
    b.feed(body_of(html))
    rtf = ("{\\rtf1\\ansi\\ansicpg1252\\deff0{\\fonttbl{\\f0\\fswiss Calibri;}{\\f1\\fmodern Consolas;}}"
           "\\viewkind4\\uc1\\fs22\n" + "".join(b.out) + "\n}")
    dest.write_text(rtf, encoding="ascii", errors="replace")
    return dest


# ------------------------------------------------------------------ html -> epub
def html_to_epub(html: str, dest: Path, title: str = "Document", author: str = "") -> Path:
    from ebooklib import epub
    body, assets = split_data_uris(body_of(html))
    book = epub.EpubBook()
    book.set_identifier("rind-" + str(abs(hash(title + body[:200]))))
    book.set_title(title or "Document")
    book.set_language("en")
    if author:
        book.add_author(author)
    css = epub.EpubItem(uid="style", file_name="style.css", media_type="text/css", content=DOC_CSS.encode())
    book.add_item(css)
    for name, data in assets.items():
        mt = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "svg": "image/svg+xml",
              "webp": "image/webp"}.get(name.rsplit(".", 1)[-1], "application/octet-stream")
        book.add_item(epub.EpubItem(uid=name, file_name=name, media_type=mt, content=data))
    chunks = re.split(r"(?=<h[12][^>]*>)", body)
    chunks = [c for c in chunks if c.strip()] or [body]
    chapters = []
    for i, c in enumerate(chunks):
        m = re.search(r"<h[12][^>]*>(.*?)</h[12]>", c, re.S)
        ctitle = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else (title if i == 0 else f"Part {i + 1}")
        ch = epub.EpubHtml(title=ctitle or f"Part {i + 1}", file_name=f"chap_{i + 1}.xhtml", lang="en")
        ch.content = f"<html><head><link rel='stylesheet' href='style.css'/></head><body>{c}</body></html>"
        ch.add_item(css)
        book.add_item(ch)
        chapters.append(ch)
    book.toc = chapters
    book.spine = ["nav"] + chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(dest), book)
    return dest


# ------------------------------------------------------------------ html -> odt
def html_to_odt(html: str, dest: Path) -> Path:
    from odf.opendocument import OpenDocumentText
    from odf.style import Style, TextProperties
    from odf.text import H, P
    from bs4 import BeautifulSoup
    doc = OpenDocumentText()
    hs = {}
    for lvl, size in ((1, "20pt"), (2, "16pt"), (3, "13pt")):
        st = Style(name=f"Heading {lvl}", family="paragraph")
        st.addElement(TextProperties(fontsize=size, fontweight="bold"))
        doc.styles.addElement(st)
        hs[lvl] = st
    soup = BeautifulSoup(body_of(html), "html.parser")
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "tr"]):
        if el.find_parent(["li", "p"]) and el.name in ("p",):
            continue
        txt = el.get_text(" ", strip=True) if el.name != "pre" else el.get_text()
        if not txt:
            continue
        if el.name.startswith("h"):
            lvl = min(int(el.name[1]), 3)
            doc.text.addElement(H(outlinelevel=lvl, stylename=hs[lvl], text=txt))
        elif el.name == "li":
            doc.text.addElement(P(text="• " + txt))
        elif el.name == "tr":
            doc.text.addElement(P(text="\t".join(c.get_text(" ", strip=True) for c in el.find_all(["td", "th"]))))
        else:
            doc.text.addElement(P(text=txt))
    doc.save(str(dest))
    return dest
