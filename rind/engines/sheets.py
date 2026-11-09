"""Spreadsheets (XLSX/XLS/ODS/CSV/TSV) and data files (JSON/XML/YAML)."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from . import htmlconv as H
from .common import ConvertError, noop, out_path, out_folder
from .docs import read_text_file


def read_all(src: str) -> dict:
    """-> {sheet_name: DataFrame}"""
    import pandas as pd
    ext = Path(src).suffix.lower().lstrip(".")
    if ext in ("csv", "tsv", "txt"):
        text = read_text_file(src)
        sep = "\t" if ext == "tsv" else None
        if sep is None:
            try:
                sep = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|").delimiter
            except csv.Error:
                sep = ","
        df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False)
        return {Path(src).stem[:31] or "Sheet1": df}
    if ext in ("xlsx", "xlsm"):
        return pd.read_excel(src, sheet_name=None, engine="openpyxl")
    if ext == "xls":
        return pd.read_excel(src, sheet_name=None, engine="xlrd")
    if ext == "ods":
        return pd.read_excel(src, sheet_name=None, engine="odf")
    if ext in ("json", "jsonl", "xml", "yaml", "yml"):
        return {"Data": data_frame(src)}
    raise ConvertError(f"can't read .{ext} as a table")


def data_load(src: str):
    ext = Path(src).suffix.lower().lstrip(".")
    text = read_text_file(src)
    if ext == "json":
        return json.loads(text)
    if ext == "jsonl":
        return [json.loads(l) for l in text.splitlines() if l.strip()]
    if ext in ("yaml", "yml"):
        import yaml
        return yaml.safe_load(text)
    if ext == "xml":
        import xml.etree.ElementTree as ET
        root = ET.fromstring(text.encode("utf-8"))

        def conv(e):
            d = dict(e.attrib)
            kids = list(e)
            if not kids:
                t = (e.text or "").strip()
                return t if not d else {**d, "#text": t} if t else d
            for k in kids:
                v = conv(k)
                if k.tag in d:
                    if not isinstance(d[k.tag], list):
                        d[k.tag] = [d[k.tag]]
                    d[k.tag].append(v)
                else:
                    d[k.tag] = v
            return d
        return {root.tag: conv(root)}
    raise ConvertError(f"can't read .{ext}")


def _records(obj):
    """Find the most table-like list inside a JSON structure."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        best = None
        for v in obj.values():
            r = _records(v)
            if isinstance(r, list) and (best is None or len(r) > len(best)):
                best = r
        return best if best is not None else [obj]
    return [{"value": obj}]


def data_frame(src: str):
    import pandas as pd
    recs = _records(data_load(src))
    if recs and all(isinstance(r, dict) for r in recs):
        return pd.json_normalize(recs)
    return pd.DataFrame({"value": recs})


def _first(sheets: dict):
    return next(iter(sheets.values()))


def _html_tables(sheets: dict) -> str:
    out = []
    for name, df in sheets.items():
        if len(sheets) > 1:
            out.append(f"<h2>{H.htmlmod.escape(str(name))}</h2>")
        out.append(df.fillna("").to_html(index=False, border=0, escape=True))
    return "\n".join(out)


def excel_pdf(src: str, dest: Path) -> bool:
    from . import office
    src_abs, dest_abs = str(Path(src).resolve()), str(Path(dest).resolve())

    def job(xl):
        wb = xl.Workbooks.Open(src_abs, ReadOnly=True, UpdateLinks=0, AddToMru=False)
        try:
            wb.ExportAsFixedFormat(0, dest_abs)
        finally:
            wb.Close(False)
        return True

    return bool(office.run("Excel.Application", job)) and dest.exists()


