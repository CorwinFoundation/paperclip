import assert from "node:assert/strict";
import { test } from "node:test";
import { restartIsIdle } from "./dev-runner-restart-guard.mjs";

test("only a supervisor reporting exactly zero active runs establishes idle", () => {
  assert.equal(restartIsIdle({ enabled: true, activeRunCount: 0 }), true);
  for (const count of [undefined, null, -1, 1, 20, NaN, Infinity, "0", 0.5]) {
    assert.equal(restartIsIdle({ enabled: true, activeRunCount: count }), false);
  }
  assert.equal(restartIsIdle(null), false);
  assert.equal(restartIsIdle({ enabled: false, activeRunCount: 0 }), false);
});
