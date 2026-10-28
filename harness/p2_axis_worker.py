"""Bounded POSIX workers for whole P2 aggregates (BEAAA-2299).

Runner objects may contain closures, so fork is intentional. Invoke from the
single-threaded scan orchestrator, before starting client background threads.
Only successful whole-axis results are reusable checkpoints. The caller owns
identity construction, including model/configuration and corpus fingerprints.
"""
from __future__ import annotations

import json
import math
import multiprocessing
import os
from pathlib import Path
import signal
import tempfile
from typing import Any, Callable


def _write_json(path: Path, value: dict[str, Any]) -> None:
    """Replace a complete JSON file atomically, retaining the old file on error."""
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _worker(run: Callable[[], dict[str, Any]], output: Path) -> None:
    os.setsid()
    try:
        result = run()
        if not isinstance(result, dict):
            raise TypeError('axis runner must return a dict')
        _write_json(output, result)
    except Exception as exc:
        # Do not persist exception text: transport exceptions may contain secrets.
        _write_json(output, {'dim_status': 'inconclusive',
                             'reason': f'axis worker failed ({type(exc).__name__})'})


def _stop(process: multiprocessing.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        # Child may not yet have established its process group.
        if process.is_alive():
            process.kill()
    finally:
        process.join()


def run_axis(
    run: Callable[[], dict[str, Any]], *, timeout_seconds: float = 1800,
    checkpoint: Path | None = None, identity: str,
) -> dict[str, Any]:
    """Run one whole aggregate, or reuse its successful matching checkpoint.

    Timeout and worker failure produce inconclusive results; neither overwrites
    a successful checkpoint. The orchestrator can continue with remaining axes.
    Files are local JSON only; no runner or credential is serialized.
    """
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError('axis timeout must be finite and positive')
    if not identity:
        raise ValueError('axis checkpoint identity must be nonempty')
    if checkpoint is not None:
        checkpoint = Path(checkpoint)
        try:
            saved = json.loads(checkpoint.read_text(encoding='utf-8'))
            if (isinstance(saved, dict) and saved.get('identity') == identity
                    and isinstance(saved.get('result'), dict)
                    and saved['result'].get('dim_status') == 'ok'):
                return saved['result']
        except (OSError, ValueError):
            pass
    context = multiprocessing.get_context('fork')
    with tempfile.TemporaryDirectory(prefix='p2-axis-') as directory:
        output = Path(directory) / 'result.json'
        process = context.Process(target=_worker, args=(run, output))
        process.start()
        try:
            process.join(timeout_seconds)
            if process.is_alive():
                _stop(process)
                return {'dim_status': 'inconclusive', 'reason': 'axis timeout exceeded'}
            if process.exitcode != 0 or not output.is_file():
                return {'dim_status': 'inconclusive', 'reason': 'axis worker exited without result'}
            result = json.loads(output.read_text(encoding='utf-8'))
        finally:
            if process.is_alive():
                _stop(process)
            process.close()
    if checkpoint is not None and result.get('dim_status') == 'ok':
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        _write_json(checkpoint, {'identity': identity, 'result': result})
    return result
