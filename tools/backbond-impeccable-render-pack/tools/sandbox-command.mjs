import path from 'node:path';

export const OUTPUT_CAP_BYTES = 64 * 1024 * 1024;
export const RENDER_ARGV = ['render', '--input', '/input', '--output', '/output'];
export const PREVIEW_ARGV = ['preview', '--input', '/input', '--output', '/output', '--plan', '/input/patch-plan.json'];

export function dockerCreateArgs({ image, inputDir, mode, name }) {
  if (!['render', 'preview'].includes(mode)) throw new Error('mode denied');
  const argv = mode === 'render' ? RENDER_ARGV : PREVIEW_ARGV;
  return [
    'create', '--name', name, '--network', 'none', '--read-only', '--user', '65532:65532',
    '--mount', `type=bind,src=${path.resolve(inputDir)},dst=/input,readonly`,
    '--tmpfs', '/output:rw,noexec,nosuid,nodev,size=64m,mode=700,uid=65532,gid=65532',
    '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=128m,mode=700,uid=65532,gid=65532',
    '--shm-size', '256m', '--cpus', '2', '--memory', '1024m', '--memory-swap', '1024m', '--pids-limit', '256',
    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
    '--env', 'HOME=/tmp', '--env', 'XDG_CONFIG_HOME=/tmp/config', '--env', 'XDG_CACHE_HOME=/tmp/cache', '--env', 'LANG=C.UTF-8', '--env', 'TZ=UTC',
    image, ...argv,
  ];
}
