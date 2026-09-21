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
