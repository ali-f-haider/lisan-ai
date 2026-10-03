"""What the server really costs on Railway, measured from inside the container.

Railway bills memory and CPU by the minute (memory about $0.000231 per GB-minute, CPU about $0.000463 per vCPU-minute),
so the cost of a dub is how much extra memory it held and for how long, plus the CPU it burned. Until now the admin
page only had a guessed price per video minute. This module measures it:

  * a light sampler (every 30 s) writes one record per hour to resource_hours.jsonl in DATA_DIR: average / highest /
    lowest memory, memory in GB-minutes, CPU seconds. From it the admin page shows the idle floor (what the server holds
    while nobody is dubbing) and the month's usage so far, which can be compared with the Railway bill;
  * every dubbing job (long dub analysis and dubbing, short dub transcription and voice generation) is wrapped in a
    meter: peak memory, the memory it held ABOVE what the server held when the job started (that part is the job's own
    cost), the CPU it used, and the resulting dollars. One line per job goes to resource_jobs.jsonl, and long dubs also
    get a `resource_use` line in their step log.

Memory is read from the container's cgroup (the same source as the admin memory card), CPU from the cgroup's usage counter.
Both fall back to the processes visible in the container. Everything here is best effort and never raises into the
caller: a job is never slowed down or broken by its own measuring.

Switch off with RESOURCE_METER=0. Prices can be changed with RAILWAY_MEM_USD_PER_GB_MIN / RAILWAY_CPU_USD_PER_VCPU_MIN.
"""
import datetime as _dt
import functools
import json
import os
import threading
import time
from pathlib import Path

from config import DATA_DIR

ENABLED = os.environ.get("RESOURCE_METER", "1").strip() not in ("0", "false", "False", "")


