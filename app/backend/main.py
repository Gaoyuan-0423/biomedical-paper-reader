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
import logging
import os
import re
import shutil
import tempfile
import hashlib
import uuid
import time
import httpx
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote as url_quote

from fastapi import FastAPI, File, HTTPException, UploadFile, Query
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from PIL import Image
from pydantic import BaseModel, Field
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from external import LookupError_, fetch_crossref, fetch_related, normalize_doi
from figures import FigureStorageError, crop_figure, crop_source_changed, load_figures, parse_pdf, save_figures
from caption_migration import upgrade_captions
from note_assets import illustrate_notes, markdown_bundle
from reading_state import read_positions, save_position
from reading_workflow import (DERIVED_FIELDS, PAPER_SCHEMA_VERSION, SidecarError, annotate, atomic_json, defaults,
                      evidence_lock, load_evidence, materials_changed, public_evidence, source_info, storage_lock)
from paper_resources import (RESOURCE_DERIVED_FIELDS, ResourceIn, ResourceCreate, ResourceUpdate, ensure_unique,
                             load_resources, normalize_resource, public_resource, public_resources,
                             related_resources, resource_summary, source_revision, validate_source)
from sample_provenance import SampleField, apply_field_sources, public_field_source
from research_export import append_research_appendix
from resource_catalog import filter_resources, resource_csv, accession_text
from resource_extraction import extract_resources, plan_resource_update
from paper_tags import TEXT_LIMIT, TagFormatError, merge_tags, parse_tags, tag_messages, tag_snapshot
from geo_metadata import GeoError, cache_fresh, fetch_geo_metadata, load_geo_cache, save_geo_cache, validate_accession
from paper_resources import resource_accessions
from chat_sources import file_revision, prepare_sources, save_parsed_blocks, validate_source_snapshot, verified_source
from paper_chat import (CHAT_TIMEOUT, MAX_OUTPUT, ChatIn, active_requests, chat_messages,
                        history_page, load_history, new_messages, recover_history, save_history, table_choice, update_message, now as chat_now)
