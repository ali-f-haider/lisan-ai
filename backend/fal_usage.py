"""Read-only fal wallet and inpainting usage, with truthful unavailable states.

API contract: https://fal.ai/docs/platform-apis/v1/account/billing
https://fal.ai/docs/platform-apis/v1/models/usage (admin-scoped API key).
"""
import datetime
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_lock = threading.Lock()
_cached = (0, {})


def _get(path, key):
    req = urllib.request.Request('https://api.fal.ai/v1/' + path,
                                 headers={'Authorization': 'Key ' + key})
    with urllib.request.urlopen(req, timeout=12) as response:
        return json.load(response)


def read(api_key='', fetch=None):
    global _cached
    fetch = fetch or _get
    key = os.environ.get('FAL_ADMIN_KEY') or api_key
    with _lock:
        if time.time() - _cached[0] < 60:
            return dict(_cached[1])
        result = {'balance': None, 'currency': 'USD', 'month_cost': None,
                  'available': False, 'note': '', 'checked_at': time.time()}
        if not key:
            result['note'] = 'Add a fal admin API key as FAL_ADMIN_KEY to read the remaining wallet balance.'
            return result
        try:
            credits = fetch('account/billing?expand=credits', key).get('credits') or {}
            balance = float(credits['current_balance'])
            if not math.isfinite(balance):
                raise ValueError('Invalid balance')
            result.update(balance=balance, currency=credits['currency'], available=True,
                          note='Live fal account wallet, shared by all fal models; not Lisan user credits.')
        except urllib.error.HTTPError as ex:
            result['note'] = ('fal requires an admin-scoped key: set FAL_ADMIN_KEY in Railway.'
                              if ex.code in (401, 403) else 'fal billing is temporarily unavailable.')
        except Exception:
            result['note'] = 'fal billing is temporarily unavailable. No balance could be verified.'
        try:
            import music_fill
            start = datetime.datetime.now(datetime.timezone.utc).date().replace(day=1).isoformat()
            query = urllib.parse.urlencode({'endpoint_id': music_fill.MODEL, 'start': start,
                                           'expand': 'summary', 'limit': 1000})
            usage = fetch('models/usage?' + query, key)
            if usage.get('has_more') or usage.get('next_cursor'):
                raise ValueError('Usage response is incomplete')
            rows = usage.get('summary')
            if not isinstance(rows, list):
                raise ValueError('Usage summary is missing')
            result['month_cost'] = sum(float(r['cost_total']) for r in rows if r.get('currency') == 'USD')
            result['usage'] = [{'quantity': r['quantity'], 'unit': r['unit']} for r in rows]
        except Exception:
            result['usage_note'] = 'Inpainting usage could not be verified from fal.'
        _cached = (time.time(), result)
        return dict(result)
