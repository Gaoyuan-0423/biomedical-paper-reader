#!/usr/bin/env python3
"""Validate release metadata and emit reviewed notes without publishing anything."""
import argparse
import re
import sys
from pathlib import Path


STABLE_VERSION = re.compile(r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)')


class ReleaseValidationError(ValueError):
    """The requested version is not ready for release."""


def require_version(text, pattern, version, source):
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise ReleaseValidationError(f'{source}: missing version declaration')
    if match[1] != version:
        raise ReleaseValidationError(
            f'{source}: declares {match[1]}, but the requested release is {version}'
        )


def prepare_release(root: Path, version: str) -> str:
    """Return release notes only when all current version declarations agree."""
    if not STABLE_VERSION.fullmatch(version):
        raise ReleaseValidationError('Version must be a stable tag such as v0.3.3 (no leading zeros).')

    changelog = (root / 'CHANGELOG.md').read_text(encoding='utf-8')
    # An optional Unreleased heading can precede the newest numbered release.
    require_version(changelog, r'^##[ \t]+(v\S+)', version, 'CHANGELOG.md (newest release)')

    readme = (root / 'README.md').read_text(encoding='utf-8')
    require_version(readme, r'^版本[：:][ \t]*(v[^\s。]+)', version, 'README.md (Chinese)')
    require_version(
        readme, r'^Version:[ \t]*(v\S+?)(?:\.(?:\s|$)|\s|$)',
        version, 'README.md (English)',
    )
    skill = (root / 'SKILL.md').read_text(encoding='utf-8')
    require_version(skill, r'版本[ \t]+(v[^\s（(]+)', version, 'SKILL.md')

    notes_path = root / 'releases' / f'{version}.md'
    notes = notes_path.read_text(encoding='utf-8')
    if not notes.splitlines() or notes.splitlines()[0] != f'# {version}':
        raise ReleaseValidationError(f'{notes_path.name}: first line must be # {version}')
    for language in ('中文', 'English'):
        section = re.search(
            rf'^##[ \t]+{language}[ \t]*\n(.*?)(?=^##[ \t]|\Z)',
            notes, re.MULTILINE | re.DOTALL,
        )
        if not section or not section[1].strip():
            raise ReleaseValidationError(f'{notes_path.name}: missing or empty {language} section')
    return notes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', help='Stable release tag, for example v0.3.3')
    parser.add_argument('--output', type=Path, help='Write validated notes here; otherwise use stdout')
    args = parser.parse_args()
    try:
        notes = prepare_release(Path(__file__).resolve().parents[1], args.version)
        if args.output is None:
            sys.stdout.write(notes)
        else:
            args.output.write_text(notes, encoding='utf-8')
    except (OSError, UnicodeError, ReleaseValidationError) as exc:
        print(f'Release validation failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
