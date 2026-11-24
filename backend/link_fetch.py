"""Long dub from a link instead of an upload (Ali, 19 Nov 2026).

The person pastes a link to their video; the server downloads it itself, into
the same place a normal upload goes (job_dir/src.part), marking the same 8 MB
parts as received. When every part is in, the page calls the normal
/finish, so the price, the checks and everything after are exactly as for an
uploaded file.

Links that work:
  * Google Drive: a single file shared as "Anyone with the link"
    (drive.google.com/file/d/ID/..., open?id=ID, uc?id=ID). Folders do not.
  * Dropbox: a shared file link (dl=0 is turned into dl=1).
  * A direct link to a video or audio file (https://.../film.mp4).
YouTube and other video sites are refused on purpose: downloading from them
breaks their terms, they block servers, and it would make it too easy to dub
videos that are not the person's own.

Safety: the server only ever fetches public internet addresses. Every address
(and every redirect) is resolved first and refused when it points at the
server itself, the private network Railway runs on (100.64.0.0/10), cloud
metadata or any other non-public range. Never more than the 2 GB upload limit
is read, and a page (HTML) instead of a file is refused with a clear message.
"""
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (compatible; LisanAI/1.0; +https://lisanai.org)"
READ_BLOCK = 1024 * 1024
SOCKET_TIMEOUT = 60
MAX_REDIRECTS = 8
MAX_SECONDS = 3 * 60 * 60          # a download that takes longer than this is given up
URL_MAX = 2000

# what the person sees (the page translates by key; the English text is the fallback)
MSG = {
    "bad_link": "This doesn't look like a link. Paste the full link, starting with https://",
    "video_site": "Links to YouTube and other video sites can't be used. Download your video and upload the file, or use a Google Drive, Dropbox or direct file link.",
    "drive_folder": "This is a Google Drive folder. Open the video itself in Drive and copy the link of that file.",
    "not_public": "We couldn't get the video from this link. In Google Drive, set the file to \"Anyone with the link\", and check that the link opens the video itself, not a page.",
    "not_reached": "We couldn't reach this link. Check that it is correct and public, then try again.",
    "no_size": "This link doesn't say how big the file is, so we can't take it. Download the video and upload the file instead.",
    "type": "This link isn't a video or audio file we can use (MP4, MOV, MKV, WEBM, AVI, MP3, WAV, M4A, AAC, FLAC or OGG).",
    "changed": "The file at this link has changed since you started. Delete this project and start again with the link.",
    "stopped": "The download from the link stopped before the end. Press \"Try the link again\".",
}

_VIDEO_SITES = ("youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "instagram.com", "facebook.com", "fb.watch",
                "twitter.com", "x.com", "dailymotion.com", "twitch.tv")
_TYPES = {
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/x-matroska": ".mkv", "video/webm": ".webm",
    "video/x-msvideo": ".avi", "video/avi": ".avi", "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav",
    "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/aac": ".aac",
    "audio/flac": ".flac", "audio/x-flac": ".flac", "audio/ogg": ".ogg",
}


class LinkError(Exception):
    def __init__(self, key, detail=""):
        super().__init__(detail or key)
        self.key = key
        self.detail = detail

    @property
    def message(self):
        return MSG.get(self.key, MSG["not_reached"])


# ------------------------------------------------------------------ the link
def _host_is(host, domains):
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def normalise(url):
    """The address to download from, for the link the person pasted. Raises LinkError."""
    url = (url or "").strip()
    if len(url) > URL_MAX or not re.match(r"^https?://", url, re.I):
        raise LinkError("bad_link")
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if not host or "." not in host:
        raise LinkError("bad_link")
    if _host_is(host, _VIDEO_SITES):
        raise LinkError("video_site")
    if _host_is(host, ("drive.google.com", "docs.google.com", "drive.usercontent.google.com")):
        if "/folders/" in p.path or "/drive/folders" in p.path:
            raise LinkError("drive_folder")
        m = re.search(r"/file/d/([A-Za-z0-9_-]{10,})", p.path)
        fid = m.group(1) if m else (urllib.parse.parse_qs(p.query).get("id") or [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,}", fid or ""):
            raise LinkError("not_public")
        # the direct download address; confirm=t skips Drive's "can't scan this large file for viruses" page
        return "https://drive.usercontent.google.com/download?" + urllib.parse.urlencode(
            {"id": fid, "export": "download", "confirm": "t"})
    if _host_is(host, ("dropbox.com",)):
        q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if k not in ("dl", "raw")]
        q.append(("dl", "1"))
        return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q), ""))
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


