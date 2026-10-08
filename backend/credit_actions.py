"""New transactional credit actions. No network, configuration or workers on import."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import uuid
from urllib.error import HTTPError

from credit_billing import credit_amount, number


class ActionProblem(Exception):
    def __init__(self, message, status=503, *, complete=False):
        self.status = status
        self.complete = complete
        super().__init__(message)


def call(rpc, name, args):
    """Retry transport failures with the exact same identity; never an old money path."""
    for attempt in range(2):
        try:
            result = rpc(name, args, strict=True)
            if isinstance(result, list) and len(result) == 1:
                result = result[0]
            if isinstance(result, dict):
                return result
        except HTTPError as ex:
            try:
                detail = json.loads(ex.read().decode('utf-8'))
            except (ValueError, OSError, AttributeError):
                detail = {}
            if ex.code == 404 or (isinstance(detail, dict) and detail.get('code') == 'PGRST202'):
                label = ('The adjustment function is not installed. Credit adjustments are temporarily paused.'
                         if name.startswith('lisan_admin_') else
                         'Subscription credit delivery is temporarily paused. Please try again later.')
                raise ActionProblem(label) from None
        except Exception:
            pass
    raise ActionProblem('This credit action could not be confirmed. It is temporarily paused; retry the same request later.')


def adjustment_request(body):
    if not isinstance(body, dict) or isinstance(body.get('delta'), bool):
        raise ValueError('Choose an account, enter a whole credit adjustment from 1 to 10000, and give a reason.')
    try:
        delta = Decimal(str(body.get('delta')))
        magnitude = number(abs(delta), 'Adjustment', integer=True, maximum=10000)
        uid = str(uuid.UUID(str(body.get('uid'))))
        operation_id = str(uuid.UUID(str(body.get('operation_id'))))
        reason = body.get('reason')
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
            raise ValueError
    except Exception:
        raise ValueError('Choose an account, enter a whole credit adjustment from 1 to 10000, give a reason, and reload the page if needed.') from None
    return {'p_operation_id': operation_id, 'p_uid': uid,
            'p_delta': magnitude if delta > 0 else -magnitude, 'p_reason': reason.strip()}


def adjust(rpc, body):
    args = adjustment_request(body)
    for name in ('lisan_admin_begin', 'lisan_admin_adjust'):
        result = call(rpc, name, args)
        if result.get('status') == 'mismatch':
            raise ActionProblem('This request ID belongs to another adjustment. Nothing was changed; contact support before retrying.', 409)
        if (result.get('operation_id') != args['p_operation_id'] or result.get('uid') != args['p_uid'] or
                type(result.get('requested_delta')) is not int or result.get('requested_delta') != args['p_delta'] or result.get('reason') != args['p_reason']):
            raise ActionProblem('The adjustment receipt could not be verified. Please retry the same request later.')
    if result.get('status') != 'done':
        raise ActionProblem('Credit adjustments are temporarily paused. Please retry the same request later.')
    try:
        before = number(result.get('balance_before'), 'Previous balance', zero=True, integer=True)
        after = number(result.get('balance_after'), 'New balance', zero=True, integer=True)
    except ValueError:
        raise ActionProblem('The adjustment receipt could not be verified. Please retry the same request later.') from None
    actual = result.get('actual_delta')
    if type(actual) is not int or actual != after - before or after != max(0, before + args['p_delta']):
        raise ActionProblem('The adjustment receipt could not be verified. Please retry the same request later.')
    return {'ok': True, 'operation_id': args['p_operation_id'], 'operation_complete': True,
            'new_credits': after, 'adjusted_credits': actual, 'replayed': result.get('replayed') is True}


def period_value(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('A valid billing period is required.')
    try:
        if isinstance(value, (int, float, Decimal)) or (isinstance(value, str) and value.isdigit()):
            value = datetime.fromtimestamp(float(value), timezone.utc)
        elif isinstance(value, str):
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError
        return value.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError, TypeError, OSError):
        raise ValueError('A valid billing period is required.') from None


def subscription_ready(rpc):
    try:
        return call(rpc, 'lisan_subscription_ready', {}).get('ready') is True
    except ActionProblem:
        return False


def subscription_grant(rpc, uid, invoice_id, amount, period_start, period_end):
    amount = credit_amount(amount)
    uid = str(uuid.UUID(str(uid)))
    if not isinstance(invoice_id, str) or not 1 <= len(invoice_id) <= 255:
        raise ValueError('A valid invoice is required.')
    start, end = period_value(period_start), period_value(period_end)
    if datetime.fromisoformat(end) <= datetime.fromisoformat(start):
        raise ValueError('A valid billing period is required.')
    args = {'p_invoice_id': invoice_id, 'p_uid': uid, 'p_amount': amount,
            'p_period_start': start, 'p_period_end': end}
    for name in ('lisan_subscription_begin', 'lisan_subscription_grant'):
        result = call(rpc, name, args)
        if result.get('status') == 'mismatch':
            return {'status': 'mismatch'}
        try:
            receipt_start, receipt_end = period_value(result.get('period_start')), period_value(result.get('period_end'))
        except ValueError:
            raise ActionProblem('The subscription receipt could not be verified. Credit delivery is temporarily paused.') from None
        if (result.get('invoice_id') != invoice_id or result.get('uid') != uid or type(result.get('amount')) is not int or result.get('amount') != amount or
                receipt_start != start or receipt_end != end):
            raise ActionProblem('The subscription receipt could not be verified. Credit delivery is temporarily paused.')
        if result.get('status') in ('stale', 'legacy_review'):
            return {'status': result['status']}
    if result.get('status') != 'done' or type(result.get('subscription_after')) is not int or result.get('subscription_after') != amount:
        raise ActionProblem('Subscription credit delivery is temporarily paused. Please try again later.')
    return {'status': 'done', 'replayed': result.get('replayed') is True, 'credits': amount}
