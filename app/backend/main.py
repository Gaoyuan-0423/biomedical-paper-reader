"""Paper Reader backend: local library of PDFs with figures, metadata lookups and
LLM-generated reading notes."""

from __future__ import annotations

import sys
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parent))

import asyncio
import base64
import io
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel
from pypdf import PdfReader

from external import LookupError_, fetch_crossref, fetch_related, normalize_doi
from figures import crop_figure, load_figures, parse_pdf, save_figures
from llm import (
    UnsafeURLError,
    UpstreamError,
    build_messages,
    chat_once,
    condense_long_text,
    stream_chat,
)

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
PAPERS_DIR = DATA_DIR / "papers"
SETTINGS_FILE = DATA_DIR / "settings.json"
FRONTEND_DIR = APP_DIR / "frontend"

MAX_UPLOAD_BYTES = 80 * 1024 * 1024
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
RELATED_TTL_SECONDS = 7 * 24 * 3600
VISION_MAX_SIDE = 1400

DEFAULT_SETTINGS = {
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "api_key": "",
    "model": "glm-4.6",
    "vision_model": "",
    "max_chars": 90000,
    "max_tokens": 0,
    "max_figures": 8,
    "research_direction": "",
    "s2_api_key": "",
}

FIGURE_READ_PROMPT = """你是读图助手。下面是论文中一张图的截图及其图注。只记录你在图中确实看到的内容，用中文、Markdown 列表，≤400 字：
- 面板布局与每个面板的实验类型（UMAP、热图、小提琴图、WB、IHC、生存曲线……）；
- 分组／条件／坐标轴含义（看不清就写「不可辨」）；
- 可见的效应方向与大致幅度、统计标注（星号、p 值）；不能从颜色或形状推断显著性；
- 与图注不一致或图注未提及但图中出现的信息。
不做研究价值评价，不复述图注。

图号：{label}
图注：{caption}"""

# The skill's 问题驱动核查: a crop exists to answer a concrete question.
FIGURE_QUESTION_SUFFIX = """

读者就这张图追问（优先回答，读不清的部分如实说明）：{question}"""

app = FastAPI(title="Biomedical Paper Reader", version="0.2.0")


# ---------- storage helpers ----------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            data.update(json.loads(SETTINGS_FILE.read_text("utf-8")))
        except json.JSONDecodeError:
            pass
    return data


