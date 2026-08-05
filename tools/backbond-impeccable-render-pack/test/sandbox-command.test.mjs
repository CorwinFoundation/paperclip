import assert from 'node:assert/strict';
import test from 'node:test';
import { dockerCreateArgs, PREVIEW_ARGV, RENDER_ARGV } from '../tools/sandbox-command.mjs';

test('fixed commands enforce offline read-only bounded execution', () => {
  for (const [mode, expected] of [['render', RENDER_ARGV], ['preview', PREVIEW_ARGV]]) {
    const args = dockerCreateArgs({ image: 'candidate@sha256:abc', inputDir: '/tmp/input', mode, name: 'bounded' });
    const joined = args.join(' ');
    assert.match(joined, /--network none/);
    assert.match(joined, /dst=\/input,readonly/);
    assert.match(joined, /--read-only/);
    assert.match(joined, /--tmpfs \/output:.*size=64m/);
    assert.match(joined, /--cpus 2 --memory 1024m --memory-swap 1024m --pids-limit 256/);
    assert.match(joined, /--cap-drop ALL --security-opt no-new-privileges/);
    assert.ok(!joined.includes('TOKEN'));
    assert.deepEqual(args.slice(-expected.length), expected);
  }
  assert.throws(() => dockerCreateArgs({ image: 'x', inputDir: '/tmp', mode: 'shell', name: 'x' }), /denied/);
});
