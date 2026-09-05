#!/usr/bin/env node
/**
 * Fail closed unless the current PR head has an evidence-backed approval from
 * a repository collaborator other than the PR author.
 *
 * Env: GH_TOKEN, GH_REPO, PR_NUMBER, PR_HEAD_SHA, PR_AUTHOR
 * Exit: always 0 after posting a check-run; the check-run is the signal.
 */

import { fileURLToPath } from 'node:url';

const CHECK_NAME = 'qa-review-evidence';
const COLLABORATOR_ASSOCIATIONS = new Set(['COLLABORATOR', 'OWNER']);

async function ghFetch(path, token, opts = {}) {
  const res = await fetch(`https://api.github.com${path}`, {
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

export function decideQaReview(reviews, headSha, prAuthor) {
  const relevantReviews = reviews.filter(review => {
    const reviewer = review.user?.login;
    return reviewer
      && reviewer.toLowerCase() !== prAuthor.toLowerCase()
      && COLLABORATOR_ASSOCIATIONS.has(review.author_association)
      && review.commit_id === headSha
      && ['APPROVED', 'CHANGES_REQUESTED'].includes(review.state);
  });

  // GitHub returns a review history, not one current state per reviewer. Reduce
  // it to each reviewer's latest decisive review on this exact head so an old
  // approval cannot mask a later request for changes.
  const latestByReviewer = new Map();
  for (const review of relevantReviews) {
    const reviewer = review.user.login.toLowerCase();
    const current = latestByReviewer.get(reviewer);
    const reviewKey = [review.submitted_at ?? '', Number(review.id ?? 0)];
    const currentKey = current
      ? [current.submitted_at ?? '', Number(current.id ?? 0)]
      : ['', -1];
    if (reviewKey[0] > currentKey[0]
        || (reviewKey[0] === currentKey[0] && reviewKey[1] > currentKey[1])) {
      latestByReviewer.set(reviewer, review);
    }
  }

  const outstandingChangeRequest = [...latestByReviewer.values()]
    .find(review => review.state === 'CHANGES_REQUESTED');
  if (outstandingChangeRequest) {
    return {
      conclusion: 'failure',
      summary: `A collaborator has an outstanding CHANGES_REQUESTED review on exact head \`${headSha}\`.`,
    };
  }

  const qualifyingApproval = [...latestByReviewer.values()].find(review => {
    const body = review.body?.trim() ?? '';
    return review.state === 'APPROVED'
      && body.length > 0
      && body.includes(headSha);
  });

  if (qualifyingApproval) {
    return {
      conclusion: 'success',
      summary: `A non-author collaborator approved exact head \`${headSha}\` with SHA-bound review evidence.`,
    };
  }

  return {
    conclusion: 'failure',
    summary: `A qualifying APPROVED review is required from a non-author collaborator for exact head \`${headSha}\`; the non-empty review body must cite that SHA.`,
  };
}

async function main() {
  const { GH_TOKEN, GH_REPO, PR_NUMBER, PR_HEAD_SHA, PR_AUTHOR } = process.env;

  if (!GH_TOKEN || !GH_REPO || !PR_NUMBER || !PR_HEAD_SHA || !PR_AUTHOR) {
    console.error('ERROR: GH_TOKEN, GH_REPO, PR_NUMBER, PR_HEAD_SHA, and PR_AUTHOR are all required');
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
  const decision = decideQaReview(reviews, PR_HEAD_SHA, PR_AUTHOR);
  if (decision.conclusion === 'success') console.log(`[qa-review-gate] ${decision.summary}`);
  else console.error(`[qa-review-gate] ${decision.summary}`);

  await postCheckRun(GH_TOKEN, GH_REPO, PR_HEAD_SHA, decision.conclusion, decision.summary);
  // The posted check-run is the merge signal; branch protection requires the
  // `qa-review-evidence` context, not this workflow job. Exiting non-zero here
  // only turns the run red and mails a failure notice, so exit 0 once the
  // check-run is posted. Every path above this line still exits 1: if we fail
  // before posting, no check-run exists and the merge stays blocked.
  process.exit(0);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(e => { console.error(e.message); process.exit(1); });
}