def convert(src: str, fmt: str, progress=noop) -> list[Path]:
    import pandas as pd
    fmt = fmt.upper()
    ext = Path(src).suffix.lower().lstrip(".")
    progress(0.1, "Reading")
    if ext in ("json", "jsonl", "xml", "yaml", "yml") and fmt in ("JSON", "YAML", "XML", "TXT"):
        return [data_convert(src, fmt)]
    if fmt == "PDF" and ext in ("xlsx", "xls", "xlsm", "ods", "csv"):
        dest = out_path(src, "pdf")
        if excel_pdf(src, dest):
            return [dest]
    sheets = read_all(src)
    progress(0.5, f"Writing {fmt}")
    if fmt in ("XLSX", "ODS"):
        dest = out_path(src, fmt.lower())
        with pd.ExcelWriter(dest, engine="openpyxl" if fmt == "XLSX" else "odf") as w:
            for name, df in sheets.items():
                df.to_excel(w, sheet_name=str(name)[:31] or "Sheet1", index=False)
            if fmt == "XLSX":
                for ws in w.book.worksheets:
                    for col in ws.columns:
                        width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                        ws.column_dimensions[col[0].column_letter].width = min(60, max(8, width + 2))
                    ws.freeze_panes = "A2"
        return [dest]
    if fmt in ("CSV", "TSV"):
        sep = "," if fmt == "CSV" else "\t"
        if len(sheets) == 1:
            dest = out_path(src, fmt.lower())
            _first(sheets).to_csv(dest, index=False, sep=sep, encoding="utf-8-sig")
            return [dest]
        folder = out_folder(src, fmt)
        for name, df in sheets.items():
            df.to_csv(folder / f"{name}.{fmt.lower()}", index=False, sep=sep, encoding="utf-8-sig")
        return [folder]
    if fmt == "JSON":
        dest = out_path(src, "json")
        if len(sheets) == 1:
            dest.write_text(_first(sheets).to_json(orient="records", indent=2, force_ascii=False), encoding="utf-8")
        else:
            payload = {str(k): json.loads(v.to_json(orient="records", force_ascii=False)) for k, v in sheets.items()}
            dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return [dest]
    if fmt == "HTML":
        dest = out_path(src, "html")
        dest.write_text(H.wrap(_html_tables(sheets), Path(src).stem), encoding="utf-8")
        return [dest]
    if fmt == "MD":
        dest = out_path(src, "md")
        parts = []
        for name, df in sheets.items():
            if len(sheets) > 1:
                parts.append(f"## {name}\n")
            cols = [str(c) for c in df.columns]
            parts.append("| " + " | ".join(cols) + " |")
            parts.append("|" + "|".join("---" for _ in cols) + "|")
            for row in df.fillna("").astype(str).itertuples(index=False):
                parts.append("| " + " | ".join(v.replace("|", "\\|") for v in row) + " |")
            parts.append("")
        dest.write_text("\n".join(parts), encoding="utf-8")
        return [dest]
    if fmt == "PDF":
        dest = out_path(src, "pdf")
        wide = max(len(df.columns) for df in sheets.values()) > 6
        H.html_to_pdf(_html_tables(sheets), dest, landscape=wide)
        return [dest]
    if fmt == "XML":
        dest = out_path(src, "xml")
        _first(sheets).to_xml(dest, index=False, parser="etree")
        return [dest]
    if fmt == "YAML":
        import yaml
        dest = out_path(src, "yaml")
        dest.write_text(yaml.safe_dump(json.loads(_first(sheets).to_json(orient="records")), allow_unicode=True, sort_keys=False), encoding="utf-8")
        return [dest]
    if fmt == "TXT":
        dest = out_path(src, "txt")
        dest.write_text(_first(sheets).to_string(index=False), encoding="utf-8")
        return [dest]
    raise ConvertError(f"can't make {fmt}")


def data_convert(src: str, fmt: str) -> Path:
    obj = data_load(src)
    dest = out_path(src, fmt.lower())
    if fmt == "JSON":
        dest.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    elif fmt == "YAML":
        import yaml
        dest.write_text(yaml.safe_dump(obj, allow_unicode=True, sort_keys=False), encoding="utf-8")
    elif fmt == "XML":
        import xml.etree.ElementTree as ET

        def build(parent, key, val):
            key = "".join(c if c.isalnum() or c in "_-." else "_" for c in str(key)) or "item"
            if key[0].isdigit():
                key = "_" + key
            if isinstance(val, list):
                for v in val:
                    build(parent, key, v)
            elif isinstance(val, dict):
                el = ET.SubElement(parent, key)
                for k, v in val.items():
                    build(el, k, v)
            else:
                ET.SubElement(parent, key).text = "" if val is None else str(val)
        if isinstance(obj, dict) and len(obj) == 1:
            k, v = next(iter(obj.items()))
            root = ET.Element(str(k))
            if isinstance(v, dict):
                for kk, vv in v.items():
                    build(root, kk, vv)
            else:
                build(root, "item", v)
        else:
            root = ET.Element("root")
            build(root, "item", obj) if isinstance(obj, list) else [build(root, k, v) for k, v in (obj or {}).items()]
        ET.indent(root)
        ET.ElementTree(root).write(dest, encoding="utf-8", xml_declaration=True)
    elif fmt == "TXT":
        dest.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    return dest