def save_settings(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def _mask(key: str) -> str:
    return (key[:4] + "…" + key[-4:]) if len(key) > 10 else ("已设置" if key else "")


def public_settings(data: dict) -> dict:
    key = data.get("api_key") or ""
    return {
        "base_url": data.get("base_url", ""),
        "model": data.get("model", ""),
        "vision_model": data.get("vision_model", ""),
        "max_chars": data.get("max_chars", DEFAULT_SETTINGS["max_chars"]),
        "max_tokens": data.get("max_tokens", 0),
        "max_figures": data.get("max_figures", DEFAULT_SETTINGS["max_figures"]),
        "research_direction": data.get("research_direction", ""),
        "has_api_key": bool(key),
        "api_key_masked": _mask(key),
        "has_s2_api_key": bool(data.get("s2_api_key")),
    }


def paper_dir(paper_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", paper_id):
        raise HTTPException(404, "paper not found")
    d = PAPERS_DIR / paper_id
    if not d.is_dir():
        raise HTTPException(404, "paper not found")
    return d


def read_meta(d: Path) -> dict:
    meta = json.loads((d / "meta.json").read_text("utf-8"))
    meta.setdefault("has_file", (d / "paper.pdf").is_file())
    meta.setdefault("tags", [])
    meta.setdefault("journal", "")
    return meta


def write_meta(d: Path, meta: dict) -> None:
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")


def summary_of(meta: dict) -> dict:
    out = dict(meta)
    out.pop("notes", None)
    out.pop("abstract", None)
    return out


def list_meta() -> list[dict]:
    items = []
    if PAPERS_DIR.is_dir():
        for d in PAPERS_DIR.iterdir():
            if (d / "meta.json").is_file():
                items.append(summary_of(read_meta(d)))
    items.sort(key=lambda m: m.get("added_at", ""), reverse=True)
    return items


def new_paper_dir() -> tuple[str, Path]:
    paper_id = uuid.uuid4().hex
    d = PAPERS_DIR / paper_id
    d.mkdir(parents=True, exist_ok=True)
    return paper_id, d


# ---------- PDF handling ----------

def _clean_title(candidate: Optional[str], filename: str) -> str:
    text = (candidate or "").strip()
    if text and not text.lower().endswith(".pdf") and 5 <= len(text) <= 300:
        return re.sub(r"\s+", " ", text)
    return Path(filename).stem


def _pypdf_fallback(pdf_path: Path) -> dict:
    """Plain pypdf extraction, used when the geometry parser fails on a file."""
    reader = PdfReader(str(pdf_path))
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            txt = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - keep going on a bad page
            txt = ""
        pages.append({"number": index, "text": txt.strip()})
    info = reader.metadata or {}
    metadata = {}
    try:
        if info.title:
            metadata["title"] = info.title
        if info.author:
            metadata["authors"] = info.author
    except Exception:  # noqa: BLE001
        pass
    return {"pages": pages, "figures": [], "metadata": metadata, "warnings": ["几何解析失败，已退回纯文本抽取"]}


def _year_from_pdf(pdf_path: Path) -> str:
    try:
        info = PdfReader(str(pdf_path)).metadata or {}
        m = re.search(r"D:(\d{4})", str(info.get("/CreationDate", "") or ""))
        return m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        return ""


def ingest_pdf(d: Path, content: bytes, filename: str, meta: dict) -> dict:
    """Write paper.pdf, extract column-ordered text + figures, merge metadata into `meta`.
    Existing non-empty metadata (e.g. from CrossRef) wins over PDF guesses."""
    pdf_path = d / "paper.pdf"
    pdf_path.write_bytes(content)
    figures_dir = d / "figures"
    if figures_dir.is_dir():
        shutil.rmtree(figures_dir, ignore_errors=True)
    try:
        parsed = parse_pdf(pdf_path, figures_dir)
    except Exception:  # noqa: BLE001 - geometry parser is best effort
        parsed = _pypdf_fallback(pdf_path)

    pages_text = [f"[Page {p['number']}]\n{p['text']}" for p in parsed["pages"]]
    full_text = "\n\n".join(pages_text)
    (d / "text.txt").write_text(full_text, "utf-8")

    pdf_meta = parsed["metadata"]
    first_page = parsed["pages"][0]["text"] if parsed["pages"] else ""
    guessed_title = pdf_meta.get("title")
    if not guessed_title:
        for line in first_page.splitlines():
            line = line.strip().lstrip("# ").strip()
            if 20 <= len(line) <= 200 and not DOI_RE.search(line):
                guessed_title = line
                break
    head = "\n".join(pages_text[:3])
    doi_match = DOI_RE.search(head)

    # Keep manual figures from a previous ingest of this paper (same geometry).
    previous = [f for f in load_figures(d) if f.get("source") == "manual"]
    figures = parsed["figures"] + previous
    save_figures(d, figures)

    meta["filename"] = filename
    meta["has_file"] = True
    meta["pages"] = len(parsed["pages"])
    meta["text_chars"] = len(full_text)
    meta["figures_count"] = len(figures)
    meta["parse_warnings"] = parsed.get("warnings", [])
    if not meta.get("title") or meta.get("title") == Path(meta.get("filename", "")).stem:
        meta["title"] = _clean_title(guessed_title, filename)
    if not meta.get("authors"):
        meta["authors"] = (pdf_meta.get("authors") or "").strip()
    if not meta.get("year"):
        meta["year"] = _year_from_pdf(pdf_path)
    if not meta.get("doi"):
        meta["doi"] = pdf_meta.get("doi") or (doi_match.group(1).rstrip(".,;)") if doi_match else "")
    return meta


def _validate_pdf_upload(filename: str, content: bytes) -> None:
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "只支持 PDF 文件")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件超过 80MB 限制")
    if not content.startswith(b"%PDF"):
        raise HTTPException(400, "文件不是有效的 PDF")


def blank_meta(paper_id: str) -> dict:
    return {
        "id": paper_id,
        "filename": "",
        "added_at": _now(),
        "favorite": False,
        "tags": [],
        "notes": "",
        "notes_mode": "",
        "notes_updated_at": "",
        "title": "",
        "authors": "",
        "year": "",
        "doi": "",
        "journal": "",
        "abstract": "",
        "url": "",
        "pages": 0,
        "text_chars": 0,
        "figures_count": 0,
        "has_file": False,
        "source": "pdf",
    }


# ---------- schemas ----------

class SettingsIn(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    vision_model: Optional[str] = None
    max_chars: Optional[int] = None
    max_tokens: Optional[int] = None
    max_figures: Optional[int] = None
    research_direction: Optional[str] = None
    s2_api_key: Optional[str] = None


class PaperPatch(BaseModel):
    title: Optional[str] = None
    favorite: Optional[bool] = None
    notes: Optional[str] = None
    tags: Optional[list[str]] = None
    authors: Optional[str] = None
    year: Optional[str] = None
    journal: Optional[str] = None
    doi: Optional[str] = None


class DoiIn(BaseModel):
    doi: str


class EnrichIn(BaseModel):
    overwrite: bool = False
    doi: Optional[str] = None


class CropIn(BaseModel):
    page: int
    bbox: list[float]
    label: Optional[str] = ""
    caption: Optional[str] = ""


class FigurePatch(BaseModel):
    label: Optional[str] = None
    caption: Optional[str] = None
    observation: Optional[str] = None


# ---------- settings ----------

@app.get("/api/settings")
def get_settings():
    return public_settings(load_settings())


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    data = load_settings()
    if body.base_url is not None:
        data["base_url"] = body.base_url.strip()
    if body.api_key is not None and body.api_key.strip():
        data["api_key"] = body.api_key.strip()
    if body.model is not None:
        data["model"] = body.model.strip()
    if body.vision_model is not None:
        data["vision_model"] = body.vision_model.strip()
    if body.max_chars is not None:
        data["max_chars"] = max(5000, min(int(body.max_chars), 2_000_000))
    if body.max_tokens is not None:
        data["max_tokens"] = max(0, min(int(body.max_tokens), 1_048_576))
    if body.max_figures is not None:
        data["max_figures"] = max(0, min(int(body.max_figures), 40))
    if body.research_direction is not None:
        data["research_direction"] = body.research_direction.strip()[:500]
    if body.s2_api_key is not None and body.s2_api_key.strip():
        data["s2_api_key"] = body.s2_api_key.strip()
    save_settings(data)
    return public_settings(data)


@app.delete("/api/settings/api-key")
def clear_api_key():
    data = load_settings()
    data["api_key"] = ""
    save_settings(data)
    return public_settings(data)


# ---------- papers ----------

@app.get("/api/papers")
def get_papers():
    return list_meta()


@app.post("/api/papers")
async def upload_paper(file: UploadFile = File(...)):
    filename = Path(file.filename or "paper.pdf").name
    content = await file.read()
    _validate_pdf_upload(filename, content)
    paper_id, d = new_paper_dir()
    try:
        meta = ingest_pdf(d, content, filename, blank_meta(paper_id))
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, f"PDF 解析失败：{exc}") from exc
    write_meta(d, meta)
    return meta


@app.post("/api/import/doi")
async def import_doi(body: DoiIn):
    try:
        record = await fetch_crossref(body.doi)
    except (LookupError_, UnsafeURLError) as exc:
        raise HTTPException(400, str(exc)) from exc
    doi = record["doi"].lower()
    for existing in list_meta():
        if (existing.get("doi") or "").lower() == doi:
            raise HTTPException(409, f"文献库里已有该 DOI：{existing['title']}")
    paper_id, d = new_paper_dir()
    meta = blank_meta(paper_id)
    meta.update({
        "title": record["title"] or doi,
        "authors": record["authors"],
        "year": record["year"],
        "doi": record["doi"],
        "journal": record["journal"],
        "abstract": record["abstract"],
        "url": record["url"],
        "source": "doi",
        "filename": "",
    })
    (d / "text.txt").write_text(record["abstract"], "utf-8")
    write_meta(d, meta)
    return meta


@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: str):
    return read_meta(paper_dir(paper_id))


