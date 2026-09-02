#!/usr/bin/env node
/**
 * check-qa-review.mjs
 * Validates that a `backbond-qa` APPROVED review is evidence-backed.
 *
 * A qualifying approval must:
 *   1. Have a body of at least MIN_BODY_LENGTH characters.
 *   2. Include the PR head SHA so the reviewer affirms the exact commit.
 *
 * If `backbond-qa` has not reviewed the PR, the check passes (this gate
 * validates review quality, not whether a review is required).
 *
 * Posts a `qa-review-evidence` check-run. Add it to branch protection
 * as a required status check to gate merges.
 *
 * Env: GH_TOKEN (github.token), GH_REPO, PR_NUMBER, PR_HEAD_SHA
 * Exit: always 0 — the check-run is the signal.
 */

import { fileURLToPath } from 'node:url';

const QA_REVIEWER = 'backbond-qa';
const MIN_BODY_LENGTH = 50;
const CHECK_NAME = 'qa-review-evidence';

async function ghFetch(path, token, opts = {}) {
  const url = `https://api.github.com${path}`;
  const res = await fetch(url, {
    ...opts,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      ...opts.headers,
    },
  });
  if (!res.ok) {
    throw new Error(`GitHub ${res.status} on ${path}: ${await res.text().catch(() => '(unreadable)')}`);
  }
  return res.json();
}

async function fetchAllReviews(token, repo, prNumber) {
  const reviews = [];
  let page = 1;
  for (;;) {
    const batch = await ghFetch(
      `/repos/${repo}/pulls/${prNumber}/reviews?per_page=100&page=${page}`,
      token,
    );
    if (!Array.isArray(batch) || batch.length === 0) break;
    reviews.push(...batch);
    if (batch.length < 100) break;
    page += 1;
  }
  return reviews;
}

async function postCheckRun(token, repo, headSha, conclusion, summary) {
  await ghFetch(`/repos/${repo}/check-runs`, token, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: CHECK_NAME,
      head_sha: headSha,
      status: 'completed',
      conclusion,
      output: {
        title: conclusion === 'success' ? 'QA review gate passed' : 'QA review gate failed',
        summary,
      },
    }),
  });
}

async function main() {
  const { GH_TOKEN, GH_REPO, PR_NUMBER, PR_HEAD_SHA } = process.env;

  if (!GH_TOKEN || !GH_REPO || !PR_NUMBER || !PR_HEAD_SHA) {
    console.error('ERROR: GH_TOKEN, GH_REPO, PR_NUMBER, PR_HEAD_SHA are all required');
    process.exit(1);
  }

  const prNumber = parseInt(PR_NUMBER, 10);
  if (!Number.isInteger(prNumber) || prNumber <= 0) {
    console.error('ERROR: PR_NUMBER must be a positive integer');
    process.exit(1);
  }

  if (!/^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/.test(GH_REPO)) {
    console.error('ERROR: GH_REPO must be in owner/repo format');
    process.exit(1);
  }

  const reviews = await fetchAllReviews(GH_TOKEN, GH_REPO, prNumber);

  // Latest APPROVED review from backbond-qa wins; earlier ones may have been superseded.
  const qaApprovals = reviews
    .filter(r => r.user?.login === QA_REVIEWER && r.state === 'APPROVED')
    .sort((a, b) => new Date(b.submitted_at) - new Date(a.submitted_at));

  const latest = qaApprovals[0];

  if (!latest) {
    console.log(`[qa-review-gate] no APPROVED review from ${QA_REVIEWER} — gate not triggered`);
    await postCheckRun(GH_TOKEN, GH_REPO, PR_HEAD_SHA, 'success',
      `No APPROVED review from \`${QA_REVIEWER}\` on this PR.`);
    process.exit(0);
  }

  const body = latest.body ?? '';
  const hasSubstantiveBody = body.length >= MIN_BODY_LENGTH;
  const containsSha = body.includes(PR_HEAD_SHA);

  if (hasSubstantiveBody && containsSha) {
    console.log(`[qa-review-gate] APPROVED review from ${QA_REVIEWER} is evidence-backed`);
    await postCheckRun(GH_TOKEN, GH_REPO, PR_HEAD_SHA, 'success',
      `\`${QA_REVIEWER}\` APPROVED review is evidence-backed: ${body.length} chars, head SHA present.`);
  } else {
    const reasons = [];
    if (!hasSubstantiveBody) reasons.push(`body is ${body.length} chars (minimum ${MIN_BODY_LENGTH})`);
    if (!containsSha) reasons.push(`body omits head SHA \`${PR_HEAD_SHA.slice(0, 12)}\``);

    console.error(`[qa-review-gate] APPROVED review from ${QA_REVIEWER} fails evidence check: ${reasons.join('; ')}`);
    await postCheckRun(GH_TOKEN, GH_REPO, PR_HEAD_SHA, 'failure',
      `\`${QA_REVIEWER}\` APPROVED review does not meet evidence requirements: ${reasons.join('; ')}.\n\n` +
      `A qualifying review must include at least ${MIN_BODY_LENGTH} characters of review notes ` +
      `and explicitly cite the head SHA \`${PR_HEAD_SHA}\`.`);
  }

  process.exit(0);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(e => { console.error(e.message); process.exit(1); });
}