def _env_float(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


MEM_USD_PER_GB_MIN = _env_float("RAILWAY_MEM_USD_PER_GB_MIN", 0.000231)
CPU_USD_PER_VCPU_MIN = _env_float("RAILWAY_CPU_USD_PER_VCPU_MIN", 0.000463)
CYCLE_DAY = int(_env_float("RAILWAY_CYCLE_DAY", 27))          # day of the month a Railway billing month starts
SAMPLE_SEC = 30
JOB_SAMPLE_SEC = 3
FLUSH_SEC = 300
KEEP_DAYS = 90

HOURS_FILE = Path(DATA_DIR) / "resource_hours.jsonl"
JOBS_FILE = Path(DATA_DIR) / "resource_jobs.jsonl"
_GB = 1024.0 ** 3
_lock = threading.Lock()


# ---------------------------------------------------------------- reading the container

def read_mem():
    """-> (used_gb, anon_gb) of THIS container, or (None, None)."""
    try:
        with open("/sys/fs/cgroup/memory.current") as f:
            used = int(f.read().strip())
        anon = None
        try:
            with open("/sys/fs/cgroup/memory.stat") as f:
                for line in f:
                    p = line.split()
                    if len(p) == 2 and p[0] == "anon":
                        anon = int(p[1])
                        break
        except Exception:
            pass
        return used / _GB, (anon / _GB if anon is not None else None)
    except Exception:
        pass
    try:
        with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as f:
            used = int(f.read().strip())
        anon = None
        try:
            with open("/sys/fs/cgroup/memory/memory.stat") as f:
                for line in f:
                    p = line.split()
                    if len(p) == 2 and p[0] == "rss":
                        anon = int(p[1])
                        break
        except Exception:
            pass
        return used / _GB, (anon / _GB if anon is not None else None)
    except Exception:
        pass
    try:                                              # last resort: every process visible in the container
        total_kb = 0
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    with open(f"/proc/{d}/status") as f:
                        for line in f:
                            if line.startswith("VmRSS:"):
                                total_kb += int(line.split()[1])
                                break
                except Exception:
                    continue
        return total_kb / 1024.0 / 1024.0, total_kb / 1024.0 / 1024.0
    except Exception:
        return None, None


def read_cpu_sec():
    """Total CPU seconds this container has used since it started, or None."""
    try:
        with open("/sys/fs/cgroup/cpu.stat") as f:
            for line in f:
                p = line.split()
                if len(p) == 2 and p[0] == "usage_usec":
                    return int(p[1]) / 1e6
    except Exception:
        pass
    for path in ("/sys/fs/cgroup/cpuacct/cpuacct.usage", "/sys/fs/cgroup/cpu/cpuacct.usage"):
        try:
            with open(path) as f:
                return int(f.read().strip()) / 1e9
        except Exception:
            continue
    try:
        t = os.times()
        return t.user + t.system + t.children_user + t.children_system
    except Exception:
        return None


# ---------------------------------------------------------------- one record per hour

class _Hours:
    def __init__(self):
        self.seg = str(int(time.time()))              # one running process = one segment (restarts never overwrite)
        self.cur = None
        self.last_t = None
        self.last_cpu = None
        self.last_flush = 0.0

    def _fresh(self, hour):
        return {"h": hour, "seg": self.seg, "n": 0, "min": 0.0, "gb_min": 0.0, "anon_gb_min": 0.0,
                "max_gb": 0.0, "min_gb": 1e9, "cpu_sec": 0.0}

    def sample(self):
        now = time.time()
        used, anon = read_mem()
        cpu = read_cpu_sec()
        if used is None:
            return
        hour = _dt.datetime.utcfromtimestamp(now).strftime("%Y-%m-%dT%H")
        if self.cur is None or self.cur["h"] != hour:
            self.flush(force=True)
            self.cur = self._fresh(hour)
        dt = min(max(now - self.last_t, 0.0), 120.0) if self.last_t else 0.0
        self.last_t = now
        c = self.cur
        c["n"] += 1
        c["min"] += dt / 60.0
        c["gb_min"] += used * dt / 60.0
        c["anon_gb_min"] += (anon if anon is not None else used) * dt / 60.0
        c["max_gb"] = max(c["max_gb"], used)
        c["min_gb"] = min(c["min_gb"], used)
        if cpu is not None:
            if self.last_cpu is not None:
                c["cpu_sec"] += (cpu - self.last_cpu) if cpu >= self.last_cpu else cpu     # the counter restarts with the container
            self.last_cpu = cpu
        if now - self.last_flush >= FLUSH_SEC:
            self.flush()

    def flush(self, force=False):
        c = self.cur
        if not c or not c["n"]:
            return
        self.last_flush = time.time()
        try:
            rec = dict(c)
            if rec["min_gb"] > 1e8:
                rec["min_gb"] = 0.0
            with _lock:
                HOURS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(HOURS_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in rec.items()}) + "\n")
        except Exception as ex:
            print(f"[resource] could not save the hourly record: {ex}")


_hours = _Hours()
_started = False


def _loop():
    while True:
        try:
            _hours.sample()
        except Exception as ex:
            print(f"[resource] sample failed: {ex}")
        time.sleep(SAMPLE_SEC)


def start():
    """Start the background sampler (once). Safe to call from main.py at startup."""
    global _started
    if not ENABLED or _started:
        return
    _started = True
    try:
        _compact()
    except Exception:
        pass
    threading.Thread(target=_loop, daemon=True, name="resource-sampler").start()