@app.patch("/api/papers/{paper_id}")
def patch_paper(paper_id: str, body: PaperPatch):
    d = paper_dir(paper_id)
    meta = read_meta(d)
    if body.title is not None and body.title.strip():
        meta["title"] = body.title.strip()
    if body.favorite is not None:
        meta["favorite"] = body.favorite
    if body.notes is not None:
        meta["notes"] = body.notes
        meta["notes_updated_at"] = _now()
    if body.tags is not None:
        seen: list[str] = []
        for t in body.tags:
            t = t.strip()[:40]
            if t and t not in seen:
                seen.append(t)
        meta["tags"] = seen[:30]
    for field in ("authors", "year", "journal"):
        value = getattr(body, field)
        if value is not None:
            meta[field] = value.strip()
    if body.doi is not None:
        meta["doi"] = body.doi.strip()
    write_meta(d, meta)
    return meta


@app.delete("/api/papers/{paper_id}")
def delete_paper(paper_id: str):
    shutil.rmtree(paper_dir(paper_id))
    return {"ok": True}


@app.post("/api/papers/{paper_id}/file")
async def attach_file(paper_id: str, file: UploadFile = File(...)):
    """Attach (or replace) the PDF of an existing entry, e.g. one imported by DOI."""
    d = paper_dir(paper_id)
    filename = Path(file.filename or "paper.pdf").name
    content = await file.read()
    _validate_pdf_upload(filename, content)
    meta = read_meta(d)
    try:
        meta = ingest_pdf(d, content, filename, meta)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"PDF 解析失败：{exc}") from exc
    write_meta(d, meta)
    return meta


