"""Durable assistant owed-credit checkpoints; no services on import."""
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import json
import os
from pathlib import Path
import threading
import uuid

from shortdub_billing import debit_confirmed

_locks = [threading.Lock() for _ in range(64)]
MAX_BYTES = 65536


class CheckpointProblem(Exception):
    pass


@contextmanager
def account_lock(uid):
    key = uuid.UUID(str(uid)).int
    with _locks[key % len(_locks)]:
        yield


def write(path, state):
    path = Path(path)
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        data = json.dumps(state, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(data) > MAX_BYTES:
            raise ValueError
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open('wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except (OSError, ValueError, TypeError):
        raise CheckpointProblem('The helper billing record could not be saved safely. Please contact support before retrying.') from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def owed_value(value):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > 2147483647:
            raise ValueError
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise CheckpointProblem('The helper billing record needs review. Please contact support before retrying.') from None


class Ledger:
    """Caller holds account_lock from recovery through the completed answer."""
    def __init__(self, data_dir, uid):
        self.uid = str(uuid.UUID(str(uid)))
        self.path = Path(data_dir) / 'assistant_payments' / (self.uid + '.json')
        try:
            if self.path.exists():
                if self.path.stat().st_size > MAX_BYTES:
                    raise ValueError
                self.state = json.loads(self.path.read_text(encoding='utf-8'))
            else:
                # Only import old debt when no new checkpoint exists. A corrupt
                # legacy file is never silently treated as no debt.
                legacy = Path(data_dir) / 'assistant_owed.json'
                prior = json.loads(legacy.read_text(encoding='utf-8')) if legacy.exists() else {}
                if not isinstance(prior, dict):
                    raise ValueError
                self.state = {'version': 1, 'uid': self.uid, 'owed': str(owed_value(prior.get(self.uid, 0))),
                              'pending': None, 'question': None}
            if not isinstance(self.state, dict) or self.state.get('uid') != self.uid or self.state.get('version') != 1:
                raise ValueError
            owed_value(self.state['owed'])
            pending = self.state.get('pending')
            if pending is not None:
                if (not isinstance(pending, dict) or type(pending.get('amount')) is not int or pending['amount'] < 1 or
                        pending['amount'] > owed_value(self.state['owed'])):
                    raise ValueError
                uuid.UUID(pending['operation_id'])
            if self.state.get('question') is not None:
                uuid.UUID(self.state['question'])
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise CheckpointProblem('The helper billing record could not be read. Please contact support before retrying.') from None

    def save(self, **updates):
        replacement = dict(self.state, **updates)
        write(self.path, replacement)
        self.state = replacement

    def settle(self, debit, balance):
        if not debit_confirmed(balance):
            raise CheckpointProblem('Your balance could not be checked. The helper is temporarily paused.')
        if self.state.get('question') is not None:
            raise CheckpointProblem('The previous helper request could not be confirmed. Please contact support before retrying.')
        pending = self.state.get('pending')
        owed = owed_value(self.state['owed'])
        if pending is None:
            whole = min(int(owed.to_integral_value(rounding=ROUND_FLOOR)), balance)
            if whole < 1:
                return balance, 0, False
            pending = {'operation_id': str(uuid.uuid4()), 'amount': whole}
            # fsync and replace BEFORE a debit. If the next save fails, disk
            # still owns this ID and amount and a restart replays it.
            self.save(pending=pending)
        try:
            confirmed = debit(pending['amount'], pending['operation_id'])
        except Exception:
            confirmed = None
        if confirmed is False:
            # A confirmed insufficient receipt is terminal and took nothing.
            # Preserve the debt, but a future funded settlement needs a new ID.
            self.save(pending=None)
            return balance, 0, True
        if not debit_confirmed(confirmed):
            return balance, 0, True
        self.save(owed=str(owed - pending['amount']), pending=None)
        return confirmed, pending['amount'], False

    def begin_question(self):
        if self.state.get('question') is not None or self.state.get('pending') is not None:
            raise CheckpointProblem('The previous helper charge is awaiting confirmation. Please retry later.')
        self.save(question=str(uuid.uuid4()))

    def finish_question(self, cost):
        value = owed_value(self.state['owed']) + owed_value(cost)
        owed_value(value)
        self.save(owed=str(value), question=None)
