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
import os
import re
import shutil
import tempfile
import hashlib
import uuid
import httpx
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote as url_quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field
from pypdf import PdfReader

from external import LookupError_, fetch_crossref, fetch_related, normalize_doi
from figures import crop_figure, load_figures, parse_pdf, save_figures
from note_assets import illustrate_notes, markdown_bundle
from reading_state import read_positions, save_position
from reading_workflow import (DERIVED_FIELDS, SidecarError, annotate, atomic_json, defaults, evidence_lock,
                      load_evidence, materials_changed, public_evidence, source_info, storage_lock)
from paper_resources import (RESOURCE_DERIVED_FIELDS, ResourceIn, ResourcePatch, ensure_unique,
                             load_resources, normalize_resource, public_resource, public_resources,
                             related_resources, resource_summary, source_revision, validate_source)
from research_export import append_research_appendix
from resource_extraction import extract_resources, identity_keys
from paper_tags import TEXT_LIMIT, TagFormatError, merge_tags, parse_tags, tag_messages, tag_snapshot
from llm import (
    UnsafeURLError,
    UpstreamError,
    build_messages,
    chat_once,
    condense_long_text,
    stream_chat,
)

APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
# Packaged applications keep mutable user files outside the application bundle.
# Source installations retain their existing data location unless explicitly set.
DATA_DIR = Path(os.environ["BIOREADER_DATA_DIR"]).expanduser() if os.environ.get("BIOREADER_DATA_DIR") else APP_DIR / "data"
PAPERS_DIR = DATA_DIR / "papers"
SETTINGS_FILE = DATA_DIR / "settings.json"
FRONTEND_DIR = APP_DIR / "frontend"

MAX_UPLOAD_BYTES = 80 * 1024 * 1024
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
RELATED_TTL_SECONDS = 7 * 24 * 3600
VISION_MAX_SIDE = 1400
TAG_TIMEOUT_SECONDS = 60.0
TAG_RETRY_DELAY_SECONDS = 1.0

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
    with storage_lock:
        meta = json.loads((d / "meta.json").read_text("utf-8"))
        meta.setdefault("has_file", (d / "paper.pdf").is_file())
        meta.setdefault("tags", [])
        meta.setdefault("tags_revision", 0)
        meta.setdefault("journal", "")
        meta.setdefault("supplements", [])
        return annotate_paper(d, meta)


def annotate_paper(d: Path, meta: dict) -> dict:
    annotate(d, meta)
    try:
        meta.update(resource_summary(d, meta))
    except SidecarError as exc:
        meta['resources_count'] = meta['resources_pending'] = None
        meta['storage_warnings'].append({'kind': exc.kind, 'message': exc.message})
    if meta['resources_pending']:
        meta['tasks'].append({'id': 'resources', 'label': '核对数据与代码', 'count': meta['resources_pending']})
    if meta['storage_warnings']:
        meta['tasks'].append({'id': 'storage', 'label': '检查本地记录'})
    return meta


def write_meta(d: Path, meta: dict) -> None:
    with storage_lock:
        defaults(meta)
        atomic_json(d / "meta.json", {k: v for k, v in meta.items() if k not in DERIVED_FIELDS + RESOURCE_DERIVED_FIELDS})
        annotate_paper(d, meta)


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


def document_path(d: Path, document_id: str = "main") -> Path:
    if document_id == "main":
        return d / "paper.pdf"
    if not re.fullmatch(r"[0-9a-f]{32}", document_id):
        raise HTTPException(404, "补充材料不存在")
    if not any(f["id"] == document_id for f in read_meta(d).get("supplements", [])):
        raise HTTPException(404, "补充材料不存在")
    return d / "supplements" / f"{document_id}.pdf"


