"""Read-only catalogue and fixed-instance projections for the native widget."""
from __future__ import annotations

from copy import deepcopy
import threading


class WidgetStore:
    def __init__(self, core):
        self.core = core
        self._cache = {}
        self._lock = threading.Lock()

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
                'source': model if isinstance(model, str) and model else '本机任务',
                'created_at': instance.get('created_at'), 'updated_at': instance.get('updated_at'),
                'state_revision': instance.get('state_revision', 0)}

    def catalogue(self, *, offset=0, limit=100, query=''):
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
                stat = path.stat()
                fingerprint = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
                with self._lock:
                    cached = self._cache.get(sid)
                if cached and cached[0] == fingerprint:
                    items = cached[1]
                else:
                    with self.core._session_lock(sid):
                        document = self.core._read(sid)
                        if document is None:
                            continue
                        if document.get('session_id') != sid:
                            raise ValueError('Session identity mismatch.')
                        items = [self._summary(sid, iid, instance, archived)
                                 for iid, instance, archived in self._instances(document)]
                    with self._lock:
                        self._cache[sid] = (fingerprint, items)
                rows.extend(items)
            except (OSError, ValueError, KeyError, TypeError, AttributeError, self.core.ProgressError):
                failed += 1
        with self._lock:
            self._cache = {sid: item for sid, item in self._cache.items() if sid in present}
        term = query.strip().casefold()
        if term:
            rows = [row for row in rows if term in (row['title'] + ' ' + row['source']).casefold()]
        rows.sort(key=lambda row: row.get('updated_at') or row.get('created_at') or '', reverse=True)
        rows.sort(key=lambda row: bool(row['archived'] or row['closed']))
        total = len(rows)
        return {'tasks': deepcopy(rows[offset:offset + limit]), 'total': total, 'offset': offset,
                'has_more': offset + limit < total, 'next_offset': offset + limit if offset + limit < total else None,
                'unreadable_sessions': failed}

    def snapshot(self, sid, iid=None, *, include_history=False):
        sid = self.core._normalize_session_id(sid)
        if not isinstance(include_history, bool):
            raise ValueError('include_history must be boolean.')
        with self.core._session_lock(sid):
            document = self.core._read(sid)
            if document is None:
                raise ValueError('任务已不存在，请刷新任务列表。')
            target = next(((key, instance, archived) for key, instance, archived in self._instances(document)
                           if iid is None or key == iid), None)
            if target is None:
                raise ValueError('任务实例已不存在，请刷新任务列表。')
            iid, instance, archived = target
            projected_archive = archived and isinstance(instance.get('nodes'), list)
            if projected_archive:
                progress = self.core._project_titles(self.core._project_leaf_metrics(deepcopy(instance)))
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
        state['widget_view_binding'] = {'session_id': sid, 'task_instance_id': iid, 'archived': archived}
        return result
