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
  const qualifyingApproval = reviews.find(review => {
    const reviewer = review.user?.login;
    const body = review.body?.trim() ?? '';
    return review.state === 'APPROVED'
      && reviewer
      && reviewer.toLowerCase() !== prAuthor.toLowerCase()
      && COLLABORATOR_ASSOCIATIONS.has(review.author_association)
      && review.commit_id === headSha
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
  process.exit(decision.conclusion === 'success' ? 0 : 1);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(e => { console.error(e.message); process.exit(1); });
}
