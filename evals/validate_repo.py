#!/usr/bin/env python3
"""Offline integrity checks. These do NOT grade scientific interpretation."""
import argparse
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+["\'][^\n]*["\'])?\s*\)')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prose(text):
    """Exclude fenced examples; their paths can be user-supplied placeholders."""
    return re.sub(r'(?ms)^\s*(`{3,}|~{3,})[^\n]*\n.*?^\s*\1\s*$', '', text)


def anchors(text):
    out, counts = set(), {}
    for line in text.splitlines():
        m = re.match(r'^#{1,6}\s+(.+?)\s*#*$', line)
        if not m:
            continue
        value = re.sub(r'[^\w\-\s]', '', m[1].lower()).replace(' ', '-')
        count = counts.get(value, 0)
        counts[value] = count + 1
        out.add(value + (f'-{count}' if count else ''))
    return out


def check_links(root):
    errors, count, external = [], 0, set()
    for path in sorted(root.rglob('*.md')):
        if any(p in {'.git', '.venv', '.validation', 'venv'} for p in path.relative_to(root).parts):
            continue
        for match in LINK.finditer(prose(path.read_text(encoding='utf-8'))):
            target = match[1].strip('<>')
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith('//'):
                external.add(target)
                continue
            count += 1
            local = unquote(parsed.path)
            if Path(local).is_absolute():
                errors.append(f'{path.relative_to(root)}: nonportable absolute link {target}')
                continue
            dest = (path.parent / local).resolve() if local else path.resolve()
            if not dest.is_relative_to(root.resolve()):
                errors.append(f'{path.relative_to(root)}: link escapes repository: {target}')
            elif not dest.exists():
                errors.append(f'{path.relative_to(root)}: missing link {target}')
            elif parsed.fragment and dest.suffix == '.md' and unquote(parsed.fragment) not in anchors(dest.read_text()):
                errors.append(f'{path.relative_to(root)}: missing anchor {target}')
    return errors, count, sorted(external)


def check_manifest(root, manifest):
    errors = []
    for case in manifest.get('cases', []):
        for item in case['artifacts']:
            path = (root / item['path']).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                errors.append(f'{case["id"]}: missing/invalid artifact {item["path"]}')
            elif digest(path) != item['sha256']:
                errors.append(f'{case["id"]}: changed artifact {item["path"]}')
        grade = root / case['grading']
        if not grade.is_file():
            errors.append(f'{case["id"]}: missing grading')
        else:
            data = json.loads(grade.read_text())
            if not data.get('criteria') or any(not c.get('evidence') for c in data['criteria']):
                errors.append(f'{case["id"]}: grading lacks decision evidence')
            for criterion in data.get('criteria', []):
                for evidence in criterion.get('evidence', []):
                    if not isinstance(evidence, dict):
                        continue
                    quoted = (grade.parent / evidence['artifact']).resolve()
                    if not quoted.is_relative_to(root.resolve()) or not quoted.is_file():
                        errors.append(f'{case["id"]}: missing grading evidence artifact')
                        continue
                    lines = quoted.read_text().splitlines()
                    n = evidence['line']
                    if not 1 <= n <= len(lines) or evidence['quote'] not in lines[n - 1]:
                        errors.append(f'{case["id"]}: grading evidence no longer matches output line')
    for name, expected in manifest.get('instruction_sha256', {}).items():
        path = root / name
        if not path.is_file() or digest(path) != expected:
            errors.append(f'instructions changed after recorded run: {name}')
    return errors


class IntegrityTests(unittest.TestCase):
    def test_links_and_anchors(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'target.md').write_text('# 可追溯证据\n')
            (root / 'README.md').write_text('[ok](target.md#可追溯证据)\n![bad](missing.png)\n[bad](target.md#absent)\n')
            errors, count, _ = check_links(root)
            self.assertEqual(count, 3)
            self.assertEqual(len(errors), 2)
            self.assertTrue(any('missing.png' in e for e in errors))
            self.assertTrue(any('absent' in e for e in errors))

    def test_tampered_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            report = root / 'report.md'
            report.write_text('reported 0.08')
            (root / 'grade.json').write_text(json.dumps({'criteria': [{'evidence': 'manual source comparison'}]}))
            manifest = {'cases': [{'id': 'mutation', 'artifacts': [{'path': 'report.md', 'sha256': digest(report)}], 'grading': 'grade.json'}]}
            self.assertEqual(check_manifest(root, manifest), [])
            report.write_text('reported 0.8')
            self.assertTrue(any('changed artifact' in e for e in check_manifest(root, manifest)))

    def test_stale_scoring_quote_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            report = root / 'report.md'
            report.write_text('reported 0.08\n')
            grade = {'criteria': [{'evidence': [{'artifact': 'report.md', 'line': 1, 'quote': 'reported 0.8'}]}]}
            (root / 'grade.json').write_text(json.dumps(grade))
            manifest = {'cases': [{'id': 'stale-quote', 'artifacts': [{'path': 'report.md', 'sha256': digest(report)}], 'grading': 'grade.json'}]}
            self.assertTrue(any('no longer matches' in e for e in check_manifest(root, manifest)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--manifest', type=Path, help='Recorded run manifest; default is the latest dated run with a manifest')
    args = parser.parse_args()
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(IntegrityTests))
        return 0 if result.wasSuccessful() else 1
    errors, count, external = check_links(ROOT)
    manifests = sorted((ROOT / 'evals/runs').glob('*/manifest.json'))
    manifest = args.manifest or (manifests[-1] if manifests else None)
    if manifest is not None and manifest.is_file():
        errors += check_manifest(ROOT, json.loads(manifest.read_text()))
    else:
        errors.append('current-version run manifest missing')
    result = {'status': 'pass' if not errors else 'fail', 'local_links_checked': count,
              'recorded_manifest': str(manifest.relative_to(ROOT)) if manifest is not None and manifest.is_relative_to(ROOT) else str(manifest),
              'external_links_not_fetched': external, 'errors': errors,
              'scope': 'link/anchor and recorded-artifact integrity; no semantic scoring or speed claim'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