# ------------------------------------------------------------------ tools
def merge(srcs: list[str], how: str = "sheets") -> Path:
    """how: sheets (one tab per file) | stack (append rows)"""
    import pandas as pd
    all_sheets = [(Path(s).stem, read_all(s)) for s in srcs]
    if how == "stack":
        frames = [df.assign(**{"Source": name}) for name, sh in all_sheets for df in sh.values()]
        merged = pd.concat(frames, ignore_index=True)
        ext = Path(srcs[0]).suffix.lower().lstrip(".")
        if ext in ("csv", "tsv"):
            dest = out_path(srcs[0], ext, "Merged")
            merged.to_csv(dest, index=False, sep="\t" if ext == "tsv" else ",", encoding="utf-8-sig")
            return dest
        dest = out_path(srcs[0], "xlsx", "Merged")
        merged.to_excel(dest, index=False)
        return dest
    dest = out_path(srcs[0], "xlsx", "Merged")
    used = set()
    with pd.ExcelWriter(dest, engine="openpyxl") as w:
        for name, sh in all_sheets:
            for sname, df in sh.items():
                label = (name if len(sh) == 1 else f"{name} {sname}")[:31]
                base, i = label, 2
                while label in used:
                    label = f"{base[:28]} {i}"
                    i += 1
                used.add(label)
                df.to_excel(w, sheet_name=label, index=False)
    return dest


def split_sheets(src: str) -> Path:
    sheets = read_all(src)
    folder = out_folder(src, "Sheets")
    for name, df in sheets.items():
        df.to_csv(folder / f"{name}.csv", index=False, encoding="utf-8-sig")
    return folder


def dedupe(src: str) -> tuple[Path, int]:
    import pandas as pd
    sheets = read_all(src)
    removed = 0
    out = {}
    for k, df in sheets.items():
        before = len(df)
        out[k] = df.drop_duplicates()
        removed += before - len(out[k])
    ext = Path(src).suffix.lower().lstrip(".")
    if ext in ("csv", "tsv") and len(out) == 1:
        dest = out_path(src, ext, "Deduped")
        _first(out).to_csv(dest, index=False, sep="\t" if ext == "tsv" else ",", encoding="utf-8-sig")
    else:
        dest = out_path(src, "xlsx", "Deduped")
        with pd.ExcelWriter(dest, engine="openpyxl") as w:
            for k, df in out.items():
                df.to_excel(w, sheet_name=str(k)[:31], index=False)
    return dest, removed


def transpose(src: str) -> Path:
    import pandas as pd
    sheets = read_all(src)
    ext = Path(src).suffix.lower().lstrip(".")
    tx = {}
    for k, df in sheets.items():
        t = df.T.reset_index()
        t.columns = [str(c) for c in range(len(t.columns))]
        tx[k] = t
    if ext in ("csv", "tsv") and len(tx) == 1:
        dest = out_path(src, ext, "Transposed")
        _first(tx).to_csv(dest, index=False, header=False, sep="\t" if ext == "tsv" else ",", encoding="utf-8-sig")
    else:
        dest = out_path(src, "xlsx", "Transposed")
        with pd.ExcelWriter(dest, engine="openpyxl") as w:
            for k, df in tx.items():
                df.to_excel(w, sheet_name=str(k)[:31], index=False, header=False)
    return dest


def stats(src: str) -> dict:
    sheets = read_all(src)
    d = {"Sheets": str(len(sheets))}
    for name, df in list(sheets.items())[:6]:
        num = df.apply(lambda c: __import__("pandas").to_numeric(c, errors="coerce")).select_dtypes("number")
        d[f"{name}"] = f"{len(df):,} rows × {len(df.columns)} cols"
        for col in list(num.columns)[:4]:
            s = num[col].dropna()
            if len(s):
                d[f"  {col}"] = f"sum {s.sum():,.2f} · avg {s.mean():,.2f} · min {s.min():,.2f} · max {s.max():,.2f}"
    return d


def pretty(src: str, minify=False) -> Path:
    ext = Path(src).suffix.lower().lstrip(".")
    text = read_text_file(src)
    dest = out_path(src, ext, "Minified" if minify else "Formatted")
    if ext in ("json", "jsonl"):
        obj = data_load(src)
        dest.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False) if minify
                        else json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    elif ext == "xml":
        import xml.dom.minidom as md
        dom = md.parseString(text.encode("utf-8"))
        if minify:
            import re
            dest.write_text(re.sub(r">\s+<", "><", text).strip(), encoding="utf-8")
        else:
            out = dom.toprettyxml(indent="  ")
            dest.write_text("\n".join(l for l in out.splitlines() if l.strip()), encoding="utf-8")
    else:
        import yaml
        obj = yaml.safe_load(text)
        if minify:
            dest.write_text(yaml.safe_dump(obj, default_flow_style=True, width=10 ** 9), encoding="utf-8")
        else:
            dest.write_text(yaml.safe_dump(obj, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return dest