@app.post("/api/papers/{paper_id}/enrich")
async def enrich_paper(paper_id: str, body: EnrichIn):
    """Fill authors / year / journal (and title when asked) from CrossRef."""
    d = paper_dir(paper_id)
    meta = read_meta(d)
    doi = (body.doi or meta.get("doi") or "").strip()
    if not doi:
        raise HTTPException(400, "该文献没有 DOI，请先在标题下方填写 DOI")
    try:
        record = await fetch_crossref(doi)
    except (LookupError_, UnsafeURLError) as exc:
        raise HTTPException(400, str(exc)) from exc
    meta["doi"] = record["doi"]
    meta["url"] = record["url"]
    for field in ("authors", "year", "journal", "abstract"):
        if record.get(field) and (body.overwrite or not meta.get(field)):
            meta[field] = record[field]
    if record["title"] and (body.overwrite or meta.get("title") == Path(meta.get("filename", "")).stem or not meta.get("title")):
        meta["title"] = record["title"]
    write_meta(d, meta)
    return meta


@app.get("/api/papers/{paper_id}/related")
async def related_papers(paper_id: str, refresh: int = 0, limit: int = 25):
    d = paper_dir(paper_id)
    meta = read_meta(d)
    cache = d / "related.json"
    if cache.is_file() and not refresh:
        try:
            cached = json.loads(cache.read_text("utf-8"))
            fetched = datetime.fromisoformat(cached.get("fetched_at", "1970-01-01T00:00:00+00:00"))
            if (datetime.now(timezone.utc) - fetched).total_seconds() < RELATED_TTL_SECONDS:
                return cached
        except (ValueError, json.JSONDecodeError):
            pass
    settings = load_settings()
    try:
        result = await fetch_related(meta.get("doi", ""), meta.get("title", ""), limit=limit, api_key=settings.get("s2_api_key", ""))
    except (LookupError_, UnsafeURLError) as exc:
        raise HTTPException(400, str(exc)) from exc
    known = {(p.get("doi") or "").lower(): p["id"] for p in list_meta() if p.get("doi")}
    for group in ("references", "citations"):
        for item in result[group]:
            item["in_library"] = known.get((item.get("doi") or "").lower(), "")
    result["fetched_at"] = _now()
    cache.write_text(json.dumps(result, ensure_ascii=False), "utf-8")
    return result


