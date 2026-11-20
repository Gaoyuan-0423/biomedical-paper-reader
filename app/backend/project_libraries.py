"""Named collections reference existing papers. Archiving leaves memberships intact."""
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from reading_workflow import atomic_json

HexId = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{32}$')]
LibraryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
LibraryTopic = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: LibraryName
    topic: LibraryTopic = ''
    paper_ids: list[HexId] = Field(default_factory=list)


class Project(ProjectCreate):
    version: Literal[1]
    id: HexId
    revision: int = Field(ge=1, strict=True)
    archived: bool = False


class ProjectEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    name: LibraryName
    topic: LibraryTopic = ''
    add: list[HexId] = Field(default_factory=list)
    remove: list[HexId] = Field(default_factory=list)
    archived: bool = False


def directory(root: Path) -> Path:
    return root / 'project-libraries'


def read(root, project_id):
    if len(project_id) != 32 or any(c not in '0123456789abcdef' for c in project_id):
        raise HTTPException(404, '未找到课题库')
    path = directory(root) / (project_id + '.json')
    try:
        record = Project.model_validate_json(path.read_bytes())
        if record.id != project_id:
            raise ValueError('identity mismatch')
        return record
    except FileNotFoundError:
        raise HTTPException(404, '未找到课题库')
    except (OSError, ValidationError, ValueError) as exc:
        raise HTTPException(409, '课题库记录损坏或版本不兼容，请检查本地文件') from exc


def projects(root):
    records, unavailable = [], 0
    for path in directory(root).glob('*.json'):
        try:
            records.append(read(root, path.stem))
        except HTTPException:
            unavailable += 1
    records.sort(key=lambda item: (item.archived, item.name.casefold(), item.id))
    return {'libraries': records, 'unavailable': unavailable}


def active_project(root, project_id):
    project = read(root, project_id)
    if project.archived:
        raise HTTPException(409, '课题库已归档')
    return project


def persist(root, record):
    directory(root).mkdir(parents=True, exist_ok=True)
    atomic_json(directory(root) / (record.id + '.json'), record.model_dump())
    return record


def create(root, body, request_id=None):
    members = list(dict.fromkeys(body.paper_ids))
    if request_id and (directory(root) / (request_id + '.json')).exists():
        existing = read(root, request_id)
        if (existing.name, existing.topic, existing.paper_ids) != (body.name, body.topic, members) or existing.archived:
            raise HTTPException(409, '课题库已被修改，请重新打开后保存')
        return existing
    record = Project(**body.model_dump(exclude={'paper_ids'}), version=1, id=request_id or uuid.uuid4().hex,
                     revision=1, paper_ids=members)
    return persist(root, record)


def edit(root, record, body):
    if body.revision != record.revision:
        raise HTTPException(409, '课题库已被修改，请重新打开后保存')
    if set(body.add).intersection(body.remove):
        raise HTTPException(422, '同一论文不能同时加入和移出')
    removed = set(body.remove)
    members = [pid for pid in dict.fromkeys(record.paper_ids + body.add) if pid not in removed]
    changed = record.model_copy(update={'name': body.name, 'topic': body.topic, 'paper_ids': members,
                                        'archived': body.archived})
    if changed == record:
        return record
    changed.revision += 1
    return persist(root, changed)


def add_paper(root, project_id, paper_id):
    project = active_project(root, project_id)
    if paper_id not in project.paper_ids:
        project.paper_ids.append(paper_id)
        project.revision += 1
        persist(root, project)
    return project
