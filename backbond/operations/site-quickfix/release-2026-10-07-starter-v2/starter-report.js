// BEAAA-38895: Starter report table for starter-100@2 and @3 (BEAAA-38898) results.
// Reads the scan result payload (starter_score + result_rows). Old starter-100@1
// rows (no `reason`) still render: passed / needs_attention, no score line.
(function () {
  "use strict";
  var LABELS = {
    prompt_injection: "Prompt injection", system_prompt_leak: "System prompt leak",
    secrets_pii_leak: "Secrets and personal data", discrimination: "Discrimination",
    made_up_facts: "Made-up facts", out_of_scope_actions: "Out-of-scope actions",
    harmful_content: "Harmful content", jailbreak_roleplay: "Jailbreak by roleplay",
    refusal_escalation: "Pressure after a refusal", unsafe_output: "Unsafe output",
    answers_normal_requests: "Answers normal requests"
  };
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]; }); }
  function families(payload) {
    if (payload.starter_score && payload.starter_score.families) return payload.starter_score.families;
    var out = {};
    (payload.result_rows || []).forEach(function (r) {
      var f = out[r.family] = out[r.family] || {ran: 0, passed: 0, needs_attention: 0, could_not_run: 0};
      if (r.verdict === "passed") { f.ran++; f.passed++; }
      else if (r.verdict === "could_not_run") { f.could_not_run++; }
      else { f.ran++; f.needs_attention++; }
    });
    return out;
  }
  window.renderStarterReport = function (payload, el) {
    var fams = families(payload), html = "";
    var s = payload.starter_score;
    if (s && s.score != null) {
      html += '<p class="starter-score"><strong>Starter score: ' + esc(Math.round(s.score)) +
        '</strong> (' + esc(s.passed) + " of " + esc(s.ran) + " tests that ran passed" +
        (s.could_not_run ? "; " + esc(s.could_not_run) + " could not run" : "") +
        ")</p><p>10 areas, 100 tests. Not the full BackBond Score.</p>";
    }
    html += "<table><thead><tr><th>Area</th><th>Ran</th><th>Passed</th>" +
      "<th>Needs attention</th><th>Could not run</th></tr></thead><tbody>";
    Object.keys(fams).forEach(function (k) {
      var f = fams[k], total = f.ran + f.could_not_run;
      html += "<tr><td>" + esc(LABELS[k] || k) + "</td><td>" + esc(f.ran) + "</td><td>" +
        esc(f.passed) + " of " + esc(total) + "</td><td>" + esc(f.needs_attention) +
        "</td><td>" + esc(f.could_not_run) + "</td></tr>";
    });
    html += "</tbody></table>";
    var failed = (payload.result_rows || []).filter(function (r) { return r.verdict === "failed"; });
    if (failed.length) {
      html += "<h3>Tests that need attention</h3>";
      failed.forEach(function (r) {
        html += '<details><summary>' + esc(LABELS[r.family] || r.family) + " &middot; " +
          esc(r.test_id) + "</summary>" +
          (r.prompt ? "<p><b>Sent:</b> " + esc(r.prompt) + "</p>" : "") +
          (r.reply ? "<p><b>Reply:</b> " + esc(r.reply) + "</p>" : "") +
          "<p><b>Why:</b> " + esc(r.reason) + "</p></details>";
      });
    }
    el.innerHTML = html;
  };
})();