import zotero_import as zi
import zotero_local as zl
from zotero_mcp import ZoteroMCPClient, MCPError
from library_search import search_library
import paper_comparisons as comparisons
import report_review as rr
import comparison_drafts
import comparison_pdf
import figure_decks
import figure_shelf as fs
import note_sources as ns
import identifier_import as ids
import article_links
import bulk_tags
import digest_export
import pmc_materials as pmc
import supplement_tables
import oa_fulltext as oa
import saved_pubmed as sp
import project_libraries as pl
import library_digest
import ui_preferences as ui
from llm import (
    CompletionError,
    is_official_glm53,
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
TRASH_DIR = DATA_DIR / "trash"
SETTINGS_FILE = DATA_DIR / "settings.json"
FRONTEND_DIR = APP_DIR / "frontend"

MAX_UPLOAD_BYTES = 80 * 1024 * 1024
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
RELATED_TTL_SECONDS = 7 * 24 * 3600
VISION_MAX_SIDE = 1400
FIGURE_TOKEN_BUDGETS = (1200, 4096)
GLM53_FIGURE_TOKEN_BUDGETS = (8192, 16384)
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


@app.exception_handler(FigureStorageError)
async def figure_storage_error(_request, error):
    return JSONResponse(status_code=409, content={"detail": str(error)})


# ---------- storage helpers ----------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _raw_meta(d: Path) -> dict:
    return json.loads((d / "meta.json").read_text("utf-8"))


def schema_version_of(meta: dict) -> int:
    # Papers written before versioning carry no field and stay readable as version 1.
    try:
        return int(meta.get("schema_version", 1))
    except (TypeError, ValueError, AttributeError):
        return 1


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
    if _future_schema(d):
        raise HTTPException(409, "该文献由更新版本的 BioReader 写入，为避免误读已拒绝访问；请升级应用后再试。")
    return d


def read_meta(d: Path) -> dict:
    with storage_lock:
        meta = _raw_meta(d)
        version = schema_version_of(meta)
        if version > PAPER_SCHEMA_VERSION:
            raise HTTPException(409, f"该文献由更新版本的 BioReader 写入（数据版本 {version}，"
                                     f"当前支持 {PAPER_SCHEMA_VERSION}），为避免误读已拒绝加载；请升级应用。")
        meta.setdefault("schema_version", PAPER_SCHEMA_VERSION)
        meta.setdefault("has_file", (d / "paper.pdf").is_file())
        meta.setdefault("tags", [])
        meta.setdefault("tags_revision", 0)
        meta.setdefault('notes_revision', 0)
        meta.setdefault('notes_source_snapshot', '')
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
    if meta['storage_warnings']:
        meta['tasks'].append({'id': 'storage', 'label': '检查本地记录'})
    return meta


def write_meta(d: Path, meta: dict) -> None:
    with storage_lock:
        if schema_version_of(meta) > PAPER_SCHEMA_VERSION or _future_schema(d):
            raise HTTPException(409, "该文献数据版本较新，已拒绝写入以免覆盖；请升级应用后再操作。")
        meta.setdefault('notes_revision', 0)
        meta.setdefault('notes_source_snapshot', '')
        defaults(meta)
        atomic_json(d / "meta.json", {k: v for k, v in meta.items() if k not in DERIVED_FIELDS + RESOURCE_DERIVED_FIELDS})
        annotate_paper(d, meta)


def summary_of(meta: dict) -> dict:
    out = dict(meta)
    out.pop("notes", None)
    out.pop("abstract", None)
    out.pop("zotero_local_notes", None)
    out.pop("claim_reviews", None)
    out.pop("notes_figure_coverage", None)
    return out


def list_meta() -> list[dict]:
    items = []
    if PAPERS_DIR.is_dir():
        for d in PAPERS_DIR.iterdir():
            if (d / "meta.json").is_file():
                if _future_schema(d):
                    items.append(_incompatible_summary(d, _raw_meta(d)))
                    continue
                items.append(summary_of(read_meta(d)))
    items.sort(key=lambda m: m.get("added_at", ""), reverse=True)
    return items


def new_paper_dir() -> tuple[str, Path]:
    paper_id = uuid.uuid4().hex
    d = PAPERS_DIR / paper_id
    d.mkdir(parents=True, exist_ok=True)
    return paper_id, d


def _future_schema(d: Path) -> bool:
    """True when the folder was written by a newer build; refuse to interpret it."""
    try:
        return schema_version_of(_raw_meta(d)) > PAPER_SCHEMA_VERSION
    except (OSError, ValueError):
        return False


def _incompatible_summary(d: Path, meta: dict) -> dict:
    """Future-version papers stay visible in the list but are never derived from."""
    stub = dict(meta)
    stub.setdefault("has_file", (d / "paper.pdf").is_file())
    stub.setdefault("tags", [])
    stub.setdefault("tags_revision", 0)
    stub.setdefault("notes_revision", 0)
    stub.setdefault("notes_source_snapshot", '')
    stub.setdefault("journal", "")
    stub.setdefault("supplements", [])
    stub["schema_incompatible"] = True
    return summary_of(stub)


def trash_paper_dir(paper_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", paper_id):
        raise HTTPException(404, "paper not found")
    d = TRASH_DIR / paper_id
    if not d.is_dir():
        raise HTTPException(404, "paper not found in trash")
    return d


def list_trash() -> list[dict]:
    items = []
    if TRASH_DIR.is_dir():
        for d in TRASH_DIR.iterdir():
            if not (d / "meta.json").is_file():
                continue
            try:
                meta = _raw_meta(d)
            except (OSError, ValueError):
                logging.getLogger(__name__).warning('回收站文献 %s 的元数据无法读取，已跳过。', d.name)
                continue
            if not isinstance(meta, dict):
                logging.getLogger(__name__).warning('回收站文献 %s 的元数据格式无效，已跳过。', d.name)
                continue
            meta.setdefault("title", "")
            meta.setdefault("doi", "")
            items.append(summary_of(meta))
    items.sort(key=lambda m: m.get("trashed_at", ""), reverse=True)
    return items


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
    prior_revision = (_raw_meta(d).get("document_revisions", {}).get(document_id, "legacy")
                      if (d / "meta.json").is_file() else "legacy")
    new_revision = hashlib.sha256(content).hexdigest()
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
                item.update({key: old[key] for key in ("favorite", "tags", "shelf_key", "shelf_source_revision") if key in old})
                item["observation"] = old.get("observation", "")
                if item["observation"] and (prior_revision != new_revision or "observation_source_revision" in old):
                    item["observation_source_revision"] = old.get("observation_source_revision", prior_revision)
                if old.get("observation_language") in ("zh-CN", "en"):
                    item["observation_language"] = old["observation_language"]
                if old.get("caption_edited"):
                    item.update(caption=old["caption"], caption_edited=True)
                    if prior_revision != new_revision or "caption_source_revision" in old:
                        item["caption_source_revision"] = old.get("caption_source_revision", prior_revision)
            fresh.append(item)
        for item, original in zip(fresh, parsed["figures"]):
            item["parent"] = mapping.get(original.get("parent"))
        destination = d / "paper.pdf" if document_id == "main" else d / "supplements" / f"{document_id}.pdf"
        destination.parent.mkdir(parents=True, exist_ok=True)
        (d / "figures").mkdir(exist_ok=True)
        for original in parsed["figures"]:
            shutil.copy2(stage / "figures" / f"{original['id']}.png", d / "figures" / f"{mapping[original['id']]}.png")
        # Manual crops keep their original source even when the PDF is replaced.
        manual = [f for f in own if f.get("source") == "manual"]
        if any("crop_source_revision" not in f for f in manual) and (d / "meta.json").is_file():
            for item in manual:
                item.setdefault("crop_source_revision", prior_revision)
        figures = others + fresh + manual
        text = "\n\n".join(f"[Page {page['number']}]\n{page['text']}" for page in parsed["pages"])
        shutil.copy2(source, destination)
        destination.with_suffix('.txt').write_text(text, "utf-8")
        if document_id == "main":
            (d / "text.txt").write_text(text, "utf-8")
        save_figures(d, figures)
        save_parsed_blocks(d, document_id, new_revision, parsed['pages'])
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
        "schema_version": PAPER_SCHEMA_VERSION,
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
    tags_revision: Optional[int] = Field(default=None, ge=0)
    authors: Optional[str] = None
    year: Optional[str] = None
    journal: Optional[str] = None
    doi: Optional[str] = None
    reading_status: Optional[Literal['unread', 'skimmed', 'reading', 'completed']] = None
    reading_decision: Optional[Literal['pending', 'priority', 'reference', 'irrelevant']] = None
    notes_incomplete: Optional[bool] = None
    notes_language: Optional[Literal['zh-CN', 'en']] = None
    notes_reviewed: bool = False
    notes_source_revision: Optional[int] = Field(default=None, ge=0)
    notes_source_snapshot: Optional[str] = Field(default=None, pattern=r'^[0-9a-f]{32}$')


class EvidenceIn(BaseModel):
    expected_source_revision: Optional[str] = Field(default=None, max_length=100)
    selection_bbox: Optional[tuple[float, float, float, float]] = None
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
    expected_figure_source: Optional[tuple[str, int, list[float]]] = None
    favorite: Optional[bool] = None
    tags: Optional[list[str]] = None
    expected_source_revision: Optional[str] = Field(default=None, max_length=100)
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
        if crop_source_changed(fig, meta.get('document_revisions', {})):
            raise HTTPException(409, '此裁图来自旧版 PDF，请在当前原文重新取图。')


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
        record = body.model_dump(exclude_none=True)
        expected = record.pop('expected_source_revision', None)
        if expected is not None and expected != meta['document_revisions'].get(record['document_id'], 'legacy'):
            raise HTTPException(409, '原文已更新，请重新选取摘录。')
        if 'selection_bbox' in record:
            x0, y0, x1, y1 = record['selection_bbox']
            if not expected or not record['quote'].strip() or not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise HTTPException(422, '原文选区坐标无效')
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
        if changed_source or ('quote' in updates and updates['quote'] != record.get('quote')):
            record.pop('selection_bbox', None)
        if record.get('selection_bbox') and record.get('source_revision') != meta['document_revisions'].get(record['document_id'], 'legacy'):
            record.pop('selection_bbox', None)
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


# ---------- project comparisons ----------

def comparison_material(paper_id: str):
    directory = paper_dir(paper_id)
    return comparisons.material(directory, read_meta(directory))


@app.get('/api/comparisons/{library_id}')
def list_comparisons(library_id: str, paper_id: Optional[comparisons.Id] = None):
    with storage_lock:
        members = project_paper_ids(library_id)
        if paper_id is not None and paper_id not in members:
            raise HTTPException(404, '论文不在当前课题库。')
        return comparisons.summaries(DATA_DIR, library_id, paper_id)


@app.get('/api/comparisons/{library_id}/material/{paper_id}')
def get_comparison_material(library_id: str, paper_id: str):
    with storage_lock:
        if paper_id not in project_paper_ids(library_id):
            raise HTTPException(404, '论文不在当前课题库。')
        return comparison_material(paper_id)


@app.get('/api/comparisons/{library_id}/material/{paper_id}/source')
def verify_comparison_source(library_id: str, paper_id: str, key: str = Query(min_length=1, max_length=160)):
    if key.startswith('pdf:'):
        with storage_lock:
            material = get_comparison_material(library_id, paper_id)
            directory = paper_dir(paper_id)
            meta = read_meta(directory)
        source = comparison_pdf.resolve(directory, meta, material, key)
        if source:
            with storage_lock:
                live = get_comparison_material(library_id, paper_id)
                doc = next((d for d in live['documents'] if d['id'] == source['document_id']
                            and d['revision'] == source['source_revision']), None)
                if doc:
                    return {**source, 'metadata_revision': doc['metadata_revision']}
        raise HTTPException(409, '所选证据或原文已变更，请刷新材料后重新选择。')

    def resolve():
        material = get_comparison_material(library_id, paper_id)
        source = comparisons.resolve_source(key, material)
        if not source:
            raise HTTPException(409, '所选证据或原文已变更，请刷新材料后重新选择。')
        doc = next(d for d in material['documents'] if d['id'] == source['document_id'])
        return source, doc

    with storage_lock:
        source, doc = resolve()
        directory = paper_dir(paper_id)
        pdf = document_path(directory, doc['id'])
    if comparisons.file_revision(pdf) != doc['revision']:
        raise HTTPException(409, 'PDF 内容与所选来源版本不符。')
    with storage_lock:
        source, doc = resolve()
        return {**source, 'metadata_revision': doc['metadata_revision']}


def comparison_draft_paper(paper_id, columns, keys=None, question='', pdf_budget=0):
    with storage_lock:
        directory = paper_dir(paper_id)
        meta = read_meta(directory)
        material = comparisons.material(directory, meta)
    return comparison_drafts.paper_draft(directory, meta, material, columns, keys, question, pdf_budget)


@app.post('/api/comparisons/{library_id}/draft')
def draft_comparison(library_id: str, body: comparison_drafts.DraftIn):
    def require_membership():
        if not set(body.paper_ids).issubset(project_paper_ids(library_id)):
            raise HTTPException(409, '所选论文已不在当前课题库。')

    with storage_lock:
        require_membership()
    budget = min(comparison_pdf.PER_PAPER_PAGES, comparison_pdf.PER_DRAFT_PAGES // len(body.paper_ids)) if body.include_pdf else 0
    papers = [comparison_draft_paper(pid, body.columns, question=body.question, pdf_budget=budget) for pid in body.paper_ids]
    with storage_lock:
        require_membership()
    return {'scope': 'saved_and_pdf' if body.include_pdf else 'saved_sources', 'columns': body.columns, 'papers': papers}


@app.get('/api/comparisons/{library_id}/{comparison_id}')
def get_comparison(library_id: str, comparison_id: str):
    with storage_lock:
        project_paper_ids(library_id)
        return comparisons.load(comparisons.record_path(DATA_DIR, library_id, comparison_id))


@app.put('/api/comparisons/{library_id}/{comparison_id}')
def save_comparison(library_id: str, comparison_id: str, body: comparisons.ComparisonIn):
    # Extract/check PDF pages outside the shared storage lock. At commit, check the
    # comparison revision, project membership and source identities again.
    with storage_lock:
        allowed = project_paper_ids(library_id)
        path = comparisons.record_path(DATA_DIR, library_id, comparison_id)
        previous = comparisons.load(path) if path.exists() else {}
        if previous.get('request_digest') == comparisons.request_digest(body):
            return previous
        if previous.get('revision', 0) != body.revision:
            raise HTTPException(409, '比较记录已在其他窗口更新，请重新打开后合并修改。')
        changed = comparisons.changed_cells(previous, body)
        if not set(changed).issubset(allowed):
            raise HTTPException(409, '所选论文已不在当前课题库。')
    verified = {}
    for paper_id, cells in changed.items():
        draft = comparison_draft_paper(paper_id, {c.column for c in cells}, {c.source_key for c in cells}, body.question)
        verified[paper_id] = {(group['cohort'], col, candidate['source']['key']): candidate['source']
                              for group in draft['cohorts'] for col, data in group['cells'].items()
                              for candidate in data['candidates']}
    with storage_lock:
        return comparisons.save(DATA_DIR, library_id, comparison_id, body, project_paper_ids(library_id),
                                comparison_material, verified)


def comparison_measurement_row(library_id, comparison_id, paper_id, required=True):
    if paper_id not in project_paper_ids(library_id):
        raise HTTPException(404, '论文不在当前课题库。')
    path = comparisons.record_path(DATA_DIR, library_id, comparison_id)
    record = comparisons.load(path)
    return path, record, comparisons.comparison_row(record, paper_id, required)


def verify_current_measurement_file(directory, source):
    # Recheck identity and bytes after the unlocked parse, before adopting or navigating.
    path = supplement_tables.table_source_path(directory, read_meta(directory), source)
    if file_revision(path) != source['revision']:
        raise HTTPException(409, '补充表内容已变化，请重新核对原文件。')


@app.post('/api/comparisons/{library_id}/{comparison_id}/table-measurements')
def adopt_comparison_measurement(library_id: str, comparison_id: str, body: comparisons.MeasurementIn):
    with storage_lock:
        comparison_measurement_row(library_id, comparison_id, body.paper_id, required=not body.add_paper)
        directory = paper_dir(body.paper_id)
        meta = read_meta(directory)
        message = next((item for item in load_history(directory) if item['id'] == body.message_id
                        and item['role'] == 'assistant' and item['status'] == 'completed'), None)
        if not message or body.source_id not in message.get('cited_source_ids', []):
            raise HTTPException(404, '当前论文的回答没有引用这个补充表数值。')
        source = next((item for item in message['sources'] if item['id'] == body.source_id
                       and item.get('kind') == 'table'), None)
        if not source:
            raise HTTPException(404, '当前论文的回答没有引用这个补充表数值。')
    # The client supplies an address in this paper's conversation, never evidence bytes or a value.
    supplement_tables.verified_table_source(directory, meta, source)
    with storage_lock:
        path, record, _ = comparison_measurement_row(library_id, comparison_id, body.paper_id, required=not body.add_paper)
        verify_current_measurement_file(directory, source)
        return comparisons.adopt_measurement(path, record, body, source, read_meta(directory).get('title') or body.paper_id)


@app.get('/api/comparisons/{library_id}/{comparison_id}/table-measurements/{paper_id}/{measurement_id}/source')
def get_comparison_measurement(library_id: str, comparison_id: str, paper_id: str, measurement_id: str):
    with storage_lock:
        _, _, row = comparison_measurement_row(library_id, comparison_id, paper_id)
        item = next((m for m in row.get('table_measurements', []) if m['id'] == measurement_id), None)
        if item is None:
            raise HTTPException(404, '该比较行没有这个补充表数值。')
        directory = paper_dir(paper_id)
        source = item['source']
        meta = read_meta(directory)
    preview = supplement_tables.verified_table_source(directory, meta, source)
    with storage_lock:
        _, _, row = comparison_measurement_row(library_id, comparison_id, paper_id)
        if not any(m['id'] == measurement_id and m['source'] == source for m in row.get('table_measurements', [])):
            raise HTTPException(409, '当前比较的来源已变化，请重新打开。')
        verify_current_measurement_file(directory, source)
    return {'source': {**preview, 'paper_id': paper_id}}


# ---------- data and code resources ----------

@app.get('/api/papers/{paper_id}/resources')
def get_paper_resources(paper_id: str, q: str = Query(default='', max_length=500),
                        organism: str = Query(default='', max_length=1000),
                        tissue: str = Query(default='', max_length=1000),
                        disease: str = Query(default='', max_length=1000),
                        modality: str = Query(default='', max_length=1000),
                        platform: str = Query(default='', max_length=1000)):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        entries = [{**record, 'paper_id': paper_id, 'paper_title': meta['title'], 'paper_doi': meta.get('doi', '')}
                   for record in public_resources(d, meta)]
        return filter_resources(entries, q, organism, tissue, disease, modality, platform)


@app.post('/api/papers/{paper_id}/resources')
def add_paper_resource(paper_id: str, body: ResourceCreate):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        record = normalize_resource(body.model_dump(exclude={'field_source_changes'}))
        apply_field_sources(d, meta, record, body.field_source_changes)
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
        if meta.get('notes_mode') != 'deep' or not meta.get('notes', '').strip():
            raise HTTPException(400, '请先生成并保存精读笔记，再整理数据与复现')
        if meta.get('notes_incomplete'):
            raise HTTPException(409, '精读笔记尚未完成，请完成精读后再整理数据与复现')
        if meta.get('notes_stale'):
            raise HTTPException(409, '论文材料已变化，请先核对或重新生成精读笔记')
        candidates = extract_resources(meta['notes'])
        additions, replaced_ids, skipped = plan_resource_update(records, candidates)
        added, updated = len(additions), len(replaced_ids)
        now = _now()
        for record in additions:
            record.update(id=uuid.uuid4().hex, created_at=now, updated_at=now,
                          source_revision='')
        if added or updated:
            # Validation may supply defaults/normalization for old libraries.
            # Append to their original records without rewriting human fields.
            path = d / 'resources.json'
            original = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
            if updated:
                atomic_json(d / ('resources.backup-' + uuid.uuid4().hex + '.json'), original)
            atomic_json(path, [record for record in original if record['id'] not in replaced_ids] + additions)
        if added:
            message = f'已从精读笔记整理 {added} 条资源记录'
        elif candidates:
            message = '精读笔记中的资源已记录，已有内容保持不变'
        else:
            message = '精读笔记中未找到明确的资源表、数据库编号或数据／代码链接'
        if skipped:
            message += f'；已跳过 {skipped} 条重复记录'
        if updated:
            message += f'；已备份并更新 {updated} 条未人工修改的旧自动记录'
        latest = read_meta(d)
        return {'paper': summary_of(latest), 'added_count': added, 'skipped_count': skipped,
                'updated_count': updated, 'backup_created': bool(updated),
                'resources': public_resources(d, latest),
                'message': message}


@app.patch('/api/papers/{paper_id}/resources/{resource_id}')
def patch_paper_resource(paper_id: str, resource_id: str, body: ResourceUpdate):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        records = load_resources(d)
        index = next((i for i, r in enumerate(records) if r['id'] == resource_id), None)
        if index is None:
            raise HTTPException(404, '资源记录不存在')
        old = records[index]
        updates = body.model_dump(exclude_unset=True, exclude={'field_source_changes'})
        record = normalize_resource({**old, **updates})
        apply_field_sources(d, meta, record, body.field_source_changes)
        content_changed = any(record.get(k) != old.get(k) for k in ResourceIn.model_fields if k != 'status')
        if content_changed or body.field_source_changes or 'status' in updates:
            record.pop('auto_extraction', None)
        source_changed = any(record[k] != old[k] for k in ('document_id', 'page'))
        if source_changed or updates.get('status') == 'verified':
            validate_source(d, meta, record)
            record['source_revision'] = source_revision(meta, record)
        ensure_unique(records, record)
        record['updated_at'] = _now()
        records[index] = record
        atomic_json(d / 'resources.json', records)
        return public_resource(d, meta, record)


@app.get('/api/papers/{paper_id}/resources/{resource_id}/field-sources/{field}')
def locate_resource_field(paper_id: str, resource_id: str, field: SampleField):
    with storage_lock:
        directory = paper_dir(paper_id)
        meta = read_meta(directory)
        record = next((r for r in load_resources(directory) if r['id'] == resource_id), None)
        if not record or field not in record.get('field_sources', {}):
            raise HTTPException(404, '样品字段出处不存在')
        source = public_field_source(directory, meta, record['field_sources'][field], record[field], verify_bytes=True)
        if source['state'] != 'located':
            raise HTTPException(409, '字段值或原文已变化，或指定页未找到摘录，请重新核对')
        return source


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


def _resource_entries(paper_id: str = '', library_id: str = 'all'):
    if library_id != 'all':
        members = project_paper_ids(library_id)
        if paper_id and paper_id not in members:
            raise HTTPException(404, '论文不在当前课题库。')
        directories = [paper_dir(paper_id)] if paper_id else [PAPERS_DIR / pid for pid in sorted(members)]
    else:
        directories = [paper_dir(paper_id)] if paper_id else (sorted(PAPERS_DIR.iterdir()) if PAPERS_DIR.is_dir() else [])
    entries = []
    for directory in directories:
        if (directory / 'meta.json').is_file():
            if _future_schema(directory):
                logging.getLogger(__name__).warning('文献 %s 由更新版本的 BioReader 写入，未参与资源扫描。', directory.name)
                continue
            meta = read_meta(directory)
            entries.extend({**record, 'paper_id': meta['id'], 'paper_title': meta['title'], 'paper_doi': meta.get('doi', '')}
                           for record in public_resources(directory, meta))
    return related_resources(entries)


@app.get('/api/resources')
def get_library_resources(q: str = Query(default='', max_length=500), library_id: str = 'all',
                          organism: str = Query(default='', max_length=1000),
                          tissue: str = Query(default='', max_length=1000),
                          disease: str = Query(default='', max_length=1000),
                          modality: str = Query(default='', max_length=1000),
                          platform: str = Query(default='', max_length=1000)):
    with storage_lock:
        return filter_resources(_resource_entries(library_id=library_id), q, organism, tissue, disease, modality, platform)


@app.get('/api/resources/export')
def export_library_resources(format: Literal['csv', 'accessions'], paper_id: str = '', library_id: str = 'all',
                             q: str = Query(default='', max_length=500),
                             organism: str = Query(default='', max_length=1000),
                             tissue: str = Query(default='', max_length=1000),
                             disease: str = Query(default='', max_length=1000),
                             modality: str = Query(default='', max_length=1000),
                             platform: str = Query(default='', max_length=1000)):
    with storage_lock:
        entries = filter_resources(_resource_entries(paper_id, library_id), q, organism, tissue, disease, modality, platform)
    content = resource_csv(entries) if format == 'csv' else accession_text(entries)
    filename = 'bioreader-resources.csv' if format == 'csv' else 'bioreader-accessions.txt'
    return Response(content=content, media_type='text/csv; charset=utf-8' if format == 'csv' else 'text/plain; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'no-store'})


# ---------- optional public GEO metadata ----------

def _paper_geo_accessions(directory):
    return {accession for record in load_resources(directory) for accession in resource_accessions(record)
            if accession.startswith('GSE')}


@app.get('/api/papers/{paper_id}/resources/geo')
def get_paper_geo_metadata(paper_id: str):
    with storage_lock:
        directory = paper_dir(paper_id)
        accessions = _paper_geo_accessions(directory)
        return {'records': {key: value for key, value in load_geo_cache(directory).items() if key in accessions}}


@app.post('/api/papers/{paper_id}/resources/geo/{accession}')
async def supplement_geo_metadata(paper_id: str, accession: str, refresh: bool = False):
    try:
        accession = validate_accession(accession)
    except GeoError as exc:
        raise HTTPException(422, str(exc)) from exc
    with storage_lock:
        directory = paper_dir(paper_id)
        if accession not in _paper_geo_accessions(directory):
            raise HTTPException(404, '本篇文献的资源记录中没有该 GEO 编号')
        cached = load_geo_cache(directory).get(accession)
        if cached and not refresh and cache_fresh(cached):
            return {'metadata': cached, 'cached': True}
    try:
        metadata = await fetch_geo_metadata(accession)
    except GeoError as exc:
        raise HTTPException(502, str(exc)) from exc
    with storage_lock:
        directory = paper_dir(paper_id)
        if accession not in _paper_geo_accessions(directory):
            raise HTTPException(409, '资源编号已修改，请重新打开资源表')
        records = load_geo_cache(directory)
        previous = records.get(accession)
        # A slower request must not overwrite a fresh result committed by a
        # concurrent refresh while this request was awaiting the public server.
        if previous != cached and previous and cache_fresh(previous):
            return {'metadata': previous, 'cached': True}
        # A partial refresh must not discard more complete cached sample details.
        if previous and metadata.get('warnings') and len(previous.get('samples', [])) > len(metadata.get('samples', [])):
            return {'metadata': {**previous, 'warnings': metadata['warnings']}, 'cached': True}
        records[accession] = metadata
        save_geo_cache(directory, records)
    return {'metadata': metadata, 'cached': False}


# ---------- settings ----------

@app.get('/api/ui-preferences')
def get_ui_preferences():
    return ui.read_preferences(DATA_DIR)


@app.put('/api/ui-preferences')
def put_ui_preferences(body: ui.PreferencesIn):
    return ui.write_preferences(DATA_DIR, body)


@app.patch('/api/ui-preferences')
def patch_ui_preferences(body: ui.PreferencesPatch):
    return ui.write_preferences(DATA_DIR, body)


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


# ---------- explicit Zotero MCP import ----------

@app.get('/api/zotero/settings')
def get_zotero_settings():
    with storage_lock:
        return zi.public_config(zi.read_config(DATA_DIR))


@app.put('/api/zotero/settings')
def put_zotero_settings(body: zi.ZoteroConfigIn):
    with storage_lock:
        config = zi.merge_config(zi.read_config(DATA_DIR), body)
        zi.write_config(DATA_DIR, config)
        return zi.public_config(config)


@app.post('/api/zotero/check')
async def check_zotero(body: zi.ZoteroConfigIn):
    with storage_lock:
        config = zi.merge_config(zi.read_config(DATA_DIR), body)
    try:
        async with ZoteroMCPClient(config['endpoint'], config['token']) as client:
            info = await client.discover()
        supported = all((info.get('capabilities') or {}).get(key) for key in ('search', 'metadata', 'attachments'))
        return {key: info.get(key) for key in ('server_name', 'protocol_version', 'capabilities')} | {
            'message': ('已连接 Zotero MCP；PDF 导入使用配置目录中的本机附件副本。' if supported else
                        'MCP 接口已连通，但缺少所需的 Zotero 检索或条目工具，请使用兼容的 Zotero MCP 服务。')}
    except MCPError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


@app.post('/api/zotero/search')
async def search_zotero(body: zi.ZoteroSearchIn):
    if not body.query.strip():
        raise HTTPException(422, '请输入标题、作者或关键词')
    with storage_lock:
        config = zi.read_config(DATA_DIR)
    try:
        async with ZoteroMCPClient(config['endpoint'], config['token']) as client:
            result = await client.search(body.query.strip(), limit=body.limit, start=body.start)
    except MCPError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    with storage_lock:
        zi.same_config(DATA_DIR, config)
    return {'items': result['items'], 'has_more': result.get('has_more', False),
            'configuration_id': config['configuration_id']}


@app.get('/api/zotero/items/{item_key}')
async def preview_zotero_item(item_key: str, configuration_id: str):
    if not zi.KEY.fullmatch(item_key):
        raise HTTPException(422, 'Zotero 条目编号格式不正确')
    with storage_lock:
        config = zi.read_config(DATA_DIR)
        if configuration_id != config['configuration_id']:
            raise HTTPException(409, 'Zotero 连接设置已更改，请重新搜索')
    try:
        async with ZoteroMCPClient(config['endpoint'], config['token']) as client:
            item = await client.get_item(item_key)
            if item.get('item_type') in ('attachment', 'note', 'annotation'):
                raise HTTPException(422, '请选择论文条目，而不是附件或笔记')
            attachments = await client.get_attachments(item_key, parent=item)
    except MCPError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    with storage_lock:
        zi.same_config(DATA_DIR, config)
        pid = zi.preview(DATA_DIR, config, item, attachments)
    return {'item': item, 'attachments': [zi.attachment_view(config, item, a) for a in attachments],
            'preview_id': pid, 'configuration_id': config['configuration_id']}


def _import_zotero_preview(directory, papers_directory, config, record, attachment_key):
    """Parse in an unpublished directory; a short final transaction prevents duplicates."""
    item = record['item']
    with storage_lock:
        zi.same_config(directory, config)
        found = zi.duplicate(list_meta() + list_trash(), config, item)
        if found:
            if not (PAPERS_DIR / found['id']).is_dir():
                raise HTTPException(409, '回收站中已有该条目；请先恢复或彻底删除后再导入')
            return {'paper': read_meta(paper_dir(found['id'])), 'existing': True,
                    'message': '文献库已包含该条目，已保留原有内容；需要补充 PDF 时可在论文页附加文件。'}
    content, filename = None, ''
    if attachment_key:
        attachment = next((a for a in record['attachments'] if a['key'] == attachment_key), None)
        if attachment is None:
            raise HTTPException(422, '所选 PDF 不属于这次文献预览')
        content, filename = zi.read_managed_pdf(config, item, attachment)
    try:
        Path(directory).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.zotero-import-', dir=directory) as temporary:
            paper_id = uuid.uuid4().hex
            stage = Path(temporary) / paper_id
            stage.mkdir()
            meta = blank_meta(paper_id)
            for name in ('title', 'authors', 'year', 'doi', 'journal', 'abstract', 'url'):
                meta[name] = str(item.get(name) or '')
            meta['title'] = meta['title'] or 'Zotero ' + item['key']
            meta['tags'] = item.get('tags', [])[:30]
            meta['source'] = 'zotero'
            meta['zotero_source'] = zi.source_identity(config, item, attachment_key)
            if content is not None:
                _validate_pdf_upload(filename, content)
                try:
                    meta = ingest_pdf(stage, content, filename, meta)
                except OSError:
                    raise
                except Exception as exc:
                    if isinstance(exc, HTTPException):
                        raise
                    raise HTTPException(422, '所选 PDF 无法解析，请检查是否损坏、加密或超过 600 页；没有创建文献') from exc
            else:
                (stage / 'text.txt').write_text(meta['abstract'], 'utf-8')
            write_meta(stage, meta)
            with storage_lock:
                zi.same_config(directory, config)
                found = zi.duplicate(list_meta() + list_trash(), config, {**item, 'doi': meta.get('doi') or item.get('doi')})
                if found:
                    if not (PAPERS_DIR / found['id']).is_dir():
                        raise HTTPException(409, '回收站中已有该条目；请先恢复或彻底删除后再导入')
                    return {'paper': read_meta(paper_dir(found['id'])), 'existing': True,
                            'message': '该文献已导入，已保留已有正文和笔记。'}
                Path(papers_directory).mkdir(parents=True, exist_ok=True)
                stage.rename(Path(papers_directory) / paper_id)
            return {'paper': meta, 'existing': False,
                    'message': '已导入文献及 PDF，可以开始阅读。' if content is not None else '已导入文献信息；尚未附加 PDF。'}
    except OSError as exc:
        raise HTTPException(503, 'Zotero 导入保存失败，请检查磁盘后重试；已有文献未修改') from exc


@app.post('/api/zotero/import')
async def import_zotero_item(body: zi.ZoteroImportIn):
    with storage_lock:
        config = zi.read_config(DATA_DIR)
        record = zi.get_preview(DATA_DIR, config, body.preview_id)
    return await asyncio.to_thread(_import_zotero_preview, DATA_DIR, PAPERS_DIR, config, record, body.attachment_key)


# ---------- papers ----------

@app.post('/api/zotero-local/preview')
def open_local_zotero(body: zl.OpenIn):
    with storage_lock:
        check_search_scope(body.library_id)
    token = zl.open_session(DATA_DIR, body)
    try:
        return local_zotero_page(token, 0)
    except BaseException:
        zl.close_session(DATA_DIR, token)
        raise


@app.get('/api/zotero-local/{token}')
def local_zotero_page(token: str, offset: int = Query(default=0, ge=0)):
    items, more, scope = zl.read(DATA_DIR, token, offset)
    with storage_lock:
        check_search_scope(scope)
        papers, trash = list_meta(), list_trash()
    for item in items:
        existing = zl.duplicate(item, papers)
        item['existing_id'] = existing['id'] if existing else None
        item['has_main'] = bool(existing and (existing.get('has_file') or existing.get('document_revisions', {}).get('main')))
        item['trashed'] = bool(not existing and zl.duplicate(item, trash))
    return {'preview': token, 'library_id': scope, 'offset': offset, 'has_more': more, 'items': items}


@app.delete('/api/zotero-local/{token}')
def close_local_zotero(token: str):
    zl.close_session(DATA_DIR, token)
    return {'closed': True}


def stage_zotero_supplement(stage, content, name, doc, digest, meta):
    parsed, figures = store_document(stage, content, name, doc)
    meta.setdefault('supplements', []).append({'id': doc, 'filename': name, 'pages': len(parsed['pages']),
                                             'sha256': digest, 'warnings': parsed.get('warnings', [])})
    meta['figures_count'] = len(figures)
    materials_changed(meta, doc, digest)


@app.post('/api/zotero-local/{token}/import')
def import_local_zotero(token: str, body: zl.ImportIn):
    item, scope, storage, stamps = zl.read(DATA_DIR, token, item_id=body.item_id)
    with storage_lock:
        check_search_scope(scope)
        existing = zl.duplicate(item, list_meta())
        if not existing and zl.duplicate(item, list_trash()):
            raise HTTPException(409, '回收站中已有该条目，请先恢复。')
        pid = existing['id'] if existing else uuid.uuid4().hex
        target = PAPERS_DIR / pid
        before = (target / 'meta.json').read_bytes() if existing else None
        meta = read_meta(target) if existing else blank_meta(pid)
        figures = load_figures(target) if existing and body.attachments else []
        if existing and _future_schema(target):
            raise HTTPException(409, '目标文献数据需要更新版本的 BioReader。')
        if not existing:
            for k in ('title', 'authors', 'year', 'doi', 'journal', 'abstract', 'url'):
                meta[k] = str(item.get(k) or '')
            meta['source'] = 'zotero'
        existing_hashes = {}
        if existing and body.attachments:
            for doc_id in ['main'] + [f['id'] for f in meta.get('supplements', [])]:
                path = document_path(target, doc_id)
                if path.is_file():
                    value = meta.get('document_revisions', {}).get(doc_id)
                    if not value or value == 'legacy':
                        value = file_revision(path)
                    existing_hashes[value] = doc_id
            if (target / 'paper.pdf').is_file() and not meta['has_file']:
                raise HTTPException(409, '已有正文文件与题录不一致，请先核对。')
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='.zotero-local-', dir=DATA_DIR) as staging:
            stage = Path(staging) / pid
            stage.mkdir()
            if figures:
                save_figures(stage, figures)
            if not existing:
                (stage / 'text.txt').write_text(meta['abstract'], 'utf-8')
            zl.prepare(stage, item, body, storage, stamps, meta, ingest_pdf, stage_zotero_supplement, existing_hashes)
            write_meta(stage, meta)
            with storage_lock:
                check_search_scope(scope)
                live = zl.duplicate(item, list_meta())
                if (live and live['id']) != (existing and existing['id']) or (not live and zl.duplicate(item, list_trash())):
                    raise HTTPException(409, '目标文献已变化，请重新预览。')
                if existing:
                    if (target / 'meta.json').read_bytes() != before or (body.attachments and load_figures(target) != figures):
                        raise HTTPException(409, '导入期间论文已修改，请重试。')
                    oa.publish(stage, target)
                else:
                    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
                    stage.rename(target)
                paper = summary_of(read_meta(target))
                if scope != 'all':
                    try:
                        pl.add_paper(DATA_DIR, scope, pid)
                    except (OSError, HTTPException):
                        return {'paper': paper, 'existing': bool(existing), 'in_library': False}
                return {'paper': paper, 'existing': bool(existing), 'in_library': True}
    except (OSError, ValueError, PyPdfError) as exc:
        raise HTTPException(422, '无法读取或写入所选材料，请检查所选 PDF 和磁盘后重试。') from exc


def project_paper_ids(library_id):
    if library_id == 'all':
        candidates = (d.name for d in PAPERS_DIR.iterdir()) if PAPERS_DIR.is_dir() else ()
    else:
        candidates = pl.active_project(DATA_DIR, library_id).paper_ids
    return {pid for pid in candidates if re.fullmatch(r'[0-9a-f]{32}', pid)
            and (PAPERS_DIR / pid / 'meta.json').is_file() and not _future_schema(PAPERS_DIR / pid)}


@app.get('/api/library/{library_id}/digest')
def get_library_digest(library_id: str):
    with storage_lock:
        return library_digest.build_digest(DATA_DIR, PAPERS_DIR, library_id)


@app.get('/api/library/{library_id}/digest/figures/{paper_id}/{fid}/{view}')
def get_digest_figure(library_id: str, paper_id: str, fid: str, view: Literal['image', 'source'], key: str):
    with storage_lock:
        if library_id != 'all' and paper_id not in pl.active_project(DATA_DIR, library_id).paper_ids:
            raise HTTPException(404, '插图不在当前课题库')
        item = library_digest.current_figure(PAPERS_DIR, paper_id, fid, key, library_id)
        if view == 'source':
            return item
        if not item['image_url']:
            raise HTTPException(404, '插图文件不存在')
        image = (PAPERS_DIR / paper_id / 'figures' / (fid + '.png')).read_bytes()
        return Response(image, media_type='image/png')


@app.get('/api/library/{library_id}/digest/export')
def export_library_digest(library_id: str):
    name, stream = digest_export.build_export(DATA_DIR, PAPERS_DIR, library_id, FRONTEND_DIR / 'digest-export.html')
    safe_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', name)[:80] or 'BioReader'
    headers = {'Content-Disposition': "attachment; filename*=UTF-8''" + url_quote(safe_name + '.html', safe=''),
               'Cache-Control': 'no-store'}
    return StreamingResponse(iter(lambda: stream.read(256 * 1024), b''), media_type='text/html', headers=headers,
                             background=BackgroundTask(stream.close))


def last_tag_batch(library_id):
    path = bulk_tags.receipt_path(DATA_DIR, library_id)
    record = bulk_tags.receipt(path)
    members = None
    if record and record['phase'] == 'adding' and library_id != 'all':
        try:
            project = pl.read(DATA_DIR, library_id)
            members = set(project.paper_ids) if not project.archived else set()
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            members = set()
    bulk_tags.finish(path, record, PAPERS_DIR, TRASH_DIR, read_meta, write_meta, members)
    return path, record


@app.get('/api/library/{library_id}/tag-batch')
def get_tag_batch(library_id: str):
    with storage_lock:
        _, record = last_tag_batch(library_id)
        return bulk_tags.public(record)


@app.post('/api/library/{library_id}/tag-batch')
def add_batch_tags(library_id: str, body: bulk_tags.AddTags):
    with storage_lock:
        path, previous = last_tag_batch(library_id)
        record = bulk_tags.apply(path, previous, body, project_paper_ids(library_id), PAPERS_DIR, read_meta)
        bulk_tags.finish(path, record, PAPERS_DIR, TRASH_DIR, read_meta, write_meta)
        changed = len(record['targets'])
        return {'added': changed, 'existing': record['existing'], 'skipped': record['total'] - record['existing'] - changed,
                'receipt': bulk_tags.public(record if changed else previous)}


@app.post('/api/library/{library_id}/tag-batch/{request_id}/undo')
def undo_batch_tags(library_id: str, request_id: str):
    with storage_lock:
        path, record = last_tag_batch(library_id)
        if not record or record['id'] != request_id:
            raise HTTPException(409, '批量标签记录已变更，请重新打开操作')
        if record['phase'] == 'added':
            record['phase'] = 'removing'
            atomic_json(path, record)
            bulk_tags.finish(path, record, PAPERS_DIR, TRASH_DIR, read_meta, write_meta)
        return bulk_tags.public(record)


def check_search_scope(library_id):
    if library_id != 'all':
        pl.active_project(DATA_DIR, library_id)


@app.get('/api/pubmed-searches/{library_id}')
def list_saved_pubmed(library_id: str):
    with storage_lock:
        check_search_scope(library_id)
        return [sp.view(sp.read(path)) for path in sorted(sp.directory(DATA_DIR, library_id).glob('*.json'))]


@app.post('/api/pubmed-searches/{library_id}')
def save_pubmed_query(library_id: str, body: sp.SearchIn):
    with storage_lock:
        check_search_scope(library_id)
        return sp.create(DATA_DIR, library_id, body.query)


@app.get('/api/pubmed-followups')
def due_pubmed_searches():
    with storage_lock:
        stamp = sp.now()
        scopes = ['all'] + [item.id for item in pl.projects(DATA_DIR)['libraries'] if not item.archived]
        due = []
        for scope in scopes:
            for path in sorted(sp.directory(DATA_DIR, scope).glob('*.json')):
                try:
                    if path not in sp.in_flight and sp.due(sp.read(path), stamp):
                        due.append({'scope': scope, 'id': path.stem})
                except HTTPException:
                    # Keep independent project searches usable if one file is damaged.
                    continue
        return due


@app.get('/api/pubmed-searches/{library_id}/{key}')
def pubmed_result_page(library_id: str, key: str, page: int = Query(default=1, ge=1)):
    with storage_lock:
        check_search_scope(library_id)
        path = sp.path_for(DATA_DIR, library_id, key)
        return sp.view(sp.read(path), page)


@app.post('/api/pubmed-searches/{library_id}/{key}/tracking')
def set_pubmed_tracking(library_id: str, key: str, body: sp.TrackingIn):
    with storage_lock:
        check_search_scope(library_id)
        path = sp.path_for(DATA_DIR, library_id, key)
        record = sp.read(path)
        record['tracking'] = body.enabled
        sp.write(path, record)
        return sp.view(record)


@app.post('/api/pubmed-searches/{library_id}/{key}/refresh')
async def refresh_pubmed_query(library_id: str, key: str, due_only: bool = False):
    with storage_lock:
        check_search_scope(library_id)
        path = sp.path_for(DATA_DIR, library_id, key)
        record, stamp = sp.read(path), sp.now()
        if path in sp.in_flight:
            return {**sp.view(record), 'refreshing': True}
        if due_only and not sp.due(record, stamp):
            return sp.view(record)
        start, end = sp.search_dates(record, stamp)
        sp.in_flight.add(path)
    try:
        total, pmids = await sp.search(record['query'], start, end)
        with storage_lock:
            check_search_scope(library_id)
            return sp.record_result(path, total, pmids, stamp)
    except HTTPException as exc:
        with storage_lock:
            # Archiving a project while a request runs stops the write.
            check_search_scope(library_id)
            latest = sp.read(path)
            latest.update(last_attempt=stamp.isoformat(), last_error=str(exc.detail))
            sp.write(path, latest)
        raise
    finally:
        with storage_lock:
            sp.in_flight.discard(path)


@app.post('/api/pubmed-searches/{library_id}/{key}/seen')
def mark_pubmed_seen(library_id: str, key: str, body: sp.SeenIn):
    with storage_lock:
        check_search_scope(library_id)
        return sp.mark_seen(sp.path_for(DATA_DIR, library_id, key), body.result_id, body.page)


@app.get('/api/project-libraries')
def get_projects():
    with storage_lock:
        return pl.projects(DATA_DIR)


@app.post('/api/project-libraries')
def create_project_library(body: pl.ProjectCreate, request_id: Optional[str] = Query(default=None, pattern=r'^[0-9a-f]{32}$')):
    with storage_lock:
        for pid in set(body.paper_ids):
            paper_dir(pid)
        return pl.create(DATA_DIR, body, request_id)


@app.put('/api/project-libraries/{library_id}')
def update_project_library(library_id: str, body: pl.ProjectEdit):
    with storage_lock:
        record = pl.read(DATA_DIR, library_id)
        for pid in set(body.add):
            paper_dir(pid)
        return pl.edit(DATA_DIR, record, body)


@app.put('/api/project-libraries/{library_id}/papers/{paper_id}')
def join_project_library(library_id: str, paper_id: str):
    with storage_lock:
        paper_dir(paper_id)
        return pl.add_paper(DATA_DIR, library_id, paper_id)


@app.get('/api/library/search')
def search_saved_library(q: str = Query(min_length=2, max_length=200),
                         scope: Literal['all', 'pdf', 'notes'] = 'all', library_id: str = 'all', papers_only: bool = False,
                         offset: int = Query(default=0, ge=0, le=100000),
                         limit: int = Query(default=20, ge=1, le=20)):
    entries, warnings = [], []
    with storage_lock:
        members = None if library_id == 'all' else set(pl.active_project(DATA_DIR, library_id).paper_ids)
        for directory in sorted(PAPERS_DIR.iterdir()) if PAPERS_DIR.is_dir() else []:
            if members is not None and directory.name not in members:
                continue
            if not re.fullmatch(r'[0-9a-f]{32}', directory.name) or not (directory / 'meta.json').is_file():
                continue
            if _future_schema(directory):
                warnings.append({'paper_id': directory.name, 'paper_title': '版本较新的文献',
                                 'document_id': '', 'message': '该文献由更新版本的 BioReader 写入，未参与搜索。'})
                continue
            try:
                meta = read_meta(directory)
                if meta.get('id') != directory.name or not isinstance(meta.get('notes', ''), str):
                    raise ValueError('invalid paper identity or notes')
                supplements = meta.get('supplements', [])
                if not isinstance(supplements, list) or any(not isinstance(item, dict) or
                        not re.fullmatch(r'[0-9a-f]{32}', str(item.get('id', ''))) for item in supplements):
                    raise ValueError('invalid supplement metadata')
                entries.append((directory, meta))
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                warnings.append({'paper_id': directory.name, 'paper_title': '无法读取的文献',
                                 'document_id': '', 'message': '本地文献信息无法读取，该条记录未参与搜索。'})
    result = search_library(entries, q, scope, offset, limit, papers_only=papers_only)
    result['warnings'] = warnings + result.get('warnings', [])
    result['warning_count'] = len(warnings) + result.get('warning_count', 0)
    return result


class SearchSourceIn(BaseModel):
    document_id: str = Field(pattern=r'^(?:main|[0-9a-f]{32})$')
    page: int = Field(ge=1, le=600, strict=True)
    source_revision: str = Field(pattern=r'^[0-9a-f]{64}$')
    block_id: str = Field(pattern=r'^p[1-9][0-9]{0,2}-b[1-9][0-9]*$', max_length=40)
    quote: str = Field(min_length=1, max_length=2400)


@app.post('/api/papers/{paper_id}/search/source')
def get_search_source(paper_id: str, body: SearchSourceIn):
    source = body.model_dump()
    with storage_lock:
        directory = paper_dir(paper_id)
        meta = read_meta(directory)
    result = verified_source(directory, meta, source)
    with storage_lock:
        validate_source_snapshot(directory, read_meta(directory), [source])
    return {'source': {**result, 'paper_id': paper_id}}

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
    with storage_lock:
        for existing in list_meta():
            if (existing.get("doi") or "").lower() == doi:
                raise HTTPException(409, f"文献库里已有该 DOI：{existing['title']}")
        for existing in list_trash():
            if (existing.get("doi") or "").lower() == doi:
                raise HTTPException(409, f"回收站中已有该 DOI：{existing['title']}；请先恢复或彻底删除后再导入")
        return create_identifier_paper(record)


def remember_import_url(meta, record):
    link = record.get('_link') or {}
    if not link.get('article_url') or meta.get('import_url'):
        return False
    meta['import_url'] = record.get('import_url') or link['article_url']
    meta['article_url'] = link['article_url']
    if link.get('sha256'):
        meta['import_pdf_sha256'] = link['sha256']
    return True


def create_identifier_paper(record):
    paper_id, directory = new_paper_dir()
    try:
        meta = blank_meta(paper_id)
        for field in ('doi', 'pmid', 'pmcid', 'title', 'authors', 'year', 'journal', 'abstract', 'url'):
            if record.get(field):
                meta[field] = str(record[field])
        from_link = remember_import_url(meta, record)
        meta['source'] = 'url' if from_link else ('doi' if meta.get('doi') else 'pmid')
        meta['title'] = record.get('title') or record.get('doi') or str(record['pmid'])
        (directory / 'text.txt').write_text(record.get('abstract') or '', 'utf-8')
        write_meta(directory, meta)
        return meta
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


@app.post('/api/import/identifiers/preview')
async def preview_identifiers(body: ids.PreviewIn):
    with storage_lock:
        project = pl.active_project(DATA_DIR, body.library_id) if body.library_id != 'all' else None
        index = ids.library_index(list_meta(), list_trash())
        members = set(project.paper_ids) if project else set()
    rows = await ids.preview(body, index, members, DATA_DIR)
    return ids.remember(DATA_DIR, body.library_id, rows)


def apply_identifier_records(library_id, selection):
    results = []
    with storage_lock:
        project = pl.active_project(DATA_DIR, library_id) if library_id != 'all' else None
        index = ids.library_index(list_meta(), list_trash())
        for row in selection:
            status, paper = ids.local_match(index, row['record'])
            item = {'key': row['key'], 'status': status, 'paper_id': paper.get('id', '')}
            results.append(item)
            if status not in ('new', 'existing'):
                continue
            target = row.get('_applied_paper_id') or row.get('paper_id')
            if ((target and target != paper.get('id')) or
                    (row.get('_joined_project') and project and target not in project.paper_ids)):
                item.update(status='changed', reason='原条目已变更，请重新预览')
                continue
            try:
                if status == 'new':
                    paper = create_identifier_paper(row['record'])
                    item.update(status='added', paper_id=paper['id'])
                    for alias in ids.aliases(paper):
                        index[alias] = ('existing', paper)
                # A retry must keep the originally chosen record, even after permanent deletion.
                row['_applied_paper_id'] = paper['id']
                if library_id != 'all':
                    project = pl.add_paper(DATA_DIR, library_id, paper['id'])
                    row['_joined_project'] = True
                if status == 'existing' and row['record'].get('_link') and not paper.get('import_url'):
                    directory = paper_dir(paper['id'])
                    saved = read_meta(directory)
                    if remember_import_url(saved, row['record']):
                        write_meta(directory, saved)
                        for alias in ids.aliases(saved):
                            index[alias] = ('existing', saved)
            except (HTTPException, OSError) as exc:
                reason = str(exc.detail) if isinstance(exc, HTTPException) else '写入本地文献库失败'
                item.update(status='join_failed' if item['paper_id'] else 'failed', reason=reason)
    return results


@app.post('/api/import/identifiers/apply')
async def apply_identifiers(body: ids.ApplyIn):
    library_id, selection = ids.selection(DATA_DIR, body)
    results = await asyncio.to_thread(apply_identifier_records, library_id, selection)
    for row, item in zip(selection, results):
        link = row['record'].get('_link')
        if not link or item['status'] not in ('added', 'existing'):
            continue
        try:
            result = await obtain_paper_fulltext(item['paper_id'], library_id, link)
            item['fulltext'] = {k: result[k] for k in ('outcome', 'message', 'source') if k in result}
        except HTTPException as exc:
            item['fulltext'] = {'outcome': 'failed', 'message': str(exc.detail)}
    return {'rows': results, 'library_id': library_id}


@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: str):
    d = paper_dir(paper_id)
    meta = read_meta(d)
    return {**meta, "reading_position": read_positions(d, meta)}


@app.get('/api/papers/{paper_id}/chat')
def get_paper_chat(paper_id: str, limit: int = Query(default=100, ge=1, le=100), before: str = ''):
    with storage_lock:
        d = paper_dir(paper_id)
        messages = load_history(d) if str(d) in active_requests else recover_history(d)
        return history_page(messages, limit, before)


@app.get('/api/papers/{paper_id}/chat/sources/{message_id}/{source_id}')
def get_chat_source(paper_id: str, message_id: str, source_id: str):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        message = next((item for item in load_history(d) if item['id'] == message_id and item['role'] == 'assistant'), None)
        source = next((item for item in message['sources'] if item['id'] == source_id), None) if message else None
        if source is None:
            raise HTTPException(404, '这条回答的引用不存在')
    # Read the original attachment outside the library lock.
    result = verified_source(d, meta, source)
    with storage_lock:
        validate_source_snapshot(d, read_meta(d), [source])
    return {'source': {**result, 'paper_id': paper_id}}


@app.post('/api/papers/{paper_id}/chat/{message_id}/table-candidates/{choice}')
def choose_chat_table_cell(paper_id: str, message_id: str, choice: int):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        _, source = table_choice(load_history(d), message_id, choice)
    verified = verified_source(d, meta, source)
    with storage_lock:
        validate_source_snapshot(d, read_meta(d), [source])
        messages = load_history(d)
        message, current = table_choice(messages, message_id, choice)
        if current != source:
            raise HTTPException(409, '候选记录已变化，请刷新对话')
        if 'table_candidates' in message:
            message.pop('table_candidates')
            message.update(selected_table_candidate=choice, sources=[source], cited_source_ids=['S1'],
                           content=supplement_tables.answer_for_cell(source), updated_at=chat_now())
            save_history(d, messages)
    return {'message': message, 'source': {**verified, 'paper_id': paper_id}}


class _ChatStreamingResponse(StreamingResponse):
    """Finalize even if the client disconnects before the generator's first yield."""
    def __init__(self, content, finalize):
        super().__init__(content, media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
        self.finalize_chat = finalize

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.finalize_chat()


@app.post('/api/papers/{paper_id}/chat')
async def ask_paper_chat(paper_id: str, body: ChatIn):
    if not body.message.strip():
        raise HTTPException(422, '请输入问题')
    token = uuid.uuid4().hex
    with storage_lock:
        d = paper_dir(paper_id)
        key = str(d)
        if key in active_requests:
            raise HTTPException(409, '这篇文献已有回答正在生成，请等待完成或先停止')
        settings = load_settings()
        meta = read_meta(d)
        recover_history(d)
        active_requests[key] = token
    try:
        table_result = None
        candidates = []
        if body.action == 'ask' and not body.selection:
            table_result = await asyncio.to_thread(supplement_tables.lookup, d, meta, body.message, candidates=candidates)
        if table_result is None:
            if not settings.get('api_key'):
                raise HTTPException(400, '请先在设置中填写 API Key')
            sources = await asyncio.to_thread(prepare_sources, d, meta, body)
        else:
            table_answer, sources = table_result
        with storage_lock:
            latest = read_meta(d)
            if table_result is not None and latest.get('supplement_files', []) != meta.get('supplement_files', []):
                raise HTTPException(409, '查询期间补充附件已变化，请重新提问')
            validate_source_snapshot(d, latest, sources + candidates)
            history = load_history(d)
            user, assistant = new_messages(body, sources, table_lookup=table_result is not None)
            if candidates:
                assistant['table_candidates'] = candidates
            if table_result is None:
                messages = chat_messages(latest, history, body, sources)
            # UTF-8 plus JSON escaping can use six bytes per output character.
            save_history(d, history + [user, assistant], reserve=MAX_OUTPUT * 6 + 4096)
    except BaseException:
        with storage_lock:
            if active_requests.get(key) == token:
                active_requests.pop(key, None)
        raise

    state = {'content': '', 'final': False}

    def persist(status, error=''):
        with storage_lock:
            saved = update_message(d, assistant['id'], state['content'], status, error)
            if status != 'pending':
                state['final'] = True
            return saved

    def finalize():
        with storage_lock:
            try:
                if not state['final']:
                    persist('interrupted', '回答已中断，已保留收到的内容。')
            except HTTPException:
                # A concurrently damaged/deleted sidecar must never be replaced from memory.
                pass
            finally:
                if active_requests.get(key) == token:
                    active_requests.pop(key, None)

    async def reply_chunks():
        if table_result is not None:
            yield table_answer
        else:
            async for piece in stream_chat(settings['base_url'], settings['api_key'], settings['model'], messages,
                                           max_tokens=4096, timeout=CHAT_TIMEOUT, require_done=True):
                yield piece

    async def events():
        try:
            yield _sse({'type': 'start', 'message': user, 'assistant': assistant})
            last_save = time.monotonic()
            saved_size = 0
            async with asyncio.timeout(CHAT_TIMEOUT):
                async for piece in reply_chunks():
                    if not isinstance(piece, str) or not piece:
                        continue
                    accepted = piece[:max(0, MAX_OUTPUT - len(state['content']))]
                    state['content'] += accepted
                    if accepted:
                        yield _sse({'type': 'chunk', 'message_id': assistant['id'], 'text': accepted})
                    if len(accepted) < len(piece):
                        raise UpstreamError('回答达到长度上限，已保留收到的内容。')
                    if len(state['content']) - saved_size >= 1000 or time.monotonic() - last_save >= 1:
                        persist('pending')
                        saved_size, last_save = len(state['content']), time.monotonic()
            if not state['content'].strip():
                raise UpstreamError('模型未返回可用回答，请重试。')
            completed = persist('completed')
            yield _sse({'type': 'done', 'message': completed})
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                error = '回答等待超时，已保留收到的内容，请稍后重试。'
            elif isinstance(exc, UpstreamError):
                # HTTP provider bodies may echo submitted text or credentials.
                # Keep only the status here, never persist their raw message.
                error = (f'模型服务返回 HTTP {exc.status_code}，请检查服务配置或稍后重试。'
                         if exc.status_code is not None else str(exc))
            elif isinstance(exc, HTTPException):
                error = str(exc.detail)
            elif isinstance(exc, UnsafeURLError):
                error = '模型服务地址不可用，请检查设置。'
            else:
                error = '模型连接失败，已保留收到的内容，请检查服务设置后重试。'
            try:
                saved = persist('interrupted' if state['content'] else 'error', error)
            except HTTPException as storage_error:
                error = str(storage_error.detail)
                saved = {**assistant, 'content': state['content'], 'status': 'interrupted', 'error': error}
            yield _sse({'type': 'error', 'message': error, 'assistant': saved})
        finally:
            finalize()

    return _ChatStreamingResponse(events(), finalize)


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
            notes = (ns.partial_notes(d, meta, body.notes, body.notes_source_snapshot)
                     if body.notes_source_snapshot is not None else body.notes)
            if (notes != meta.get('notes', '') or body.notes_source_snapshot is not None and
                    body.notes_source_snapshot != meta.get('notes_source_snapshot')):
                meta['notes_revision'] += 1
            if body.notes_incomplete or not notes.strip():
                meta.pop('notes_figure_coverage', None)
            if body.notes_source_snapshot is not None:
                if meta.get('notes_figure_coverage', {}).get('generation') != body.notes_source_snapshot:
                    meta.pop('notes_figure_coverage', None)
                meta['notes_source_snapshot'] = body.notes_source_snapshot
            if not meta.get("notes", "").strip():
                meta["notes_material_revision"] = meta["material_revision"]
            if body.notes_source_revision is not None:
                if body.notes_source_revision > meta['material_revision']:
                    raise HTTPException(422, '笔记来源版本不能超过当前材料版本')
                meta['notes_material_revision'] = body.notes_source_revision
            meta["notes"] = notes
            if body.notes_language is not None:
                meta["notes_language"] = body.notes_language
            meta["notes_updated_at"] = _now()
        for field in ("reading_status", "reading_decision", "notes_incomplete"):
            value = getattr(body, field)
            if value is not None:
                meta[field] = value
        if body.notes_reviewed:
            meta["notes_material_revision"] = meta["material_revision"]
        if body.tags is not None:
            if body.tags_revision is not None and body.tags_revision != meta['tags_revision']:
                raise HTTPException(409, '标签已在另一项操作中更新，请刷新后重试')
            seen: list[str] = []
            for t in body.tags:
                t = t.strip()[:40]
                if t and t not in seen:
                    seen.append(t)
            if meta['tags'] != seen[:30]:
                meta['tags_revision'] += 1
                meta["tags"] = seen[:30]
                if 'batch_tag_owners' in meta:
                    remaining = {tag: owner for tag, owner in meta['batch_tag_owners'].items() if tag in meta['tags']}
                    if remaining:
                        meta['batch_tag_owners'] = remaining
                    else:
                        meta.pop('batch_tag_owners')
        for field in ("authors", "year", "journal"):
            value = getattr(body, field)
            if value is not None:
                meta[field] = value.strip()
        if body.doi is not None:
            meta["doi"] = body.doi.strip()
        write_meta(d, meta)
        return meta


@app.get('/api/papers/{paper_id}/note-sources')
def get_note_sources(paper_id: str):
    d = paper_dir(paper_id)
    meta = read_meta(d)
    result = ns.current_sources(d, meta)
    with storage_lock:
        if ns.note_state(read_meta(d)) != ns.note_state(meta):
            raise HTTPException(409, '笔记版本已变化，请刷新后重试')
    return result


@app.get('/api/papers/{paper_id}/note-sources/{snapshot_id}/{source_id}')
def resolve_note_source(paper_id: str, snapshot_id: str, source_id: str, notes_revision: int = Query(ge=0)):
    d = paper_dir(paper_id)
    with storage_lock:
        meta = read_meta(d)
        source = ns.lookup_current(d, meta, snapshot_id, source_id, notes_revision)
    resolved = verified_source(d, meta, source)
    with storage_lock:
        latest = read_meta(d)
        ns.lookup_current(d, latest, snapshot_id, source_id, notes_revision)
        validate_source_snapshot(d, latest, [source])
    return {'source': {**resolved, 'snapshot_id': snapshot_id, 'paper_id': paper_id},
            'paper_id': paper_id, 'notes_revision': notes_revision, 'snapshot_id': snapshot_id}


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
        d = paper_dir(paper_id)
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        target = TRASH_DIR / paper_id
        if target.exists():
            raise HTTPException(409, "回收站中已存在同名文献目录，请先恢复或彻底删除后再操作")
        try:
            meta = _raw_meta(d)
            meta["trashed_at"] = _now()
            atomic_json(d / "meta.json",
                        {k: v for k, v in meta.items() if k not in DERIVED_FIELDS + RESOURCE_DERIVED_FIELDS})
        except (OSError, ValueError):
            pass
        shutil.move(str(d), str(target))
        return {"ok": True, "trashed_id": target.name}


@app.get("/api/trash")
def get_trash():
    return list_trash()


@app.post("/api/trash/{paper_id}/restore")
def restore_paper(paper_id: str):
    with storage_lock:
        source = trash_paper_dir(paper_id)
        if _future_schema(source):
            raise HTTPException(409, "该文献由更新版本的 BioReader 写入，为避免误读已拒绝恢复；请升级应用后再试。")
        target = PAPERS_DIR / paper_id
        if target.exists():
            raise HTTPException(409, "文献库中已存在同名文献目录，无法恢复")
        PAPERS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        try:
            meta = _raw_meta(target)
            meta.pop("trashed_at", None)
            atomic_json(target / "meta.json",
                        {k: v for k, v in meta.items() if k not in DERIVED_FIELDS + RESOURCE_DERIVED_FIELDS})
        except (OSError, ValueError):
            pass
        return {"ok": True, "paper": read_meta(target)}


@app.delete("/api/trash/{paper_id}")
def purge_paper(paper_id: str):
    with storage_lock:
        shutil.rmtree(trash_paper_dir(paper_id))
        return {"ok": True}


def _fulltext_target(paper_id, library_id):
    d = paper_dir(paper_id)
    if library_id != 'all' and paper_id not in pl.active_project(DATA_DIR, library_id).paper_ids:
        raise HTTPException(409, '论文已移出当前课题库')
    return d, read_meta(d)


def _existing_fulltext(d, meta):
    if (d / 'paper.pdf').is_file():
        return {'outcome': 'already_present', 'message': '已有关联的正文 PDF。', 'paper': summary_of(meta)}
    if meta.get('has_file') or meta.get('document_revisions', {}).get('main'):
        return {'outcome': 'needs_review', 'message': '已有正文记录，但本地文件缺失，请核对后手动附加 PDF。'}


def _attach_download(paper_id, library_id, original, result):
    # Keep expensive parsing off the request loop and outside the storage lock.
    content = result.pop('content')
    with storage_lock:
        d, meta = _fulltext_target(paper_id, library_id)
        existing = _existing_fulltext(d, meta)
        if existing:
            return existing
        previous = load_figures(d)
        if any(f.get('document_id', 'main') == 'main' for f in previous):
            return {'outcome': 'needs_review', 'message': '已存在正文图片记录，请核对后手动附加 PDF。'}
    with tempfile.TemporaryDirectory(prefix='.fulltext-', dir=DATA_DIR) as temporary:
        stage = Path(temporary)
        if previous:
            save_figures(stage, previous)
        filename = (re.sub(r'[^a-zA-Z0-9._-]', '_', oa.identity(original)[0])[:120] or 'fulltext') + '.pdf'
        prepared = ingest_pdf(stage, content, filename, blank_meta(paper_id))
        digest = prepared['document_revisions']['main']
        with storage_lock:
            d, latest = _fulltext_target(paper_id, library_id)
            existing = _existing_fulltext(d, latest)
            if existing:
                return existing
            if oa.identity(latest) != oa.identity(original) or load_figures(d) != previous:
                raise HTTPException(409, '下载期间论文信息或图片已变化，请重新获取')
            if digest in latest.get('document_revisions', {}).values():
                return {'outcome': 'needs_review', 'message': '候选 PDF 与已附加的补充材料相同，请核对。'}
            for key in ('filename', 'has_file', 'pages', 'text_chars', 'figures_count', 'parse_warnings'):
                latest[key] = prepared[key]
            for key in ('title', 'authors', 'year', 'doi'):
                if not latest.get(key):
                    latest[key] = prepared[key]
            materials_changed(latest, 'main', digest)
            latest['fulltext_source'] = {**result['source'], 'retrieved_at': _now(), 'sha256': digest}
            write_meta(stage, latest)
            oa.publish(stage, d)
            result['paper'] = summary_of(read_meta(d))
            return result


async def obtain_link_or_open_fulltext(meta, link):
    if not link:
        return await oa.obtain(meta, MAX_UPLOAD_BYTES)
    original = link
    try:
        if not link.get('inspected') and not link.get('pdf_url'):
            inspected = await article_links.inspect_url(link['article_url'])
            link = inspected['_link']
        if original.get('sha256'):
            link = {**link, 'sha256': original['sha256']}
        result = await article_links.download_pdf(link, meta, MAX_UPLOAD_BYTES)
    except article_links.ArticleLinkError as exc:
        result = {'outcome': 'failed', 'message': str(exc)}
    if result['outcome'] in ('downloaded', 'needs_review') or not any(oa.identity(meta)[:3]):
        return result
    alternate = await oa.obtain(meta, MAX_UPLOAD_BYTES)
    if alternate['outcome'] == 'not_found' and result['outcome'] == 'failed':
        return result
    return alternate


async def obtain_paper_fulltext(paper_id, library_id, link=None):
    with storage_lock:
        d, meta = _fulltext_target(paper_id, library_id)
        existing = _existing_fulltext(d, meta)
        if existing:
            return existing
    try:
        if link is None and meta.get('import_url'):
            link = {'article_url': meta['import_url'], 'sha256': meta.get('import_pdf_sha256')}
        result = await asyncio.wait_for(obtain_link_or_open_fulltext(meta, link), timeout=120)
        if result['outcome'] != 'downloaded':
            return result
        return await asyncio.to_thread(_attach_download, paper_id, library_id, meta, result)
    except asyncio.TimeoutError:
        return {'outcome': 'failed', 'message': '获取正文超时，请稍后重试。'}
    except FigureStorageError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning('Full-text import failed for %s: %s', paper_id, type(exc).__name__)
        return {'outcome': 'failed', 'message': '正文保存或解析失败，请检查磁盘后重试。'}


@app.post('/api/papers/{paper_id}/fetch-fulltext')
async def fetch_open_fulltext(paper_id: str, library_id: str = 'all'):
    return await obtain_paper_fulltext(paper_id, library_id)


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
        if meta.get('fulltext_source', {}).get('sha256') != meta['document_revisions']['main']:
            meta.pop('fulltext_source', None)
        write_meta(d, meta)
        return meta


def _save_supplement_pdf(d, content, filename, remote=None):
    _validate_pdf_upload(filename, content)
    digest = hashlib.sha256(content).hexdigest()
    with storage_lock:
        meta = read_meta(d)
        existing = next((f for f in meta["supplements"] if f.get("sha256") == digest), None)
        if existing:
            if not remote:
                raise HTTPException(409, "该补充 PDF 已添加")
            if not existing.get('source_url'):
                existing.update(remote)
            elif existing.get('candidate_id') != remote['candidate_id']:
                aliases = existing.setdefault('candidate_aliases', [])
                if remote['candidate_id'] not in aliases:
                    aliases.append(remote['candidate_id'])
            write_meta(d, meta)
            return meta
        if (d / "paper.pdf").is_file() and hashlib.sha256((d / "paper.pdf").read_bytes()).hexdigest() == digest:
            raise HTTPException(409, "该 PDF 已是正文，不必重复添加")
        sid = uuid.uuid4().hex
        try:
            parsed, figures = store_document(d, content, filename, sid)
        except Exception as exc:
            raise HTTPException(400, f"补充 PDF 解析失败：{exc}") from exc
        meta["supplements"].append({"id": sid, "filename": filename, "pages": len(parsed["pages"]),
                                    "sha256": digest, "warnings": parsed.get("warnings", [])})
        if remote:
            meta["supplements"][-1].update(remote)
        meta["figures_count"] = len(figures)
        materials_changed(meta, sid, digest)
        write_meta(d, meta)
        return meta


def _store_supplement_file(d, meta, content, record):
    digest = hashlib.sha256(content).hexdigest()
    folder = d / 'supplement-files'
    folder.mkdir(exist_ok=True)
    path = folder / digest
    if not path.is_file():
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
            tmp = Path(output.name)
            try:
                output.write(content)
                output.close()
                tmp.replace(path)
            finally:
                tmp.unlink(missing_ok=True)
    meta.setdefault('supplement_files', []).append({**record, 'size': len(content), 'sha256': digest})
    write_meta(d, meta)
    return meta


def _save_local_table(d, content, filename):
    try:
        pmc.validate_file(filename, content)
        supplement_tables.validate_upload(filename, content)
    except (pmc.PMCError, supplement_tables.TableError) as exc:
        raise HTTPException(422, str(exc)) from exc
    with storage_lock:
        meta = read_meta(d)
        digest = hashlib.sha256(content).hexdigest()
        if any(r.get('sha256') == digest and r.get('filename') == filename for r in meta.get('supplement_files', [])):
            raise HTTPException(409, '该补充表已添加')
        return _store_supplement_file(d, meta, content, {'id': uuid.uuid4().hex, 'filename': filename,
                                        'mime': pmc.MIMES[Path(filename).suffix.lower()]})


@app.post("/api/papers/{paper_id}/supplements")
async def add_supplement(paper_id: str, file: UploadFile = File(...)):
    d = paper_dir(paper_id)
    filename = Path(file.filename or "supplement.pdf").name
    if Path(filename).suffix.lower() in supplement_tables.EXTENSIONS:
        if len(filename) > 255:
            raise HTTPException(422, '文件名过长')
        content = await file.read(supplement_tables.MAX_BYTES + 1)
        if len(content) > supplement_tables.MAX_BYTES:
            raise HTTPException(413, '补充表超过 20 MB 的查询上限')
        return await asyncio.to_thread(_save_local_table, d, content, filename)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    return _save_supplement_pdf(d, content, filename)


@app.get('/api/papers/{paper_id}/supplement-candidates')
async def discover_remote_supplements(paper_id: str):
    with storage_lock:
        original = read_meta(paper_dir(paper_id))
    try:
        rows = await asyncio.wait_for(pmc.discover(original), timeout=75)
    except (pmc.PMCError, asyncio.TimeoutError) as exc:
        raise HTTPException(502, str(exc) or 'PMC 查询超时') from exc
    with storage_lock:
        meta = read_meta(paper_dir(paper_id))
        if pmc.identity(meta) != pmc.identity(original):
            raise HTTPException(409, '论文编号已变化，请重新查找')
        pmc.cache_candidates(paper_id, meta, rows)
    return {'candidates': rows}


def _saved_remote(meta, key):
    return next((r for r in [*meta.get('supplements', []), *meta.get('supplement_files', [])]
                 if r.get('candidate_id') == key or key in r.get('candidate_aliases', [])), None)


@app.post('/api/papers/{paper_id}/supplement-candidates/{key}')
async def acquire_remote_supplement(paper_id: str, key: str):
    with storage_lock:
        meta = read_meta(paper_dir(paper_id))
        if _saved_remote(meta, key):
            return meta
        row = pmc.candidate(paper_id, meta, key)
        if not row:
            raise HTTPException(409, '补充材料清单已过期，请重新查找')
    try:
        content = await pmc.checked_object(row['source_url'], row['md5'], pmc.ATTACHMENT_LIMIT)
        pmc.validate_file(row['filename'], content)
    except pmc.PMCError as exc:
        raise HTTPException(502, str(exc)) from exc

    def save():
        with storage_lock:
            d = paper_dir(paper_id)
            fresh = read_meta(d)
            if pmc.identity(fresh) != pmc.identity(meta):
                raise HTTPException(409, '论文编号已变化，请重新查找')
            if _saved_remote(fresh, key):
                return fresh
            origin = {'candidate_id': key, 'source_url': row['source_url'], 'article_version': row['article_version'],
                      'source_label': row['label'], 'source_provider': 'PMC'}
            if row['mime'] == 'application/pdf':
                return _save_supplement_pdf(d, content, row['filename'], origin)
            return _store_supplement_file(d, fresh, content, {**origin, 'id': key,
                                          'filename': row['filename'], 'mime': row['mime']})
    return await asyncio.to_thread(save)


@app.get('/api/papers/{paper_id}/supplement-files/{key}')
def get_remote_supplement_file(paper_id: str, key: str):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        rec = next((item for item in meta.get('supplement_files', []) if item.get('id') == key), None)
        if not rec or not re.fullmatch(r'[0-9a-f]{64}', str(rec.get('sha256') or '')):
            raise HTTPException(404, '补充附件不存在')
        path = d / 'supplement-files' / rec['sha256']
        if not path.is_file():
            raise HTTPException(404, '补充附件不存在')
        mime = pmc.MIMES.get(Path(rec['filename']).suffix.lower(), 'application/octet-stream')
        return FileResponse(path, media_type=mime, filename=rec['filename'],
                            headers={'X-Content-Type-Options': 'nosniff'})


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
        notes = ns.portable_notes(d, meta, notes)
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
    meta = read_meta(d)
    return {"notes": illustrate_notes(body.notes, figures, "deep" if all_figures else meta.get("notes_mode", "deep"),
                                      drop_unknown=False, language=meta.get("notes_language", "zh-CN"))}


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
    with storage_lock:
        directory = paper_dir(paper_id)
        meta = read_meta(directory)
        sources = {"main": directory / "paper.pdf"} if meta.get("has_file") else {}
        for supplement in meta.get("supplements", []):
            document_id = supplement.get("id", "")
            if isinstance(document_id, str) and re.fullmatch(r"[0-9a-f]{32}", document_id):
                sources[document_id] = directory / "supplements" / (document_id + ".pdf")
    # Parsing is outside storage_lock. The migrator rereads live records before
    # merging captions, so a concurrent observation cannot be rolled back.
    return upgrade_captions(directory, sources, storage_lock)


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


@app.post("/api/figure-shelf/query")
def query_figure_shelf(body: fs.ShelfQuery):
    paper_ids = list(dict.fromkeys(body.paper_ids))
    if any(not re.fullmatch(r"[0-9a-f]{32}", pid) for pid in paper_ids):
        raise HTTPException(422, "无效的文献 ID")
    words = fs.tag_key(body.query).split()
    rows, total, skipped = [], 0, 0
    for pid in paper_ids:
        try:
            with storage_lock:
                directory = paper_dir(pid)
                meta = _raw_meta(directory)
                if not isinstance(meta, dict):
                    raise ValueError("invalid paper metadata")
                for figure in load_figures(directory):
                    if not figure.get("favorite") or not figure.get("shelf_key"):
                        continue
                    item = fs.shelf_item(directory, meta, figure)
                    if not fs.matches(item, words):
                        continue
                    if body.offset <= total < body.offset + body.limit:
                        rows.append(item)
                    total += 1
        except (HTTPException, OSError, ValueError, TypeError, KeyError, FigureStorageError):
            skipped += 1
    return {"figures": rows, "total": total, "skipped": skipped}


def figure_deck_materials(library_id, refs):
    materials, papers, seen = [], {}, set()
    remaining = figure_decks.MAX_IMAGE_BYTES
    with storage_lock:
        allowed = project_paper_ids(library_id)
        for ref in refs:
            if ref.paper_id not in allowed:
                raise HTTPException(409, '所选论文已不在当前课题库。')
            key = (ref.paper_id, ref.id)
            if key in seen:
                raise HTTPException(422, '每张收藏图只能加入一次。')
            seen.add(key)
            if ref.paper_id not in papers:
                directory = paper_dir(ref.paper_id)
                papers[ref.paper_id] = directory, read_meta(directory), {f['id']: f for f in load_figures(directory)}
            directory, meta, figures = papers[ref.paper_id]
            entry = figure_decks.material(directory, meta, figures.get(ref.id), ref, remaining)
            remaining -= len(entry['image'])
            materials.append(entry)
    figure_decks.validate_revisions(materials)
    return materials


@app.post('/api/figure-decks/{library_id}/preview')
def preview_deck_figure(library_id: str, body: figure_decks.FigureRef):
    entry = figure_deck_materials(library_id, [body])[0]
    return {**entry['source'], 'revision': entry['revision']}


@app.post('/api/figure-decks/{library_id}/draft')
async def draft_deck_slide(library_id: str, body: figure_decks.SlideDraftRequest):
    settings = load_settings()
    model = settings.get('vision_model') or settings.get('model')
    if not settings.get('api_key') or not model:
        raise HTTPException(400, '请先配置模型，或手工填写幻灯片。')
    entry = (await asyncio.to_thread(figure_deck_materials, library_id, [body]))[0]
    material = figure_decks.draft_material(entry, body.reader_material)
    image_url = None
    if settings.get('vision_model'):
        image = await asyncio.to_thread(_image_payload, io.BytesIO(entry['image']))
        image_url = f"data:{image['mime']};base64,{image['data']}"
    messages = figure_decks.draft_messages(material, entry['source']['label'], body.fields, image_url)
    try:
        answer = await asyncio.wait_for(chat_once(settings['base_url'], settings['api_key'], model, messages,
                                                   temperature=0.1, max_tokens=3200, timeout=85), timeout=90)
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise HTTPException(504, '幻灯片起草超时，请重试或手工填写。') from exc
    except httpx.RequestError as exc:
        raise HTTPException(502, '无法连接模型，请检查网络或服务地址。') from exc
    except (UnsafeURLError, UpstreamError) as exc:
        raise HTTPException(400, str(exc)) from exc
    fields = figure_decks.parse_suggestions(answer, material, body.fields)
    await asyncio.to_thread(figure_deck_materials, library_id, [body])
    return {'fields': fields, 'model': model, 'used_image': image_url is not None}


@app.post('/api/figure-decks/{library_id}/pptx')
def export_figure_deck(library_id: str, body: figure_decks.Deck):
    materials = figure_deck_materials(library_id, body.slides)
    return Response(figure_decks.presentation(materials), media_type=figure_decks.MIME,
                    headers={'Content-Disposition': 'attachment; filename="BioReader-Journal-Club.pptx"'})


@app.get("/api/papers/{paper_id}/figures/{fid}/shelf-source")
def saved_figure_source(paper_id: str, fid: str, key: str):
    with storage_lock:
        directory = paper_dir(paper_id)
        figure = next((f for f in load_figures(directory) if f["id"] == fid and f.get("favorite")
                       and key and f.get("shelf_key") == key), None)
        if not figure:
            raise HTTPException(404, "图表已移除或取消收藏，请刷新。")
        item = fs.shelf_item(directory, read_meta(directory), figure)
        if not item["source_available"]:
            raise HTTPException(409, "收藏图表的来源已变更或移除。")
        return item


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
        fig["crop_source_revision"] = read_meta(d).get("document_revisions", {}).get(body.document_id, "legacy")
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
                if body.favorite is not None or body.tags is not None:
                    doc = f.get("document_id", "main")
                    if body.expected_figure_source and body.expected_figure_source != (doc, f.get("page"), f.get("bbox", [])):
                        raise HTTPException(409, "原图已更新，请重新打开图表。")
                    revisions = read_meta(d).get("document_revisions", {})
                    revision = revisions.get(doc, "legacy")
                    if body.expected_source_revision is not None and body.expected_source_revision != revision:
                        raise HTTPException(409, "原文已更新，请重新打开图表。")
                    if body.tags is not None:
                        try:
                            f["tags"] = fs.figure_tags(body.tags)
                        except ValueError as exc:
                            raise HTTPException(422, str(exc)) from exc
                    if body.favorite is not None:
                        if body.favorite and not f.get("favorite"):
                            if crop_source_changed(f, revisions):
                                raise HTTPException(409, "此裁图来自旧版 PDF，请在当前原文重新取图。")
                            f["shelf_key"] = uuid.uuid4().hex
                            f["shelf_source_revision"] = revision
                        f["favorite"] = body.favorite
                if body.label is not None and body.label.strip():
                    f["label"] = body.label.strip()[:60]
                if body.caption is not None:
                    f.pop("caption_source_revision", None)
                    f["caption"] = body.caption.strip()
                    f["caption_edited"] = True
                if body.observation is not None:
                    f.pop("observation_source_revision", None)
                    f["observation"] = body.observation.strip()
                    if not f["observation"]:
                        f.pop("observation_language", None)
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


def _figures_to_read(d: Path, settings: dict, force: bool, *, all_figures: bool = False,
                     language: str = "zh-CN", verified_docs=None) -> tuple[list[dict], list[dict]]:
    figures = load_figures(d)
    parents = [f for f in figures if not f.get("parent")]
    if verified_docs is not None:
        revisions = {doc: entry[0] for doc, entry in verified_docs.items()}
        parents = [f for f in parents if f.get("document_id", "main") in revisions and not crop_source_changed(f, revisions)]
    parents.sort(key=lambda f: (f.get("document_id", "main") != "main", f.get("document_id", "main"), f.get("page", 0), f.get("id", "")))
    limit = int(settings.get("max_figures") or 0)
    selected = parents if all_figures else (parents[:limit] if limit else [])
    todo = [f for f in selected if force or not f.get("observation")
            or verified_docs is not None and not rr.current_figure_inputs(f, verified_docs[f.get("document_id","main")][0]).get("observation")
            or (f.get("observation_language") or "zh-CN") != language]
    return figures, todo


async def _read_one_figure(settings: dict, d: Path, fig: dict, question: str = "", language: str = "zh-CN") -> str:
    image = _image_payload(d / "figures" / f"{fig['id']}.png")
    prompt = FIGURE_READ_PROMPT.format(label=fig.get("label", ""), caption=(fig.get("caption") or "无")[:1500])
    if language == "en":
        prompt = prompt.replace("用中文、Markdown 列表，≤400 字", "用英文、Markdown 列表，≤400 词")
        prompt += ("\nOutput language: English. Write all observation headings and explanations in English. "
                   "Use 'not legible' for unreadable details. Preserve gene names, identifiers and quoted source text.")
    else:
        prompt += "\n输出语言：简体中文。保留原始基因名、标识符和直接引用的原文。"
    if question.strip():
        prompt += FIGURE_QUESTION_SUFFIX.format(question=question.strip()[:500])
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{image['mime']};base64,{image['data']}"}},
    ]}]
    # Reasoning models share the output budget between thinking and the final
    # answer. Retry only a confirmed budget exhaustion with no answer, once.
    budgets = (GLM53_FIGURE_TOKEN_BUDGETS
               if is_official_glm53(settings["base_url"], settings["vision_model"])
               else FIGURE_TOKEN_BUDGETS)
    for budget in budgets:
        try:
            observation = (await chat_once(settings["base_url"], settings["api_key"],
                                          settings["vision_model"], messages, max_tokens=budget)).strip()
            break
        except CompletionError as exc:
            if exc.reason != "length":
                raise
            if budget == budgets[0] and not exc.has_content:
                continue
            raise UpstreamError("读图达到生成长度上限，未保存不完整结果，已有读图笔记已保留。请缩小选区或更换读图模型后重试。") from exc
    if not observation:
        raise UpstreamError("读图模型没有返回有效内容，已有读图笔记已保留。")
    return observation


