"""Best-effort off-site backup of finished dubbing outputs to Cloudflare R2.

Called once per sweep of the existing _cleanup_worker loop in main.py (see
CLEANUP_INTERVAL_MIN there) -- it scans OUTPUT_DIR for "final output" files
(the same _is_final_output() definition main.py already uses to decide what
survives the 30-day retention window) and uploads any that aren't already
in the R2 bucket yet.

Entirely optional: if R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
/ R2_BUCKET_NAME aren't all set in the environment, backup_final_outputs()
is a no-op, so a deployment that hasn't configured R2 is unaffected.

Local files are never touched, moved, or deleted by this module -- R2 is a
backup copy, not the primary storage. Any failure (network, credentials,
a single file's upload) is logged and swallowed, never raised into the
caller, so a backup problem can never break the app or the cleanup sweep
it rides along with.
"""
from botocore.exceptions import ClientError, BotoCoreError
from botocore.config import Config as BotoConfig

from config import R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME

_client = None
_client_init_failed = False


def _enabled():
    return bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME)


def _get_client():
    global _client, _client_init_failed
    if _client is not None or _client_init_failed or not _enabled():
        return _client
    try:
        import boto3
        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=BotoConfig(signature_version="s3v4"),
        )
    except Exception as e:
        # boto3 missing, or a malformed credential/endpoint -- disable
        # backups for the rest of this process rather than retrying (and
        # printing) every single sweep.
        print(f"[r2-backup] could not create R2 client, backups disabled for this run: {e}")
        _client_init_failed = True
        _client = None
    return _client


def _already_backed_up(client, key):
    try:
        client.head_object(Bucket=R2_BUCKET_NAME, Key=key)
        return True
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        print(f"[r2-backup] head_object check failed for {key}: {e}")
        raise


def backup_final_outputs(output_dir, is_final_output_fn):
    """Upload any finished job output in output_dir that isn't in R2 yet.

    is_final_output_fn is main.py's _is_final_output -- passed in rather
    than imported, so this module has no import-time dependency on main.py.
    Safe to call every sweep: already-backed-up files are skipped via a
    HEAD check and never re-uploaded.
    """
    if not _enabled():
        return
    client = _get_client()
    if client is None:
        return
    try:
        candidates = [p for p in output_dir.glob("*") if p.is_file() and is_final_output_fn(p)]
    except Exception as e:
        print(f"[r2-backup] could not scan {output_dir}: {e}")
        return
    uploaded = 0
    for path in candidates:
        key = path.name
        try:
            if _already_backed_up(client, key):
                continue
        except Exception:
            continue
        try:
            client.upload_file(str(path), R2_BUCKET_NAME, key)
            uploaded += 1
        except (ClientError, BotoCoreError, OSError) as e:
            print(f"[r2-backup] upload failed for {key}: {e}")
    if uploaded:
        print(f"[r2-backup] uploaded {uploaded} new final output(s) to R2")


def upload_temp_and_get_url(local_path, key, expires_in=3600):
    """Upload a local file to R2 and hand back a time-limited presigned GET
    URL for it, without making the bucket itself public. Used to stage a
    video/audio file somewhere with a real public-ish URL before handing it
    to a third-party API (e.g. Alibaba VideoRetalk lip-sync) that requires
    an HTTP(S) URL rather than accepting raw uploaded bytes.

    key should be namespaced (e.g. "lipsync-tmp/<job_id>_video.mp4") so
    these never collide with the final-output backups above, which use the
    bare filename as their key.

    Returns the presigned URL, or None if R2 isn't configured or the upload
    fails -- the caller should treat None as "can't proceed" rather than
    retrying, same as every other best-effort R2 operation in this module.
    """
    if not _enabled():
        return None
    client = _get_client()
    if client is None:
        return None
    try:
        client.upload_file(str(local_path), R2_BUCKET_NAME, key)
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": R2_BUCKET_NAME, "Key": key},
            ExpiresIn=expires_in,
        )
    except (ClientError, BotoCoreError, OSError) as e:
        print(f"[r2-backup] temp upload failed for {key}: {e}")
        return None


def delete_temp_object(key):
    """Best-effort cleanup of an object uploaded via upload_temp_and_get_url.
    Never raises: a leftover temp object costs a little R2 storage, not
    correctness, so a cleanup failure should never fail the caller's job."""
    if not _enabled():
        return
    client = _get_client()
    if client is None:
        return
    try:
        client.delete_object(Bucket=R2_BUCKET_NAME, Key=key)
    except Exception as e:
        print(f"[r2-backup] temp object cleanup failed for {key}: {e}")


def check_reachable():
    """Lightweight, free, read-only reachability check for the admin
    dashboard's Health panel -- a head_bucket call confirms both that the
    R2 credentials are valid AND that the configured bucket actually
    exists, without listing or transferring anything. Returns "ok",
    "not_configured", or "fail"."""
    if not _enabled():
        return "not_configured"
    client = _get_client()
    if client is None:
        return "fail"
    try:
        client.head_bucket(Bucket=R2_BUCKET_NAME)
        return "ok"
    except Exception:
        return "fail"


