# Rind

Drag a file, tap **Shift**, drop it on a slice. A Windows rebuild of the Mac app *Tangerine*, with a solid dark UI instead of liquid glass. Everything runs on your PC: no uploads, no accounts, no trial.

## Install

1. Have **Python 3.11–3.13** (`winget install Python.Python.3.12` is easiest).
2. Double-click **`install.bat`**. It creates `.venv\` in this folder, installs everything (ffmpeg comes bundled), adds a Start Menu shortcut, the right-click entry, Send To and auto-start, then launches Rind.
3. Look for the orange wheel in the system tray.

- `run.bat` starts it manually.
- `uninstall.bat` removes the shell bits. Delete the folder afterwards.
- `build_exe.bat` (optional) bundles a standalone `dist\Rind\Rind.exe`.

## Using it

| Do this while dragging a file in Explorer or on the desktop | Wheel |
|---|---|
| tap **Shift** | **Convert**: every format that file can become |
| tap **Ctrl** | **Tools**: compress, crop, trim, merge… |
| tap **Alt** | **Actions**: copy, move, float, zip… |

Drop on a slice to run it. Drop on the centre or press **Esc** to cancel. You can switch wheels mid-drag by tapping a different key. Results are saved **next to the original**, and the original is never touched. A toast in the corner shows progress, the size change, and **Open / Show in folder / Undo** (Undo sends the new file to the Recycle Bin).

There are other ways in:

- Select files, press **Ctrl+Alt+Space**, then click a slice. **Tab** or **← →** switches wheels.
- Right-click → **Rind…** (on Windows 11 it's under *Show more options*).
- Right-click → **Send to → Rind**. This is the best route for big multi-selections.
- Tray → **Open files…**

Select several files to batch. Many images plus PDF combines them into one PDF.

## What it does

**Convert** (all local)

| From | To |
|---|---|
| Images: JPG PNG WEBP HEIC AVIF TIFF BMP GIF ICO SVG PSD TGA JP2 QOI… (+ RAW with the optional rawpy) | JPG PNG WEBP HEIC AVIF TIFF BMP GIF ICO SVG PDF DOCX |
| Animated GIF | MP4 WEBM animated WEBP / AVIF, PNG, JPG, PDF (one page per frame) |
| PDF | DOCX (layout-aware), JPG/PNG/WEBP (every page), TXT, MD, HTML, EPUB, XLSX (detects tables), PPTX, multipage TIFF, SVG |
| DOCX DOC ODT RTF TXT MD HTML | PDF DOCX TXT MD HTML RTF EPUB ODT |
| EPUB MOBI FB2 XPS CBZ | PDF EPUB TXT DOCX HTML MD |
| XLSX XLS ODS CSV TSV | XLSX CSV ODS TSV PDF HTML JSON MD |
| JSON XML YAML | JSON CSV XLSX YAML XML TXT |
| PPTX PPT ODP | PDF PPTX JPG PNG (every slide), TXT, MD |
| Video: MP4 MOV MKV WEBM AVI WMV FLV M4V MPEG 3GP TS MTS… | MP4 MOV MKV WEBM AVI WMV FLV M4V MPEG 3GP GIF + MP3 M4A WAV |
| Audio: MP3 M4A AAC WAV FLAC OGG OPUS AIFF WMA AMR AC3… | MP3 M4A WAV FLAC OGG OPUS AIFF AAC WMA |
| Subtitles: SRT VTT ASS SSA SBV | SRT VTT ASS TXT |
| Archives: ZIP 7Z RAR TAR GZ BZ2 XZ ZST CAB ISO | ZIP 7Z TAR TAR.GZ TAR.BZ2 TAR.XZ |
| Fonts: TTF OTF WOFF WOFF2 | TTF OTF WOFF WOFF2 |

Video conversions that only need a new container are remuxed losslessly in about a second.

**Tools**

- **Images:** Compress (quality / target size / %, with live estimate), Metadata (view, edit, map link, strip location or everything), Edit (auto, light, colour, sharpness, vignette, B&W, rotate/flip, hold to compare), Annotate (arrows, boxes, circles, numbered steps, text, pen, highlighter, undo/redo), Add BG (gradients, solids, blurred backdrop, padding, corners, shadow, aspect), Crop (aspect presets, exact px, rotate/flip), Redact (black box, pixelate, blur), Resize, Rotate, Watermark, Read QR, Make PDF, Collage, Make GIF
- **PDF:** Compress, Merge (drag to order, mixes in images), Split (every page / every N / halves / ranges), Organize (thumbnail grid: reorder, rotate, delete), Rotate, Protect (AES-256), Unlock, Extract images, Metadata, Redact (real redaction by search, presets for emails/phones/cards/IBANs/URLs/dates, or drawn boxes), Watermark, Page numbers
- **Video:** Compress (CRF or exact target size with 2-pass, e.g. "under 8 MB"), Trim (timeline with thumbnails, playback, frame-accurate or fast), Crop, Speed (keeps pitch), Split (at marked cuts), Join, Snapshot, Extract audio, Mute, Rotate/flip, Reverse, Resize, To GIF, Metadata
- **Audio:** Compress, Trim (waveform), Normalize (loudness targets), Bleep (beep or silence over marked ranges), Channels (mono/stereo/swap/L/R), Speed, Split, Join, Fade, Reverse, Metadata
- **Docs:** Stats (words, reading time…), Find & replace (keeps formatting), Merge, Metadata, Extract images, fix encoding to UTF-8
- **Sheets:** Merge (one tab per file, or stack rows), Split sheets to CSV, Remove duplicates, Transpose, Stats
- **Slides:** Extract images, Speaker notes → TXT, Stats, Metadata
- **Subtitles:** Shift timing (plus frame-rate fixes), Clean (strips tags, [music], SPEAKER:), Merge
- **Archives:** Extract (unwraps single folders, asks for passwords), Contents, Protect (AES ZIP/7Z), Test
- **JSON/XML/YAML:** Format, Minify

**Actions** (any file or folder): Copy (paste anywhere in Explorer), Move to, Copy to, Copy Path, Terminal here, **Float** (an always-on-top shelf you park files on and drag back out later), Zip, Open with, batch Rename (pattern / counter / find-replace / case, live preview), Checksum (MD5/SHA-1/SHA-256 + compare).

## Notes

- **Best quality for Word, PowerPoint and Excel:** if Microsoft Office is installed, Rind drives it for DOCX/PPTX/XLSX→PDF. Otherwise it uses LibreOffice if found, and otherwise built-in renderers. Slides without Office or LibreOffice render approximately. You can switch this off in Settings → Output.
- **Odd archives** (some RAR5 / ISO / CAB) fall back to 7-Zip if installed, or Windows' own `tar.exe`.
- **Settings** (double-click the tray icon) let you remap the Shift/Ctrl/Alt keys, change the hotkey, output folder, quality defaults, wheel size, accent colour and drag-detection mode, and see job history.
- If the wheel ever pops up when you didn't want it, set **Settings → Keys → Drag detection** to *Strict*. If it never pops up, try *Loose*. **Pause drag detection** is in the tray menu.
- Logs are in `%APPDATA%\Rind\log.txt`.

## Layout

```
rind/
  app.py            tray, single-instance, wiring
  winhooks.py       drag+key detection (GetAsyncKeyState + drag image), hotkey, Explorer selection, shell integration
  wheel.py          the radial menu (drop target)
  registry.py       file kinds -> convert targets / tools / actions
  tools.py          what each slice does
  jobs.py           background jobs + toasts
  engines/          images, pdfs, docs (+htmlconv), sheets, slides, media (ffmpeg), archives, subs (+fonts)
  dialogs/          crop, add bg, annotate, redact, edit, compress, metadata, timeline, organize, rename, checksum, float, settings
```