def store_document(d: Path, content: bytes, filename: str, document_id: str):
    """Parse first; keep each source separate and preserve stable figure IDs."""
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted or not 0 < len(reader.pages) <= 600:
        raise ValueError("PDF 必须未加密且包含 1–600 页")
    previous = load_figures(d)
    own = [f for f in previous if f.get("document_id", "main") == document_id]
    others = [f for f in previous if f.get("document_id", "main") != document_id]
    def identity(f):
        return (f.get("label"), f.get("page"), tuple(f.get("bbox", [])), bool(f.get("parent")))
    old_by_key = {identity(f): f for f in own if f.get("source") != "manual"}
    occupied = [int(p.stem[1:]) for p in (d / "figures").glob("*.png") if re.fullmatch(r"[a-z]\d{3,6}", p.stem)]
    counter = max(occupied + [int(f["id"][1:]) for f in previous], default=0)
    with tempfile.TemporaryDirectory(prefix=".parse-", dir=d) as tmp:
        stage = Path(tmp)
        source = stage / "paper.pdf"
        source.write_bytes(content)
        try:
            parsed = parse_pdf(source, stage / "figures")
        except Exception:
            parsed = _pypdf_fallback(source)
        mapping = {}
        fresh = []
        for fig in parsed["figures"]:
            old = old_by_key.get(identity(fig))
            if old:
                fid = old["id"]
            else:
                counter += 1
                fid = f"{'f' if document_id == 'main' else 's'}{counter:03d}"
            mapping[fig["id"]] = fid
            item = dict(fig, id=fid, document_id=document_id, document_name="正文" if document_id == "main" else filename)
            if old:
                item["observation"] = old.get("observation", "")
                if old.get("caption_edited"):
                    item.update(caption=old["caption"], caption_edited=True)
            fresh.append(item)
        for item, original in zip(fresh, parsed["figures"]):
            item["parent"] = mapping.get(original.get("parent"))
        destination = d / "paper.pdf" if document_id == "main" else d / "supplements" / f"{document_id}.pdf"
        destination.parent.mkdir(parents=True, exist_ok=True)
        (d / "figures").mkdir(exist_ok=True)
        for original in parsed["figures"]:
            shutil.copy2(stage / "figures" / f"{original['id']}.png", d / "figures" / f"{mapping[original['id']]}.png")
        # Manual crops are not overwritten by automatic extraction.
        figures = others + fresh + [f for f in own if f.get("source") == "manual"]
        text = "\n\n".join(f"[Page {page['number']}]\n{page['text']}" for page in parsed["pages"])
        shutil.copy2(source, destination)
        destination.with_suffix('.txt').write_text(text, "utf-8")
        if document_id == "main":
            (d / "text.txt").write_text(text, "utf-8")
        save_figures(d, figures)
    return parsed, figures


def reading_text(d: Path, meta: dict) -> str:
    sections = ["[正文 PDF：" + meta.get("filename", "") + "]\n" + (d / "text.txt").read_text("utf-8")]
    offset = meta.get("pages", 0)
    for supplement in meta.get("supplements", []):
        text = document_path(d, supplement["id"]).with_suffix('.txt').read_text("utf-8")
        text = re.sub(r"\[Page (\d+)\]", lambda m: f"[Page {offset + int(m[1])}] 补充材料 {supplement['filename']} 原文件第 {m[1]} 页", text)
        sections.append(f"[补充 PDF：{supplement['filename']}]\n{text}")
        offset += supplement["pages"]
    return "\n\n".join(sections)


def ingest_pdf(d: Path, content: bytes, filename: str, meta: dict) -> dict:
    """Write paper.pdf, extract column-ordered text + figures, merge metadata into `meta`.
    Existing non-empty metadata (e.g. from CrossRef) wins over PDF guesses."""
    parsed, figures = store_document(d, content, filename, "main")
    pdf_path = d / "paper.pdf"
    full_text = (d / "text.txt").read_text("utf-8")
    pages_text = [f"[Page {p['number']}]\n{p['text']}" for p in parsed["pages"]]

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

    meta["filename"] = filename
    meta["has_file"] = True
    meta["pages"] = len(parsed["pages"])
    meta["text_chars"] = len(full_text)
    meta["figures_count"] = len(figures)
    meta["parse_warnings"] = parsed.get("warnings", [])
    materials_changed(meta, "main", hashlib.sha256(content).hexdigest())
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
        "tags_revision": 0,
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
    reading_status: Optional[Literal['unread', 'skimmed', 'reading', 'completed']] = None
    reading_decision: Optional[Literal['pending', 'priority', 'reference', 'irrelevant']] = None
    notes_incomplete: Optional[bool] = None
    notes_reviewed: bool = False
    notes_source_revision: Optional[int] = Field(default=None, ge=0)


