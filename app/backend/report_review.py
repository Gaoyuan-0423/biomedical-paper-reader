"""Per-report figure inputs and reader-confirmed revisions to individual claims."""

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

import note_sources
from chat_sources import document_source, file_revision
from figures import crop_source_changed

METHODS = re.compile(
    r"\b(?:methods?|methodology|protocol|cohort|participants|randomi[sz]ed|sample preparation|spatial transcriptomics)\b|实验方法|研究方法|入组|取样|实验设计",
    re.I,
)
INTERVENTION = re.compile(
    r"knock(?:down|out)|deplet(?:ed|ion)|inhibit(?:ed|or|ion)|randomi[sz]|perturb|blockade|intervention|rescue|敲除|敲低|阻断|干预|救援",
    re.I,
)


class Selection(BaseModel):
    original: str = Field(min_length=1, max_length=4000)
    notes_revision: int = Field(ge=0)
    occurrence: int | None = Field(default=None, ge=0)
    start: int | None = Field(default=None, ge=0)


class Decision(Selection):
    replacement: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=4000)
    support: Literal["insufficient", "association", "intervention", "qualified"]
    intervention: Literal["unconfirmed", "present", "absent_in_reviewed_sources"]
    keys: list[str] = Field(default_factory=list, max_length=40)
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")


def selected_position(meta, selection):
    text = meta.get("notes", "")
    if selection.notes_revision != meta.get("notes_revision", 0):
        raise HTTPException(409, "报告已修改，请重新选择要复核的句子。")
    fragment = selection.original
    if not fragment.strip():
        raise HTTPException(422, "请先选中报告里的一句话。")
    if selection.start is not None:
        if not text.startswith(fragment, selection.start):
            raise HTTPException(409, "选中的原句已变化。")
        return selection.start
    count = text.count(fragment)
    if not count or (selection.occurrence is None and count != 1):
        raise HTTPException(409, "请在「编辑」中重新选中原句，以确定需要修改的位置。")
    index = selection.occurrence or 0
    if index >= count:
        raise HTTPException(409, "选中的原句已变化。")
    start = 0
    for _ in range(index + 1):
        pos = text.find(fragment, start)
        start = pos + len(fragment)
    return pos


def verified_figure_documents(directory, meta, figures):
    """Older extracted pixels are only usable when their PDF version is known."""
    verified = {}
    for doc in {f.get("document_id", "main") for f in figures}:
        expected = meta.get("document_revisions", {}).get(doc)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            continue
        try:
            path, _, name = document_source(directory, meta, doc)
            if file_revision(path) == expected:
                verified[doc] = (expected, (meta.get("filename") or name) if doc == "main" else name)
        except HTTPException:
            continue
    return verified


def current_figure_inputs(figure, revision):
    """Keep historic text on disk, supply only text bound to this document."""
    current = dict(figure)
    for field in ("observation", "caption"):
        if not revision or revision == "legacy" or figure.get(field + "_source_revision", revision) != revision:
            current[field] = ""
    return current


def figure_identity(fig, meta):
    doc = fig.get("document_id", "main")
    return [
        fig["id"],
        doc,
        fig.get("page"),
        fig.get("bbox"),
        fig.get("source"),
        meta.get("document_revisions", {}).get(doc, "legacy"),
    ]


def figure_coverage(meta, inventory, prompt, failures, generation):
    supplied = {f["id"]: f for f in prompt}
    names = {
        "main": meta.get("filename") or "正文",
        **{s["id"]: s["filename"] for s in meta.get("supplements", [])},
    }
    rows = []
    for f in inventory:
        if f.get("parent"):
            continue
        actual = supplied.get(f["id"], {})
        doc = f.get("document_id", "main")
        used = (
            "visual"
            if (actual.get("observation") or "").strip()
            else "caption"
            if (actual.get("caption") or "").strip()
            else "none"
        )
        rows.append(
            {
                "id": f["id"],
                "label": f.get("label") or f["id"],
                "document_id": doc,
                "document_name": names.get(doc, doc),
                "page": f.get("page"),
                "used": used,
                "failed": f["id"] in failures,
                "manual": f.get("source") == "manual",
                "identity": figure_identity(f, meta),
            }
        )
    return {"generation": generation, "figures": rows, "created": datetime.now(timezone.utc).isoformat()}


def tokens(text):
    # Keep gene symbols and Chinese bigrams searchable across both languages.
    value = text.casefold()
    words = set(re.findall(r"[a-z0-9][a-z0-9-]{1,}", value)) - {
        "the",
        "for",
        "and",
        "were",
        "with",
        "from",
        "are",
        "was",
        "that",
    }
    for run in re.findall(r"[\u3400-\u9fff]+", value):
        words.update(run[i : i + 2] for i in range(len(run) - 1))
    return words


