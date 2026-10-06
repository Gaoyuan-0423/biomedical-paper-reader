"""Regression tests for coverage failures observed in the TAAB reading report."""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_reading_record.py'
SPEC = importlib.util.spec_from_file_location('check_reading_record', SCRIPT)
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def figure(number, status='overview', required=True):
    viewed = status in {'overview', 'panels'}
    return {
        'id': f'S{number}', 'role': 'supplementary', 'source': f'assets/supp{number}.png',
        'status': status, 'required': required,
        'view_event_ids': [f'view-{number}'] if viewed else [],
        'observation': 'Observed panel labels and group layout.' if viewed else '',
        'checked_panels': ['A', 'B'] if status == 'panels' else [],
        'limitation': 'The source labels remain illegible at original resolution.' if status == 'unreadable' else '',
    }


def event(number, kind='image_view', success=True):
    return {'event_id': f'view-{number}', 'kind': kind, 'success': success, 'figure_ids': [f'S{number}']}


def record(mode='full', delivery='complete', figures=None):
    return {
        'schema_version': 1, 'mode': mode, 'delivery_status': delivery,
        'inventory_complete': True,
        'figures': [figure(1)] if figures is None else figures,
        'claims': [], 'report_assertions': [],
    }


def claim(basis='image', status='supported'):
    return {
        'id': 'C1', 'kind': 'criticism', 'figure_ids': ['S1'], 'basis': basis,
        'status': status, 'comparison_checked': True,
        'verification_note': 'Compared the cited source annotations for the same groups and statistic.',
    }