@app.get("/api/papers/{paper_id}/file")
def get_paper_file(paper_id: str):
    d = paper_dir(paper_id)
    if not (d / "paper.pdf").is_file():
        raise HTTPException(404, "该文献尚未附加 PDF")
    return FileResponse(d / "paper.pdf", media_type="application/pdf", filename=read_meta(d)["filename"] or "paper.pdf")


@app.get("/api/papers/{paper_id}/text")
def get_paper_text(paper_id: str):
    return PlainTextResponse((paper_dir(paper_id) / "text.txt").read_text("utf-8"))


@app.get("/api/papers/{paper_id}/export.md")
def export_markdown(paper_id: str):
    meta = read_meta(paper_dir(paper_id))
    safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", meta["title"])[:80] or "notes"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}.md"}
    return PlainTextResponse(meta.get("notes", ""), media_type="text/markdown; charset=utf-8", headers=headers)


# ---------- figures ----------

def _figure_file(d: Path, fid: str) -> Path:
    if not re.fullmatch(r"[a-z]\d{3,6}", fid):
        raise HTTPException(404, "figure not found")
    f = d / "figures" / f"{fid}.png"
    if not f.is_file():
        raise HTTPException(404, "figure not found")
    return f


def _next_fid(figures: list[dict]) -> str:
    nums = [int(f["id"][1:]) for f in figures if re.fullmatch(r"[a-z]\d+", f.get("id", ""))]
    return f"m{(max(nums) + 1) if nums else 1:03d}"


def _update_count(d: Path, figures: list[dict]) -> None:
    meta = read_meta(d)
    meta["figures_count"] = len(figures)
    write_meta(d, meta)


@app.get("/api/papers/{paper_id}/figures")
def get_figures(paper_id: str):
    return load_figures(paper_dir(paper_id))


@app.post("/api/papers/{paper_id}/figures/extract")
def reextract_figures(paper_id: str):
    d = paper_dir(paper_id)
    if not (d / "paper.pdf").is_file():
        raise HTTPException(400, "该文献尚未附加 PDF")
    previous = load_figures(d)
    try:
        parsed = parse_pdf(d / "paper.pdf", d / "figures")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"图片提取失败：{exc}") from exc
    # Manual crops and already-written observations survive a re-run (ReadBox merge rule).
    by_label = {f["label"]: f for f in previous if f.get("observation") or f.get("caption_edited")}
    figures = []
    for f in parsed["figures"]:
        old = by_label.get(f["label"])
        if old:
            f["observation"] = old.get("observation", "")
            if old.get("caption_edited"):
                f["caption"] = old["caption"]
                f["caption_edited"] = True
        figures.append(f)
    figures.extend(f for f in previous if f.get("source") == "manual")
    save_figures(d, figures)
    _update_count(d, figures)
    return {"figures": figures, "warnings": parsed.get("warnings", [])}


@app.post("/api/papers/{paper_id}/figures/crop")
def crop_manual_figure(paper_id: str, body: CropIn):
    d = paper_dir(paper_id)
    if not (d / "paper.pdf").is_file():
        raise HTTPException(400, "该文献尚未附加 PDF")
    figures = load_figures(d)
    fid = _next_fid(figures)
    try:
        fig = crop_figure(d / "paper.pdf", d / "figures", body.page, body.bbox, fid)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    fig["label"] = (body.label or "").strip()[:60] or f"手动取图 {fid[1:].lstrip('0') or '1'}"
    fig["caption"] = (body.caption or "").strip()
    fig["observation"] = ""
    figures.append(fig)
    save_figures(d, figures)
    _update_count(d, figures)
    return fig


@app.patch("/api/papers/{paper_id}/figures/{fid}")
def patch_figure(paper_id: str, fid: str, body: FigurePatch):
    d = paper_dir(paper_id)
    figures = load_figures(d)
    for f in figures:
        if f["id"] == fid:
            if body.label is not None and body.label.strip():
                f["label"] = body.label.strip()[:60]
            if body.caption is not None:
                f["caption"] = body.caption.strip()
                f["caption_edited"] = True
            if body.observation is not None:
                f["observation"] = body.observation.strip()
            save_figures(d, figures)
            return f
    raise HTTPException(404, "figure not found")