class EvidenceIn(BaseModel):
    claim: str = Field(min_length=1, max_length=5000)
    quote: str = Field(default='', max_length=12000)
    document_id: str = 'main'
    page: int = Field(ge=1, le=600)
    figure_id: str = ''
    status: Literal['pending', 'verified', 'rejected'] = 'pending'


class EvidencePatch(BaseModel):
    claim: Optional[str] = Field(default=None, min_length=1, max_length=5000)
    quote: Optional[str] = Field(default=None, max_length=12000)
    document_id: Optional[str] = None
    page: Optional[int] = Field(default=None, ge=1, le=600)
    figure_id: Optional[str] = None
    status: Optional[Literal['pending', 'verified', 'rejected']] = None


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
    document_id: str = "main"


class FigurePatch(BaseModel):
    label: Optional[str] = None
    caption: Optional[str] = None
    observation: Optional[str] = None


def validate_evidence(d: Path, meta: dict, record: dict) -> None:
    if not record['claim'].strip():
        raise HTTPException(422, '请填写需要核查的结论')
    source, pages, _ = source_info(d, meta, record['document_id'])
    if not source or not source.is_file():
        raise HTTPException(404, '证据对应的 PDF 不存在')
    if not 1 <= record['page'] <= pages:
        raise HTTPException(422, '证据页码超出该 PDF 的范围')
    fid = record.get('figure_id')
    if fid:
        fig = next((f for f in load_figures(d) if f['id'] == fid), None)
        if not fig or fig.get('document_id', 'main') != record['document_id'] or fig['page'] != record['page']:
            raise HTTPException(422, '图片不属于所选文件和页码')


@app.get('/api/papers/{paper_id}/evidence')
def get_evidence(paper_id: str):
    d = paper_dir(paper_id)
    meta = read_meta(d)
    return [public_evidence(d, meta, r) for r in load_evidence(d)]


@app.post('/api/papers/{paper_id}/evidence')
def add_evidence(paper_id: str, body: EvidenceIn):
    d = paper_dir(paper_id)
    with evidence_lock:
        meta = read_meta(d)
        record = body.model_dump()
        validate_evidence(d, meta, record)
        record.update(id=uuid.uuid4().hex, claim=record['claim'].strip(), created_at=_now(), updated_at=_now(),
                      source_revision=meta['document_revisions'].get(record['document_id'], 'legacy'))
        records = load_evidence(d)
        records.append(record)
        atomic_json(d / 'evidence.json', records)
        return public_evidence(d, meta, record)


@app.patch('/api/papers/{paper_id}/evidence/{evidence_id}')
def patch_evidence(paper_id: str, evidence_id: str, body: EvidencePatch):
    d = paper_dir(paper_id)
    with evidence_lock:
        meta = read_meta(d)
        records = load_evidence(d)
        record = next((r for r in records if r['id'] == evidence_id), None)
        if record is None:
            raise HTTPException(404, '证据记录不存在')
        updates = body.model_dump(exclude_none=True)
        content_changed = any(k in updates and updates[k] != record.get(k) for k in ('claim', 'quote', 'document_id', 'page', 'figure_id'))
        changed_source = any(k in updates and updates[k] != record.get(k) for k in ('document_id', 'page', 'figure_id'))
        record.update(updates)
        if content_changed and 'status' not in updates:
            record['status'] = 'pending'
        # Pending/rejected records may still be retained after their source is removed.
        explicitly_verified = updates.get('status') == 'verified'
        if changed_source or explicitly_verified:
            validate_evidence(d, meta, record)
        elif not record['claim'].strip():
            raise HTTPException(422, '请填写需要核查的结论')
        explicitly_rejected = updates.get('status') == 'rejected' and public_evidence(d, meta, record)['source_available']
        if changed_source or explicitly_verified or explicitly_rejected:
            record['source_revision'] = meta['document_revisions'].get(record['document_id'], 'legacy')
        record.update(claim=record['claim'].strip(), updated_at=_now())
        atomic_json(d / 'evidence.json', records)
        return public_evidence(d, meta, record)


