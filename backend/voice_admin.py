"""Admin voice inventory, configurable retention and durable cleanup receipts."""
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import threading
import time
import urllib.parse
import uuid

_LOCK = threading.RLock()
BUSY = {'uploading', 'payment_pending', 'accepted', 'analyzing', 'confirmed', 'dubbing'}


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def read_settings(root):
    path = Path(root) / 'voice_management.json'
    if not path.exists():
        return {'retention_days': 2}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return validate_settings(value)
    except (OSError, ValueError, TypeError):
        raise ValueError('Voice retention settings could not be read. Save them again in admin.') from None


def validate_settings(value):
    days = value.get('retention_days') if isinstance(value, dict) else None
    if type(days) is not int or not 1 <= days <= 30:
        raise ValueError('Choose a whole number of days from 1 to 30.')
    return {'retention_days': days}


def save_settings(root, value):
    clean = validate_settings(value)
    with _LOCK:
        atomic(Path(root) / 'voice_management.json', clean)
    return clean


def retention_days(root):
    return read_settings(root)['retention_days']


def expires_at(job, days):
    stamp = job.get('edit_voice_last_used', (job.get('result') or {}).get('finished'))
    try:
        stamp = float(stamp)
        if not math.isfinite(stamp) or stamp < 0:
            return None
        return stamp + days * 86400
    except (TypeError, ValueError, OverflowError):
        return None


def list_custom(provider, key):
    if not key:
        raise ValueError('Voice storage is not configured.')
    found, token, seen = {}, None, set()
    for _ in range(25):
        path = '/voices/v1/voices?pageSize=200'
        if token:
            path += '&pageToken=' + urllib.parse.quote(str(token), safe='')
        data = provider._request('GET', path, key, timeout=30)
        if not isinstance(data, dict) or not isinstance(data.get('voices'), list):
            raise ValueError('The complete voice list could not be verified.')
        for row in data['voices']:
            if not isinstance(row, dict):
                raise ValueError('The voice list contains an unreadable entry.')
            source, name = str(row.get('source') or '').upper(), str(row.get('displayName') or '')
            if source == 'SYSTEM':
                continue
            if source not in {'IVC', 'TVD', 'PVC'} and not name.startswith(('Cloned_', 'Custom_', 'lisan-tmp-')):
                continue
            vid = row.get('voiceId')
            if not isinstance(vid, str) or not vid or len(vid) > 256:
                raise ValueError('The voice list contains an invalid voice ID.')
            found[vid] = {'id': vid, 'name': name, 'source': source or 'UNKNOWN'}
        token = data.get('nextPageToken')
        if not token:
            return list(found.values())
        if str(token) in seen:
            break
        seen.add(str(token))
    raise ValueError('The complete voice list could not be verified. Nothing was deleted.')


def delete_custom(provider, key, vid):
    try:
        provider._request('DELETE', '/voices/v1/voices/' + urllib.parse.quote(vid, safe=''), key, timeout=30)
        return True
    except Exception as error:
        if getattr(error, 'code', None) == 404:
            return True
        return False


def voice_ids(job):
    return set(job.get('voices_pending_delete') or []) | set((job.get('dub') or {}).get('voices', {}).values())


def classify(voices, projects, saved, now, days):
    refs = {}
    for job in projects:
        for vid in voice_ids(job):
            if isinstance(vid, str) and vid:
                refs.setdefault(vid, []).append(job)
    library = {}
    for row in saved:
        vid = row.get('elevenlabs_voice_id')
        if vid:
            library.setdefault(vid, []).append(row)
    rows = []
    for voice in voices:
        row = dict(voice)
        jobs = refs.get(row['id'], [])
        expiry = [expires_at(j, days) for j in jobs]
        busy = any(j.get('status') in BUSY for j in jobs)
        if row['id'] in library:
            category = 'saved'
        elif busy:
            category = 'active'
        elif jobs:
            if any(j.get('status') == 'done' and j.get('edit_assets') and (e is None or e > now) for j, e in zip(jobs, expiry)):
                category = 'retained'
            else:
                category = 'expired'
        elif row['name'].startswith('lisan-tmp-'):
            category = 'orphan'
        elif row['name'].startswith(('Cloned_', 'Custom_')):
            category = 'short_untracked'
        else:
            category = 'other'
        row.update(category=category, projects=[{'id': j['id'], 'name': j.get('name') or j.get('filename') or j['id'],
                    'expires_at': e} for j, e in zip(jobs, expiry)], saved_rows=len(library.get(row['id'], [])),
                    saved_ids=sorted(str(r['id']) for r in library.get(row['id'], [])),
                    selectable=not busy and category not in {'active', 'other'})
        rows.append(row)
    counts = {category: sum(r['category'] == category for r in rows) for category in
              ('saved', 'active', 'retained', 'expired', 'orphan', 'short_untracked', 'other')}
    return {'voices': sorted(rows, key=lambda r: (r['category'], r['name'], r['id'])), 'counts': counts,
            'total': len(rows), 'retention_days': days, 'checked_at': now}