def _read_lines(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    except Exception as ex:
        print(f"[resource] could not read {path}: {ex}")
    return out


def _hour_records():
    """{(hour, seg): latest record}"""
    recs = {}
    for r in _read_lines(HOURS_FILE):
        if isinstance(r, dict) and r.get("h"):
            recs[(r["h"], r.get("seg", ""))] = r
    cur = _hours.cur
    if cur and cur.get("n"):
        recs[(cur["h"], cur["seg"])] = dict(cur, min_gb=(cur["min_gb"] if cur["min_gb"] < 1e8 else 0.0))
    return recs


def _compact():
    """Keep the files small: drop what is older than KEEP_DAYS and keep only the last record of every hour segment."""
    cutoff = (_dt.datetime.utcnow() - _dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H")
    if HOURS_FILE.exists() and HOURS_FILE.stat().st_size > 400_000:
        recs = {k: v for k, v in _hour_records().items() if k[0] >= cutoff}
        tmp = HOURS_FILE.with_suffix(".tmp")
        with _lock:
            with open(tmp, "w", encoding="utf-8") as f:
                for k in sorted(recs):
                    f.write(json.dumps(recs[k]) + "\n")
            os.replace(tmp, HOURS_FILE)
    if JOBS_FILE.exists() and JOBS_FILE.stat().st_size > 2_000_000:
        keep = [r for r in _read_lines(JOBS_FILE) if str(r.get("ts", "")) >= cutoff]
        tmp = JOBS_FILE.with_suffix(".tmp")
        with _lock:
            with open(tmp, "w", encoding="utf-8") as f:
                for r in keep:
                    f.write(json.dumps(r) + "\n")
            os.replace(tmp, JOBS_FILE)


# ---------------------------------------------------------------- one job

class JobMeter:
    """with JobMeter("longdub_dub", job_id) as m: ...   (m.result after the block)."""

    def __init__(self, kind, job_id):
        self.kind, self.job_id = kind, str(job_id or "")
        self.result = None
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        try:
            if ENABLED:
                self.t0 = time.time()
                self.start_gb, _ = read_mem()
                self.cpu0 = read_cpu_sec()
                self.peak = self.start_gb or 0.0
                self.gb_min = 0.0
                self.extra_gb_min = 0.0
                self._last = self.t0
                self._thread = threading.Thread(target=self._run, daemon=True, name="job-meter")
                self._thread.start()
        except Exception:
            self._thread = None
        return self

    def _tick(self):
        now = time.time()
        used, _ = read_mem()
        if used is None:
            return
        dt = min(max(now - self._last, 0.0), 60.0) / 60.0
        self._last = now
        self.peak = max(self.peak, used)
        self.gb_min += used * dt
        self.extra_gb_min += max(0.0, used - (self.start_gb or 0.0)) * dt

    def _run(self):
        while not self._stop.wait(JOB_SAMPLE_SEC):
            try:
                self._tick()
            except Exception:
                pass

    def __exit__(self, *exc):
        try:
            if self._thread is not None:
                self._stop.set()
                self._thread.join(timeout=2)
                self._tick()
                secs = time.time() - self.t0
                cpu1 = read_cpu_sec()
                cpu_min = max(0.0, (cpu1 - self.cpu0)) / 60.0 if (cpu1 is not None and self.cpu0 is not None) else 0.0
                usd = self.extra_gb_min * MEM_USD_PER_GB_MIN + cpu_min * CPU_USD_PER_VCPU_MIN
                self.result = {
                    "ts": _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"), "kind": self.kind, "job": self.job_id,
                    "sec": round(secs, 1), "start_gb": round(self.start_gb or 0.0, 2), "peak_gb": round(self.peak, 2),
                    "gb_min": round(self.gb_min, 2), "extra_gb_min": round(self.extra_gb_min, 2),
                    "cpu_min": round(cpu_min, 2), "usd": round(usd, 5), "ok": exc[0] is None}
                self.result["text"] = (f"{self.kind}: {secs / 60:.1f} min, memory {self.result['start_gb']:.1f} -> peak {self.result['peak_gb']:.1f} GB, "
                                       f"{self.extra_gb_min:.1f} GB-min above the start, {cpu_min:.1f} vCPU-min, "
                                       f"Railway cost about ${usd:.4f}")
                with _lock:
                    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with open(JOBS_FILE, "a", encoding="utf-8") as f:
                        f.write(json.dumps({k: v for k, v in self.result.items() if k != "text"}) + "\n")
                print(f"[resource] {self.job_id} {self.result['text']}")
        except Exception as ex:
            print(f"[resource] could not finish measuring {self.kind}: {ex}")
        return False


def metered(kind, job_id_of, after=None):
    """Decorator: measure every call of the function as one job. job_id_of(*args, **kwargs) gives the job id.
    after(args, kwargs, result_dict) may log the result (it must not raise; it is wrapped anyway)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            if not ENABLED:
                return fn(*a, **k)
            try:
                jid = job_id_of(*a, **k)
            except Exception:
                jid = ""
            m = JobMeter(kind, jid)
            try:
                with m:
                    return fn(*a, **k)
            finally:
                if after and m.result:
                    try:
                        after(a, k, m.result)
                    except Exception:
                        pass
        return wrapper
    return deco


# ---------------------------------------------------------------- reports

def _cycle_bounds(today=None):
    today = today or _dt.datetime.utcnow().date()
    y, m = today.year, today.month
    day = min(CYCLE_DAY, 28)
    start = _dt.date(y, m, day)
    if start > today:
        m, y = (12, y - 1) if m == 1 else (m - 1, y)
        start = _dt.date(y, m, day)
    ny, nm = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return start, _dt.date(ny, nm, day)


def _hour_usd(r):
    return float(r.get("gb_min", 0.0)) * MEM_USD_PER_GB_MIN + float(r.get("cpu_sec", 0.0)) / 60.0 * CPU_USD_PER_VCPU_MIN


def period_stats(start_day, end_day):
    """Measured usage between two UTC dates (inclusive, ISO strings): the numbers the business page needs.
    -> {"hours": measured hours covered, "usage_usd", "idle_usd_per_hour", "jobs": {kind: {"n","avg_usd","total_usd"}}}"""
    recs = [r for k, r in _hour_records().items() if start_day <= k[0][:10] <= end_day]
    covered = sum(float(r.get("min", 0.0)) for r in recs) / 60.0
    usage = sum(_hour_usd(r) for r in recs)
    jobs = {}
    jobs_usd = 0.0
    for j in _read_lines(JOBS_FILE):
        d = str(j.get("ts", ""))[:10]
        if not (start_day <= d <= end_day):
            continue
        e = jobs.setdefault(j.get("kind", "?"), {"n": 0, "total_usd": 0.0})
        e["n"] += 1
        e["total_usd"] += float(j.get("usd", 0.0))
        jobs_usd += float(j.get("usd", 0.0))
    for e in jobs.values():
        e["avg_usd"] = round(e["total_usd"] / e["n"], 5) if e["n"] else 0.0
        e["total_usd"] = round(e["total_usd"], 4)
    idle = max(0.0, usage - jobs_usd) / covered if covered >= 1 else None
    return {"hours": round(covered, 1), "usage_usd": round(usage, 3), "jobs_usd": round(jobs_usd, 3),
            "idle_usd_per_hour": None if idle is None else round(idle, 5), "jobs": jobs}


def summary():
    """For the admin page: now, the last 24 hours, the billing month so far and what each kind of job costs."""
    now_gb, anon_gb = read_mem()
    recs = sorted(_hour_records().values(), key=lambda r: (r["h"], r.get("seg", "")))
    last = _dt.datetime.utcnow() - _dt.timedelta(hours=24)
    last_h = last.strftime("%Y-%m-%dT%H")
    r24 = [r for r in recs if r["h"] >= last_h]
    mins = sorted(float(r.get("min_gb", 0.0)) for r in r24 if r.get("n"))
    mins_total = sum(float(r.get("min", 0.0)) for r in r24)
    out = {"enabled": ENABLED, "now_gb": None if now_gb is None else round(now_gb, 2),
           "now_anon_gb": None if anon_gb is None else round(anon_gb, 2),
           "mem_usd_per_gb_month": round(MEM_USD_PER_GB_MIN * 60 * 24 * 30, 2)}
    if r24 and mins_total > 0:
        out["last24"] = {"avg_gb": round(sum(float(r.get("gb_min", 0.0)) for r in r24) / mins_total, 2),
                         "avg_anon_gb": round(sum(float(r.get("anon_gb_min", 0.0)) for r in r24) / mins_total, 2),
                         "peak_gb": round(max(float(r.get("max_gb", 0.0)) for r in r24), 2),
                         "idle_floor_gb": round(mins[len(mins) // 2], 2) if mins else None,
                         "hours": round(mins_total / 60.0, 1)}
    start, end = _cycle_bounds()
    st = period_stats(start.isoformat(), (_dt.datetime.utcnow().date()).isoformat())
    days_total = (end - start).days
    days_gone = max((_dt.datetime.utcnow().date() - start).days + 1, 1)
    out["cycle"] = dict(st, start=start.isoformat(), end=end.isoformat(),
                        projected_usd=(round(st["usage_usd"] / st["hours"] * days_total * 24, 2) if st["hours"] >= 6 else None),
                        coverage_hours=st["hours"], days_gone=days_gone, days_total=days_total)
    return out
