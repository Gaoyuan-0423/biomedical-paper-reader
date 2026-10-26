"""Metadata lookups borrowed from the CiteBox feature set: CrossRef (DOI → record)
and Semantic Scholar (references / citations). Only two fixed public hosts are
ever contacted, each re-checked with the same guard the LLM client uses."""

from __future__ import annotations

import asyncio
import re
from html import unescape
from urllib.parse import quote

import httpx

from llm import UnsafeURLError, assert_safe_external_url

CROSSREF_HOST = "api.crossref.org"
S2_HOST = "api.semanticscholar.org"
USER_AGENT = "biomedical-paper-reader-app/0.2 (local; https://github.com/Gaoyuan-0423/biomedical-paper-reader)"
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
S2_FIELDS = "paperId,title,year,venue,citationCount,externalIds,authors"


class LookupError_(RuntimeError):
    pass


def normalize_doi(raw: str) -> str:
    text = (raw or "").strip()
    text = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", text, flags=re.I)
    text = re.sub(r"^doi:\s*", "", text, flags=re.I)
    m = DOI_RE.search(text)
    if not m:
        raise LookupError_("不是有效的 DOI（形如 10.1038/s41586-024-xxxxx）")
    return m.group().rstrip(".,;)")


async def _get_json(url: str, allowed_host: str, headers: dict | None = None) -> dict:
    assert_safe_external_url(url)
    if httpx.URL(url).host != allowed_host:
        raise UnsafeURLError(f"只允许访问 {allowed_host}")
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0), follow_redirects=False) as client:
        for attempt in range(3):
            try:
                resp = await client.get(url, headers=hdrs)
            except httpx.HTTPError as exc:
                raise LookupError_(f"连接 {allowed_host} 失败：{type(exc).__name__}") from exc
            # Anonymous Semantic Scholar quota is a small shared pool; back off instead of failing.
            if resp.status_code == 429 and attempt < 2:
                await asyncio.sleep(4.0 * (attempt + 1))
                continue
            break
    if resp.status_code == 404:
        raise LookupError_("未找到该文献记录")
    if resp.status_code == 429:
        raise LookupError_(f"{allowed_host} 限流，请稍后再试")
    if resp.status_code >= 400:
        raise LookupError_(f"{allowed_host} 返回 HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise LookupError_(f"{allowed_host} 返回了非 JSON 内容") from exc


def _strip_jats(text: str) -> str:
    text = re.sub(r"<jats:title>.*?</jats:title>", "", text or "", flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _year_from(issued: dict) -> str:
    parts = (issued or {}).get("date-parts") or [[]]
    return str(parts[0][0]) if parts and parts[0] and parts[0][0] else ""


async def fetch_crossref(doi: str) -> dict:
    """CrossRef work record normalised to the app's paper metadata fields."""
    doi = normalize_doi(doi)
    data = await _get_json(f"https://{CROSSREF_HOST}/works/{quote(doi, safe='')}", CROSSREF_HOST)
    msg = data.get("message") or {}
    authors = []
    for a in msg.get("author") or []:
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name") or ""
        if name:
            authors.append(name)
    title = " ".join((msg.get("title") or [""])[0].split())
    journal = (msg.get("container-title") or [""])[0] or msg.get("publisher") or ""
    return {
        "doi": msg.get("DOI") or doi,
        "title": title,
        "authors": "; ".join(authors),
        "year": _year_from(msg.get("published-print") or msg.get("published-online") or msg.get("issued") or {}),
        "journal": journal,
        "abstract": _strip_jats(msg.get("abstract") or ""),
        "url": msg.get("URL") or f"https://doi.org/{doi}",
        "type": msg.get("type") or "",
    }


def _s2_item(p: dict) -> dict | None:
    if not p or not p.get("title"):
        return None
    ext = p.get("externalIds") or {}
    doi = ext.get("DOI") or ""
    names = [a.get("name", "") for a in (p.get("authors") or []) if a.get("name")]
    authors = ", ".join(names[:3]) + (" 等" if len(names) > 3 else "")
    return {
        "s2_id": p.get("paperId") or "",
        "title": p["title"],
        "year": p.get("year") or "",
        "venue": p.get("venue") or "",
        "doi": doi,
        "citations": p.get("citationCount") or 0,
        "authors": authors,
        "url": f"https://doi.org/{doi}" if doi else (f"https://www.semanticscholar.org/paper/{p.get('paperId')}" if p.get("paperId") else ""),
    }


async def fetch_related(doi: str, title: str = "", limit: int = 25, api_key: str = "") -> dict:
    """References and citations of one paper from Semantic Scholar; DOI first, title search as fallback."""
    headers = {"x-api-key": api_key} if api_key else None
    base = f"https://{S2_HOST}/graph/v1/paper"
    ident = ""
    if doi:
        try:
            ident = "DOI:" + normalize_doi(doi)
        except LookupError_:
            ident = ""
    if not ident and title:
        found = await _get_json(f"{base}/search?query={quote(title[:200])}&limit=1&fields=paperId,title", S2_HOST, headers)
        hits = found.get("data") or []
        if not hits:
            raise LookupError_("Semantic Scholar 没有找到匹配标题的文献")
        ident = hits[0]["paperId"]
    if not ident:
        raise LookupError_("该文献没有 DOI 也没有标题，无法检索")
    limit = max(1, min(int(limit), 100))
    paper = await _get_json(f"{base}/{quote(ident, safe=':')}?fields={S2_FIELDS}", S2_HOST, headers)
    await asyncio.sleep(1.1)
    refs = await _get_json(f"{base}/{quote(ident, safe=':')}/references?fields={S2_FIELDS}&limit={limit}", S2_HOST, headers)
    await asyncio.sleep(1.1)
    cites = await _get_json(f"{base}/{quote(ident, safe=':')}/citations?fields={S2_FIELDS}&limit={limit}", S2_HOST, headers)
    references = [x for x in (_s2_item(r.get("citedPaper")) for r in refs.get("data") or []) if x]
    citations = [x for x in (_s2_item(c.get("citingPaper")) for c in cites.get("data") or []) if x]
    citations.sort(key=lambda x: (-(x["citations"] or 0), -(x["year"] or 0)))
    return {"paper": _s2_item(paper), "references": references, "citations": citations}