@app.delete("/api/papers/{paper_id}/figures/{fid}")
def delete_figure(paper_id: str, fid: str):
    d = paper_dir(paper_id)
    figures = load_figures(d)
    keep = [f for f in figures if f["id"] != fid and f.get("parent") != fid]
    if len(keep) == len(figures):
        raise HTTPException(404, "figure not found")
    for f in figures:
        if f not in keep:
            (d / "figures" / f"{f['id']}.png").unlink(missing_ok=True)
    save_figures(d, keep)
    _update_count(d, keep)
    return {"ok": True}


@app.get("/api/papers/{paper_id}/figures/{fid}.png")
def get_figure_image(paper_id: str, fid: str):
    return FileResponse(_figure_file(paper_dir(paper_id), fid), media_type="image/png")


def _image_payload(path: Path) -> dict:
    with Image.open(path) as im:
        im = im.convert("RGB")
        longest = max(im.size)
        if longest > VISION_MAX_SIDE:
            ratio = VISION_MAX_SIDE / longest
            im = im.resize((max(1, round(im.width * ratio)), max(1, round(im.height * ratio))))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82, optimize=True)
    return {"mime": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode("ascii")}


def _figures_to_read(d: Path, settings: dict, force: bool) -> tuple[list[dict], list[dict]]:
    figures = load_figures(d)
    parents = [f for f in figures if not f.get("parent")]
    parents.sort(key=lambda f: (f.get("page", 0), f.get("id", "")))
    limit = int(settings.get("max_figures") or 0)
    selected = parents[:limit] if limit else []
    todo = [f for f in selected if force or not f.get("observation")]
    return figures, todo


async def _read_one_figure(settings: dict, d: Path, fig: dict, question: str = "") -> str:
    image = _image_payload(d / "figures" / f"{fig['id']}.png")
    prompt = FIGURE_READ_PROMPT.format(label=fig.get("label", ""), caption=(fig.get("caption") or "无")[:1500])
    if question.strip():
        prompt += FIGURE_QUESTION_SUFFIX.format(question=question.strip()[:500])
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{image['mime']};base64,{image['data']}"}},
    ]}]
    return (await chat_once(settings["base_url"], settings["api_key"], settings["vision_model"], messages, max_tokens=1200)).strip()


async def _read_figures_events(settings: dict, d: Path, force: bool):
    """Async generator: {'type':'progress'} per figure, then {'type':'result', figures}."""
    figures, todo = _figures_to_read(d, settings, force)
    if not todo:
        yield {"type": "result", "figures": figures}
        return
    sem = asyncio.Semaphore(2)

    async def one(fig: dict) -> tuple[str, str]:
        async with sem:
            return fig["id"], await _read_one_figure(settings, d, fig)

    done = 0
    for coro in asyncio.as_completed([one(f) for f in todo]):
        fid, observation = await coro
        done += 1
        for f in figures:
            if f["id"] == fid:
                f["observation"] = observation
        save_figures(d, figures)
        yield {"type": "progress", "done": done, "total": len(todo), "figure": fid}
    yield {"type": "result", "figures": figures}


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


class FigureQuestion(BaseModel):
    question: str = ""


@app.post("/api/papers/{paper_id}/figures/read")
async def read_figures(paper_id: str, force: int = 0):
    """Run the vision model over main figures (SSE progress), caching observations."""
    d = paper_dir(paper_id)
    settings = load_settings()
    if not settings.get("api_key"):
        raise HTTPException(400, "尚未设置 API Key")
    if not settings.get("vision_model"):
        raise HTTPException(400, "尚未设置读图模型（设置 → 读图模型）")

    async def generate():
        yield _sse({"type": "start", "model": settings["vision_model"]})
        try:
            async for ev in _read_figures_events(settings, d, bool(force)):
                if ev["type"] == "progress":
                    yield _sse({"type": "stage", "stage": "figures", **{k: v for k, v in ev.items() if k != "type"}})
                else:
                    yield _sse({"type": "done", "figures": ev["figures"]})
        except (UnsafeURLError, UpstreamError, RuntimeError, OSError) as exc:
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/papers/{paper_id}/figures/{fid}/read")
async def read_one_figure(paper_id: str, fid: str, body: FigureQuestion | None = None):
    """Read a single figure (e.g. a fresh manual crop) with the vision model,
    optionally focused on a concrete question; the observation is cached."""
    d = paper_dir(paper_id)
    settings = load_settings()
    if not settings.get("api_key"):
        raise HTTPException(400, "尚未设置 API Key")
    if not settings.get("vision_model"):
        raise HTTPException(400, "尚未设置读图模型（设置 → 读图模型）")
    if not _figure_file(d, fid).is_file():
        raise HTTPException(404, "figure not found")
    figures = load_figures(d)
    fig = next((f for f in figures if f["id"] == fid), None)
    if fig is None:
        raise HTTPException(404, "figure not found")
    question = (body.question if body else "") or ""
    try:
        fig["observation"] = await _read_one_figure(settings, d, fig, question)
    except (UnsafeURLError, UpstreamError) as exc:
        raise HTTPException(400, str(exc)) from exc
    save_figures(d, figures)
    return fig


