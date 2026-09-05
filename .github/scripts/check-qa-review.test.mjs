import assert from 'node:assert/strict';
import test from 'node:test';

import { decideQaReview } from './check-qa-review.mjs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HEAD_SHA = 'd6be8c86bca9ee6e8bc3bc7d2a6e091faeed73c3';
const PR_AUTHOR = 'pr-author';

function approval(overrides = {}) {
  return {
    id: 1,
    user: { login: 'repo-collaborator' },
    author_association: 'COLLABORATOR',
    state: 'APPROVED',
    commit_id: HEAD_SHA,
    body: `Approved exact head ${HEAD_SHA}.`,
    submitted_at: '2026-09-02T00:00:00Z',
    ...overrides,
  };
}

test('zero reviews fail', () => {
  assert.equal(decideQaReview([], HEAD_SHA, PR_AUTHOR).conclusion, 'failure');
});

test('an approval from the PR author fails', () => {
  assert.equal(decideQaReview([approval({ user: { login: PR_AUTHOR } })], HEAD_SHA, PR_AUTHOR).conclusion, 'failure');
});

test('an approval for a different commit fails', () => {
  assert.equal(decideQaReview([approval({ commit_id: 'a'.repeat(40) })], HEAD_SHA, PR_AUTHOR).conclusion, 'failure');
});

test('a commented review that satisfies every other condition fails', () => {
  assert.equal(decideQaReview([approval({ state: 'COMMENTED' })], HEAD_SHA, PR_AUTHOR).conclusion, 'failure');
});

test('an approval with an empty body fails', () => {
  assert.equal(decideQaReview([approval({ body: '   ' })], HEAD_SHA, PR_AUTHOR).conclusion, 'failure');
});

test('an approval whose body does not cite the head SHA fails', () => {
  assert.equal(decideQaReview([approval({ body: 'Targeted checks passed for this candidate.' })], HEAD_SHA, PR_AUTHOR).conclusion, 'failure');
});

test('a non-author collaborator approval for the exact cited head succeeds', () => {
  assert.equal(decideQaReview([approval()], HEAD_SHA, PR_AUTHOR).conclusion, 'success');
});

test('a non-collaborator approval fails', () => {
  assert.equal(decideQaReview([approval({ author_association: 'CONTRIBUTOR' })], HEAD_SHA, PR_AUTHOR).conclusion, 'failure');
});

test('an org MEMBER approval fails — org membership does not imply push access', () => {
  assert.equal(decideQaReview([approval({ author_association: 'MEMBER' })], HEAD_SHA, PR_AUTHOR).conclusion, 'failure');
});

test('a later changes-requested review on the exact head invalidates an approval', () => {
  const changesRequested = approval({
    id: 2,
    state: 'CHANGES_REQUESTED',
    submitted_at: '2026-09-02T00:01:00Z',
  });
  assert.equal(
    decideQaReview([approval(), changesRequested], HEAD_SHA, PR_AUTHOR).conclusion,
    'failure',
  );
});

test('a later approval supersedes the same reviewer changes request', () => {
  const changesRequested = approval({ state: 'CHANGES_REQUESTED' });
  const laterApproval = approval({ id: 2, submitted_at: '2026-09-02T00:01:00Z' });
  assert.equal(
    decideQaReview([changesRequested, laterApproval], HEAD_SHA, PR_AUTHOR).conclusion,
    'success',
  );
});

test('one reviewer cannot approve over another reviewer outstanding changes request', () => {
  const changesRequested = approval({
    id: 2,
    user: { login: 'required-reviewer' },
    state: 'CHANGES_REQUESTED',
    submitted_at: '2026-09-02T00:01:00Z',
  });
  assert.equal(
    decideQaReview([approval(), changesRequested], HEAD_SHA, PR_AUTHOR).conclusion,
    'failure',
  );
});

// Fail-closed guard for BEAAA-29852: main() now exits 0 once the check-run is
// posted, so the pre-post error paths are the only thing keeping a broken run
// from looking like a pass. They must still exit non-zero, and they must do it
// without reaching the network.
const SCRIPT = fileURLToPath(new URL('./check-qa-review.mjs', import.meta.url));

test('missing env exits non-zero before any check-run is posted', () => {
  const r = spawnSync(process.execPath, [SCRIPT], { env: { PATH: process.env.PATH }, encoding: 'utf8' });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /GH_TOKEN, GH_REPO, PR_NUMBER, PR_HEAD_SHA, and PR_AUTHOR are all required/);
});

test('a non-numeric PR_NUMBER exits non-zero before any check-run is posted', () => {
  const env = {
    PATH: process.env.PATH,
    GH_TOKEN: 'x', GH_REPO: 'owner/repo', PR_NUMBER: 'not-a-number',
    PR_HEAD_SHA: 'deadbeef', PR_AUTHOR: 'someone',
  };
  const r = spawnSync(process.execPath, [SCRIPT], { env, encoding: 'utf8' });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /PR_NUMBER must be a positive integer/);
});