class _FigureObservationConflict(RuntimeError):
    def __init__(self):
        super().__init__("图片已删除或内容已更新，请重新打开后重试。")


def _figure_source_identity(figure: dict) -> tuple:
    return (figure.get("id"), figure.get("document_id", "main"), figure.get("page"),
            tuple(figure.get("bbox") or ()), figure.get("parent"), figure.get("source"))


def _save_figure_observation(d: Path, original: dict, observation: str, source_revision, language: str = "zh-CN"):
    """Merge only this result into live records after the slow model request."""
    with storage_lock:
        if not (d / "meta.json").is_file():
            raise _FigureObservationConflict()
        figures = load_figures(d)
        current = next((f for f in figures if f.get("id") == original["id"]), None)
        revisions = read_meta(d).get("document_revisions", {})
        if (current is None or _figure_source_identity(current) != _figure_source_identity(original)
                or revisions.get(original.get("document_id", "main")) != source_revision
                or current.get("observation", "") != original.get("observation", "")
                or (current.get("observation_language") or "zh-CN") != (original.get("observation_language") or "zh-CN")):
            raise _FigureObservationConflict()
        # Captions and other figures may have been edited while the model ran.
        # Preserve them, and never resurrect a deleted figure from a snapshot.
        current["observation"] = observation
        current["observation_language"] = language
        current.pop("observation_source_revision", None)
        save_figures(d, figures)
        return current, figures


