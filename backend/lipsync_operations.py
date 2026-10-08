"""Durable lip-sync intent/outcome records. Refund rule: credits return whenever no video was delivered."""
import hashlib
import json
import os
from pathlib import Path
import uuid

from credit_billing import rpc_result
from shortdub_billing import debit_confirmed
from shortdub_operations import RequestProblem, audio_receipt, read
from assistant_billing import write as durable_write, CheckpointProblem


def write(path, record):
    try:
        durable_write(path, record)
    except CheckpointProblem:
        raise RequestProblem('We could not save this request safely. Please contact support before retrying.', 503) from None


def paths(output, job_id, operation_id):
    from shortdub_operations import operation_path
    try:
        operation_id = str(uuid.UUID(str(operation_id)))
    except (ValueError, TypeError, AttributeError):
        raise RequestProblem('Please reload this page before starting lip-sync.', 400) from None
    operation = operation_path(output, job_id, operation_id)
    folder = Path(output) / (job_id + '_lipsync_requests')
    return folder / operation.name, Path(output) / (job_id + '_lipsync_active.json')


def work(req, resolution, assets, watermark):
    try:
        hashes = audio_receipt(assets)
        encoded = json.dumps({'job_id': req.job_id, 'resolution': resolution, 'audio': hashes,
                              'watermark': bool(watermark)}, sort_keys=True, allow_nan=False).encode()
        return hashlib.sha256(encoded).hexdigest()
    except (OSError, ValueError, RequestProblem):
        raise RequestProblem('We could not find the video or dubbed audio. Please generate the dubbing again.', 400,
                             operation_complete=True) from None


def replay(record, output):
    if record['status'] == 'done':
        try:
            current = audio_receipt([Path(output) / name for name in record.get('audio', {})])
        except (OSError, RequestProblem):
            current = {}
        if not current or current != record.get('audio'):
            raise RequestProblem('This lip-sync take is no longer available. Nothing was charged again.', 410,
                                 operation_complete=True)
    if record['status'] in ('done', 'failed', 'running'):
        return dict(record['result'], replayed=True)
    if record['status'] in ('starting', 'unknown'):
        raise RequestProblem('This lip-sync result could not be confirmed. Please contact support before starting another take.', 503)
    if record['status'] == 'refused':
        raise RequestProblem(record['error'], record.get('http_status', 409), operation_complete=True)
    return None


def run(output, req, uid, fingerprint, prepare, charge, launch, progress, rpc, test_mode=False):
    """The existing project lock covers checking the active job through thread start."""
    path, active_path = paths(output, req.job_id, getattr(req, 'operation_id', None))
    operation_id = path.stem
    record = read(path)
    if record is not None:
        if record.get('uid') != uid or record.get('work') != fingerprint:
            raise RequestProblem('This request ID belongs to different lip-sync work. Please contact support before retrying.', 409)
        answer = replay(record, output)
        if answer is not None:
            if record['status'] == 'running' and not progress:
                raise RequestProblem('This lip-sync result could not be confirmed. Please contact support before starting another take.', 503)
            return answer
    active = read(active_path)
    if active and active.get('operation_id') != operation_id:
        previous_path, _ = paths(output, req.job_id, active.get('operation_id'))
        previous = read(previous_path)
        if previous is None or previous.get('status') not in ('done', 'failed', 'refused'):
            raise RequestProblem('Lip-sync is already running for this project. Please wait before starting another take.', 409,
                                 operation_complete=True)
    if progress and progress.get('status') == 'processing' and not (active and active.get('operation_id') == operation_id):
        raise RequestProblem('Lip-sync is already running for this project. Please wait before starting another take.', 409,
                             operation_complete=True)
    if record is None:
        amount = prepare()
        if type(amount) is not int or amount < 0 or (amount == 0 and not test_mode):
            raise RequestProblem('The lip-sync price could not be checked. Please try again later.', 503)
        record = {'operation_id': operation_id, 'uid': uid, 'work': fingerprint, 'status': 'created', 'amount': amount}
        write(path, record)
    # Exclude a second intent even while the first debit reply is unresolved.
    write(active_path, {'operation_id': operation_id, 'uid': uid})
    if record['status'] == 'created':
        if not test_mode:
            existing = rpc_result(rpc, 'lisan_credit_begin', {'p_operation_id': operation_id, 'p_uid': uid,
                'p_kind': 'debit', 'p_amount': record['amount'], 'p_debit_id': None})
            if not existing or existing.get('operation_id') != operation_id or existing.get('uid') != uid or type(existing.get('amount')) is not int or existing.get('amount') != record['amount'] or existing.get('kind') != 'debit':
                raise RequestProblem('Lip-sync payments are temporarily paused. Retry the same request later.', 503)
            if existing.get('status') != 'pending':
                record.update(status='refused', http_status=410,
                    error='This earlier lip-sync request has no available saved result. Nothing was charged again.')
                write(path, record)
                raise RequestProblem(record['error'], 410, operation_complete=True)
        record['status'] = 'charging'
        write(path, record)
    if record['status'] == 'charging':
        paid = 0 if test_mode else charge(record['amount'], operation_id)
        if paid is False:
            record.update(status='refused', http_status=402, error='You do not have enough credits for lip-sync. Add credits and start a new take.')
            write(path, record)
            raise RequestProblem(record['error'], 402, operation_complete=True)
        if not debit_confirmed(paid):
            raise RequestProblem('Payment could not be confirmed. Retry this same request; no lip-sync generation has started.', 503)
        record.update(status='paid', balance_after=paid)
        write(path, record)
    if record['status'] != 'paid':
        raise RequestProblem('This lip-sync request needs review. Please contact support before retrying.', 503)
    write(active_path, {'operation_id': operation_id, 'uid': uid})
    result = {'status': 'started', 'operation_id': operation_id, 'credits_charged': record['amount'],
              'resolution': getattr(req, 'resolution', '')}
    record.update(status='starting', result=result)
    write(path, record)
    # Persist the running state BEFORE launch. The runner alone marks a known
    # outcome; a restart never authorizes another provider against this intent.
    record['status'] = 'running'
    write(path, record)
    try:
        launch(path)
    except Exception:
        record['status'] = 'unknown'
        write(path, record)
        raise RequestProblem('This lip-sync result could not be confirmed. Please contact support before starting another take.', 503) from None
    return result