@app.delete('/api/papers/{paper_id}/evidence/{evidence_id}')
def delete_evidence(paper_id: str, evidence_id: str):
    d = paper_dir(paper_id)
    with evidence_lock:
        records = load_evidence(d)
        remaining = [r for r in records if r['id'] != evidence_id]
        if len(records) == len(remaining):
            raise HTTPException(404, '证据记录不存在')
        atomic_json(d / 'evidence.json', remaining)
    return {'ok': True}


# ---------- data and code resources ----------

@app.get('/api/papers/{paper_id}/resources')
def get_paper_resources(paper_id: str):
    with storage_lock:
        d = paper_dir(paper_id)
        return public_resources(d, read_meta(d))


@app.post('/api/papers/{paper_id}/resources')
def add_paper_resource(paper_id: str, body: ResourceIn):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        record = normalize_resource(body.model_dump())
        validate_source(d, meta, record)
        records = load_resources(d)
        ensure_unique(records, record)
        record.update(id=uuid.uuid4().hex, created_at=_now(), updated_at=_now(),
                      source_revision=source_revision(meta, record))
        records.append(record)
        atomic_json(d / 'resources.json', records)
        return public_resource(d, meta, record)


@app.post('/api/papers/{paper_id}/resources/extract')
def extract_paper_resources(paper_id: str):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        records = load_resources(d)  # Corruption must never look like an empty list.
        existing_count = len(records)
        if meta.get('notes_mode') != 'deep' or not meta.get('notes', '').strip():
            raise HTTPException(400, '请先生成并保存精读笔记，再整理数据与代码')
        if meta.get('notes_incomplete'):
            raise HTTPException(409, '精读笔记尚未完成，请完成精读后再整理数据与代码')
        if meta.get('notes_stale'):
            raise HTTPException(409, '论文材料已变化，请先核对或重新生成精读笔记')
        candidates = extract_resources(meta['notes'])
        known = set().union(*(identity_keys(record) for record in records))
        added, skipped = 0, 0
        now = _now()
        for record in candidates:
            keys = identity_keys(record)
            if known.intersection(keys):
                skipped += 1
                continue
            record.update(id=uuid.uuid4().hex, created_at=now, updated_at=now,
                          source_revision='')
            records.append(record)
            known.update(keys)
            added += 1
        if added:
            # Validation may supply defaults/normalization for old libraries.
            # Append to their original records without rewriting human fields.
            path = d / 'resources.json'
            original = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
            atomic_json(path, original + records[existing_count:])
        if added:
            message = f'已从精读笔记整理 {added} 条数据与代码，均待核查原文'
        elif candidates:
            message = '精读笔记中的资源已记录，已有内容保持不变'
        else:
            message = '精读笔记中未找到明确的数据库编号或数据与代码链接'
        if skipped:
            message += f'；已跳过 {skipped} 条重复记录'
        latest = read_meta(d)
        return {'paper': summary_of(latest), 'added_count': added, 'skipped_count': skipped,
                'resources': [public_resource(d, latest, record) for record in records],
                'message': message}


@app.patch('/api/papers/{paper_id}/resources/{resource_id}')
def patch_paper_resource(paper_id: str, resource_id: str, body: ResourcePatch):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        records = load_resources(d)
        index = next((i for i, r in enumerate(records) if r['id'] == resource_id), None)
        if index is None:
            raise HTTPException(404, '资源记录不存在')
        old = records[index]
        updates = body.model_dump(exclude_unset=True)
        record = normalize_resource({**old, **updates})
        content_changed = any(record.get(k) != old.get(k) for k in ResourceIn.model_fields if k != 'status')
        source_changed = any(record[k] != old[k] for k in ('document_id', 'page'))
        if content_changed and 'status' not in updates:
            record['status'] = 'pending'
        if source_changed or updates.get('status') == 'verified':
            validate_source(d, meta, record)
            record['source_revision'] = source_revision(meta, record)
        ensure_unique(records, record)
        record['updated_at'] = _now()
        records[index] = record
        atomic_json(d / 'resources.json', records)
        return public_resource(d, meta, record)