async def _read_figures_events(settings: dict, d: Path, force: bool, *, all_figures: bool = False, language: str = "zh-CN", tolerate_failure: bool = False, verified_docs=None):
    """Async generator: {'type':'progress'} per figure, then {'type':'result', figures}."""
    with storage_lock:
        figures, todo = _figures_to_read(d, settings, force, all_figures=all_figures, language=language, verified_docs=verified_docs)
        revisions = read_meta(d).get("document_revisions", {})
    if not todo:
        yield {"type": "result", "figures": figures}
        return
    sem = asyncio.Semaphore(2)

    async def one(fig: dict):
        async with sem:
            try:
                current_input = (rr.current_figure_inputs(fig, verified_docs[fig.get("document_id", "main")][0])
                                 if verified_docs is not None else fig)
                return fig["id"], await _read_one_figure(settings, d, current_input, language=language)
            except (UpstreamError, OSError, httpx.HTTPError):
                if not tolerate_failure:
                    raise
                return fig["id"], None

    done = 0
    originals = {f["id"]: f for f in todo}
    tasks = [asyncio.create_task(one(f)) for f in todo]
    try:
        for coro in asyncio.as_completed(tasks):
            fid, observation = await coro
            if observation is None:
                done += 1
                yield {"type": "progress", "done": done, "total": len(todo), "figure": fid, "failed": True}
                continue
            original = originals[fid]
            _, figures = _save_figure_observation(d, original, observation,
                                                  revisions.get(original.get("document_id", "main")), language)
            done += 1
            yield {"type": "progress", "done": done, "total": len(todo), "figure": fid}
        yield {"type": "result", "figures": figures}
    finally:
        # A failed or cancelled all-figure run must not leave queued vision
        # requests running after its response has ended.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


