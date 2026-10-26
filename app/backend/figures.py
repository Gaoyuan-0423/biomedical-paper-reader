"""Geometry-aware PDF parsing: column-ordered text and caption-anchored figure crops.

Ported from the user's ReadBox project (python/parsing.py + presentation.py) and
trimmed to what this app needs. Coordinates are PDF points, top-left origin.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

HEADING = re.compile(
    r"^(?:\d+(?:\.\d+)*[.\s]+)?(?:abstract|introduction|background|related work|materials(?: and methods)?|methods|"
    r"results|discussion|conclusions?|limitations?|references|acknowledg\w*|data availability|supplement\w*|"
    r"摘要|引言|背景|材料与方法|方法|结果|讨论|结论|参考文献)(?:\s|[:：.]|$)",
    re.I,
)
CAPTION = re.compile(r"^(?:fig(?:ure)?\.?\s*([sS]?\d+)|图\s*([sS]?\d+))\b[\s.:：|–—-]*", re.I)
FOOTER = re.compile(r"\bet\s+al\b.*(?:doi\s*:|10\.\d{4,9}/)|^(?:copyright|©|downloaded from)", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
RENDER_SCALE = 2
MAX_PAGES = 600


# ---------- text geometry ----------

def words_text(words):
    """Spans split at font changes; only insert a space for a real horizontal gap."""
    text = ""
    previous = None
    for word in words:
        if previous:
            gap = word["x0"] - previous["x1"]
            size = min(float(previous.get("size", 10)), float(word.get("size", 10)))
            if gap > max(0.3, size * 0.06):
                text += " "
        text += word["text"]
        previous = word
    return text


def lines_from_words(words):
    rows = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        tolerance = max(3, float(word.get("size", 10)) * 0.35)
        target = next(
            (r for r in reversed(rows[-6:]) if abs(r[0]["top"] - word["top"]) < tolerance or abs(r[0]["bottom"] - word["bottom"]) < tolerance),
            None,
        )
        if target is None:
            rows.append([word])
        else:
            target.append(word)
    lines = []
    for row in rows:
        fragments = [[]]
        for word in sorted(row, key=lambda w: w["x0"]):
            if fragments[-1] and word["x0"] - fragments[-1][-1]["x1"] > max(22, float(word.get("size", 10)) * 2.3):
                fragments.append([])
            fragments[-1].append(word)
        for fragment in fragments:
            lines.append({
                "text": words_text(fragment),
                "bbox": [min(w["x0"] for w in fragment), min(w["top"] for w in fragment), max(w["x1"] for w in fragment), max(w["bottom"] for w in fragment)],
                "size": statistics.median(float(w.get("size", 10)) for w in fragment),
                "bold": sum("bold" in w.get("fontname", "").lower() for w in fragment) > len(fragment) / 2,
            })
    return lines


def reading_order(lines, width):
    """Two-column aware ordering: full-width spans split the page into regions."""
    mid = width / 2
    left = [l for l in lines if l["bbox"][2] < mid + 4 and l["bbox"][0] < width * 0.3]
    right = [l for l in lines if l["bbox"][0] >= mid - 5]
    if len(left) < 4 or len(right) < 4:
        return sorted(lines, key=lambda l: (l["bbox"][1], l["bbox"][0]))
    spans = sorted([l for l in lines if l["bbox"][0] < mid - 30 and l["bbox"][2] > mid + 30], key=lambda l: l["bbox"][1])
    remaining = [l for l in lines if l not in spans]
    result = []
    for span in spans + [None]:
        boundary = span["bbox"][1] if span else float("inf")
        region = [l for l in remaining if l["bbox"][1] < boundary]
        remaining = [l for l in remaining if l not in region]
        result.extend(sorted(region, key=lambda l: (l["bbox"][0] >= mid - 5, l["bbox"][1], l["bbox"][0])))
        if span:
            result.append(span)
    return result


def heading_level(line, body_size):
    text = line["text"].strip()
    if not text or len(text) > 150 or CAPTION.match(text):
        return 0
    if HEADING.fullmatch(text) or (HEADING.match(text) and line["bold"] and len(text.split()) <= 8 and not text.endswith((".", ",", ";"))):
        return 1
    if len(text) < 130 and (line["size"] >= body_size * 1.25 or (line["bold"] and not text.endswith((".", ",", ";")))):
        return 2 if line["size"] < body_size * 1.5 else 1
    return 0


def joinable_boxes(a, b):
    if not a or not b:
        return False
    height = max(a[3] - a[1], b[3] - b[1], 1)
    return b[1] > a[1] and -height * 0.25 <= b[1] - a[3] <= height * 0.8 and abs(a[0] - b[0]) <= height * 0.65 and min(a[2], b[2]) > max(a[0], b[0])


def join_source_text(a, b):
    if a.endswith("\u00ad"):
        return a[:-1] + b
    if re.search(r"[^\W\d_]-$", a) and re.match(r"[^\W\d_]", b):
        return a + b
    cjk = re.search(r"[\u3400-\u9fff]$", a) and re.match(r"[\u3400-\u9fff]", b)
    return a + ("" if cjk else " ") + b


def paragraph_lines(lines, body_size):
    """Reflow contiguous lines inside a column into paragraphs; keep headings separate."""
    result = []
    previous = None
    for line in lines:
        level = heading_level(line, body_size)
        heading_continuation = (
            previous is not None and level and result[-1]["level"] == level and line["bold"] == previous["bold"]
            and not HEADING.fullmatch(line["text"]) and not HEADING.fullmatch(previous["text"])
        )
        can_join = (
            previous is not None and ((not level and not result[-1]["level"]) or heading_continuation)
            and not CAPTION.match(line["text"])
            and abs(line["size"] - previous["size"]) <= max(1, body_size * 0.12)
            and joinable_boxes(previous["bbox"], line["bbox"])
        )
        if can_join and re.search(r"[.!?。！？]$", previous["text"]):
            a, b = previous["bbox"], line["bbox"]
            if a[2] - a[0] < (b[2] - b[0]) * 0.8:
                can_join = False
        if can_join:
            current = result[-1]
            current["text"] = join_source_text(current["text"], line["text"])
            a, b = current["bbox"], line["bbox"]
            current["bbox"] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
        else:
            result.append({**line, "bbox": list(line["bbox"]), "level": level})
        previous = line
    return result


def reflow_caption(text):
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if FOOTER.search(line) and i > 0 and all(not t.strip() or re.fullmatch(r"\d{1,4}", t.strip()) for t in lines[i + 1:]):
            lines = lines[:i]
            break
    paragraphs = []
    current = ""
    for line in lines:
        line = line.strip()
        if not line:
            if current:
                paragraphs.append(current)
                current = ""
            continue
        line = re.sub(r"([\w]{2,})-[ \t]+(?=[^\W\d_])", r"\1-", line)
        current = join_source_text(current, line) if current else line
    if current:
        paragraphs.append(current)
    text = "\n\n".join(paragraphs)
    known = set(re.findall(r"\b[\w]+-[\w]+\b", text))
    return re.sub(r"\b([\w]+-[\w]+)[ \t]+([\w]+)\b", lambda m: m[1] + m[2] if m[1] + m[2] in known else m[0], text)


# ---------- figures ----------

def figure_regions(page, lines):
    """Caption-anchored unions keep multi-panel figures whole instead of bitmap pieces."""
    captions = [l for l in lines if CAPTION.match(l["text"])]
    objects = []
    header_band = page.height * 0.07
    for kind, items in (("image", page.images), ("vector", page.curves), ("vector", page.rects)):
        for obj in items:
            x0, top, x1, bottom = [float(obj.get(k, 0)) for k in ("x0", "top", "x1", "bottom")]
            # Running-header bars and footer rules are wide vector shapes hugging the page edge.
            if kind == "vector" and (bottom < header_band or top > page.height - header_band) and x1 - x0 > page.width * 0.4:
                continue
            if x1 - x0 > 3 and bottom - top > 3 and (x1 - x0) * (bottom - top) < page.width * page.height * 0.92:
                objects.append([max(0, x0), max(0, top), min(page.width, x1), min(page.height, bottom)])
    regions = []
    for caption in sorted(captions, key=lambda l: l["bbox"][1]):
        x0, top, x1, bottom = caption["bbox"]
        previous = max((c["bbox"][3] for c in captions if c["bbox"][3] < top - 20), default=20)
        candidates = [o for o in objects if o[1] >= previous - 4 and o[3] <= top + 4 and o[3] > previous + 10]
        if len(captions) > 1 and x1 - x0 < page.width * 0.55:
            same_column = [o for o in candidates if o[0] < x1 + 12 and o[2] > x0 - 12]
            if same_column:
                candidates = same_column
        if not candidates:
            continue
        bbox = [
            max(0, min(o[0] for o in candidates) - 8), max(0, min(o[1] for o in candidates) - 12),
            min(page.width, max(o[2] for o in candidates) + 8), min(page.height, max(o[3] for o in candidates) + 5),
        ]
        panel_labels = [l for l in lines if re.fullmatch(r"[a-lA-L][.)]?", l["text"].strip()) and bbox[0] - 10 <= l["bbox"][0] <= bbox[2] and bbox[1] - 45 <= l["bbox"][1] <= bbox[3]]
        if len(panel_labels) >= 2:
            bbox[1] = max(0, min(bbox[1], min(l["bbox"][1] for l in panel_labels) - 5))
        if (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) < 1000:
            continue
        parts = [caption["text"]]
        last_bottom = bottom
        for candidate in sorted(lines, key=lambda l: (l["bbox"][1], l["bbox"][0])):
            bx0, bt, bx1, bb = candidate["bbox"]
            if bt <= top + 2 or bt > last_bottom + 16 or bx0 > x1 + 30 or bx1 < x0 - 10:
                continue
            footer = bt > page.height * 0.85 and (FOOTER.search(candidate["text"]) or re.fullmatch(r"\d{1,4}", candidate["text"].strip()))
            if footer or CAPTION.match(candidate["text"]) or HEADING.fullmatch(candidate["text"]) or abs(candidate["size"] - caption["size"]) > max(0.7, caption["size"] * 0.1):
                break
            parts.append(candidate["text"])
            last_bottom = max(last_bottom, bb)
        match = CAPTION.match(caption["text"])
        regions.append((f"Figure {match.group(1) or match.group(2)}", "\n".join(parts), bbox))
    if not regions:
        bitmaps = [[i["x0"], i["top"], i["x1"], i["bottom"]] for i in page.images if i["width"] > 60 and i["height"] > 45 and i["width"] * i["height"] < page.width * page.height * 0.85]
        groups = []
        for box in bitmaps:
            group = next((g for g in groups if not (box[0] > g[2] + 30 or box[2] < g[0] - 30 or box[1] > g[3] + 30 or box[3] < g[1] - 30)), None)
            if group is None:
                groups.append(list(box))
            else:
                group[:] = [min(group[0], box[0]), min(group[1], box[1]), max(group[2], box[2]), max(group[3], box[3])]
        for i, box in enumerate(groups):
            regions.append((f"第 {page.page_number} 页候选图 {i + 1}", "图注未自动匹配，请对照原文或手动修正", box))
    return regions


def subfigure_regions(lines, bbox):
    labels = [l for l in lines if re.fullmatch(r"[a-lA-L][.)]?", l["text"].strip()) and bbox[0] <= l["bbox"][0] < bbox[2] and bbox[1] <= l["bbox"][1] < bbox[3] - 35]
    labels = sorted(labels, key=lambda l: (round(l["bbox"][1] / 18), l["bbox"][0]))
    names = [l["text"][0].lower() for l in labels]
    if len(names) < 2 or names != list("abcdefghijkl"[:len(names)]) or labels[0]["bbox"][1] > bbox[1] + 50:
        return []
    rows = []
    for label in labels:
        row = next((r for r in rows if abs(r[0]["bbox"][1] - label["bbox"][1]) < 18), None)
        if row is None:
            rows.append([label])
        else:
            row.append(label)
    regions = []
    for row_index, row in enumerate(rows):
        row = sorted(row, key=lambda l: l["bbox"][0])
        top = max(bbox[1], min(l["bbox"][1] for l in row) - 5)
        bottom = min(l["bbox"][1] for l in rows[row_index + 1]) - 7 if row_index + 1 < len(rows) else bbox[3]
        for i, label in enumerate(row):
            left = bbox[0] if i == 0 else max(bbox[0], label["bbox"][0] - 6)
            right = row[i + 1]["bbox"][0] - 8 if i + 1 < len(row) else bbox[2]
            if right - left > 45 and bottom - top > 45:
                regions.append((label["text"][0], [left, top, right, bottom]))
    return regions


def render_page(pdf, index, scale=RENDER_SCALE):
    page = pdf[index]
    try:
        width, height = page.get_size()
        scale = min(scale, 6000 / max(width, height))
        bitmap = page.render(scale=scale)
        try:
            return bitmap.to_pil().convert("RGB")
        finally:
            bitmap.close()
    finally:
        page.close()


def checked_bbox(bbox, width, height):
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("裁剪范围必须包含四个 PDF 坐标")
    x0, y0, x1, y1 = [float(v) for v in bbox]
    if x0 < 0 or y0 < 0 or x1 > width + 0.1 or y1 > height + 0.1 or x1 <= x0 or y1 <= y0:
        raise ValueError("裁剪范围超出页面或为空")
    return [round(x0, 2), round(y0, 2), round(min(x1, width), 2), round(min(y1, height), 2)]


def save_crop(image, bbox, page_width, page_height, destination: Path):
    sx, sy = image.width / page_width, image.height / page_height
    crop = image.crop((round(bbox[0] * sx), round(bbox[1] * sy), round(bbox[2] * sx), round(bbox[3] * sy)))
    crop.save(destination, format="PNG", optimize=True)
    return crop.width, crop.height


# ---------- public API ----------

def parse_pdf(pdf_path: Path, figures_dir: Path) -> dict:
    """Column-ordered page text plus caption-anchored figure crops in one pass."""
    figures_dir.mkdir(parents=True, exist_ok=True)
    pages, figures, warnings = [], [], []
    metadata = {}
    counter = 0
    with pdfplumber.open(str(pdf_path)) as source, pdfium.PdfDocument(str(pdf_path)) as renderer:
        if len(source.pages) > MAX_PAGES:
            raise ValueError(f"PDF 超过 {MAX_PAGES} 页，请拆分后导入")
        title = str(source.metadata.get("Title") or "").strip()
        if title and not re.match(r"^(Microsoft|untitled|Adobe|Word)", title, re.I):
            metadata["title"] = title
        authors = str(source.metadata.get("Author") or "").strip()
        if authors:
            metadata["authors"] = authors
        for index, page in enumerate(source.pages):
            number = index + 1
            try:
                words = page.extract_words(extra_attrs=["size", "fontname"], use_text_flow=False)
                lines = lines_from_words(words)
            except Exception as exc:  # noqa: BLE001 - keep other pages
                lines = []
                warnings.append(f"第 {number} 页文本解析失败：{type(exc).__name__}")
            lines = reading_order(lines, page.width)
            body_size = statistics.median([l["size"] for l in lines]) if lines else 10
            blocks = paragraph_lines(lines, body_size)
            text = "\n".join(("## " if b["level"] else "") + b["text"] for b in blocks)
            if not metadata.get("doi"):
                m = DOI_RE.search(text)
                if m:
                    metadata["doi"] = m.group().rstrip(".,;)")
            pages.append({"number": number, "text": text, "width": float(page.width), "height": float(page.height)})
            try:
                regions = figure_regions(page, lines)
            except Exception as exc:  # noqa: BLE001
                regions = []
                warnings.append(f"第 {number} 页候选图提取失败：{type(exc).__name__}")
            if regions:
                image = render_page(renderer, index)
                for label, caption, bbox in regions:
                    bbox = checked_bbox(bbox, page.width, page.height)
                    counter += 1
                    fid = f"f{counter:03d}"
                    w, h = save_crop(image, bbox, page.width, page.height, figures_dir / f"{fid}.png")
                    parent = {"id": fid, "label": label, "caption": reflow_caption(caption), "page": number, "bbox": bbox, "width": w, "height": h, "source": "auto", "parent": None}
                    figures.append(parent)
                    for panel_label, panel_bbox in subfigure_regions(lines, bbox):
                        counter += 1
                        cid = f"f{counter:03d}"
                        pw, ph = save_crop(image, panel_bbox, page.width, page.height, figures_dir / f"{cid}.png")
                        figures.append({"id": cid, "label": label + panel_label, "caption": parent["caption"], "page": number, "bbox": [round(v, 2) for v in panel_bbox], "width": pw, "height": ph, "source": "auto", "parent": fid})
            page.close()
    if not figures:
        warnings.append("未自动定位到图片；矢量图、跨页图或扫描件可用「取图」手动框选")
    return {"pages": pages, "figures": figures, "metadata": metadata, "warnings": warnings}


def crop_figure(pdf_path: Path, figures_dir: Path, page_number: int, bbox, fid: str) -> dict:
    """Manual crop in PDF points for one page; the caller assigns id/label/caption.
    Rendered at a higher scale than automatic extraction: manual crops are targeted
    zoom-ins (the skill's 放大核查), so keep panel labels and stats legible."""
    figures_dir.mkdir(parents=True, exist_ok=True)
    with pdfium.PdfDocument(str(pdf_path)) as pdf:
        index = int(page_number) - 1
        if index < 0 or index >= len(pdf):
            raise ValueError("页面不存在")
        page = pdf[index]
        width, height = page.get_size()
        page.close()
        bbox = checked_bbox(bbox, width, height)
        image = render_page(pdf, index, scale=3.0)
        w, h = save_crop(image, bbox, width, height, figures_dir / f"{fid}.png")
    return {"id": fid, "page": int(page_number), "bbox": bbox, "width": w, "height": h, "source": "manual", "parent": None}


def load_figures(d: Path) -> list[dict]:
    f = d / "figures.json"
    if not f.is_file():
        return []
    try:
        return json.loads(f.read_text("utf-8"))
    except json.JSONDecodeError:
        return []


def save_figures(d: Path, figures: list[dict]) -> None:
    (d / "figures.json").write_text(json.dumps(figures, ensure_ascii=False, indent=1), "utf-8")
