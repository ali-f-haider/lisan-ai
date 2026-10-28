"""What the server sends out over the network, and to whom (Railway bills every GB sent out: $0.05 per GB).

Measured inside the server, the same way resource_meter.py measures memory:
- every byte our code sends over an encrypted connection is counted under the name of the place it goes to
  (Gemini, R2 storage, Supabase, the voice services, ...): ssl.SSLSocket.send is wrapped, which also covers sendall,
  urllib, requests and boto3;
- bytes sent on plain connections to visitors (pages, downloads, previews: Railway's edge adds the encryption) are counted
  as "visitors" when the web server sends them through Python's own sockets. Our server runs uvicorn with uvloop, which
  sends from C, so in practice what visitors download lands in "not attributed";
- the container's total (from /proc/net/dev) is read too, so whatever is not attributed shows up as "not attributed".
Counts are kept per UTC day in DATA_DIR/net_meter.json (written every 10 minutes, no network use). Never raises into
the code it measures."""
import datetime as _dt
import json
import os
import socket
import ssl
import threading
import time
import weakref
from pathlib import Path

try:
    from config import DATA_DIR
except Exception:                       # tests without the app's config
    DATA_DIR = Path(os.environ.get("DATA_DIR") or "/tmp")

FILE = Path(DATA_DIR) / "net_meter.json"
KEEP_DAYS = 70
FLUSH_SEC = 600
try:
    NET_USD_PER_GB = float(os.environ.get("RAILWAY_NET_USD_PER_GB") or 0.05)
except ValueError:
    NET_USD_PER_GB = 0.05
APP_PORT = int(os.environ.get("PORT") or 8000)

# where the bytes go: the first rule whose text is in the host name gives the name shown on the admin page
GROUPS = (
    ("generativelanguage.googleapis.com", "Gemini (Google AI)"),
    ("r2.cloudflarestorage.com", "R2 storage (backups, temporary files)"),
    ("supabase.co", "Supabase (database)"),
    ("inworld.ai", "Inworld (voices)"),
    ("elevenlabs.io", "ElevenLabs (voices)"),
    ("fal.run", "fal (lip-sync)"), ("fal.ai", "fal (lip-sync)"), ("fal.media", "fal (lip-sync)"),
    ("aliyuncs.com", "Alibaba (lip-sync)"),
    ("youtube", "YouTube (publishing)"), ("www.googleapis.com", "YouTube / Google (publishing)"),
    ("oauth2.googleapis.com", "Google sign-in"),
    ("railway.app", "Railway API"), ("railway.com", "Railway API"),
    ("huggingface.co", "Hugging Face (models)"), ("hf.co", "Hugging Face (models)"),
    ("resend.com", "Resend (email)"),
    ("sentry.io", "Sentry (error reports)"),
    ("stripe.com", "Stripe (payments)"),
)
VISITORS = "visitors (pages, downloads, previews)"

_lock = threading.Lock()
_counts = {}                    # label -> bytes since the last flush
_labels = weakref.WeakKeyDictionary()
_installed = False
_proc_last = [None]             # last /proc/net/dev reading (tx bytes)


def label_for_host(host):
    h = str(host or "").lower().strip(".")
    if not h:
        return "other"
    for needle, name in GROUPS:
        if needle in h:
            return name
    return h


def _add(label, n):
    if n and n > 0:
        with _lock:
            _counts[label] = _counts.get(label, 0) + int(n)


def _ssl_label(sock):
    try:
        lab = _labels.get(sock)
    except TypeError:
        lab = None
    if lab is None:
        lab = label_for_host(getattr(sock, "server_hostname", None) or _peer(sock))
        try:
            _labels[sock] = lab
        except TypeError:
            pass
    return lab


def _peer(sock):
    try:
        return sock.getpeername()[0]
    except Exception:
        return ""


def _plain_label(sock):
    try:
        lab = _labels.get(sock)
    except TypeError:
        lab = None
    if lab is None:
        try:
            local_port = sock.getsockname()[1]
        except Exception:
            local_port = None
        lab = VISITORS if local_port == APP_PORT else label_for_host(_peer(sock))
        try:
            _labels[sock] = lab
        except TypeError:
            pass
    return lab