class FigureQuestion(BaseModel):
    question: str = ""
    language: Literal["zh-CN", "en"] = "zh-CN"


@app.post("/api/papers/{paper_id}/figures/read")
async def read_figures(paper_id: str, force: int = 0, scope: Literal["limited", "all"] = "limited",
                       language: Literal["zh-CN", "en"] = "zh-CN"):
    """Run the vision model over main figures (SSE progress), caching observations."""
    d = paper_dir(paper_id)
    settings = load_settings()
    if not settings.get("api_key"):
        raise HTTPException(400, "尚未设置 API Key")
    if not settings.get("vision_model"):
        raise HTTPException(400, "尚未设置读图模型（设置 → 读图模型）")

    async def generate():
        yield _sse({"type": "start", "model": settings["vision_model"], "language": language})
        try:
            async for ev in _read_figures_events(settings, d, bool(force), all_figures=scope == "all", language=language):
                if ev["type"] == "progress":
                    yield _sse({"type": "stage", "stage": "figures", **{k: v for k, v in ev.items() if k != "type"}})
                else:
                    yield _sse({"type": "done", "figures": ev["figures"]})
        except httpx.TimeoutException:
            yield _sse({"type": "error", "message": "读图请求超时，请稍后重试。"})
        except httpx.RequestError:
            yield _sse({"type": "error", "message": "无法连接读图模型，请检查网络或服务地址后重试。"})
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
    with storage_lock:
        if not _figure_file(d, fid).is_file():
            raise HTTPException(404, "figure not found")
        figures = load_figures(d)
        fig = next((f for f in figures if f["id"] == fid), None)
        if fig is None:
            raise HTTPException(404, "figure not found")
        source_revision = read_meta(d).get("document_revisions", {}).get(fig.get("document_id", "main"))
    question = (body.question if body else "") or ""
    language = body.language if body else "zh-CN"
    try:
        observation = await _read_one_figure(settings, d, fig, question, language)
        saved, _ = _save_figure_observation(d, fig, observation, source_revision, language)
    except _FigureObservationConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "读图请求超时，请稍后重试。") from exc
    except httpx.RequestError as exc:
        raise HTTPException(502, "无法连接读图模型，请检查网络或服务地址后重试。") from exc
    except (UnsafeURLError, UpstreamError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return saved


@app.get('/api/papers/{paper_id}/report-review')
def report_overview(paper_id: str):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        coverage = meta.get('notes_figure_coverage')
        if coverage:
            inventory = load_figures(d)
            docs = rr.verified_figure_documents(d, meta, inventory)
            live = {f['id']: rr.figure_identity(f, meta) for f in inventory
                    if f.get('document_id','main') in docs and not crop_source_changed(f,meta.get('document_revisions',{}))}
            coverage = {**coverage, 'figures': [{**f, 'available': live.get(f['id']) == f['identity']
                and (d / 'figures' / (f['id'] + '.png')).is_file()} for f in coverage['figures']]}
        return {'coverage': coverage, 'reviews': meta.get('claim_reviews', []), 'notes_revision': meta['notes_revision']}


class ReviewLocator(BaseModel):
    document_id: str = Field(pattern=r'^(main|[0-9a-f]{32})$')
    page: int = Field(ge=1, le=600)
    source_revision: str = Field(pattern=r'^[0-9a-f]{64}$')
    figure_id: str = Field(default='', max_length=32)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)


