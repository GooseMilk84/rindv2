"""HTML that keeps the look.

docx_to_html: walks the DOCX XML directly and resolves run/paragraph/style/theme formatting,
so colours, highlights, shading, fonts, sizes, alignment, lists, images and table fills survive.

pdf_to_layout_html: page-faithful HTML. Each page is its graphics (fills, lines, images) rendered
as a background, with the real text laid on top in its original colour, font, size and position.
Text stays selectable and searchable."""
from __future__ import annotations

import base64
import html as H
import re
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
V = "{urn:schemas-microsoft-com:vml}"

HIGHLIGHT = {"yellow": "#ffff00", "green": "#00ff00", "cyan": "#00ffff", "magenta": "#ff00ff", "blue": "#0000ff",
             "red": "#ff0000", "darkBlue": "#000080", "darkCyan": "#008080", "darkGreen": "#008000",
             "darkMagenta": "#800080", "darkRed": "#800000", "darkYellow": "#808000", "darkGray": "#808080",
             "lightGray": "#c0c0c0", "black": "#000000", "white": "#ffffff"}
THEME_ALIAS = {"text1": "dk1", "background1": "lt1", "text2": "dk2", "background2": "lt2", "dark1": "dk1",
               "light1": "lt1", "dark2": "dk2", "light2": "lt2", "hyperlink": "hlink", "followedHyperlink": "folHlink"}


def _attr(el, name):
    return None if el is None else el.get(W + name)


def _on(el) -> bool | None:
    """toggle property: <w:b/> = on, <w:b w:val="0"/> = off, missing = None"""
    if el is None:
        return None
    v = el.get(W + "val")
    return v not in ("0", "false", "off", "none")