@app.delete('/api/papers/{paper_id}/resources/{resource_id}')
def delete_paper_resource(paper_id: str, resource_id: str):
    with storage_lock:
        d = paper_dir(paper_id)
        records = load_resources(d)
        remaining = [r for r in records if r['id'] != resource_id]
        if len(records) == len(remaining):
            raise HTTPException(404, '资源记录不存在')
        atomic_json(d / 'resources.json', remaining)
        return {'ok': True}


@app.get('/api/resources')
def get_library_resources(q: str = ''):
    with storage_lock:
        entries = []
        if PAPERS_DIR.is_dir():
            for d in sorted(PAPERS_DIR.iterdir()):
                if (d / 'meta.json').is_file():
                    meta = read_meta(d)
                    entries.extend({**r, 'paper_id': meta['id'], 'paper_title': meta['title']} for r in public_resources(d, meta))
        return related_resources(entries, q)


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
    d = paper_dir(paper_id)
    meta = read_meta(d)
    return {**meta, "reading_position": read_positions(d, meta)}


class ReadingPosition(BaseModel):
    page: int = Field(ge=1, le=600)
    scale: float = Field(ge=0.3, le=3, allow_inf_nan=False)
    fit_width: bool


@app.put("/api/papers/{paper_id}/reading-position")
def update_reading_position(paper_id: str, body: ReadingPosition, document_id: str = "main"):
    d = paper_dir(paper_id)
    if not document_path(d, document_id).is_file():
        raise HTTPException(404, "PDF 不存在")
    meta = read_meta(d)
    pages = meta["pages"] if document_id == "main" else next(s["pages"] for s in meta["supplements"] if s["id"] == document_id)
    position = {"page": min(body.page, pages), "scale": body.scale, "fit_width": body.fit_width}
    save_position(d, document_id, position)
    return position


@app.patch("/api/papers/{paper_id}")
def patch_paper(paper_id: str, body: PaperPatch):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        if body.title is not None and body.title.strip():
            meta["title"] = body.title.strip()
        if body.favorite is not None:
            meta["favorite"] = body.favorite
        if body.notes is not None:
            if not meta.get("notes", "").strip():
                meta["notes_material_revision"] = meta["material_revision"]
            if body.notes_source_revision is not None:
                if body.notes_source_revision > meta['material_revision']:
                    raise HTTPException(422, '笔记来源版本不能超过当前材料版本')
                meta['notes_material_revision'] = body.notes_source_revision
            meta["notes"] = body.notes
            meta["notes_updated_at"] = _now()
        for field in ("reading_status", "reading_decision", "notes_incomplete"):
            value = getattr(body, field)
            if value is not None:
                meta[field] = value
        if body.notes_reviewed:
            meta["notes_material_revision"] = meta["material_revision"]
        if body.tags is not None:
            seen: list[str] = []
            for t in body.tags:
                t = t.strip()[:40]
                if t and t not in seen:
                    seen.append(t)
            if meta['tags'] != seen[:30]:
                meta['tags_revision'] += 1
                meta["tags"] = seen[:30]
        for field in ("authors", "year", "journal"):
            value = getattr(body, field)
            if value is not None:
                meta[field] = value.strip()
        if body.doi is not None:
            meta["doi"] = body.doi.strip()
        write_meta(d, meta)
        return meta