def kind(url):
    """'drive' / 'dropbox' / 'direct', for the log."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if _host_is(host, ("google.com", "googleusercontent.com")):
        return "drive"
    if _host_is(host, ("dropbox.com", "dropboxusercontent.com")):
        return "dropbox"
    return "direct"


# ------------------------------------------------------------ safe fetching
def _check_public(host, port):
    """Refuse an address that is not on the public internet (the server itself, Railway's network, metadata...)."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception:
        raise LinkError("not_reached", f"cannot resolve {host}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise LinkError("not_reached", f"{host} resolves to a non-public address {ip}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def open_stream(url, start=0):
    """GET the url, following redirects by hand so every hop is checked. Returns the open response."""
    for _ in range(MAX_REDIRECTS + 1):
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise LinkError("not_reached", f"bad redirect {url[:200]}")
        _check_public(p.hostname, p.port or (443 if p.scheme == "https" else 80))
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        if start:
            headers["Range"] = f"bytes={start}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            resp = _OPENER.open(req, timeout=SOCKET_TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            if e.code in (401, 403, 404):
                raise LinkError("not_public", f"HTTP {e.code}")
            raise LinkError("not_reached", f"HTTP {e.code}")
        except LinkError:
            raise
        except Exception as e:
            raise LinkError("not_reached", str(e)[:200])
        return resp
    raise LinkError("not_reached", "too many redirects")


def _filename(resp, url):
    cd = resp.headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", cd, re.I)
    if m:
        return urllib.parse.unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I) or re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
    if m:
        return m.group(1).strip().strip('"')
    return urllib.parse.unquote(Path(urllib.parse.urlsplit(resp.geturl() or url).path).name or "")


def probe(url, allowed_exts, max_bytes):
    """Open the link and look at it: (open response, filename, size). The response is handed to download()."""
    resp = open_stream(url)
    try:
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype.startswith("text/") or ctype in ("application/json", "application/xhtml+xml"):
            raise LinkError("not_public", f"got a page ({ctype}), not a file")
        try:
            size = int(resp.headers.get("Content-Length") or 0)
        except ValueError:
            size = 0
        if size <= 0:
            raise LinkError("no_size")
        name = re.sub(r"[\\/\x00-\x1f]", "_", _filename(resp, url)).strip()[:200]
        ext = Path(name).suffix.lower()
        if ext not in allowed_exts:
            ext = _TYPES.get(ctype, "")
            if not ext:
                raise LinkError("type", f"type={ctype} name={name}")
            name = (Path(name).stem or "video") + ext
        return resp, name, size
    except Exception:
        try:
            resp.close()
        except Exception:
            pass
        raise


# --------------------------------------------------------------- download
_ACTIVE = set()                      # job ids being downloaded right now (in this server process)
_ACTIVE_LOCK = threading.Lock()


def active(job_id):
    with _ACTIVE_LOCK:
        return job_id in _ACTIVE


def start(job, resp, ld):
    """Download in the background into the job's src.part. ld = the longdub_service module."""
    with _ACTIVE_LOCK:
        if job["id"] in _ACTIVE:
            try:
                resp.close()
            except Exception:
                pass
            return False
        _ACTIVE.add(job["id"])
    threading.Thread(target=_run, args=(job, resp, ld), daemon=True, name="longdub-link").start()
    return True


def _run(job, resp, ld):
    jid = job["id"]
    size, chunk = int(job["size"]), int(ld.CHUNK_BYTES)
    total = int(job["total_chunks"])
    part = ld.job_dir(jid) / "src.part"
    got, began, last_save = 0, time.time(), 0
    try:
        with open(part, "wb") as f:
            while True:
                cur = ld.load_job(jid)
                if cur is not job or job.get("status") != "uploading":
                    return                                  # deleted (or finished) meanwhile: stop quietly
                block = resp.read(READ_BLOCK)
                if not block:
                    break
                if got + len(block) > size:
                    raise LinkError("changed", f"more than the {size} bytes announced")
                f.write(block)
                got += len(block)
                if time.time() - began > MAX_SECONDS:
                    raise LinkError("stopped", "took too long")
                done = got // chunk if got < size else total
                with ld._lock_for(jid):
                    have = set(job.get("received") or [])
                    new = [i for i in range(done) if i not in have]
                    if new:
                        job["received"] = sorted(have.union(new))
                        job["percent"] = int(100 * len(job["received"]) / max(1, total))
                if len(job["received"]) - last_save >= 8:
                    last_save = len(job["received"])
                    ld._save(job)
        if got != size:
            raise LinkError("stopped", f"got {got} of {size} bytes")
        with ld._lock_for(jid):
            job["received"] = list(range(total))
            job["percent"] = 100
            job["link"]["state"] = "done"
        ld._save(job)
        ld._ev(job, "link_download", "ok", f"{got} bytes in {time.time() - began:.0f}s from {job['link'].get('kind')}")
    except Exception as e:
        key = e.key if isinstance(e, LinkError) else "stopped"
        detail = e.detail if isinstance(e, LinkError) else str(e)[:300]
        if ld.load_job(jid) is job and job.get("status") == "uploading":
            with ld._lock_for(jid):
                job["received"] = []
                job["percent"] = 0
                job["link"].update(state="failed", error=key)
            ld._save(job)
            ld._ev(job, "link_download", "failed", f"{key}: {detail} after {got} bytes")
    finally:
        try:
            resp.close()
        except Exception:
            pass
        with _ACTIVE_LOCK:
            _ACTIVE.discard(jid)


def view(job):
    """What the page needs to know about a link download: {state, error}. A download that this server is no
    longer running (the server restarted) is reported as stopped, so the page can offer to try again."""
    link = job.get("link")
    if not isinstance(link, dict):
        return None
    state = link.get("state") or "fetching"
    err = link.get("error") or ""
    if state == "fetching" and not active(job["id"]):
        state, err = "failed", "stopped"
    return {"state": state, "error": err, "message": MSG.get(err, "") if state == "failed" else "", "kind": link.get("kind")}