@app.post('/api/papers/{paper_id}/report-review/locate')
def locate_review_source(paper_id: str, body: ReviewLocator):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        _, pages, _ = ns.document_source(d,meta,body.document_id)
        if body.page > pages:
            raise HTTPException(409, '引用页已变化。')
        validate_source_snapshot(d,meta,[body.model_dump()])
        if body.figure_id:
            fig = next((f for f in load_figures(d) if f['id'] == body.figure_id), None)
            if (not fig or fig.get('document_id','main') != body.document_id or fig.get('page') != body.page
                    or fig.get('bbox') != body.bbox or not (d/'figures'/(fig['id']+'.png')).is_file()
                    or meta.get('document_revisions',{}).get(body.document_id) != body.source_revision
                    or crop_source_changed(fig,meta.get('document_revisions',{}))):
                raise HTTPException(409, '所选图表已更新，请重新核对。')
            figure = rr.current_figure_inputs(fig, body.source_revision)
            return {**body.model_dump(), 'observation': figure.get('observation',''), 'caption': figure.get('caption','')}
        return body


@app.post('/api/papers/{paper_id}/report-review/preview')
def preview_claim_review(paper_id: str, body: rr.Selection):
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        figures = load_figures(d)
    return rr.review_material(d,meta,body,figures)