def get_storage_usage():
    """Sums the size of every object currently in the R2 bucket, via a
    paginated list_objects_v2 walk. This is real, live usage -- but R2's
    S3-compatible API has no endpoint for the account's *plan limit*, so
    there's nothing to compute a percentage against here; the admin panel
    just shows the raw total for Ali to compare against whatever his
    Cloudflare plan actually allows. Used by /api/admin/service_usage.

    Returns {"bytes": int, "count": int} or {"error": ...}."""
    if not _enabled():
        return {"error": "R2 not configured"}
    client = _get_client()
    if client is None:
        return {"error": "could not create R2 client"}
    try:
        total_bytes = 0
        count = 0
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=R2_BUCKET_NAME):
            for obj in page.get("Contents", []):
                total_bytes += obj["Size"]
                count += 1
        return {"bytes": total_bytes, "count": count}
    except (ClientError, BotoCoreError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Nightly snapshot of the Supabase tables that hold money / customer records.
# The file backup above only covers finished video outputs -- without this, a
# deleted or corrupted Supabase project would take the credit balances, orders
# and invoices with it. Supabase Pro keeps its own daily backups too; this is
# a second, independent copy that sits in your own R2 bucket.
#
# Session tables (app_sessions, admin_sessions) are deliberately NOT copied:
# they hold login tokens and are worthless after a restore anyway.
# ---------------------------------------------------------------------------
DB_BACKUP_TABLES = (
    "profiles", "credit_orders", "credit_spends", "credit_audit",
    "subscription_invoices", "pricing_config", "user_voices",
    "consent_records", "long_dub_events", "lipsync_runs", "expiry_notices",
)
DB_BACKUP_PREFIX = "db-backups/"
DB_BACKUP_KEEP_DAYS = 30
_DB_PAGE = 1000


def _fetch_table(supabase_url, service_key, table):
    """All rows of one table via PostgREST, paged. Returns a list or raises."""
    import json as _json
    import urllib.request as _rq
    rows = []
    offset = 0
    while True:
        req = _rq.Request(
            f"{supabase_url}/rest/v1/{table}?select=*&limit={_DB_PAGE}&offset={offset}",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
        )
        with _rq.urlopen(req, timeout=60) as r:
            page = _json.loads(r.read().decode("utf-8"))
        rows.extend(page)
        if len(page) < _DB_PAGE:
            return rows
        offset += _DB_PAGE


def backup_db_tables(supabase_url, service_key, force=False):
    """Once per UTC day: write every table in DB_BACKUP_TABLES to R2 as JSON
    under db-backups/<date>/<table>.json, then drop snapshots older than
    DB_BACKUP_KEEP_DAYS. A table that fails is skipped (logged); the day is
    only marked done when every table succeeded, so a failed day is retried
    on the next sweep. force=True redoes today's copy even if it is already
    complete (the admin "Back up now" button). Never raises.

    Returns a small dict for the admin page: {"status": "done" | "already_done" |
    "incomplete" | "not_configured" | "error", "date", "rows": {table: n},
    "failed": {table: reason}, "message"}."""
    if not _enabled():
        return {"status": "not_configured", "message": "Cloudflare R2 is not configured."}
    if not supabase_url or not service_key:
        return {"status": "not_configured", "message": "Supabase is not configured."}
    client = _get_client()
    if client is None:
        return {"status": "error", "message": "Could not create the R2 client."}
    import json as _json
    import datetime as _dt
    try:
        today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
        marker = f"{DB_BACKUP_PREFIX}{today}/_complete.json"
        if not force and _already_backed_up(client, marker):
            return {"status": "already_done", "date": today, "message": "Today's backup is already complete."}
        failed = {}
        counts = {}
        for table in DB_BACKUP_TABLES:
            try:
                rows = _fetch_table(supabase_url, service_key, table)
                body = _json.dumps(rows, ensure_ascii=False, default=str).encode("utf-8")
                client.put_object(Bucket=R2_BUCKET_NAME, Key=f"{DB_BACKUP_PREFIX}{today}/{table}.json",
                                  Body=body, ContentType="application/json")
                counts[table] = len(rows)
            except Exception as e:
                reason = str(e)
                try:                      # Supabase's own explanation (e.g. "relation does not exist")
                    reason += " | " + e.read().decode("utf-8", "replace")[:200]
                except Exception:
                    pass
                failed[table] = reason[:300]
                print(f"[db-backup] {table} failed: {reason[:300]}")
        if failed:
            print(f"[db-backup] incomplete ({', '.join(failed)}); will retry next sweep")
            return {"status": "incomplete", "date": today, "rows": counts, "failed": failed,
                    "message": "Some tables could not be copied."}
        client.put_object(Bucket=R2_BUCKET_NAME, Key=marker,
                          Body=_json.dumps({"date": today, "rows": counts}).encode("utf-8"),
                          ContentType="application/json")
        print(f"[db-backup] saved {sum(counts.values())} rows from {len(counts)} tables for {today}")
        cutoff = (_dt.datetime.utcnow() - _dt.timedelta(days=DB_BACKUP_KEEP_DAYS)).strftime("%Y-%m-%d")
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=DB_BACKUP_PREFIX):
            for obj in page.get("Contents", []):
                day = obj["Key"][len(DB_BACKUP_PREFIX):].split("/", 1)[0]
                if day < cutoff:
                    client.delete_object(Bucket=R2_BUCKET_NAME, Key=obj["Key"])
        return {"status": "done", "date": today, "rows": counts, "failed": {}, "message": "Backup complete."}
    except Exception as e:
        print(f"[db-backup] error: {e}")
        return {"status": "error", "message": str(e)[:300]}


def latest_db_backup():
    """For the admin Health panel: the newest completed snapshot as
    {"date": ..., "rows": {...}} or None."""
    if not _enabled():
        return None
    client = _get_client()
    if client is None:
        return None
    try:
        import json as _json
        days = set()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=DB_BACKUP_PREFIX, Delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                days.add(cp["Prefix"][len(DB_BACKUP_PREFIX):].strip("/"))
        for day in sorted(days, reverse=True):
            try:
                o = client.get_object(Bucket=R2_BUCKET_NAME, Key=f"{DB_BACKUP_PREFIX}{day}/_complete.json")
                return _json.loads(o["Body"].read().decode("utf-8"))
            except Exception:
                continue
    except Exception:
        return None
    return None
