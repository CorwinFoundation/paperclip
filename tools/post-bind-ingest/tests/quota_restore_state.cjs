const fs = require("fs");
const { chromium } = require("playwright");

const designPath = process.argv[2];
if (!designPath) throw new Error("usage: node quota_restore_state.cjs DESIGN_PATH");

const panels = [
  "Hallucination rate (factual)",
  "Self-contradiction under perturbation",
  "Out-of-scope refusal accuracy",
  "Confidence calibration vs error",
].map((name, index) => ({
  index,
  name,
  pillar: ["GOVERNANCE", "ROBUSTNESS", "BIAS_FAIRNESS", "OPS"][index],
  availability: "executable",
  result: "held",
  probe_count: 20 + index,
  raw: 4,
  evidence_id: `evidence-${index}`,
  dimension_id: `dimension-${index}`,
  applicable: true,
}));

const scan = {
  agent_id: "qa-state-regression",
  scan_id: "qa-state-regression",
  score: 81,
  rubric_label: "1.3.0 candidate",
  panels,
  registry: {
    measured_dimension_count: 4,
    dimension_count: 4,
    unique_executable_count: panels.reduce((total, panel) => total + panel.probe_count, 0),
  },
  band: { posture: "INCOMPLETE" },
  subscores: {
    GOVERNANCE: 20,
    ROBUSTNESS: 20,
    BIAS_FAIRNESS: 20,
    OPS: 21,
  },
};

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: "/usr/bin/google-chrome",
  });
  try {
    const html = fs.readFileSync(designPath, "utf8");
    const completed = {
      lifecycle: {
        updated_scan: scan,
        documents: { certificate_url: "about:blank" },
      },
    };
    const openQuotaState = async (latest) => {
      const page = await browser.newPage();
      await page.route("http://demo.test/", (route) =>
        route.fulfill({ status: 200, contentType: "text/html", body: html }),
      );
      await page.route("**/api/runs", (route) =>
        route.fulfill({
          status: 429,
          contentType: "application/json",
          body: JSON.stringify({
            error: "run_quota_exceeded",
            message: "This demo has reached its visitor run limit. Please contact your BackBond partner manager to continue.",
          }),
        }),
      );
      await page.route("**/api/latest", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(latest),
        }),
      );
      await page.goto("http://demo.test/");
      await page.locator("#partnerToken").fill("qa-state-token");
      await page.locator("#scanBtn").click();
      return page;
    };

    const emptyPage = await openQuotaState({ status: "not_run" });
    await emptyPage.locator("#submitFeedback").waitFor({ state: "visible" });
    const emptyState = await emptyPage.evaluate(() => ({
      stage: document.body.dataset.stage,
      feedback: document.querySelector("#submitFeedback")?.textContent,
    }));
    if (emptyState.stage !== "submit" || !emptyState.feedback.includes("partner manager")) {
      throw new Error(`empty quota state lacked visible submit feedback: ${JSON.stringify(emptyState)}`);
    }
    await emptyPage.close();

    const page = await openQuotaState({ status: "passed", result: completed });
    await page.locator("body[data-stage='rated']").waitFor();

    const state = await page.locator("#exam .probe").evaluateAll((rows) =>
      rows.map((row) => ({
        className: row.className,
        opacity: getComputedStyle(row).opacity,
        result: row.querySelector(".res")?.textContent.trim().toLowerCase(),
      })),
    );
    if (state.length !== 4) throw new Error(`expected 4 Examination rows, got ${state.length}`);
    for (const [index, row] of state.entries()) {
      if (row.className.includes("pending") || row.opacity !== "1" || row.result !== "clean") {
        throw new Error(`row ${index} was not visibly restored CLEAN: ${JSON.stringify(row)}`);
      }
    }
    await page.close();
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