class DocxHtml:
    def __init__(self, src: str):
        from docx import Document
        self.doc = Document(src)
        self.part = self.doc.part
        self.theme = self._theme_colors()
        self.styles = {}
        st_el = self.part.styles.element
        for s in st_el.findall(W + "style"):
            self.styles[s.get(W + "styleId")] = s
        dd = st_el.find(W + "docDefaults")
        self.default_rpr = dd.find(f"{W}rPrDefault/{W}rPr") if dd is not None else None
        self.default_ppr = dd.find(f"{W}pPrDefault/{W}pPr") if dd is not None else None
        self.default_pstyle = next((sid for sid, s in self.styles.items()
                                    if s.get(W + "type") == "paragraph" and s.get(W + "default") in ("1", "true")), None)
        self.numfmt = self._numbering()

    # ------------------------------------------------------------ theme / numbering
    def _theme_colors(self) -> dict:
        out = {}
        try:
            from lxml import etree
            for rel in self.part.rels.values():
                if rel.reltype.endswith("/theme"):
                    root = etree.fromstring(rel.target_part.blob)
                    scheme = root.find(f".//{A}clrScheme")
                    for c in scheme:
                        tag = c.tag.split("}")[1]
                        s = c.find(A + "srgbClr")
                        y = c.find(A + "sysClr")
                        if s is not None:
                            out[tag] = s.get("val")
                        elif y is not None:
                            out[tag] = y.get("lastClr")
        except Exception:
            pass
        return out

    def _numbering(self) -> dict:
        """(numId, ilvl) -> numFmt"""
        out = {}
        try:
            num = self.part.numbering_part.element
        except Exception:
            return out
        abstract = {}
        for an in num.findall(W + "abstractNum"):
            lv = {}
            for l in an.findall(W + "lvl"):
                f = l.find(W + "numFmt")
                lv[l.get(W + "ilvl")] = f.get(W + "val") if f is not None else "bullet"
            abstract[an.get(W + "abstractNumId")] = lv
        for n in num.findall(W + "num"):
            aid = n.find(W + "abstractNumId")
            if aid is not None:
                for ilvl, fmt in abstract.get(aid.get(W + "val"), {}).items():
                    out[(n.get(W + "numId"), ilvl)] = fmt
        return out

    def theme_hex(self, name, tint=None, shade=None) -> str | None:
        key = THEME_ALIAS.get(name, name)
        hx = self.theme.get(key)
        if not hx:
            return None
        r, g, b = (int(hx[i:i + 2], 16) for i in (0, 2, 4))
        if shade:
            k = int(shade, 16) / 255
            r, g, b = (int(c * k) for c in (r, g, b))
        if tint:
            k = int(tint, 16) / 255
            r, g, b = (int(c + (255 - c) * (1 - k)) for c in (r, g, b))
        return f"#{r:02x}{g:02x}{b:02x}"

    # ------------------------------------------------------------ property resolution
    def _style_chain(self, sid, kind="rPr"):
        out, seen = [], set()
        while sid and sid not in seen and sid in self.styles:
            seen.add(sid)
            st = self.styles[sid]
            el = st.find(W + kind)
            if el is not None:
                out.append(el)
            b = st.find(W + "basedOn")
            sid = b.get(W + "val") if b is not None else None
        return out

    def _pstyle(self, p):
        ppr = p.find(W + "pPr")
        ps = ppr.find(W + "pStyle") if ppr is not None else None
        return ps.get(W + "val") if ps is not None else self.default_pstyle

    def run_chain(self, r, p):
        chain = []
        rpr = r.find(W + "rPr")
        if rpr is not None:
            chain.append(rpr)
            rs = rpr.find(W + "rStyle")
            if rs is not None:
                chain += self._style_chain(rs.get(W + "val"))
        chain += self._style_chain(self._pstyle(p))
        if self.default_rpr is not None:
            chain.append(self.default_rpr)
        return chain

    def para_chain(self, p):
        chain = []
        ppr = p.find(W + "pPr")
        if ppr is not None:
            chain.append(ppr)
        chain += self._style_chain(self._pstyle(p), "pPr")
        if self.default_ppr is not None:
            chain.append(self.default_ppr)
        return chain

    @staticmethod
    def first(chain, tag):
        for el in chain:
            f = el.find(W + tag)
            if f is not None:
                return f
        return None

    def color_of(self, el) -> str | None:
        if el is None:
            return None
        tc = _attr(el, "themeColor")
        if tc:
            c = self.theme_hex(tc, _attr(el, "themeTint"), _attr(el, "themeShade"))
            if c:
                return c
        v = _attr(el, "val")
        if v and v != "auto" and re.fullmatch(r"[0-9A-Fa-f]{6}", v):
            return "#" + v.lower()
        return None

    def fill_of(self, shd) -> str | None:
        if shd is None:
            return None
        tf = _attr(shd, "themeFill")
        if tf:
            c = self.theme_hex(tf, _attr(shd, "themeFillTint"), _attr(shd, "themeFillShade"))
            if c:
                return c
        f = _attr(shd, "fill")
        if f and f != "auto" and re.fullmatch(r"[0-9A-Fa-f]{6}", f):
            return "#" + f.lower()
        return None

    def run_css(self, r, p) -> str:
        ch = self.run_chain(r, p)
        css = []
        c = self.color_of(self.first(ch, "color"))
        if c:
            css.append(f"color:{c}")
        hl = self.first(ch, "highlight")
        if hl is not None and _attr(hl, "val") in HIGHLIGHT:
            css.append(f"background-color:{HIGHLIGHT[_attr(hl, 'val')]}")
        else:
            f = self.fill_of(self.first(ch, "shd"))
            if f:
                css.append(f"background-color:{f}")
        sz = self.first(ch, "sz")
        if sz is not None and _attr(sz, "val"):
            css.append(f"font-size:{int(_attr(sz, 'val')) / 2:g}pt")
        rf = self.first(ch, "rFonts")
        if rf is not None:
            face = _attr(rf, "ascii") or _attr(rf, "hAnsi")
            if not face and _attr(rf, "asciiTheme"):
                face = None
            if face:
                css.append(f"font-family:'{face}',sans-serif")
        if _on(self.first(ch, "b")):
            css.append("font-weight:bold")
        if _on(self.first(ch, "i")):
            css.append("font-style:italic")
        deco = []
        u = self.first(ch, "u")
        if u is not None and _attr(u, "val") not in (None, "none"):
            deco.append("underline")
        if _on(self.first(ch, "strike")) or _on(self.first(ch, "dstrike")):
            deco.append("line-through")
        if deco:
            css.append("text-decoration:" + " ".join(deco))
        if _on(self.first(ch, "caps")):
            css.append("text-transform:uppercase")
        if _on(self.first(ch, "smallCaps")):
            css.append("font-variant:small-caps")
        va = self.first(ch, "vertAlign")
        if va is not None and _attr(va, "val") in ("superscript", "subscript"):
            css.append("vertical-align:" + ("super" if _attr(va, "val") == "superscript" else "sub") + ";font-size:smaller")
        if _on(self.first(ch, "vanish")):
            css.append("display:none")
        return ";".join(css)

    # ------------------------------------------------------------ content
    def run_html(self, r, p) -> str:
        parts = []
        for child in r:
            tag = child.tag.split("}")[1]
            if tag == "t":
                parts.append(H.escape(child.text or ""))
            elif tag == "tab":
                parts.append("&emsp;")
            elif tag in ("br", "cr"):
                parts.append("<br/>" if _attr(child, "type") != "page" else '<br class="pb"/>')
            elif tag in ("drawing", "pict"):
                parts.append(self.image_html(child))
            elif tag == "sym":
                try:
                    parts.append(chr(int(_attr(child, "char"), 16)))
                except Exception:
                    pass
        text = "".join(parts)
        if not text:
            return ""
        css = self.run_css(r, p)
        return f'<span style="{css}">{text}</span>' if css else text

    def image_html(self, el) -> str:
        blip = el.find(f".//{A}blip")
        rid = blip.get(R + "embed") if blip is not None else None
        if rid is None:
            im = el.find(f".//{V}imagedata")
            rid = im.get(R + "id") if im is not None else None
        if not rid or rid not in self.part.related_parts:
            return ""
        part = self.part.related_parts[rid]
        ct = part.content_type
        data = part.blob
        if ct in ("image/x-emf", "image/x-wmf", "image/emf", "image/wmf"):
            return ""
        if ct == "image/tiff":
            try:
                import io
                from PIL import Image
                buf = io.BytesIO()
                Image.open(io.BytesIO(data)).save(buf, "PNG")
                data, ct = buf.getvalue(), "image/png"
            except Exception:
                return ""
        ext = el.find(f".//{WP}extent")
        size = ""
        if ext is not None:
            w = int(ext.get("cx", 0)) / 9525
            h = int(ext.get("cy", 0)) / 9525
            if w and h:
                size = f' width="{w:.0f}" height="{h:.0f}"'
        return f'<img src="data:{ct};base64,{base64.b64encode(data).decode()}"{size} style="max-width:100%;height:auto"/>'

    def para_inner(self, p) -> str:
        out = []
        for child in p:
            tag = child.tag.split("}")[1]
            if tag == "r":
                out.append(self.run_html(child, p))
            elif tag == "hyperlink":
                rid = child.get(R + "id")
                href = ""
                if rid and rid in self.part.rels:
                    href = self.part.rels[rid].target_ref
                elif child.get(W + "anchor"):
                    href = "#" + child.get(W + "anchor")
                inner = "".join(self.run_html(r, p) for r in child.findall(W + "r"))
                out.append(f'<a href="{H.escape(href)}">{inner}</a>' if href else inner)
            elif tag in ("ins", "smartTag", "customXml", "sdt", "fldSimple"):
                for r in child.iter(W + "r"):
                    out.append(self.run_html(r, p))
        return "".join(out)

    def para_css(self, p) -> tuple[str, str]:
        ch = self.para_chain(p)
        css = []
        jc = self.first(ch, "jc")
        if jc is not None:
            v = _attr(jc, "val")
            css.append("text-align:" + {"center": "center", "right": "right", "end": "right", "both": "justify",
                                        "distribute": "justify"}.get(v, "left"))
        f = self.fill_of(self.first(ch, "shd"))
        if f:
            css.append(f"background-color:{f};padding:2pt 4pt")
        ind = self.first(ch, "ind")
        if ind is not None:
            left = _attr(ind, "left") or _attr(ind, "start")
            if left and left.lstrip("-").isdigit() and int(left) > 0:
                css.append(f"margin-left:{int(left) / 20:g}pt")
        sp = self.first(ch, "spacing")
        if sp is not None:
            for k, prop in (("before", "margin-top"), ("after", "margin-bottom")):
                v = _attr(sp, k)
                if v and v.isdigit():
                    css.append(f"{prop}:{int(v) / 20:g}pt")
        sid = self._pstyle(p) or ""
        st = self.styles.get(sid)
        name = ""
        if st is not None and st.find(W + "name") is not None:
            name = st.find(W + "name").get(W + "val", "")
        tag = "p"
        m = re.match(r"heading\s*(\d)", name, re.I)
        if m:
            tag = f"h{min(6, int(m.group(1)))}"
        elif name.lower() == "title":
            tag = "h1"
        elif name.lower() == "subtitle":
            tag = "h2"
        return tag, ";".join(css)

    def list_info(self, p):
        ch = self.para_chain(p)
        numpr = self.first(ch, "numPr")
        if numpr is None:
            return None
        nid = numpr.find(W + "numId")
        il = numpr.find(W + "ilvl")
        nid = _attr(nid, "val") if nid is not None else None
        if not nid or nid == "0":
            return None
        ilvl = _attr(il, "val") if il is not None else "0"
        fmt = self.numfmt.get((nid, ilvl), "bullet")
        return ("ul" if fmt in ("bullet", "none") else "ol"), int(ilvl or 0)

    def table_html(self, tbl) -> str:
        rows = []
        tblpr = tbl.find(W + "tblPr")
        borders = tblpr is not None and tblpr.find(W + "tblBorders") is not None
        style = tblpr.find(W + "tblStyle") if tblpr is not None else None
        if style is not None and "grid" in (_attr(style, "val") or "").lower():
            borders = True
        for tr in tbl.findall(W + "tr"):
            cells = []
            for tc in tr.findall(W + "tc"):
                tcpr = tc.find(W + "tcPr")
                attrs, css = "", []
                if tcpr is not None:
                    gs = tcpr.find(W + "gridSpan")
                    if gs is not None and _attr(gs, "val"):
                        attrs += f' colspan="{_attr(gs, "val")}"'
                    fill = self.fill_of(tcpr.find(W + "shd"))
                    if fill:
                        css.append(f"background-color:{fill}")
                    va = tcpr.find(W + "vAlign")
                    if va is not None:
                        css.append("vertical-align:" + {"center": "middle", "bottom": "bottom"}.get(_attr(va, "val"), "top"))
                    vm = tcpr.find(W + "vMerge")
                    if vm is not None and _attr(vm, "val") != "restart":
                        css.append("border-top-color:transparent")
                inner = self.blocks(tc)
                st = f' style="{";".join(css)}"' if css else ""
                cells.append(f"<td{attrs}{st}>{inner}</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        cls = ' class="grid"' if borders else ""
        return f"<table{cls}>" + "".join(rows) + "</table>"

    def blocks(self, parent) -> str:
        out = []
        list_stack: list[str] = []

        def close_lists(to=0):
            while len(list_stack) > to:
                out.append(f"</{list_stack.pop()}>")
        for el in parent:
            tag = el.tag.split("}")[1]
            if tag == "p":
                li = self.list_info(el)
                inner = self.para_inner(el)
                ptag, css = self.para_css(el)
                if li:
                    kind, lvl = li
                    while len(list_stack) > lvl + 1:
                        out.append(f"</{list_stack.pop()}>")
                    if len(list_stack) == lvl + 1 and list_stack[-1] != kind:   # bullets -> numbers at same level
                        out.append(f"</{list_stack.pop()}>")
                    while len(list_stack) < lvl + 1:
                        list_stack.append(kind)
                        out.append(f"<{kind}>")
                    out.append(f'<li style="{css}">{inner or "&nbsp;"}</li>' if css else f"<li>{inner or '&nbsp;'}</li>")
                    continue
                close_lists()
                st = f' style="{css}"' if css else ""
                out.append(f"<{ptag}{st}>{inner or '&nbsp;'}</{ptag}>")
            elif tag == "tbl":
                close_lists()
                out.append(self.table_html(el))
            elif tag == "sdt":
                content = el.find(W + "sdtContent")
                if content is not None:
                    close_lists()
                    out.append(self.blocks(content))
        close_lists()
        return "\n".join(out)

    def render(self) -> tuple[str, str, str]:
        body = self.doc.element.body
        html = self.blocks(body)
        # document-wide defaults
        css = []
        drf = self.first([self.default_rpr] if self.default_rpr is not None else [], "rFonts")
        face = _attr(drf, "ascii") if drf is not None else None
        if not face and self.default_pstyle:
            rf = self.first(self._style_chain(self.default_pstyle), "rFonts")
            face = _attr(rf, "ascii") if rf is not None else None
        theme_font = None
        try:
            from lxml import etree
            for rel in self.part.rels.values():
                if rel.reltype.endswith("/theme"):
                    root = etree.fromstring(rel.target_part.blob)
                    mf = root.find(f".//{A}minorFont/{A}latin")
                    theme_font = mf.get("typeface") if mf is not None else None
        except Exception:
            pass
        css.append(f"font-family:'{face or theme_font or 'Calibri'}','Segoe UI',Arial,sans-serif")
        sz = self.first(([self.default_rpr] if self.default_rpr is not None else []) + self._style_chain(self.default_pstyle), "sz")
        css.append(f"font-size:{int(_attr(sz, 'val')) / 2:g}pt" if sz is not None and _attr(sz, "val") else "font-size:11pt")
        bg = self.doc.element.find(W + "background")
        page_bg = self.color_of(bg) if bg is not None else None
        if page_bg is None and bg is not None and _attr(bg, "color"):
            c = _attr(bg, "color")
            page_bg = "#" + c.lower() if re.fullmatch(r"[0-9A-Fa-f]{6}", c or "") else None
        title = self.doc.core_properties.title or ""
        return html, ";".join(css), page_bg or "#ffffff"


def docx_to_html(src: str) -> tuple[str, str, str]:
    """-> (body_html, body_css, page_background)"""
    return DocxHtml(src).render()


def docx_html_page(src: str) -> str:
    body, css, bg = docx_to_html(src)
    title = H.escape(Path(src).stem)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
html {{ background:#e9e9ec; }}
.page {{ {css}; background:{bg}; color:#000; max-width:8.27in; margin:24px auto; padding:0.9in 1in;
        box-shadow:0 2px 14px rgba(0,0,0,.15); line-height:1.15; box-sizing:border-box; }}
.page p, .page li {{ margin:0 0 8pt; }}
.page h1,.page h2,.page h3,.page h4,.page h5,.page h6 {{ margin:14pt 0 6pt; line-height:1.2; }}
.page table {{ border-collapse:collapse; margin:6pt 0; width:auto; }}
.page td {{ padding:3pt 6pt; vertical-align:top; }}
.page td p {{ margin:0; }}
.page table.grid td {{ border:1px solid #999; }}
.page img {{ max-width:100%; height:auto; }}
.page .pb {{ display:block; page-break-after:always; }}
@media print {{ html {{ background:none; }} .page {{ box-shadow:none; margin:0; max-width:none; }} }}
</style></head><body><div class="page">
{body}
</div></body></html>"""


# ------------------------------------------------------------------ PDF -> layout-faithful HTML
def _font_stack(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("courier", "mono", "consol", "cour")):
        return "'Courier New',Consolas,monospace"
    if any(k in n for k in ("times", "serif", "georgia", "garamond", "cambria", "minion", "tiro")) and "sans" not in n:
        return "'Times New Roman',Georgia,serif"
    base = re.sub(r"^[A-Z]{6}\+", "", name).split("-")[0].split(",")[0]
    return f"'{base}',Arial,Helvetica,sans-serif"


def pdf_to_layout_html(src: str, progress=None, zoom: float = 2.0) -> str:
    import pymupdf
    doc = pymupdf.open(src)
    k = 96 / 72                       # pt -> css px
    pages = []
    n = doc.page_count
    for pi in range(n):
        if progress:
            progress(pi / n, f"Page {pi + 1} of {n}")
        page = doc[pi]
        rect = page.rect
        # 1) background = page with its text removed (keeps fills, lines, images, colours)
        tmp = pymupdf.open()
        tmp.insert_pdf(doc, from_page=pi, to_page=pi)
        tp = tmp[0]
        tp.add_redact_annot(tp.rect)
        try:
            tp.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        except (TypeError, AttributeError):
            tp.apply_redactions(images=0)          # older PyMuPDF: graphics may be lost, text still removed
        pix = tp.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        # blank-ish backgrounds compress far better as PNG
        samples = pix.samples
        try:
            jpg = pix.tobytes("jpeg", jpg_quality=88)
        except TypeError:
            jpg = pix.tobytes("jpg")
        png = pix.tobytes("png")
        img, mime = (png, "png") if len(png) < len(jpg) * 1.15 else (jpg, "jpeg")
        tmp.close()
        # 2) text spans on top
        spans = []
        d = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES)
        for b in d["blocks"]:
            for line in b.get("lines", []):
                dx, dy = line.get("dir", (1, 0))
                rot = math_deg(dy, dx)
                for s in line["spans"]:
                    t = s["text"]
                    if not t.strip():
                        continue
                    x0, y0, x1, y1 = s["bbox"]
                    size = s["size"]
                    col = f"#{s['color']:06x}"
                    flags = s["flags"]
                    fname = s.get("font", "")
                    bold = bool(flags & 16) or "bold" in fname.lower() or "black" in fname.lower() or "heavy" in fname.lower()
                    ital = bool(flags & 2) or "italic" in fname.lower() or "oblique" in fname.lower()
                    asc = s.get("ascender", 0.9) or 0.9
                    ox, oy = s.get("origin", (x0, y1))
                    top = oy - asc * size
                    style = (f"left:{ox * k:.2f}px;top:{top * k:.2f}px;font-size:{size * k:.2f}px;color:{col};"
                             f"font-family:{_font_stack(fname)};"
                             + ("font-weight:700;" if bold else "") + ("font-style:italic;" if ital else "")
                             + (f"transform:rotate({rot:.1f}deg);transform-origin:0 {asc * size * k:.2f}px;" if abs(rot) > 0.5 else ""))
                    spans.append(f'<span style="{style}" data-w="{(x1 - x0) * k:.2f}">{H.escape(t)}</span>')
        links = []
        for ln in page.get_links():
            uri = ln.get("uri")
            if uri:
                r = ln["from"]
                links.append(f'<a class="lnk" href="{H.escape(uri)}" style="left:{r.x0 * k:.1f}px;top:{r.y0 * k:.1f}px;'
                             f'width:{r.width * k:.1f}px;height:{r.height * k:.1f}px"></a>')
        pages.append(f'<div class="pg" style="width:{rect.width * k:.1f}px;height:{rect.height * k:.1f}px;'
                     f'background-image:url(data:image/{mime};base64,{base64.b64encode(img).decode()})">'
                     + "".join(spans) + "".join(links) + "</div>")
    title = H.escape(doc.metadata.get("title") or Path(src).stem)
    doc.close()
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
html {{ background:#e9e9ec; }}
body {{ margin:0; padding:24px 0; }}
.pg {{ position:relative; margin:0 auto 24px; background-size:100% 100%; background-color:#fff;
      box-shadow:0 2px 14px rgba(0,0,0,.18); overflow:hidden; }}
.pg span {{ position:absolute; white-space:pre; line-height:1; transform-origin:0 0; }}
.pg span::selection {{ background:rgba(0,120,215,.35); }}
.pg .lnk {{ position:absolute; display:block; }}
@media print {{ html {{ background:none; }} body {{ padding:0; }} .pg {{ box-shadow:none; margin:0; page-break-after:always; }} }}
</style></head><body>
{"".join(pages)}
<script>
// match each text run to its original width (fonts on this PC may differ slightly from the PDF's)
document.fonts.ready.then(function () {{
  document.querySelectorAll('.pg span[data-w]').forEach(function (s) {{
    var want = parseFloat(s.dataset.w), have = s.getBoundingClientRect().width;
    if (have > 0 && want > 0 && Math.abs(want - have) > 0.5 && !s.style.transform) {{
      var k = want / have; if (k > 0.6 && k < 1.6) s.style.transform = 'scaleX(' + k.toFixed(4) + ')';
    }}
  }});
}});
</script>
</body></html>"""


def math_deg(dy, dx):
    import math
    return math.degrees(math.atan2(dy, dx))
