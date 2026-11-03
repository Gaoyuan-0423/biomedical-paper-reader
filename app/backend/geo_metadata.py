"""Bounded, on-demand public GEO metadata; kept separate from curated resources."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from external import USER_AGENT
from llm import UnsafeURLError, assert_safe_external_url
from reading_workflow import SidecarError, atomic_json

ENDPOINT = 'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi'
MAX_BYTES = 2 * 1024 * 1024
CACHE_SECONDS = 7 * 24 * 3600
SAMPLE_LIMIT = 3


class GeoError(RuntimeError):
    pass


class GeoSample(BaseModel):
    accession: str
    title: str = ''
    source_url: str
    organism: list[str] = Field(default_factory=list)
    source: list[str] = Field(default_factory=list)
    characteristics: list[str] = Field(default_factory=list)
    molecule: list[str] = Field(default_factory=list)
    library_strategy: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_identity(self):
        if validate_accession(self.accession, 'GSM') != self.accession or self.source_url != geo_url(self.accession):
            raise ValueError('GEO sample identity or source mismatch')
        return self


class GeoRecord(BaseModel):
    accession: str
    title: str = ''
    source_url: str
    fetched_at: str
    geo_sample_count: int = Field(ge=0)
    experiment_types: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    summary: str = ''
    overall_design: str = ''
    samples: list[GeoSample] = Field(default_factory=list, max_length=SAMPLE_LIMIT)
    sample_scope: Literal['examples'] = 'examples'
    samples_total: int = Field(ge=0)
    samples_shown: int = Field(ge=0, le=SAMPLE_LIMIT)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_counts_and_identity(self):
        if validate_accession(self.accession) != self.accession or self.source_url != geo_url(self.accession):
            raise ValueError('GEO series identity or source mismatch')
        if (self.geo_sample_count != self.samples_total or self.samples_shown != len(self.samples)
                or self.samples_shown > self.samples_total
                or len({sample.accession for sample in self.samples}) != len(self.samples)):
            raise ValueError('GEO sample examples and total counts are inconsistent')
        return self


def geo_url(accession):
    return ENDPOINT + '?acc=' + accession


def validate_accession(accession, prefix='GSE'):
    value = str(accession).strip().upper()
    if not re.fullmatch(prefix + r'[1-9][0-9]{0,11}', value):
        raise GeoError('请输入有效的 GEO 编号')
    return value


def load_geo_cache(directory):
    path = directory / 'geo_metadata.json'
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text('utf-8'))
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('records'), dict):
            raise ValueError('invalid GEO cache')
        records = {}
        for accession, raw in data['records'].items():
            record = GeoRecord.model_validate(raw).model_dump()
            if validate_accession(accession) != accession or record['accession'] != accession:
                raise ValueError('GEO accession mismatch')
            records[accession] = record
        return records
    except (OSError, UnicodeError, ValueError, TypeError, ValidationError, GeoError) as exc:
        raise SidecarError('geo_metadata') from exc


def save_geo_cache(directory, records):
    atomic_json(directory / 'geo_metadata.json', {'version': 1, 'records': records})


def cache_fresh(record):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(record['fetched_at'])).total_seconds()
        return 0 <= age < CACHE_SECONDS and not record.get('warnings')
    except (KeyError, ValueError, TypeError):
        return False


def parse_soft(text, accession, entity):
    """Read attributes only, reject HTML/errors and mismatched public records."""
    if not re.search(r'^\^' + entity + r'\s*=\s*' + re.escape(accession) + r'\s*$', text, re.M):
        raise GeoError('GEO 未返回该编号的公开元数据')
    fields = {}
    for line in text.splitlines():
        if line.startswith('!') and ' = ' in line:
            key, value = line[1:].split(' = ', 1)
            if value.strip():
                fields.setdefault(key.lower(), []).append(value.strip())
    return fields


def values(fields, prefix):
    # Preserve channel-specific metadata; do not infer that examples describe all samples.
    result = []
    for key, items in fields.items():
        if key == prefix or re.fullmatch(re.escape(prefix) + r'_ch\d+', key):
            for item in items:
                if item not in result:
                    result.append(item)
    return result


async def _get_soft(client, accession):
    url = str(httpx.URL(ENDPOINT, params={'acc': accession, 'targ': 'self', 'form': 'text', 'view': 'brief'}))
    try:
        assert_safe_external_url(url)
    except UnsafeURLError as exc:
        raise GeoError('无法安全连接 GEO，请检查网络后重试') from exc
    # Fixed host, no redirects, metadata only, and a strict decompressed payload cap.
    for attempt in range(2):
        try:
            async with client.stream('GET', url) as response:
                if response.status_code in (429, 502, 503, 504) and attempt == 0:
                    await asyncio.sleep(1)
                    continue
                if response.status_code != 200:
                    raise GeoError(f'GEO 返回 HTTP {response.status_code}，请稍后重试')
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise GeoError('该 GEO 记录元数据过大，请打开 GEO 原始记录查看')
                    chunks.append(chunk)
                return b''.join(chunks).decode('utf-8', errors='replace')
        except httpx.HTTPError as exc:
            if attempt == 0:
                await asyncio.sleep(1)
                continue
            raise GeoError('连接 GEO 超时或失败，请稍后重试') from exc
    raise GeoError('GEO 暂不可用')


async def fetch_geo_metadata(accession):
    accession = validate_accession(accession)
    try:
        return await asyncio.wait_for(_fetch_geo_metadata(accession), timeout=60)
    except asyncio.TimeoutError as exc:
        raise GeoError('GEO 查询超过 60 秒，请稍后重试') from exc


async def _fetch_geo_metadata(accession):
    async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=8), follow_redirects=False,
                                 headers={'User-Agent': USER_AGENT, 'Accept': 'text/plain'}) as client:
        series = parse_soft(await _get_soft(client, accession), accession, 'SERIES')
        raw_sample_ids = series.get('series_sample_id', [])
        if not raw_sample_ids or any(not re.fullmatch(r'GSM[1-9][0-9]{0,11}', value) for value in raw_sample_ids):
            raise GeoError('GEO 未返回完整的样品编号列表，无法确认样品总数，请打开原始记录核对')
        sample_ids = list(dict.fromkeys(raw_sample_ids))
        selected = sample_ids if len(sample_ids) <= SAMPLE_LIMIT else [sample_ids[0], sample_ids[len(sample_ids) // 2], sample_ids[-1]]
        samples, warnings = [], []
        for sample_id in selected:
            await asyncio.sleep(0.4)
            try:
                sample = parse_soft(await _get_soft(client, sample_id), sample_id, 'SAMPLE')
                samples.append({
                    'accession': sample_id, 'source_url': geo_url(sample_id),
                    'title': '\n'.join(sample.get('sample_title', [])),
                    **{target: values(sample, 'sample_' + field) for target, field in (
                        ('organism', 'organism'), ('source', 'source_name'),
                        ('characteristics', 'characteristics'), ('molecule', 'molecule'),
                        ('library_strategy', 'library_strategy'))}})
            except GeoError as exc:
                warnings.append(f'{sample_id} 样品信息暂未获取：{exc}')
        result = {
            'accession': accession, 'title': '\n'.join(series.get('series_title', [])),
            'source_url': geo_url(accession), 'fetched_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'geo_sample_count': len(sample_ids), 'experiment_types': series.get('series_type', []),
            'platforms': series.get('series_platform_id', []), 'summary': '\n'.join(series.get('series_summary', [])),
            'overall_design': '\n'.join(series.get('series_overall_design', [])),
            'samples': samples, 'samples_total': len(sample_ids), 'samples_shown': len(samples),
            'sample_scope': 'examples', 'warnings': warnings,
        }
        return GeoRecord.model_validate(result).model_dump()
