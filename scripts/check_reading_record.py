#!/usr/bin/env python3
"""Validate a reading record's coverage and provenance, not its science.

Sources are portable relative paths; this checker does not require the source
files or report to exist. Host-adapted event JSONL is optional. Without it, event
IDs remain reader assertions and cannot independently prove image viewing.
"""
import argparse
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath


ROOT_FIELDS = {
    'schema_version', 'mode', 'delivery_status', 'inventory_complete',
    'figures', 'claims', 'report_assertions',
}
FIGURE_FIELDS = {
    'id', 'role', 'source', 'status', 'required', 'view_event_ids',
    'observation', 'checked_panels', 'limitation',
}
CLAIM_FIELDS = {
    'id', 'kind', 'figure_ids', 'basis', 'status', 'comparison_checked',
    'verification_note',
}
ASSERTION_FIELDS = {'figure_id', 'level', 'report_location'}
EVENT_FIELDS = {'event_id', 'kind', 'success', 'figure_ids'}
VIEWED = {'overview', 'panels'}
READABLE = {'overview', 'panels'}


def validate_record(record, events=None):
    """Return structured diagnostics; `events` must be host-adapted actual calls."""
    errors, warnings = [], []

    def issue(code, path, message):
        errors.append({'code': code, 'path': path, 'message': message})

    def shape(value, fields, path):
        if not isinstance(value, dict):
            issue('object_required', path, 'Expected an object.')
            return False
        for field in sorted(fields - value.keys()):
            issue('missing_field', f'{path}.{field}', 'Required field is missing.')
        for field in sorted(value.keys() - fields):
            issue('unknown_field', f'{path}.{field}', 'Field is not in schema version 1.')
        return fields <= value.keys()

    def string(value, path, nonempty=False):
        if not isinstance(value, str) or (nonempty and not value.strip()):
            issue('string_required', path, 'Expected a nonempty string.' if nonempty else 'Expected a string.')
            return False
        return True

    def boolean(value, path):
        if type(value) is not bool:
            issue('boolean_required', path, 'Expected a JSON boolean.')
            return False
        return True

    def choice(value, allowed, path):
        if not isinstance(value, str) or value not in allowed:
            issue('invalid_value', path, f'Expected one of {", ".join(sorted(allowed))}.')
            return False
        return True

    def strings(value, path):
        if not isinstance(value, list):
            issue('list_required', path, 'Expected a list of unique nonempty strings.')
            return False
        seen = set()
        for i, item in enumerate(value):
            if string(item, f'{path}[{i}]', True):
                if item in seen:
                    issue('duplicate_reference', f'{path}[{i}]', f'Duplicate value: {item}.')
                seen.add(item)
        return all(isinstance(x, str) and x.strip() for x in value)

    def array(value, path):
        if not isinstance(value, list):
            issue('list_required', path, 'Expected a list.')
            return []
        return value

    def reference_list(value, path, known):
        if strings(value, path):
            for i, key in enumerate(value):
                if key not in known:
                    issue('unknown_figure', f'{path}[{i}]', f'Unknown figure ID: {key}.')

    if not shape(record, ROOT_FIELDS, '$'):
        return _result(errors, warnings, events is not None, False, None)
    if type(record['schema_version']) is not int or record['schema_version'] != 1:
        issue('schema_version', '$.schema_version', 'Only integer schema_version 1 is supported.')
    mode_ok = choice(record['mode'], {'full', 'quick', 'focused'}, '$.mode')
    delivery_ok = choice(record['delivery_status'], {'complete', 'limited'}, '$.delivery_status')
    boolean(record['inventory_complete'], '$.inventory_complete')
    complete = delivery_ok and record['delivery_status'] == 'complete'
    if complete and record['inventory_complete'] is not True:
        issue('inventory_incomplete', '$.inventory_complete', 'Complete delivery requires a completed material inventory.')

    figures = {}
    for i, figure in enumerate(array(record['figures'], '$.figures')):
        p = f'$.figures[{i}]'
        if not shape(figure, FIGURE_FIELDS, p):
            continue
        id_ok = string(figure['id'], p + '.id', True)
        if id_ok:
            if figure['id'] in figures:
                issue('duplicate_figure', p + '.id', 'Figure IDs must be unique.')
            else:
                figures[figure['id']] = (figure, p)
        choice(figure['role'], {'main', 'supplementary'}, p + '.role')
        if string(figure['source'], p + '.source', True):
            source = figure['source']
            if (PurePosixPath(source).is_absolute() or PureWindowsPath(source).drive
                    or '\\' in source or '..' in PurePosixPath(source).parts
                    or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', source)
                    or source in {'.', './'}):
                issue('relative_source_required', p + '.source', 'Use a portable relative file path without parent traversal or a URI.')
        status_ok = choice(figure['status'], {'pending', 'caption_only', 'overview', 'panels', 'unreadable'}, p + '.status')
        boolean(figure['required'], p + '.required')
        event_ids_ok = strings(figure['view_event_ids'], p + '.view_event_ids')
        string(figure['observation'], p + '.observation')
        panels_ok = strings(figure['checked_panels'], p + '.checked_panels')
        string(figure['limitation'], p + '.limitation')
        if status_ok and figure['status'] in VIEWED:
            if event_ids_ok and not figure['view_event_ids']:
                issue('view_event_required', p + '.view_event_ids', 'A viewed status requires at least one image-view event ID.')
            if not isinstance(figure['observation'], str) or not figure['observation'].strip():
                issue('observation_required', p + '.observation', 'Record what was actually observed.')
        if figure['status'] == 'panels' and panels_ok and not figure['checked_panels']:
            issue('checked_panels_required', p + '.checked_panels', 'Panel verification requires named checked panels.')
        if figure['status'] == 'unreadable' and (not isinstance(figure['limitation'], str) or not figure['limitation'].strip()):
            issue('unreadable_limitation_required', p + '.limitation', 'Specify the source/tool/readability obstacle and the remaining limitation.')
        required = mode_ok and (record['mode'] == 'full' or figure['required'] is True)
        if figure['status'] == 'unreadable' and complete:
            issue('unreadable_requires_limited', p + '.status', 'Unreadable material requires limited delivery; it is not verified image coverage.')
        if required and (not status_ok or figure['status'] not in READABLE):
            if complete:
                issue('incomplete_coverage', p + '.status', 'Required figure lacks readable image coverage; delivery cannot be complete.')
            elif not isinstance(figure['limitation'], str) or not figure['limitation'].strip():
                issue('limited_reason_required', p + '.limitation', 'Limited delivery must give a specific reason for each required incomplete figure.')

    event_map = {}
    if events is not None:
        for i, event in enumerate(array(events, '$events')):
            p = f'$events[{i}]'
            if not shape(event, EVENT_FIELDS, p):
                continue
            if string(event['event_id'], p + '.event_id', True):
                if event['event_id'] in event_map:
                    issue('duplicate_event', p + '.event_id', 'Event IDs must be unique.')
                else:
                    event_map[event['event_id']] = event
            choice(event['kind'], {'image_view', 'convert', 'copy'}, p + '.kind')
            boolean(event['success'], p + '.success')
            reference_list(event['figure_ids'], p + '.figure_ids', figures)
        for figure_id, (figure, p) in figures.items():
            if not isinstance(figure['view_event_ids'], list):
                continue
            for j, event_id in enumerate(figure['view_event_ids']):
                if not isinstance(event_id, str):
                    continue
                event = event_map.get(event_id)
                ep = f'{p}.view_event_ids[{j}]'
                if event is None:
                    issue('unknown_event', ep, f'Event ID {event_id} is absent from the supplied event log.')
                elif event['kind'] != 'image_view' or (event['success'] is not True and figure['status'] != 'unreadable'):
                    issue('invalid_view_event', ep, 'Only a successful image_view event proves a viewing action; conversion/copy do not count.')
                elif not isinstance(event['figure_ids'], list) or figure_id not in event['figure_ids']:
                    issue('event_figure_mismatch', ep, 'The image-view event is not associated with this figure.')
    else:
        warnings.append({'code': 'record_only', 'path': '$', 'message': 'Record-only verification: event IDs are reader assertions; no independent tool-event proof was checked.'})

    claim_ids = set()
    for i, claim in enumerate(array(record['claims'], '$.claims')):
        p = f'$.claims[{i}]'
        if not shape(claim, CLAIM_FIELDS, p):
            continue
        if string(claim['id'], p + '.id', True):
            if claim['id'] in claim_ids:
                issue('duplicate_claim', p + '.id', 'Claim IDs must be unique.')
            claim_ids.add(claim['id'])
        choice(claim['kind'], {'result', 'criticism', 'recommendation'}, p + '.kind')
        reference_list(claim['figure_ids'], p + '.figure_ids', figures)
        choice(claim['basis'], {'text', 'image'}, p + '.basis')
        choice(claim['status'], {'supported', 'unresolved', 'conflict'}, p + '.status')
        boolean(claim['comparison_checked'], p + '.comparison_checked')
        string(claim['verification_note'], p + '.verification_note')
        if claim['status'] == 'conflict':
            if claim['comparison_checked'] is not True:
                issue('conflict_comparison_required', p + '.comparison_checked', 'Conflict claims require a checked comparison.')
            if not isinstance(claim['verification_note'], str) or not claim['verification_note'].strip():
                issue('conflict_note_required', p + '.verification_note', 'Record the sources, comparison and verification used for the conflict claim.')
        if claim['basis'] == 'image' and isinstance(claim['figure_ids'], list):
            if not claim['figure_ids']:
                issue('image_figure_required', p + '.figure_ids', 'An image-basis claim must identify its figure sources.')
            for figure_id in claim['figure_ids']:
                if isinstance(figure_id, str) and figure_id in figures:
                    figure = figures[figure_id][0]
                    if not isinstance(figure['status'], str) or figure['status'] not in VIEWED:
                        issue('unviewed_image_claim', p + '.figure_ids', f'{figure_id} has no recorded image view.')

    assertion_keys = set()
    for i, assertion in enumerate(array(record['report_assertions'], '$.report_assertions')):
        p = f'$.report_assertions[{i}]'
        if not shape(assertion, ASSERTION_FIELDS, p):
            continue
        figure_ok = string(assertion['figure_id'], p + '.figure_id', True)
        level_ok = choice(assertion['level'], {'caption_only', 'overview', 'panels'}, p + '.level')
        location_ok = string(assertion['report_location'], p + '.report_location', True)
        if figure_ok and level_ok and location_ok:
            key = (assertion['figure_id'], assertion['level'], assertion['report_location'])
            if key in assertion_keys:
                issue('duplicate_assertion', p, 'Duplicate report assertion.')
            assertion_keys.add(key)
        if not figure_ok or assertion['figure_id'] not in figures:
            issue('unknown_figure', p + '.figure_id', 'Report assertion refers to an unknown figure.')
            continue
        status = figures[assertion['figure_id']][0]['status']
        if assertion['level'] == 'overview' and (not isinstance(status, str) or status not in READABLE):
            issue('unsupported_overview_assertion', p + '.level', 'The report asserts readable image viewing without recorded readable coverage.')
        if assertion['level'] == 'panels' and status != 'panels':
            issue('unsupported_panel_assertion', p + '.level', 'The report asserts panel verification without a panel verification record.')
        if assertion['level'] == 'caption_only' and status == 'pending':
            issue('unsupported_caption_assertion', p + '.level', 'The figure is still pending; caption reading has not been recorded.')

    if delivery_ok and record['delivery_status'] == 'limited':
        warnings.append({'code': 'limited_delivery', 'path': '$.delivery_status', 'message': 'This is an honest limited-delivery record, not completed reading coverage.'})
    summary = _coverage_summary()
    for figure, _ in figures.values():
        role, status = figure['role'], figure['status']
        if not isinstance(role, str) or role not in summary:
            continue
        summary[role]['total'] += 1
        if not isinstance(status, str):
            continue
        if status in READABLE:
            summary[role]['overview_completed'] += 1
        if status == 'panels':
            summary[role]['panels_checked'] += 1
        if status in {'pending', 'caption_only', 'unreadable'}:
            summary[role][status] += 1
    return _result(errors, warnings, events is not None, complete, record['delivery_status'], summary)


