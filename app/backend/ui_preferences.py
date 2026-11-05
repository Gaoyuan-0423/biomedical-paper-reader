"""Local interface preferences, independent of model settings and library content."""
import json
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from reading_workflow import atomic_json, storage_lock


class PreferencesIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    locale: Literal['zh-CN', 'en']


def read_preferences(directory: Path) -> dict:
    path = Path(directory) / 'ui-preferences.json'
    if not path.exists():
        return {'locale': 'zh-CN'}
    try:
        data = json.loads(path.read_text('utf-8'))
        if not isinstance(data, dict) or data.get('locale') not in ('zh-CN', 'en'):
            raise ValueError('Invalid interface language')
    except (OSError, UnicodeError, ValueError) as error:
        raise HTTPException(409, 'Interface preferences could not be read; the existing file was preserved.') from error
    return {'locale': data['locale']}


def write_preferences(directory: Path, body: PreferencesIn) -> dict:
    directory = Path(directory)
    with storage_lock:
        # A corrupt file must not be silently replaced while changing language.
        read_preferences(directory)
        directory.mkdir(parents=True, exist_ok=True)
        value = {'locale': body.locale}
        atomic_json(directory / 'ui-preferences.json', value)
        return value
