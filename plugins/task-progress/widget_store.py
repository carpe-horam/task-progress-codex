"""Read-only catalogue and fixed-instance projections for the native widget."""
from __future__ import annotations

from copy import deepcopy
import threading


class WidgetStore:
    SOURCES = ('codex', 'cindy', 'dsh', 'unknown')

    def __init__(self, core):
        self.core = core
        self._cache = {}
        self._lock = threading.Lock()

    def session_source(self, sid):
        sid = self.core._normalize_session_id(sid)
        path = self.core.SESSION_DIR / (sid + '.source.json')
        if path.is_symlink():
            raise ValueError('Unsafe task source record.')
        if not path.exists():
            return 'unknown'
        import json
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Invalid task source record.')
        source = value.get('source')
        if type(value.get('version')) is not int or value['version'] != 1 or source not in self.SOURCES[:-1]:
            raise ValueError('Invalid task source record.')
        return source

    def claim_source(self, sid, source):
        if source not in self.SOURCES[:-1]:
            raise ValueError('Invalid task source.')
        sid = self.core._normalize_session_id(sid)
        with self.core._session_lock(sid):
            if self.core._read(sid) is None:
                raise ValueError('Task session does not exist.')
            previous = self.session_source(sid)
            if previous not in ('unknown', source):
                raise ValueError('Task belongs to a different source.')
            if previous == 'unknown':
                self.core._atomic_json(self.core.SESSION_DIR / (sid + '.source.json'),
                                       {'version': 1, 'source': source})
        return sid

    def _instances(self, document):
        seen = set()
        current = self.core._task_instance_id(document)
        seen.add(current)
        yield current, document, False
        for iid, instance in document.get('session_work_state', {}).get('instances', {}).items():
            if iid not in seen and isinstance(instance, dict):
                seen.add(iid)
                yield iid, instance, bool(self.core._parallel_is_archivable(instance))
        for instance in document.get('task_history', []):
            iid = instance.get('task_instance_id')
            # Old archives without identity remain available in the original
            # App's history tab; never borrow the foreground's identity.
            if iid and iid not in seen:
                seen.add(iid)
                yield iid, instance, True

    def _summary(self, sid, iid, instance, archived):
        if isinstance(instance.get('nodes'), list):
            progress = self.core._project_titles(self.core._project_leaf_metrics(instance))
        else:
            progress = self.core._project_titles(self.core._project_leaf_metrics(
                self.core._progress_payload(deepcopy(instance))))
        status = progress.get('status', 'unknown')
        closed = status in ('completed', 'closed', 'handed_off')
        if not closed:
            if progress.get('pending_review_count'):
                status = 'awaiting_review'
            elif progress.get('executing_count') or progress.get('root_status') == 'executing':
                status = 'executing'
            elif progress.get('root_status') == 'waiting_synthesis':
                status = 'waiting_synthesis'
            elif progress.get('blocked_count'):
                status = 'blocked'
        model = (instance.get('main_session') or {}).get('model')
        percent = (progress.get('accepted_resolved_percent', 0) if progress.get('schema_version') == 2
                   else progress.get('percent', 0))
        return {'id': sid + ':' + iid, 'session_id': sid, 'task_instance_id': iid,
                'title': progress.get('display_title') or progress.get('task_title') or '未命名任务',
                'status': status, 'percent': percent, 'closed': closed, 'archived': archived,
                'historical': archived,
                'source': model if isinstance(model, str) and model else '本机任务',
                'created_at': instance.get('created_at'), 'updated_at': instance.get('updated_at'),
                'state_revision': instance.get('state_revision', 0)}

    def catalogue(self, *, offset=0, limit=100, query='', source=None):
        if source is not None and source not in self.SOURCES:
            raise ValueError('Invalid task source.')
        if (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or
                isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100 or
                not isinstance(query, str) or len(query) > 200):
            raise ValueError('Invalid widget catalogue pagination or search.')
        rows, failed, present = [], 0, set()
        for path in self.core.SESSION_DIR.glob('*.json'):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                sid = self.core._normalize_session_id(path.stem)
            except self.core.ProgressError:
                continue  # Order files, view leases and initialization ledgers.
            if path.stem != sid:
                continue
            present.add(sid)
            try:
                owner = self.session_source(sid)
                if source is not None and owner != source:
                    continue
                stat = path.stat()
                archive_path = self.core._host_archive_file(sid)
                archive_stat = archive_path.stat() if archive_path.exists() else None
                fingerprint = (stat.st_ino, stat.st_mtime_ns, stat.st_size,
                               (archive_stat.st_ino, archive_stat.st_mtime_ns, archive_stat.st_size) if archive_stat else None)
                with self._lock:
                    cached = self._cache.get(sid)
                if cached and cached[0] == fingerprint:
                    items = cached[1]
                else:
                    with self.core._session_lock(sid):
                        # A legacy unknown session may be claimed after the
                        # first source read, before its document is locked.
                        if source is not None and self.session_source(sid) != source:
                            continue
                        document = self.core._read(sid)
                        if document is None:
                            continue
                        if document.get('session_id') != sid:
                            raise ValueError('Session identity mismatch.')
                        items = [self._summary(sid, iid, instance, archived)
                                 for iid, instance, archived in self._instances(document)]
                        host_archive = self.core._read_host_archive(sid)
                        if host_archive:
                            for item in items:
                                item.update(archived=True, host_archive=host_archive)
                    with self._lock:
                        self._cache[sid] = (fingerprint, items)
                # Catalogue identity and pagination belong to the session. Keep
                # fixed-instance identities nested for explicit record viewing.
                row = dict(items[0])
                row.update(id=sid, instances=items, task_source=owner,
                           closed=all(item['closed'] or item['historical'] for item in items))
                rows.append(row)
            except (OSError, ValueError, KeyError, TypeError, AttributeError, self.core.ProgressError):
                failed += 1
        with self._lock:
            self._cache = {sid: item for sid, item in self._cache.items() if sid in present}
        term = query.strip().casefold()
        if term:
            rows = [row for row in rows if any(
                term in (item['title'] + ' ' + item['source']).casefold()
                for item in row['instances'])]
        rows.sort(key=lambda row: row.get('updated_at') or row.get('created_at') or '', reverse=True)
        rows.sort(key=lambda row: bool(row['archived'] or row['closed']))
        total = len(rows)
        return {'tasks': deepcopy(rows[offset:offset + limit]), 'total': total, 'offset': offset,
                'has_more': offset + limit < total, 'next_offset': offset + limit if offset + limit < total else None,
                'unreadable_sessions': failed}

    def snapshot(self, sid, iid=None, *, include_history=False, source=None):
        sid = self.core._normalize_session_id(sid)
        if not isinstance(include_history, bool):
            raise ValueError('include_history must be boolean.')
        with self.core._session_lock(sid):
            if source is not None and (source not in self.SOURCES or self.session_source(sid) != source):
                raise ValueError('Task is outside this source list.')
            document = self.core._read(sid)
            if document is None:
                raise ValueError('任务已不存在，请刷新任务列表。')
            target = next(((key, instance, archived) for key, instance, archived in self._instances(document)
                           if iid is None or key == iid), None)
            if target is None:
                raise ValueError('任务实例已不存在，请刷新任务列表。')
            iid, instance, archived = target
            # Frozen archives include both HTN and legacy linear records. They
            # must never be routed through the current-instance lookup.
            projected_archive = archived
            if projected_archive:
                frozen = deepcopy(instance)
                if isinstance(frozen.get('nodes'), dict):
                    frozen = self.core._progress_payload(frozen)
                progress = self.core._project_titles(self.core._project_leaf_metrics(frozen))
                progress = self.core._project_execution_records(progress, include_history)
                progress['history_summary'] = {'task_count': len(progress.get('task_history', [])),
                    'plan_count': len(progress.get('history', [])), 'legacy_task_count': 0}
                progress['history_included'] = include_history
                if not include_history:
                    for field in ('task_history', 'history', 'plan_snapshots', 'plan_revisions'):
                        progress.pop(field, None)
                order = self.core._read_order(sid)
                result = {'content': [], 'structuredContent': {'session_id': sid,
                    'state_revision': order['state_revision'], 'view_sequence': order['view_sequence'],
                    'progress': progress}}
        if not projected_archive:
            # get_progress_view owns its lock; a second flock on a separate
            # descriptor would deadlock despite the reentrant Python lock.
            result = self.core.get_progress_view(sid, task_instance_id=iid, include_history=include_history)
        # The widget is an independent fixed-instance viewer. Its selection must
        # not follow the agent's foreground request or duplicate peer roots.
        state = result['structuredContent']
        state.pop('session_work', None)
        state['progress'].pop('session_work', None)
        state['progress'].pop('parallel_tasks', None)
        host_archive = self.core._read_host_archive(sid)
        if host_archive:
            state['host_archive'] = host_archive
        state['widget_view_binding'] = {'session_id': sid, 'task_instance_id': iid,
                                       'archived': bool(archived or host_archive)}
        # Current-instance projection reacquires the core lock separately.
        # Do not return newly classified data to an older unknown token.
        if source is not None and self.session_source(sid) != source:
            raise ValueError('Task is outside this source list.')
        return result
