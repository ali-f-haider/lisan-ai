"""Read-only memory inventory. Standard library only; never imports a worker.

Counts are a best-effort snapshot while jobs may run. They are not byte sizes.
Only counts and flags leave this module, never cache keys, tokens or values.
"""
import gc
import sys
import threading
from collections import Counter
from pathlib import Path


# Neutral diagnostic labels; source identifiers stay internal.
CONTAINERS = (
    ('app_state', 'jobs_progress', 'progress_entries'),
    ('app_state', 'USAGE', 'usage_jobs'),
    ('app_state', 'diarization_pipelines', 'speaker_detection_cache'),
    ('eleven_service', 'USER_GAINS', 'voice_gain_jobs'),
    ('eleven_service', 'VOICE_ANCHORS', 'speaker_level_jobs'),
    ('eleven_service', 'ROOM_SETTINGS', 'room_setting_jobs'),
    ('eleven_service', 'ROOM_LAST', 'room_result_jobs'),
    ('audio_enhance', '_jobs', 'enhancement_jobs'),
    ('longdub_service', '_JOBS', 'long_projects'),
    ('longdub_service', '_JOB_LOCKS', 'long_project_locks'),
    ('longdub_service', '_ACCOUNT_LOCKS', 'long_account_locks'),
    ('longdub_service', '_PREVIEW_LOCKS', 'long_preview_locks'),
    ('longdub_service', '_RUNNING', 'long_running_jobs'),
    ('shortdub_paths', '_active_operations', 'short_active_jobs'),
    ('resource_meter', '_meters', 'active_measurements'),
    ('subs_align', '_sim_cache', 'subtitle_comparisons'),
    ('main', '_job_started', 'tracked_short_jobs'),
    ('main', '_job_charges', 'charge_summaries'),
    ('main', '_abandoned_jobs', 'abandoned_jobs'),
    ('main', '_job_owner', 'job_owners'),
    ('main', '_job_owner_lookup', 'job_owner_lookups'),
    ('main', '_sessions', 'sessions'),
    ('main', '_valid_tokens', 'session_tokens'),
    ('main', '_session_users', 'session_accounts'),
    ('main', '_user_info_cache', 'account_names'),
    ('main', '_me_cache', 'landing_accounts'),
    ('main', '_wm_cache', 'watermark_accounts'),
    ('main', '_login_fails', 'login_addresses'),
    ('main', '_contact_attempts', 'contact_addresses'),
    ('main', '_rate_buckets', 'rate_groups'),
    ('main', '_ADMIN_TOKENS', 'admin_sessions'),
    ('main', '_admin_fail_all', 'admin_failed_attempts'),
    ('main', '_biz_cache', 'business_reports'),
    ('main', '_assistant_acct_cache', 'assistant_accounts'),
    ('main', '_assist_owed', 'assistant_balance_entries'),
)


def _namespace(modules, name, main_namespace):
    module = modules.get(name)
    return main_namespace if name == 'main' else (vars(module) if module is not None else {})


def _length(value):
    return len(value) if isinstance(value, (dict, list, tuple, set, frozenset)) else None


def snapshot(main_namespace, modules=None):
    modules = sys.modules if modules is None else modules
    counts = {label: _length(_namespace(modules, module, main_namespace).get(name))
              for module, name, label in CONTAINERS}
    for module, container, key, label in (
        ('voice_match_service', '_voices_cache', 'voices', 'matching_library_voices'),
        ('inworld_service', '_library', 'voices', 'voice_library_voices'),
        ('assistant_service', '_today', 'per', 'assistant_daily_accounts'),
    ):
        cache = _namespace(modules, module, main_namespace).get(container)
        counts[label] = _length(cache.get(key)) if isinstance(cache, dict) else None

    groups = main_namespace.get('_rate_buckets')
    counts['rate_callers'] = sum(len(group) for group in list(groups.values()) if isinstance(group, dict)) if isinstance(groups, dict) else None
    event_queue = main_namespace.get('_ld_event_q')
    counts['pending_project_events'] = event_queue.qsize() if event_queue is not None else None

    progress = _namespace(modules, 'app_state', main_namespace).get('jobs_progress')
    prefixes = dict.fromkeys(('transcription', 'emotion', 'generation', 'lip_sync', 'other'), 0)
    if isinstance(progress, dict):
        for key in list(progress):
            group = next((label for prefix, label in (('emotions_', 'emotion'), ('generate_', 'generation'),
                           ('lipsync_', 'lip_sync')) if isinstance(key, str) and key.startswith(prefix)), None)
            prefixes[group or ('transcription' if isinstance(key, str) and len(key) >= 20 else 'other')] += 1
    else:
        prefixes = None

    transcription = _namespace(modules, 'whisper_service', main_namespace)
    pipeline_count = counts['speaker_detection_cache']
    vad_module = _namespace(modules, 'faster_whisper.vad', main_namespace)
    vad_cache = vad_module.get('get_vad_model')
    vad_entries = None
    if vad_cache is not None and hasattr(vad_cache, 'cache_info'):
        vad_entries = int(vad_cache.cache_info().currsize)
    queue = transcription.get('_transcribe_queue')
    models = {
        'transcription_loaded': transcription.get('_model') is not None if transcription else None,
        'speaker_detection_loaded': pipeline_count > 0 if pipeline_count is not None else None,
        'activity_detection_loaded': vad_entries > 0 if vad_entries is not None else None,
        'activity_detection_cache_entries': vad_entries,
        'separation_model_location': 'separate_process',
        'separation_model_loaded': None,  # No parent model; child residency was not inspected.
        'processing_jobs': queue.running() if queue is not None else None,
        'waiting_jobs': queue.waiting_count() if queue is not None else None,
    }

    # One linear pass over GC-tracked references. No repr(), recursive sizing,
    # tracing, collection, model loading or native allocator changes.
    objects = gc.get_objects()
    object_count = len(objects)
    types = Counter(map(type, objects))
    del objects
    top = [{'type': kind.__name__, 'count': count} for kind, count in types.most_common(5)]

    native = {}
    try:
        wanted = {'Rss', 'Pss', 'Private_Clean', 'Private_Dirty', 'Anonymous'}
        for line in Path('/proc/self/smaps_rollup').read_text().splitlines():
            key, _, value = line.partition(':')
            if key in wanted:
                native[key.lower() + '_mib'] = round(int(value.split()[0]) / 1024, 3)
    except (OSError, ValueError, IndexError):
        pass
    os_threads = None
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('Threads:'):
                os_threads = int(line.split()[1])
                break
    except (OSError, ValueError, IndexError):
        pass
    return {'models': models, 'container_entries': counts, 'progress_entries_by_kind': prefixes,
            'threads': {'python_live': threading.active_count(), 'os_live': os_threads},
            'python_objects': {'gc_tracked_count': object_count, 'top_types_by_count': top,
                               'includes_native_allocations': False},
            'process_rollup_mib': native,
            'note': 'Counts are a live snapshot, not memory sizes. Null means unavailable. Native allocations need separate measurements.'}