def install():
    """Wrap the socket send functions once. Safe to call more than once."""
    global _installed
    if _installed:
        return
    _installed = True
    ssl_send = ssl.SSLSocket.send

    def send_ssl(self, data, flags=0):
        n = ssl_send(self, data, flags)
        try:
            if getattr(self, "_sslobj", None) is not None:      # without it the plain send below counts it
                _add(_ssl_label(self), n)
        except Exception:
            pass
        return n
    ssl.SSLSocket.send = send_ssl

    plain_send, plain_sendall = socket.socket.send, socket.socket.sendall

    def send_plain(self, data, *a):
        n = plain_send(self, data, *a)
        try:
            if not isinstance(self, ssl.SSLSocket) or getattr(self, "_sslobj", None) is None:
                _add(_plain_label(self), n)
        except Exception:
            pass
        return n

    def sendall_plain(self, data, *a):
        r = plain_sendall(self, data, *a)
        try:
            if not isinstance(self, ssl.SSLSocket) or getattr(self, "_sslobj", None) is None:
                _add(_plain_label(self), len(memoryview(data).cast("B")))
        except Exception:
            pass
        return r
    socket.socket.send = send_plain
    socket.socket.sendall = sendall_plain
    threading.Thread(target=_flush_loop, daemon=True, name="net-meter").start()


def proc_tx_bytes(path="/proc/net/dev"):
    """Bytes the container sent on all interfaces except loopback, or None."""
    try:
        total = 0
        for line in Path(path).read_text().splitlines()[2:]:
            name, rest = line.split(":", 1)
            if name.strip() == "lo":
                continue
            total += int(rest.split()[8])
        return total
    except Exception:
        return None


def _load():
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def flush(now=None):
    """Adds what was counted since the last flush (and the container's own total) to today's record. No network."""
    day = (now or _dt.datetime.utcnow()).date().isoformat()
    with _lock:
        counts = dict(_counts)
        _counts.clear()
    tx = proc_tx_bytes()
    total_delta = None
    if tx is not None:
        last = _proc_last[0]
        if last is None:
            total_delta = 0              # the first reading: what was sent before it is not known
        elif tx < last:
            total_delta = tx             # the counter started again (a new container)
        else:
            total_delta = tx - last
        _proc_last[0] = tx
    if not counts and not total_delta:
        return
    data = _load()
    rec = data.setdefault(day, {})
    for k, v in counts.items():
        rec[k] = int(rec.get(k, 0)) + int(v)
    if total_delta:
        rec["_total"] = int(rec.get("_total", 0)) + int(total_delta)
    keep = sorted(data)[-KEEP_DAYS:]
    data = {k: data[k] for k in keep}
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, FILE)
    except Exception as ex:
        print(f"[net-meter] could not save: {ex}")


def _flush_loop():
    if _proc_last[0] is None:
        _proc_last[0] = proc_tx_bytes()      # the starting point of the container's own total
    while True:
        time.sleep(FLUSH_SEC)
        try:
            flush()
        except Exception as ex:
            print(f"[net-meter] flush error: {ex}")


def summary(start=None, end=None):
    """For the admin page: GB sent out from `start` (a date, default the billing month's first day) to today, by place."""
    try:
        flush()
    except Exception:
        pass
    if start is None:
        try:
            import resource_meter
            start, end = resource_meter._cycle_bounds()
        except Exception:
            start = _dt.datetime.utcnow().date().replace(day=1)
    data = _load()
    first = start.isoformat() if hasattr(start, "isoformat") else str(start)
    days = [d for d in sorted(data) if d >= first]
    by = {}
    total = 0
    for d in days:
        for k, v in data[d].items():
            if k == "_total":
                total += int(v)
            else:
                by[k] = by.get(k, 0) + int(v)
    gb = lambda b: round(b / 1e9, 3)
    named = sum(by.values())
    rows = sorted(by.items(), key=lambda kv: -kv[1])
    out = {"since": first, "days_measured": len(days), "usd_per_gb": NET_USD_PER_GB,
           "total_gb": gb(total) if total else None,
           "total_usd": round(total / 1e9 * NET_USD_PER_GB, 2) if total else None,
           "by_place": [{"place": k, "gb": gb(v), "usd": round(v / 1e9 * NET_USD_PER_GB, 3)} for k, v in rows[:15]],
           "not_attributed_gb": gb(total - named) if total and total > named else 0.0,
           "per_day_gb": {d: gb(int(data[d].get("_total", 0)) or sum(int(v) for k, v in data[d].items() if k != "_total")) for d in days[-10:]}}
    return out