def source_key(item):
    return hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def review_material(directory, meta, selection, figures):
    pos = selected_position(meta, selection)
    claim_tokens = tokens(selection.original)
    notes = meta["notes"]
    # An adjacent citation is relevant to this sentence. Never assign every
    # citation in a long report to every selected claim.
    end = pos + len(selection.original)
    # Only an immediately adjacent string of citation/figure links can belong
    # to this selection. Prose that follows it may cite a different claim.
    marker = rf"(?:{note_sources.LINK.pattern}|!\[[^\]\n]*\]\(figure:[a-z]\d+\))"
    adjacent = re.match(rf"(?:[ \t.,;:!?，。；：！？、（）()]*{marker})*", notes[end : end + 2000])
    near = notes[pos:end] + adjacent.group()
    cited = {(snapshot, sid) for _, snapshot, sid in note_sources.visible_links(near)}
    entries = []
    try:
        _, blocks, truncated = note_sources.prepare_sources(directory, meta)
    except HTTPException as exc:
        if exc.status_code not in (404, 422):
            raise
        blocks, truncated = [], True
    cited_blocks = set()
    if cited and meta.get("notes_source_snapshot"):
        snap = note_sources.load_snapshot(directory, meta["notes_source_snapshot"])
        cited_blocks = {
            (v["document_id"], v["page"], v["source_revision"], v["quote"])
            for v in snap["sources"]
            if (snap["snapshot_id"], v["id"]) in cited
        }
    for b in blocks:
        quote = b["quote"]
        cited_here = (b["document_id"], b["page"], b["source_revision"], quote) in cited_blocks
        methods = bool(METHODS.search(quote))
        intervention = bool(INTERVENTION.search(quote))
        score = len(claim_tokens & tokens(quote))
        if not (score or methods or intervention or cited_here):
            continue
        groups = [
            g
            for g, yes in [("citation", cited_here), ("methods", methods), ("intervention", intervention)]
            if yes
        ] or ["excerpt"]
        item = {
            "kind": "text",
            "groups": groups,
            **{k: b[k] for k in ("document_id", "document_name", "page", "quote", "source_revision", "bbox")},
        }
        if b["document_id"] == "main" and meta.get("filename"):
            item["document_name"] = meta["filename"]
        entries.append((score + 1000 * cited_here, item))
    # Keep candidate pools independent so a long Methods section can't hide a
    # citation attached to the selected sentence or an intervention excerpt.
    picked, seen = [], set()
    for group, limit in [("citation", 8), ("intervention", 5), ("methods", 6), ("excerpt", 5)]:
        eligible = sorted((pair for pair in entries if group in pair[1]["groups"]), key=lambda pair: -pair[0])
        for _, item in eligible[:limit]:
            key = source_key(item)
            if key not in seen:
                picked.append({**item, "key": key})
                seen.add(key)
    figures_ranked = []
    revisions = verified_figure_documents(directory, meta, figures)
    for fig in figures:
        fid = fig["id"]
        if (
            fig.get("parent")
            or crop_source_changed(fig, meta.get("document_revisions", {}))
            or not (directory / "figures" / f"{fid}.png").is_file()
        ):
            continue
        doc = fig.get("document_id", "main")
        if doc not in revisions:
            continue
        rev, name = revisions[doc]
        caption = current_figure_inputs(fig, rev).get("caption") or ""
        item = {
            "kind": "figure",
            "groups": ["figure"],
            "figure_id": fid,
            "label": fig.get("label") or fid,
            "quote": caption,
            "document_id": doc,
            "document_name": name,
            "source_revision": rev,
            "page": fig["page"],
            "bbox": fig.get("bbox"),
        }
        score = len(claim_tokens & tokens(caption)) + 1000 * (f"figure:{fid}" in near)
        figures_ranked.append((score, {**item, "key": source_key(item)}))
    picked.extend(item for _, item in sorted(figures_ranked, key=lambda p: -p[0]))
    return {"sources": picked, "truncated": truncated, "notes_revision": selection.notes_revision}


def apply(meta, decision, sources):
    pos = selected_position(meta, decision)
    selected = {s["key"]: s for s in sources}
    if len(set(decision.keys)) != len(decision.keys) or any(key not in selected for key in decision.keys):
        raise HTTPException(409, "所选依据已变化，请重新打开复核。")
    if not decision.replacement.strip() or not decision.reason.strip():
        raise HTTPException(422, "请填写复核后的结论和理由。")
    if decision.support == "intervention" and decision.intervention != "present":
        raise HTTPException(422, "请选择已核对的干预实验，或调整证据支持程度。")
    if not decision.keys and (decision.support != "insufficient" or decision.intervention == "present"):
        raise HTTPException(422, "请勾选支持当前判断的原句或图表。")
    record = {
        **decision.model_dump(exclude={"keys"}),
        "sources": [selected[k] for k in decision.keys],
        "at": datetime.now(timezone.utc).isoformat(),
    }
    meta["notes"] = meta["notes"][:pos] + decision.replacement + meta["notes"][pos + len(decision.original) :]
    meta["notes_revision"] += 1
    meta["notes_updated_at"] = record["at"]
    meta.setdefault("claim_reviews", []).append(record)
    return record