async def auto_tag_paper(paper_id: str):
    """Infer labels independently; never modify notes or replace human tags."""
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        settings = load_settings()
        if not settings.get('api_key'):
            raise HTTPException(400, '尚未设置 API Key，请先在「设置」中填写')
        snapshot = tag_snapshot(meta)
        text = ''
        text_path = d / 'text.txt'
        if text_path.is_file():
            with text_path.open(encoding='utf-8', errors='replace') as source:
                text = source.read(TEXT_LIMIT)
        try:
            messages = tag_messages(meta, text)
        except TagFormatError as exc:
            raise HTTPException(400, str(exc)) from exc
    for attempt in range(2):
        # Do not spend another request on a snapshot already invalidated by an edit.
        with storage_lock:
            if tag_snapshot(read_meta(paper_dir(paper_id))) != snapshot:
                raise HTTPException(409, '识别期间标签、论文材料或笔记已变化，本次结果未写入，请重试')
        try:
            output = await asyncio.wait_for(chat_once(
                settings['base_url'], settings['api_key'], settings['model'], messages,
                temperature=0.1, max_tokens=2048, timeout=TAG_TIMEOUT_SECONDS,
            ), timeout=TAG_TIMEOUT_SECONDS)
            proposed = parse_tags(output)
            break
        except Exception as exc:
            timed_out = isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException))
            transient = (timed_out or isinstance(exc, (httpx.NetworkError, httpx.RemoteProtocolError, TagFormatError))
                         or isinstance(exc, UpstreamError) and exc.status_code in (429, 500, 502, 503, 504))
            if attempt == 0 and transient:
                if isinstance(exc, TagFormatError):
                    messages = messages + [{'role': 'user', 'content': '请重新整理为合法 JSON 字符串数组。最多6个，只保留材料中有依据的标签；无依据则返回 []，不要解释或补充其他字段。'}]
                await asyncio.sleep(TAG_RETRY_DELAY_SECONDS)
                continue
            if timed_out:
                raise HTTPException(504, f'标签识别等待超时（每次最多{TAG_TIMEOUT_SECONDS:g}秒，已尝试{attempt + 1}次），已有标签与笔记已保留，可稍后重试') from exc
            if isinstance(exc, (UnsafeURLError, UpstreamError, TagFormatError)):
                raise HTTPException(502, str(exc)) from exc
            raise HTTPException(502, '自动识别标签失败，已有标签与笔记已保留，请稍后重试') from exc
    with storage_lock:
        d = paper_dir(paper_id)
        latest = read_meta(d)
        if tag_snapshot(latest) != snapshot:
            raise HTTPException(409, '识别期间标签、论文材料或笔记已变化，本次结果未写入，请重试')
        merged, added = merge_tags(latest['tags'], proposed)
        if added:
            latest['tags'] = merged
            latest['tags_revision'] += 1
            write_meta(d, latest)
            message = f'已自动添加 {len(added)} 个标签'
        elif not proposed:
            message = '未识别到有充分依据的标签，已有标签与笔记已保留'
        elif len(latest['tags']) >= 30:
            message = '标签已达到30个上限，已有标签保持不变'
        else:
            message = '识别出的标签已存在，无需重复添加'
        return {'paper': summary_of(latest), 'added_tags': added, 'message': message}


@app.post('/api/papers/{paper_id}/tags/auto')
async def auto_tag_endpoint(paper_id: str):
    return await auto_tag_paper(paper_id)


@app.delete("/api/papers/{paper_id}")
def delete_paper(paper_id: str):
    with storage_lock:
        shutil.rmtree(paper_dir(paper_id))
        return {"ok": True}


@app.post("/api/papers/{paper_id}/file")
async def attach_file(paper_id: str, file: UploadFile = File(...)):
    """Attach (or replace) the PDF of an existing entry, e.g. one imported by DOI."""
    d = paper_dir(paper_id)
    filename = Path(file.filename or "paper.pdf").name
    content = await file.read()
    _validate_pdf_upload(filename, content)
    with storage_lock:
        meta = read_meta(d)
        try:
            meta = ingest_pdf(d, content, filename, meta)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"PDF 解析失败：{exc}") from exc
        write_meta(d, meta)
        return meta


@app.post("/api/papers/{paper_id}/supplements")
async def add_supplement(paper_id: str, file: UploadFile = File(...)):
    d = paper_dir(paper_id)
    filename = Path(file.filename or "supplement.pdf").name
    content = await file.read()
    _validate_pdf_upload(filename, content)
    digest = hashlib.sha256(content).hexdigest()
    with storage_lock:
        meta = read_meta(d)
        if any(f.get("sha256") == digest for f in meta["supplements"]):
            raise HTTPException(409, "该补充 PDF 已添加")
        if (d / "paper.pdf").is_file() and hashlib.sha256((d / "paper.pdf").read_bytes()).hexdigest() == digest:
            raise HTTPException(409, "该 PDF 已是正文，不必重复添加")
        sid = uuid.uuid4().hex
        try:
            parsed, figures = store_document(d, content, filename, sid)
        except Exception as exc:
            raise HTTPException(400, f"补充 PDF 解析失败：{exc}") from exc
        meta["supplements"].append({"id": sid, "filename": filename, "pages": len(parsed["pages"]),
                                    "sha256": digest, "warnings": parsed.get("warnings", [])})
        meta["figures_count"] = len(figures)
        materials_changed(meta, sid, digest)
        write_meta(d, meta)
        return meta