# ---------- notes generation ----------

MODE_NAMES = {"deep": "完整精读", "quick": "速读筛选"}


@app.post("/api/papers/{paper_id}/read")
async def read_paper(paper_id: str, mode: str = "deep", use_figures: int = 1):
    d = paper_dir(paper_id)
    meta = read_meta(d)
    settings = load_settings()
    if not settings.get("api_key"):
        raise HTTPException(400, "尚未设置 API Key，请先在「设置」中填写")
    if mode not in MODE_NAMES:
        raise HTTPException(400, "mode 只能是 deep 或 quick")
    if not meta.get("has_file"):
        raise HTTPException(400, "该文献只有元数据，请先附加 PDF 再生成笔记")
    text = (d / "text.txt").read_text("utf-8")
    if len(text.strip()) < 200:
        raise HTTPException(400, "PDF 提取到的文本过少（可能是扫描件），无法生成笔记")
    budget = int(settings["max_chars"])
    max_tokens = int(settings.get("max_tokens") or 0) or None

    async def generate():
        chunks: list[str] = []
        yield _sse({"type": "start", "mode": mode, "model": settings["model"], "mode_name": MODE_NAMES[mode]})
        try:
            figures_for_prompt: list[dict] = []
            if use_figures:
                if settings.get("vision_model") and mode != "quick":
                    async for ev in _read_figures_events(settings, d, force=False):
                        if ev["type"] == "progress":
                            yield _sse({"type": "stage", "stage": "figures", "done": ev["done"], "total": ev["total"],
                                        "message": f"读图模型正在观察图片 {ev['done']}/{ev['total']}"})
                        else:
                            figures_for_prompt = [f for f in ev["figures"] if not f.get("parent")]
                else:
                    figures_for_prompt = [f for f in load_figures(d) if not f.get("parent")]
            body_text = text
            if len(text) > budget:
                async for ev in condense_long_text(settings["base_url"], settings["api_key"], settings["model"], text, budget):
                    if ev["type"] == "progress":
                        yield _sse({"type": "stage", "stage": "condense", "done": ev["done"], "total": ev["total"],
                                    "message": f"全文 {len(text)} 字符超出预算 {budget}，正在分段整理 {ev['done']}/{ev['total']}"})
                    else:
                        body_text = ev["text"]
                meta_for_prompt = {**meta, "condensed": True}
            else:
                meta_for_prompt = meta
            messages = build_messages(body_text, mode, meta_for_prompt, figures=figures_for_prompt,
                                      direction=settings.get("research_direction", ""))
            yield _sse({"type": "stage", "stage": "write", "message": f"{settings['model']} 正在撰写{MODE_NAMES[mode]}笔记…"})
            async for piece in stream_chat(settings["base_url"], settings["api_key"], settings["model"], messages, max_tokens=max_tokens):
                chunks.append(piece)
                yield _sse({"type": "chunk", "text": piece})
        except (UnsafeURLError, UpstreamError) as exc:
            yield _sse({"type": "error", "message": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 - surface upstream failures to the UI
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            return
        notes = "".join(chunks).strip()
        current = read_meta(d)
        current["notes"] = notes
        current["notes_mode"] = mode
        current["notes_updated_at"] = _now()
        write_meta(d, current)
        yield _sse({"type": "done", "notes": notes})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