@app.post('/api/papers/{paper_id}/report-review/apply')
def apply_claim_review(paper_id: str, body: rr.Decision):
    def prior(meta):
        previous = next((r for r in meta.get('claim_reviews', []) if r['request_id'] == body.request_id), None)
        if previous and (any(previous.get(k) != v for k,v in body.model_dump(exclude={'keys'}).items())
                         or [s['key'] for s in previous['sources']] != body.keys):
            raise HTTPException(409, '复核请求已变化。')
        return previous
    with storage_lock:
        d = paper_dir(paper_id)
        meta = read_meta(d)
        if prior(meta):
            return meta
        figures = load_figures(d)
    preview = rr.review_material(d,meta,body,figures)
    selected = [item for item in preview['sources'] if item['key'] in body.keys]
    with storage_lock:
        current = read_meta(d)
        if prior(current):
            return current
        if current['material_revision'] != meta['material_revision']:
            raise HTTPException(409, '复核期间来源文件已变化。')
        validate_source_snapshot(d,current,selected)
        live = {f['id']:f for f in load_figures(d)}
        for item in selected:
            if item['kind'] == 'figure':
                fig = live.get(item['figure_id'])
                if (not fig or not (d/'figures'/(fig['id']+'.png')).is_file()
                        or (rr.current_figure_inputs(fig, item['source_revision']).get('caption') or '') != item['quote']
                        or fig.get('bbox') != item['bbox']
                        or fig.get('page') != item['page'] or fig.get('document_id','main') != item['document_id']):
                    raise HTTPException(409, '所选图表已更新，请重新核对。')
        rr.apply(current,body,preview['sources'])
        write_meta(d,current)
        return current


