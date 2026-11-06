"""Local interface preferences, independent of model settings and library content."""
import json
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from reading_workflow import atomic_json


DEFAULTS = {'locale': 'zh-CN', 'scheme': 'system', 'accent': 'blue',
            'ui_font_size': 13, 'note_font_size': 14}
# Preferences do not wait for long PDF parsing or library writes. Every partial
# update rereads the latest file while holding this dedicated lock.
_preferences_lock = RLock()


class PreferencesPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    locale: Literal['zh-CN', 'en'] | None = None
    scheme: Literal['system', 'light', 'dark'] | None = None
    accent: Literal['blue', 'teal', 'violet'] | None = None
    ui_font_size: Annotated[int, Field(strict=True, ge=12, le=18)] | None = None
    note_font_size: Annotated[int, Field(strict=True, ge=12, le=24)] | None = None

    @model_validator(mode='after')
    def reject_explicit_null(self):
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError('Preference values cannot be null')
        return self


class PreferencesIn(PreferencesPatch):
    # Existing locale-only PUT clients retain their validation contract, while
    # using the same merge operation as the new appearance PATCH endpoint.
    locale: Literal['zh-CN', 'en']


def _saved_preferences(directory: Path) -> dict:
    path = Path(directory) / 'ui-preferences.json'
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text('utf-8'))
        if not isinstance(data, dict) or 'locale' not in data:
            raise ValueError('Invalid interface preferences')
        PreferencesPatch.model_validate({name: data[name] for name in DEFAULTS if name in data})
    except (OSError, UnicodeError, ValueError) as error:
        raise HTTPException(409, 'Interface preferences could not be read; the existing file was preserved.') from error
    # Preserve future on-disk fields on partial writes, but never reflect unknown
    # fields through the API or accept arbitrary request properties.
    return data


def _public_preferences(saved: dict) -> dict:
    return {name: saved.get(name, default) for name, default in DEFAULTS.items()}


def read_preferences(directory: Path) -> dict:
    with _preferences_lock:
        return _public_preferences(_saved_preferences(directory))


def write_preferences(directory: Path, body: PreferencesPatch) -> dict:
    directory = Path(directory)
    with _preferences_lock:
        saved = _saved_preferences(directory)
        changes = body.model_dump(exclude_unset=True)
        if not changes:
            return _public_preferences(saved)
        value = {**DEFAULTS, **saved, **changes}
        try:
            directory.mkdir(parents=True, exist_ok=True)
            atomic_json(directory / 'ui-preferences.json', value)
        except OSError as error:
            raise HTTPException(503, 'Interface preferences could not be saved; the previous preferences were preserved.') from error
        return _public_preferences(value)