def finish(path, output, progress, refund=None):
    """refund(amount, operation_id) returns a confirmed receipt or None; it must be safe to repeat."""
    record = read(path)
    if not record:
        return
    if progress.get('status') == 'done':
        result_file = (progress.get('result') or {}).get('video')
        if not isinstance(result_file, str) or Path(result_file).name != result_file:
            raise RequestProblem('The lip-sync output needs review.', 503)
        record['audio'] = audio_receipt([Path(output) / result_file])
        record.update(status='done', progress={'status': 'done', 'percent': 100, 'message': 'Lip-sync complete.',
                                               'result': {'video': result_file}, 'error': None})
    elif progress.get('status') == 'error':
        message = 'Lip-sync could not finish. Please contact support if you need help.'
        amount = record.get('amount')
        # Owner's rule: credits come back whenever the customer received NO video (provider failure or our own
        # finishing failure). A delivered video, good or poor, keeps the charge. An unclear outcome (restart,
        # unreadable status) is never refunded automatically: it waits for a person.
        if refund is not None and type(amount) is int and amount > 0:
            try:
                confirmed = refund(amount, record['operation_id']) is not None
            except Exception:
                confirmed = False
            record['refund'] = {'amount': amount, 'confirmed': confirmed}
            message = ('Lip-sync could not finish. Your credits were returned.' if confirmed else
                       'Lip-sync could not finish. Your credits will be returned. If they do not appear shortly, please contact support.')
        record.update(status='failed', progress={'status': 'error', 'percent': 0, 'message': message,
             'error': message, 'result': None})
        progress.update(message=message, error=message)   # the live page shows the same honest sentence
    else:
        record['status'] = 'unknown'
    write(path, record)
    return record['status']


def saved_progress(output, job_id):
    try:
        _, active_path = paths(output, job_id, uuid.UUID(int=0))
        active = read(active_path)
        if not active:
            return None
        path, _ = paths(output, job_id, active['operation_id'])
        record = read(path)
        if record and record.get('status') in ('done', 'failed'):
            return dict(record['progress'], operation_id=record['operation_id'], operation_complete=True)
        return {'status': 'error', 'percent': 0, 'error': 'The lip-sync result could not be confirmed. Please contact support before starting another take.',
                'operation_id': active['operation_id'], 'operation_complete': False}
    except (RequestProblem, OSError, ValueError, KeyError):
        return {'status': 'error', 'percent': 0, 'error': 'The lip-sync result needs review. Please contact support before starting another take.',
                'operation_complete': False}


def maintain_requests(output):
    """Keep durable intent folders until the owner chooses a retention policy."""
    output = Path(output)
    for folder in output.glob('*_lipsync_requests'):
        if folder.is_dir() and folder.resolve().parent == output.resolve():
            os.utime(folder, None)