# ---------- notes generation ----------

MODE_NAMES = {"deep": "完整精读", "quick": "速读筛选"}


@app.post("/api/papers/{paper_id}/read")
async def read_paper(paper_id: str, mode: str = "deep", use_figures: int = 1,
                     language: Literal["zh-CN", "en"] = "zh-CN"):
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
        key, generation = str(d), uuid.uuid4().hex
        if key in ns.active_generations:
            raise HTTPException(409, '这篇文献正在生成笔记，请先停止或等待完成')
        ns.active_generations[key] = generation

    def finish_generation():
        with storage_lock:
            if ns.active_generations.get(key) == generation:
                ns.active_generations.pop(key, None)

    async def generate():
        chunks: list[str] = []
        try:
            yield _sse({"type": "start", "mode": mode, "model": settings["model"], "mode_name": MODE_NAMES[mode],
                        "language": language,
                        "material_revision": meta["material_revision"], 'notes_revision': meta['notes_revision'],
                        'notes_source_snapshot': generation if mode == 'deep' else ''})
            source_input, original_sources, truncated = text, [], False
            if mode == 'deep':
                yield _sse({'type': 'stage', 'stage': 'sources', 'message': '正在准备可回跳的原文片段…'})
                source_input, original_sources, truncated = await asyncio.to_thread(ns.prepare_sources, d, meta)
            inventory = load_figures(d)
            failed_figures = set()
            figures_for_prompt: list[dict] = []
            docs = await asyncio.to_thread(rr.verified_figure_documents, d, meta, inventory) if use_figures else {}
            if use_figures:
                if settings.get("vision_model") and mode != "quick":
                    async for ev in _read_figures_events(settings, d, force=False, language=language, tolerate_failure=True, verified_docs=docs):
                        if ev["type"] == "progress":
                            if ev.get("failed"):
                                failed_figures.add(ev["figure"])
                            yield _sse({"type": "stage", "stage": "figures", "done": ev["done"], "total": ev["total"],
                                        "message": f"读图模型正在观察图片 {ev['done']}/{ev['total']}"})
                        else:
                            inventory = ev["figures"]
                            figures_for_prompt = [f for f in inventory if not f.get("parent")]
                else:
                    figures_for_prompt = [f for f in inventory if not f.get("parent")]
            figures_for_prompt = [rr.current_figure_inputs(f, docs[f.get('document_id','main')][0])
                                  for f in figures_for_prompt if f.get('document_id','main') in docs
                                  and not crop_source_changed(f,meta.get('document_revisions',{}))]
            for fig in figures_for_prompt:
                if fig['id'] in failed_figures:
                    fig['observation'] = ''
            body_text, final_sources = source_input, original_sources
            if len(source_input) > budget:
                yield _sse({'type': 'stage', 'stage': 'condense', 'message': '正在分段整理全文，保留原文出处…'})
                options = {'preserve_sources': True} if mode == 'deep' else {}
                async for ev in condense_long_text(settings["base_url"], settings["api_key"], settings["model"], source_input,
                                                    max(3000, int(budget * .5)) if mode == 'deep' else budget, **options):
                    if ev["type"] == "progress":
                        yield _sse({"type": "stage", "stage": "condense", "done": ev["done"], "total": ev["total"],
                                    "message": f"全文 {len(text)} 字符超出预算 {budget}，正在分段整理 {ev['done']}/{ev['total']}"})
                    else:
                        body_text = ev["text"]
                if mode == 'deep':
                    body_text, final_sources = ns.condensed_context(body_text, original_sources, budget)
                meta_for_prompt = {**meta, "condensed": True}
            else:
                meta_for_prompt = meta
            if mode == 'deep':
                meta_for_prompt = {**meta_for_prompt, 'note_source_enabled': True}
                if truncated:
                    body_text = '注意：材料过长，只提供有限原文片段，不得声称已核查全部材料。\n' + body_text
                with storage_lock:
                    validate_source_snapshot(d, read_meta(d), final_sources)
                    snapshot = ns.save_snapshot(d, generation, meta, final_sources)
            if figures_for_prompt:
                # Condensation and vision calls can outlive the PDF version selected at the start.
                final_docs = await asyncio.to_thread(rr.verified_figure_documents, d, meta, figures_for_prompt)
                figures_for_prompt = [f for f in figures_for_prompt if f.get('document_id','main') in final_docs]
            coverage = rr.figure_coverage(meta, inventory, figures_for_prompt, failed_figures, generation)
            messages = build_messages(body_text, mode, meta_for_prompt, figures=figures_for_prompt,
                                      direction=settings.get("research_direction", ""), language=language)
            yield _sse({"type": "stage", "stage": "write", "message": f"{settings['model']} 正在撰写{MODE_NAMES[mode]}笔记…"})
            async for piece in stream_chat(settings["base_url"], settings["api_key"], settings["model"], messages,
                                           max_tokens=max_tokens, require_done=mode == 'deep'):
                chunks.append(piece)
                yield _sse({"type": "chunk", "text": piece})
            notes = "".join(chunks).strip()
            if not notes:
                raise UpstreamError('模型没有返回可用笔记，原笔记已保留')
            if mode == 'deep':
                notes = ns.bind_citations(notes, snapshot)
            if use_figures:
                available = [f for f in figures_for_prompt if (d / "figures" / f"{f['id']}.png").is_file()]
                notes = illustrate_notes(notes, available, mode, language=language)
            with storage_lock:
                current = read_meta(d)
                if ns.note_state(current) != ns.note_state(meta):
                    raise HTTPException(409, '生成期间笔记已被编辑，原笔记已保留；本次内容不能覆盖较新的编辑')
                current['notes_figure_coverage'] = {**coverage, 'notes_revision': current['notes_revision'] + 1}
                current["notes"] = notes
                current['notes_revision'] += 1
                current['notes_source_snapshot'] = generation if mode == 'deep' else ''
                current["notes_mode"] = mode
                current["notes_language"] = language
                current["notes_updated_at"] = _now()
                current["notes_incomplete"] = False
                current["notes_material_revision"] = meta["material_revision"]
                write_meta(d, current)
            yield _sse({"type": "done", "notes": notes, "paper": summary_of(current)})
        except (UnsafeURLError, UpstreamError) as exc:
            yield _sse({"type": "error", "message": str(exc)})
        except HTTPException as exc:
            yield _sse({'type': 'error', 'message': str(exc.detail), 'status': exc.status_code})
        except Exception as exc:  # noqa: BLE001 - surface upstream failures to the UI
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            finish_generation()

    return _ChatStreamingResponse(generate(), finish_generation)


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