class ReadingRecordTests(unittest.TestCase):
    def assertCode(self, result, code):
        self.assertEqual(result['status'], 'fail', result)
        self.assertIn(code, {x['code'] for x in result['errors']}, result)
        self.assertFalse(result['completion_verified'])

    def test_true_full_coverage_with_actual_events(self):
        r = record(figures=[figure(i) for i in range(1, 9)])
        result = CHECKER.validate_record(r, [event(i) for i in range(1, 9)])
        self.assertEqual(result['status'], 'pass')
        self.assertTrue(result['completion_verified'])
        self.assertEqual(result['verification_scope'], 'record_and_supplied_events')

    def test_exact_eight_converted_only_six_seven_viewed_fake_two(self):
        r = record(figures=[figure(i, 'overview' if i in {2, 6, 7} else 'caption_only') for i in range(1, 9)])
        events = [{'event_id': f'convert-{i}', 'kind': 'convert', 'success': True, 'figure_ids': [f'S{i}']} for i in range(1, 9)]
        events += [event(6), event(7)]
        r['report_assertions'] = [{'figure_id': 'S2', 'level': 'overview', 'report_location': 'report.md:479'}]
        result = CHECKER.validate_record(r, events)
        self.assertCode(result, 'unknown_event')
        self.assertCode(result, 'incomplete_coverage')

    def test_caption_only_cannot_support_image_claim_or_assertion(self):
        r = record(mode='quick', figures=[figure(1, 'caption_only', False)])
        r['claims'] = [claim()]
        r['report_assertions'] = [{'figure_id': 'S1', 'level': 'overview', 'report_location': 'report.md:42'}]
        result = CHECKER.validate_record(r)
        self.assertCode(result, 'unviewed_image_claim')
        self.assertCode(result, 'unsupported_overview_assertion')

    def test_full_cannot_skip_figure_by_marking_it_optional(self):
        r = record(figures=[figure(1), figure(2, 'pending', False)])
        self.assertCode(CHECKER.validate_record(r), 'incomplete_coverage')

    def test_limited_unreadable_is_honest_but_not_complete(self):
        r = record(delivery='limited', figures=[figure(1, 'unreadable')])
        r['claims'] = [claim(basis='text', status='unresolved')]
        result = CHECKER.validate_record(r, [])
        self.assertEqual(result['status'], 'pass', result)
        self.assertFalse(result['completion_verified'])
        self.assertFalse(result['completion_record_consistent'])
        r['figures'][0]['view_event_ids'] = ['view-1']
        self.assertEqual(CHECKER.validate_record(r, [event(1, success=False)])['status'], 'pass')
        r['claims'][0]['basis'] = 'image'
        self.assertCode(CHECKER.validate_record(r, [event(1, success=False)]), 'unviewed_image_claim')
        r['claims'][0]['basis'] = 'text'
        r['delivery_status'] = 'complete'
        self.assertCode(CHECKER.validate_record(r, [event(1)]), 'incomplete_coverage')

    def test_limited_pending_needs_a_specific_reason(self):
        r = record(delivery='limited', figures=[figure(1, 'pending')])
        self.assertCode(CHECKER.validate_record(r), 'limited_reason_required')
        r['figures'][0]['limitation'] = 'S1 remains unopened; sample-level inference is withheld pending its patient mapping.'
        result = CHECKER.validate_record(r)
        self.assertEqual(result['status'], 'pass', result)
        self.assertFalse(result['completion_verified'])

    def test_quick_and_focused_allow_unselected_figures(self):
        for mode in ['quick', 'focused']:
            with self.subTest(mode=mode):
                r = record(mode=mode, figures=[figure(1), figure(2, 'pending', False)])
                result = CHECKER.validate_record(r, [event(1)])
                self.assertEqual(result['status'], 'pass', result)
                self.assertTrue(result['completion_verified'])
                r['figures'][1]['required'] = True
                self.assertCode(CHECKER.validate_record(r, [event(1)]), 'incomplete_coverage')

    def test_conversion_copy_failed_and_wrong_figure_are_not_views(self):
        for supplied, code in [
            (event(1, 'convert'), 'invalid_view_event'),
            (event(1, 'copy'), 'invalid_view_event'),
            (event(1, success=False), 'invalid_view_event'),
            ({**event(1), 'figure_ids': []}, 'event_figure_mismatch'),
        ]:
            with self.subTest(event=supplied):
                self.assertCode(CHECKER.validate_record(record(), [supplied]), code)
        r = record(mode='quick', figures=[figure(1), figure(2, 'pending', False)])
        wrong_figure = {**event(1), 'figure_ids': ['S2']}
        self.assertCode(CHECKER.validate_record(r, [wrong_figure]), 'event_figure_mismatch')

    def test_record_only_explicitly_disclaims_independent_proof(self):
        result = CHECKER.validate_record(record())
        self.assertEqual(result['status'], 'pass', result)
        self.assertEqual(result['verification_scope'], 'record_only')
        self.assertTrue(result['completion_record_consistent'])
        self.assertFalse(result['completion_verified'])
        self.assertIn('record_only', {x['code'] for x in result['warnings']})
        self.assertIn('no independent tool-event proof', result['warnings'][0]['message'])

    def test_canonical_coverage_counts_per_role(self):
        main = figure(1, 'panels'); main['role'] = 'main'
        r = record(mode='quick', delivery='limited', figures=[
            main, figure(2, 'unreadable', False), figure(3, 'caption_only', False),
            figure(4, 'pending', False), figure(5),
        ])
        result = CHECKER.validate_record(r)
        self.assertEqual(result['status'], 'pass', result)
        self.assertEqual(result['coverage_summary']['main'], {
            'total': 1, 'overview_completed': 1, 'panels_checked': 1,
            'pending': 0, 'caption_only': 0, 'unreadable': 0,
        })
        self.assertEqual(result['coverage_summary']['supplementary'], {
            'total': 4, 'overview_completed': 1, 'panels_checked': 0,
            'pending': 1, 'caption_only': 1, 'unreadable': 1,
        })

    def test_panels_require_names_observation_and_event_ids(self):
        for field, value, code in [
            ('checked_panels', [], 'checked_panels_required'),
            ('observation', '', 'observation_required'),
            ('view_event_ids', [], 'view_event_required'),
        ]:
            r = record(figures=[figure(1, 'panels')])
            r['figures'][0][field] = value
            with self.subTest(field=field):
                self.assertCode(CHECKER.validate_record(r), code)
        r = record()
        r['report_assertions'] = [{'figure_id': 'S1', 'level': 'panels', 'report_location': 'report.md:99'}]
        self.assertCode(CHECKER.validate_record(r), 'unsupported_panel_assertion')

    def test_conflict_requires_checked_comparison_and_note(self):
        r = record()
        r['claims'] = [claim(status='conflict')]
        r['claims'][0]['comparison_checked'] = False
        r['claims'][0]['verification_note'] = ''
        result = CHECKER.validate_record(r)
        self.assertCode(result, 'conflict_comparison_required')
        self.assertCode(result, 'conflict_note_required')

    def test_portable_sources_do_not_need_to_exist(self):
        self.assertEqual(CHECKER.validate_record(record())['status'], 'pass')
        for source in ['/private/foo.png', '../foo.png', 'C:\\foo.png', 'file:foo.png', '.']:
            r = record()
            r['figures'][0]['source'] = source
            with self.subTest(source=source):
                self.assertCode(CHECKER.validate_record(r), 'relative_source_required')

    def test_ids_references_and_schema_are_checked(self):
        cases = []
        r = record(); r['figures'].append(copy.deepcopy(r['figures'][0])); cases.append((r, None, 'duplicate_figure'))
        r = record(); r['claims'] = [claim(), claim()]; cases.append((r, None, 'duplicate_claim'))
        r = record(); r['claims'] = [claim()]; r['claims'][0]['figure_ids'] = ['missing']; cases.append((r, None, 'unknown_figure'))
        r = record(); r['figures'][0]['view_event_ids'] *= 2; cases.append((r, None, 'duplicate_reference'))
        r = record(); del r['figures'][0]['observation']; cases.append((r, None, 'missing_field'))
        r = record(); r['schema_version'] = True; cases.append((r, None, 'schema_version'))
        r = record(); r['inventory_complete'] = 1; cases.append((r, None, 'boolean_required'))
        r = record(); r['mode'] = 'deep'; cases.append((r, None, 'invalid_value'))
        r = record(); r['figures'][0]['status'] = []; cases.append((r, None, 'invalid_value'))
        r = record(); r['figures'][0]['unknown'] = True; cases.append((r, None, 'unknown_field'))
        cases.append((record(), [event(1), event(1)], 'duplicate_event'))
        for r, events, code in cases:
            with self.subTest(code=code):
                self.assertCode(CHECKER.validate_record(r, events), code)

    def test_cli_jsonl_and_nonzero_failure_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'record.json'; events = Path(tmp) / 'events.jsonl'
            src.write_text(json.dumps(record()), encoding='utf-8')
            events.write_text(json.dumps(event(1)) + '\n', encoding='utf-8')
            ok = subprocess.run([sys.executable, str(SCRIPT), str(src), '--events', str(events)], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertTrue(json.loads(ok.stdout)['completion_verified'])
            events.write_text(json.dumps(event(1, 'convert')) + '\n', encoding='utf-8')
            bad = subprocess.run([sys.executable, str(SCRIPT), str(src), '--events', str(events)], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertCode(json.loads(bad.stdout), 'invalid_view_event')
            src.write_text('{"schema_version":1,"schema_version":1}', encoding='utf-8')
            duplicate = subprocess.run([sys.executable, str(SCRIPT), str(src)], capture_output=True, text=True)
            self.assertEqual(duplicate.returncode, 1)
            self.assertCode(json.loads(duplicate.stdout), 'input_error')
            src.write_text(json.dumps(record()), encoding='utf-8')
            events.write_text('{broken JSON}\n', encoding='utf-8')
            malformed = subprocess.run([sys.executable, str(SCRIPT), str(src), '--events', str(events)], capture_output=True, text=True)
            self.assertEqual(malformed.returncode, 1)
            self.assertCode(json.loads(malformed.stdout), 'input_error')
            self.assertIn('line 1', json.loads(malformed.stdout)['errors'][0]['message'])


if __name__ == '__main__':
    unittest.main()