def _coverage_summary():
    return {role: {key: 0 for key in (
        'total', 'overview_completed', 'panels_checked', 'pending',
        'caption_only', 'unreadable',
    )} for role in ('main', 'supplementary')}


def _result(errors, warnings, events_supplied, complete, delivery_status, summary=None):
    consistent = bool(complete and not errors)
    return {
        'status': 'fail' if errors else 'pass',
        'verification_scope': 'record_and_supplied_events' if events_supplied else 'record_only',
        'delivery_status': delivery_status,
        'completion_record_consistent': consistent,
        'completion_verified': bool(consistent and events_supplied),
        'coverage_summary': summary if summary is not None else _coverage_summary(),
        'errors': errors,
        'warnings': warnings,
        'limitations': ['Checks declared coverage/provenance consistency only; does not judge scientific semantics, extract report assertions, or authenticate host-adapted events.'],
    }


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f'Duplicate JSON object key: {key}')
        value[key] = item
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('record', type=Path, help='Reading record JSON')
    parser.add_argument('--events', type=Path, help='Optional host-adapted actual tool-event JSONL')
    args = parser.parse_args(argv)
    try:
        record = json.loads(args.record.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
        events = None
        if args.events is not None:
            events = []
            for n, line in enumerate(args.events.read_text(encoding='utf-8').splitlines(), 1):
                if line.strip():
                    try:
                        events.append(json.loads(line, object_pairs_hook=_unique_object))
                    except ValueError as exc:
                        raise ValueError(f'Events JSONL line {n}: {exc}') from exc
        result = validate_record(record, events)
    except (OSError, ValueError) as exc:
        result = _result([{'code': 'input_error', 'path': '$input', 'message': str(exc)}], [], args.events is not None, False, None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