@app.delete("/api/papers/{paper_id}/supplements/{document_id}")
def delete_supplement(paper_id: str, document_id: str):
    with storage_lock:
        d = paper_dir(paper_id)
        path = document_path(d, document_id)
        if document_id == "main":
            raise HTTPException(400, "此接口只移除补充材料")
        meta = read_meta(d)
        figures = [f for f in load_figures(d) if f.get("document_id", "main") != document_id]
        # Keep image files referenced by existing notes. New readings no longer use them.
        save_figures(d, figures)
        path.unlink(missing_ok=True)
        path.with_suffix('.txt').unlink(missing_ok=True)
        meta["supplements"] = [f for f in meta["supplements"] if f["id"] != document_id]
        meta["figures_count"] = len(figures)
        materials_changed(meta, document_id, 'removed')
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
    with storage_lock:
        meta = read_meta(d)
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
def get_paper_file(paper_id: str, document_id: str = "main"):
    d = paper_dir(paper_id)
    path = document_path(d, document_id)
    if not path.is_file():
        raise HTTPException(404, "该文献尚未附加 PDF")
    meta = read_meta(d)
    filename = meta["filename"] if document_id == "main" else next(f["filename"] for f in meta["supplements"] if f["id"] == document_id)
    return FileResponse(path, media_type="application/pdf", filename=filename or "paper.pdf")


@app.get("/api/papers/{paper_id}/text")
def get_paper_text(paper_id: str):
    return PlainTextResponse((paper_dir(paper_id) / "text.txt").read_text("utf-8"))


@app.get("/api/papers/{paper_id}/export.md")
def export_markdown(paper_id: str):
    with storage_lock:
        meta = read_meta(paper_dir(paper_id))
        safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", meta["title"])[:80] or "notes"
        headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{url_quote(safe_name, safe='')}.md"}
        notes = prepare_note_export(paper_id, NoteExport(notes=meta.get('notes', '')))['notes']
        return PlainTextResponse(notes, media_type="text/markdown; charset=utf-8", headers=headers)


class NoteExport(BaseModel):
    notes: str


@app.post('/api/papers/{paper_id}/notes/export-preview')
def prepare_note_export(paper_id: str, body: NoteExport):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        # Strip only our intact generated appendix before figure inference so
        # literal resource fields cannot be mistaken for narrative citations.
        raw_notes = append_research_appendix(body.notes, [], [])
        notes = illustrate_existing_notes(paper_id, NoteExport(notes=raw_notes))['notes']
        evidence = [public_evidence(d, meta, r) for r in load_evidence(d)]
        return {'notes': append_research_appendix(notes, evidence, public_resources(d, meta))}


