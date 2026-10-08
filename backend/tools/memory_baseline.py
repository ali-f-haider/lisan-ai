"""Offline import/RSS probe. Never imported by the app; never loads model weights.

Run from backend: python tools/memory_baseline.py --json memory-baseline.json
Optional: --site-packages PATH (a local environment), --first-use (separate probe
of libraries used on demand). RSS is current residency, not peak or container
usage. Deltas include transitive imports and are sensitive to import order/OS.
"""
import argparse
import ast
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import ctypes
import importlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HEAVY = {'fastapi', 'numpy', 'soundfile', 'scipy', 'torch', 'faster_whisper',
         'av', 'elevenlabs', 'urllib3', 'botocore', 'stripe'}


def startup_imports(root=ROOT):
    """Follow eager local-module imports in source order; defer function/if bodies.

    Conditional monitoring imports are deliberately absent with empty credentials.
    A try's primary import branch is included; failed imports are reported below.
    """
    visited, seen, result = set(), set(), []

    def visit(path):
        if path in visited:
            return
        visited.add(path)
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))

        def statements(nodes):
            for node in nodes:
                if isinstance(node, ast.Try):
                    statements(node.body)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
                    for module in names:
                        base = module.split('.')[0]
                        local = root / (base + '.py')
                        if local.is_file():
                            visit(local)
                        elif base in HEAVY and base not in seen:
                            seen.add(base)
                            if module == 'scipy':
                                module = 'scipy.signal'
                            result.append((module, f'{path.name}:{node.lineno}'))
        statements(tree.body)
    visit(root / 'main.py')
    return result


def rss_bytes():
    if sys.platform == 'win32':
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(counters.WorkingSetSize)
    if sys.platform.startswith('linux'):
        return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    raise RuntimeError('Current RSS measurement is supported on Windows and Linux only.')


def offline_guard(data_dir):
    """Suppress .env reads, outbound sockets and app startup threads in the child."""
    stack = ExitStack()
    original_exists = Path.exists
    stack.enter_context(patch.object(Path, 'exists', lambda path: False if path.name in ('.env', '.env.local') else original_exists(path)))

    def blocked(*args, **kwargs):
        raise RuntimeError('Network is disabled by the offline memory probe.')
    stack.enter_context(patch.object(socket.socket, 'connect', blocked))
    stack.enter_context(patch.object(socket.socket, 'connect_ex', blocked))
    stack.enter_context(patch.object(socket, 'create_connection', blocked))
    stack.enter_context(patch.object(threading.Thread, 'start', lambda self: None))
    stack.enter_context(patch.dict(os.environ, {
        'DATA_DIR': str(data_dir), 'LOCAL_DEV': '0', 'AWS_EC2_METADATA_DISABLED': 'true',
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'RESOURCE_METER': '0',
        'OMP_NUM_THREADS': '4', 'MKL_NUM_THREADS': '4', 'WHISPER_COMPUTE': 'int8',
        'GEMINI_API_KEY': '', 'ELEVENLABS_API_KEY': '', 'INWORLD_API_KEY': '',
        'SUPABASE_URL': '', 'SUPABASE_ANON_KEY': '', 'SUPABASE_SERVICE_KEY': '',
        'STRIPE_SECRET_KEY': '', 'STRIPE_WEBHOOK_SECRET': '', 'SENTRY_DSN': '',
        'FAL_API_KEY': '', 'FAL_ADMIN_KEY': '', 'HF_TOKEN': '', 'RESEND_API_KEY': '',
        'R2_ACCOUNT_ID': '', 'RAILWAY_API_TOKEN': '', 'APP_PASSWORD': '', 'ADMIN_PASSWORD': '',
    }))
    return stack


def measure_import(module, source):
    before = rss_bytes()
    already = module in sys.modules
    start = time.perf_counter()
    error = None
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            importlib.import_module(module)
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'[:400]
    after = rss_bytes()
    return {'module': module, 'source': source, 'rss_mib': round(after / 1048576, 3),
            'delta_mib': round((after - before) / 1048576, 3),
            'seconds': round(time.perf_counter() - start, 4), 'already_loaded': already,
            'status': 'unavailable' if error else 'ok', 'error': error}


def worker(first_use=False, site_packages=None):
    if site_packages:
        sys.path.insert(0, site_packages)
    sys.path.insert(0, str(ROOT))
    plan = [('pyannote.audio', 'whisper_service.get_speaker_turns'),
            ('torchaudio', 'speaker detection dependency'),
            ('demucs.separate', 'separation subprocess; NOT the server process'),
            ('transformers', 'optional dependency; NOT an eager app import'),
            ('boto3', 'r2_backup._client'), ('fal_client', 'on-demand repair'),
            ('dashscope', 'on-demand lip sync'), ('sentry_sdk', 'conditional monitoring')] if first_use else startup_imports() + [('main', 'main.py, dummy credentials; startup threads disabled')]
    with tempfile.TemporaryDirectory(prefix='lisan-memory-', ignore_cleanup_errors=True) as directory, offline_guard(directory):
        baseline = round(rss_bytes() / 1048576, 3)
        rows = [measure_import(module, source) for module, source in plan]
        versions = {}
        for package in ('numpy', 'scipy', 'torch', 'torchaudio', 'faster-whisper', 'ctranslate2',
                        'pyannote.audio', 'transformers', 'demucs', 'fastapi', 'onnxruntime'):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = None
        return {'profile': 'first_use_libraries' if first_use else 'eager_startup',
                'platform': platform.platform(), 'python': platform.python_version(),
                'baseline_mib': baseline, 'versions': versions, 'rows': rows,
                'note': 'Measured here: current RSS in MiB. Failed imports can leave allocations; their deltas are NOT successful library costs. No model weights loaded.'}


def child_environment():
    # Do not pass credentials or user PYTHONPATH to the fresh interpreter.
    keep = {'PATH', 'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'TEMP', 'TMP',
            'USERPROFILE', 'HOME', 'LOCALAPPDATA', 'APPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)'}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(PYTHONUTF8='1', OMP_NUM_THREADS='4', MKL_NUM_THREADS='4')
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path, help='Save measured results locally.')
    parser.add_argument('--site-packages', help='Use this existing local dependency directory.')
    parser.add_argument('--first-use', action='store_true', help='Also probe deferred libraries in a separate fresh process.')
    parser.add_argument('--worker', choices=('startup', 'first_use'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker == 'first_use', args.site_packages), allow_nan=False))
        return
    reports = []
    for profile in (['startup', 'first_use'] if args.first_use else ['startup']):
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', profile]
        if args.site_packages:
            command += ['--site-packages', str(Path(args.site_packages).resolve())]
        result = subprocess.run(command, cwd=ROOT, env=child_environment(), capture_output=True,
                                text=True, encoding='utf-8', timeout=180)
        if result.returncode:
            raise RuntimeError(f'Memory probe exited {result.returncode}: {result.stderr[-1000:]}')
        report = json.loads(result.stdout)
        reports.append(report)
        print(f"\n{report['profile']} | {report['platform']} | Python {report['python']}")
        print(f"Fresh interpreter + probe: {report['baseline_mib']:.3f} MiB")
        print(f"{'Import':28} {'RSS MiB':>10} {'Added MiB':>10} {'Seconds':>9}  Status")
        for row in report['rows']:
            print(f"{row['module']:28} {row['rss_mib']:10.3f} {row['delta_mib']:10.3f} {row['seconds']:9.4f}  {row['status']}")
            if row['error']:
                print('  ' + row['error'])
    if args.json:
        args.json.write_text(json.dumps(reports, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