def fingerprint(rows):
    return hashlib.sha256(json.dumps(sorted(rows, key=lambda r: r['id']), sort_keys=True, allow_nan=False).encode()).hexdigest()


class Manager:
    def __init__(self, root, provider_list, projects, saved, save_project, delete_saved, provider_delete,
                 busy=lambda: False, context=nullcontext, clock=time.time):
        self.root, self.provider_list, self.projects, self.saved = Path(root), provider_list, projects, saved
        self.save_project, self.delete_saved, self.provider_delete = save_project, delete_saved, provider_delete
        self.busy, self.context, self.clock = busy, context, clock

    def inventory(self):
        result = classify(self.provider_list(), self.projects(), self.saved(), self.clock(), retention_days(self.root))
        result['cleanup_busy'] = bool(self.busy())
        result['pending_cleanup'] = []
        folder = self.root / 'voice_cleanup'
        if folder.exists():
            for path in sorted(folder.glob('*.json')):
                data = json.loads(path.read_text(encoding='utf-8'))
                if data.get('state') == 'started':
                    result['pending_cleanup'].append(path.stem)
        return result

    def _path(self, token):
        if not isinstance(token, str) or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
            raise ValueError('Invalid cleanup confirmation. Refresh the inventory.')
        return self.root / 'voice_cleanup' / (token + '.json')

    def preview(self, ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(not isinstance(v, str) for v in ids) or len(set(ids)) != len(ids):
            raise ValueError('Select between 1 and 100 distinct voices.')
        with _LOCK, self.context():
            if self.busy():
                raise ValueError('Wait for running dubbing and correction jobs to finish.')
            inventory = self.inventory()
            selected = [row for row in inventory['voices'] if row['id'] in ids]
            if len(selected) != len(ids) or any(not r['selectable'] for r in selected):
                raise ValueError('The selection changed or includes a protected voice. Refresh the inventory.')
            token = uuid.uuid4().hex
            plan = {'state': 'preview', 'ids': ids, 'snapshot': fingerprint(selected),
                    'expires': self.clock() + 300, 'completed': [], 'failed': []}
            atomic(self._path(token), plan)
            return {'token': token, 'voices': selected, 'count': len(selected)}

    def status(self, token):
        data = json.loads(self._path(token).read_text(encoding='utf-8'))
        return {'state': data['state'], 'total': len(data['ids']), 'deleted': len(data['completed']),
                'failed': len(data['failed']), 'token': token}

    def execute(self, token):
        with _LOCK, self.context():
            path = self._path(token)
            plan = json.loads(path.read_text(encoding='utf-8'))
            if plan['state'] == 'complete':
                return self.status(token)
            if self.busy():
                raise ValueError('Wait for running dubbing and correction jobs to finish.')
            if plan['state'] == 'preview':
                if self.clock() > plan['expires']:
                    raise ValueError('The cleanup preview expired. Refresh the inventory.')
                current = [r for r in self.inventory()['voices'] if r['id'] in plan['ids']]
                if fingerprint(current) != plan['snapshot']:
                    raise ValueError('The voice inventory changed. Preview the cleanup again.')
            plan['state'] = 'started'
            atomic(path, plan)  # durable intent BEFORE references or provider voices are changed
            plan['failed'] = []
            for vid in plan['ids']:
                if vid in plan['completed']:
                    continue
                try:
                    self.delete_saved(vid)
                    for job in self.projects():
                        if vid not in voice_ids(job):
                            continue
                        pending = list(job.get('voices_pending_delete') or [])
                        if vid not in pending:
                            pending.append(vid)
                        job['voices_pending_delete'] = pending
                        voices = (job.get('dub') or {}).get('voices', {})
                        job.setdefault('dub', {})['voices'] = {s: v for s, v in voices.items() if v != vid}
                        self.save_project(job)
                    if not self.provider_delete(vid):
                        raise ValueError('Provider deletion could not be confirmed.')
                    for job in self.projects():
                        if vid in (job.get('voices_pending_delete') or []):
                            job['voices_pending_delete'] = [v for v in job['voices_pending_delete'] if v != vid]
                            self.save_project(job)
                    plan['completed'].append(vid)
                except Exception:
                    plan['failed'].append(vid)
                atomic(path, plan)
            if not plan['failed']:
                plan['state'] = 'complete'
            atomic(path, plan)
            return self.status(token)