@app.post("/api/papers/{paper_id}/export.zip")
def export_note_bundle(paper_id: str, body: NoteExport):
    try:
        with storage_lock:
            d = paper_dir(paper_id)
            notes = prepare_note_export(paper_id, body)["notes"]
            data = markdown_bundle(notes, d)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(data, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="notes.zip"'})


@app.post("/api/papers/{paper_id}/notes/illustrate")
def illustrate_existing_notes(paper_id: str, body: NoteExport, all_figures: int = 0):
    d = paper_dir(paper_id)
    figures = [f for f in load_figures(d) if (d / 'figures' / f"{f['id']}.png").is_file()]
    return {"notes": illustrate_notes(body.notes, figures, "deep" if all_figures else read_meta(d).get("notes_mode", "deep"), drop_unknown=False)}


# ---------- figures ----------

def _figure_file(d: Path, fid: str) -> Path:
    if not re.fullmatch(r"[a-z]\d{3,6}", fid):
        raise HTTPException(404, "figure not found")
    f = d / "figures" / f"{fid}.png"
    if not f.is_file():
        raise HTTPException(404, "figure not found")
    return f


def _next_fid(figures: list[dict], d: Path) -> str:
    nums = [int(p.stem[1:]) for p in (d / "figures").glob("*.png") if re.fullmatch(r"[a-z]\d{3,6}", p.stem)]
    nums += [int(f["id"][1:]) for f in figures if re.fullmatch(r"[a-z]\d+", f.get("id", ""))]
    return f"m{(max(nums) + 1) if nums else 1:03d}"


def _update_count(d: Path, figures: list[dict]) -> None:
    with storage_lock:
        meta = read_meta(d)
        meta["figures_count"] = len(figures)
        write_meta(d, meta)


@app.get("/api/papers/{paper_id}/figures")
def get_figures(paper_id: str):
    return load_figures(paper_dir(paper_id))


@app.post("/api/papers/{paper_id}/figures/extract")
def reextract_figures(paper_id: str, document_id: str = "main"):
    with storage_lock:
        d = paper_dir(paper_id)
        path = document_path(d, document_id)
        if not path.is_file():
            raise HTTPException(400, "该文献尚未附加 PDF")
        meta = read_meta(d)
        filename = meta["filename"] if document_id == "main" else next(f["filename"] for f in meta["supplements"] if f["id"] == document_id)
        try:
            parsed, figures = store_document(d, path.read_bytes(), filename, document_id)
        except Exception as exc:
            raise HTTPException(400, f"图片提取失败：{exc}") from exc
        _update_count(d, figures)
        return {"figures": figures, "warnings": parsed.get("warnings", [])}


@app.post("/api/papers/{paper_id}/figures/crop")
def crop_manual_figure(paper_id: str, body: CropIn):
    with storage_lock:
        d = paper_dir(paper_id)
        source = document_path(d, body.document_id)
        if not source.is_file():
            raise HTTPException(400, "该文献尚未附加 PDF")
        figures = load_figures(d)
        fid = _next_fid(figures, d)
        try:
            fig = crop_figure(source, d / "figures", body.page, body.bbox, fid)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        fig["label"] = (body.label or "").strip()[:60] or f"手动取图 {fid[1:].lstrip('0') or '1'}"
        fig["caption"] = (body.caption or "").strip()
        fig["observation"] = ""
        fig["document_id"] = body.document_id
        fig["document_name"] = "正文" if body.document_id == "main" else next(f["filename"] for f in read_meta(d)["supplements"] if f["id"] == body.document_id)
        figures.append(fig)
        save_figures(d, figures)
        _update_count(d, figures)
        return fig


@app.patch("/api/papers/{paper_id}/figures/{fid}")
def patch_figure(paper_id: str, fid: str, body: FigurePatch):
    with storage_lock:
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
    with storage_lock:
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
    parents.sort(key=lambda f: (f.get("document_id", "main") != "main", f.get("document_id", "main"), f.get("page", 0), f.get("id", "")))
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
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        settings = load_settings()
        if not settings.get("api_key"):
            raise HTTPException(400, "尚未设置 API Key，请先在「设置」中填写")
        if mode not in MODE_NAMES:
            raise HTTPException(400, "mode 只能是 deep 或 quick")
        if not meta.get("has_file"):
            raise HTTPException(400, "该文献只有元数据，请先附加 PDF 再生成笔记")
        text = reading_text(d, meta)
        if len(text.strip()) < 200:
            raise HTTPException(400, "PDF 提取到的文本过少（可能是扫描件），无法生成笔记")
        budget = int(settings["max_chars"])
        max_tokens = int(settings.get("max_tokens") or 0) or None

    async def generate():
        chunks: list[str] = []
        yield _sse({"type": "start", "mode": mode, "model": settings["model"], "mode_name": MODE_NAMES[mode],
                    "material_revision": meta["material_revision"]})
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
        if use_figures:
            available = [f for f in figures_for_prompt if (d / "figures" / f"{f['id']}.png").is_file()]
            notes = illustrate_notes(notes, available, mode)
        with storage_lock:
            current = read_meta(d)
            current["notes"] = notes
            current["notes_mode"] = mode
            current["notes_updated_at"] = _now()
            current["notes_incomplete"] = False
            current["notes_material_revision"] = meta["material_revision"]
            write_meta(d, current)
        yield _sse({"type": "done", "notes": notes, "paper": summary_of(current)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
