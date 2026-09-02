import assert from 'node:assert/strict';
import test from 'node:test';

import { decideQaReview } from './check-qa-review.mjs';

const HEAD_SHA = 'd6be8c86bca9ee6e8bc3bc7d2a6e091faeed73c3';
const PR_AUTHOR = 'pr-author';

function approval(overrides = {}) {
  return {
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
