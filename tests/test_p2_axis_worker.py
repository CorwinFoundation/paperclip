"""Offline subprocess and resume checks for BEAAA-2299."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from harness.p2_axis_worker import run_axis


class AxisWorkerTests(unittest.TestCase):
    def test_success_runs_in_child_and_resumes_without_running(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / 'p2_emission_P2.1.json'
            parent = os.getpid()
            result = run_axis(lambda: {'dim_status': 'ok', 'dim_raw': 12,
                                      'pid': os.getpid()},
                              checkpoint=checkpoint, identity='corpus:model:config')
            self.assertNotEqual(result['pid'], parent)
            def forbidden():
                raise AssertionError('checkpoint should avoid rerun')
            self.assertEqual(run_axis(forbidden, checkpoint=checkpoint,
                                      identity='corpus:model:config'), result)
            changed = run_axis(lambda: {'dim_status': 'ok', 'dim_raw': 17},
                               checkpoint=checkpoint, identity='new-corpus:model:config')
            self.assertEqual(changed['dim_raw'], 17)
            self.assertEqual(json.loads(checkpoint.read_text())['result'], changed)

    def test_timeout_does_not_checkpoint_and_next_axis_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / 'axis.json'
            started = time.monotonic()
            result = run_axis(lambda: time.sleep(60), timeout_seconds=.05,
                              checkpoint=checkpoint, identity='a')
            self.assertEqual(result['dim_status'], 'inconclusive')
            self.assertLess(time.monotonic() - started, 3)
            self.assertFalse(checkpoint.exists())
            self.assertEqual(run_axis(lambda: {'dim_status': 'ok'}, identity='b'),
                             {'dim_status': 'ok'})

    def test_exception_is_inconclusive_and_not_cached(self):
        def fail():
            raise RuntimeError('secret transport text')
        result = run_axis(fail, identity='a')
        self.assertEqual(result['dim_status'], 'inconclusive')
        self.assertNotIn('secret', json.dumps(result))

    def test_corrupt_checkpoint_is_recomputed(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / 'axis.json'
            checkpoint.write_text('{broken')
            self.assertEqual(run_axis(lambda: {'dim_status': 'ok'},
                                      checkpoint=checkpoint, identity='a'),
                             {'dim_status': 'ok'})

    def test_failed_new_identity_preserves_old_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / 'axis.json'
            run_axis(lambda: {'dim_status': 'ok', 'dim_raw': 1},
                     checkpoint=checkpoint, identity='old')
            original = checkpoint.read_bytes()
            run_axis(lambda: {'dim_status': 'inconclusive'},
                     checkpoint=checkpoint, identity='new')
            self.assertEqual(checkpoint.read_bytes(), original)

    def test_abrupt_exit_is_inconclusive(self):
        result = run_axis(lambda: os._exit(7), identity='a')
        self.assertEqual(result['dim_status'], 'inconclusive')

    def test_invalid_timeout_does_not_start_worker(self):
        for timeout in (0, -1, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                run_axis(lambda: {}, identity='a', timeout_seconds=timeout)


if __name__ == '__main__':
    unittest.main()
