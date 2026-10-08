(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.BackBondScanProgress = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";
  const copy = {
    queued: ["Scan queued", "Waiting for capacity. This scan will start automatically."],
    running: ["Scan in progress", ""],
    reconnecting: ["Reconnecting to live updates", "The scan may still be running. We’ll restore updates automatically."],
    unconfirmed: ["Progress unconfirmed", "No new progress has been confirmed. The scan may still be running."],
    finished: ["Processing finished", "All accepted packs were processed. Review the results to see whether the assessment is usable and complete."],
    incomplete: ["Scan ended before all packs were processed", "Review available results."],
    error: ["Scan failed before producing results", "Enter your connection details and test the connection before retrying."],
  };
  const terminal = new Set(["finished", "incomplete", "error"]);
  function seconds(value) {
    value = Math.max(0, Math.round(Number(value) || 0));
    const h = Math.floor(value / 3600), m = Math.floor((value % 3600) / 60), s = value % 60;
    return h ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}` : `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
  }
  function model(raw) {
    const state = copy[raw.state] ? raw.state : "unconfirmed";
    const total = Number.isFinite(raw.accepted_pack_total) ? Math.max(0, raw.accepted_pack_total) : null;
    const processed = Number.isFinite(raw.processed_count) ? Math.max(0, raw.processed_count) : null;
    const estimate = raw.timing && raw.timing.estimated_remaining_seconds;
    return {
      state, heading: copy[state][0], guidance: raw.terminal_message || copy[state][1],
      stage: String(raw.stage_label || "Stage unavailable"), processed, total,
      usable: Number.isFinite(raw.usable_count) ? Math.max(0, raw.usable_count) : null,
      unavailable: Number.isFinite(raw.unavailable_count) ? Math.max(0, raw.unavailable_count) : null,
      percent: total > 0 && processed !== null ? Math.max(0, Math.min(100, processed / total * 100)) : null,
      elapsed: seconds(raw.timing && raw.timing.elapsed_seconds),
      estimate: estimate ? `${seconds(estimate.low)}–${seconds(estimate.high)} remaining` : "Estimating",
      lastProgressAt: raw.updated_at || raw.heartbeat_at || null,
      action: state === "finished" ? "Review results" : state === "incomplete" ? "Review available results" : state === "error" ? "Check connection" : "View run details",
      terminal: terminal.has(state),
    };
  }
  function announcement(value) {
    const count = value.processed === null ? "Accepted-pack count unavailable" : value.total === null ? `${value.processed} accepted packs processed` : `${value.processed} of ${value.total} accepted packs processed`;
    const outcomes = value.usable === null || value.unavailable === null ? "Outcome counts unavailable" : `${value.usable} usable, ${value.unavailable} unavailable`;
    if (value.state === "running") return `Scan progress: ${count}. ${outcomes}. Stage: ${value.stage}.`;
    if (value.state === "finished") return `Processing finished. ${count}. Review results for assessment status.`;
    return `${value.heading}. ${value.guidance}`.trim();
  }
  function render(root, raw, options) {
    const value = model(raw), doc = root.ownerDocument, opts = options || {};
    const el = (tag, cls, text) => { const node=doc.createElement(tag); if(cls) node.className=cls; if(text!==undefined) node.textContent=text; return node; };
    const section=el("section","scan-progress"); section.setAttribute("aria-labelledby","scan-progress-heading");
    const head=el("div","scan-progress__head");
    const heading=el("h2","",value.heading); heading.id="scan-progress-heading"; head.append(heading,el("span","scan-progress__state",value.state));
    section.append(head,el("p","scan-progress__stage",value.stage));
    section.append(el("p","scan-progress__count",value.processed===null ? "Accepted-pack count unavailable" : value.total===null ? `${value.processed} accepted packs processed` : `${value.processed} of ${value.total} accepted packs processed`));
    const track=el("div","scan-progress__track");
    if(value.percent!==null){ track.setAttribute("role","progressbar"); track.setAttribute("aria-label","Accepted packs processed"); track.setAttribute("aria-valuemin","0"); track.setAttribute("aria-valuemax",String(value.total)); track.setAttribute("aria-valuenow",String(value.processed)); }
    const bar=el("div","scan-progress__bar"); bar.style.width=(value.percent===null?0:value.percent)+"%"; track.append(bar); section.append(track);
    const facts=el("div","scan-progress__facts"); facts.append(el("span","",value.usable===null?"Usable count unavailable":`${value.usable} usable`),el("span","",value.unavailable===null?"Unavailable count unavailable":`${value.unavailable} unavailable`)); section.append(facts);
    const times=el("div","scan-progress__times scan-progress__muted"); times.append(el("span","",`Elapsed ${value.elapsed}`),el("span","",`Estimated ${value.estimate}`)); section.append(times);
    const freshness=el("p","scan-progress__muted","Last confirmed progress ");
    const time=el("time","",value.lastProgressAt ? new Date(value.lastProgressAt).toLocaleString() : "not yet available"); if(value.lastProgressAt) time.setAttribute("datetime",value.lastProgressAt); freshness.append(time); section.append(freshness);
    if(value.guidance) section.append(el("p","",value.guidance));
    const href=value.terminal ? opts.resultsHref : opts.detailsHref;
    if(href){ const action=el("a","scan-progress__action",value.action); action.setAttribute("href",href); section.append(action); }
    const live=el("div","scan-progress__sr-only"); live.setAttribute("role",raw.state==="error"&&opts.watched?"alert":"status"); live.setAttribute("aria-live",raw.state==="error"&&opts.watched?"assertive":"polite"); live.setAttribute("aria-atomic","true");
    root.replaceChildren(section,live); return {section,live,value};
  }
  class Announcer {
    constructor(live, clock){ this.live=live; this.clock=clock||Date.now; this.lastAt=-Infinity; this.signature=""; }
    update(raw){ const value=model(raw), signature=[value.state,value.stage,value.processed,value.usable,value.unavailable].join("|"); if(signature===this.signature)return false; const now=this.clock(); if(now-this.lastAt<5000)return false; this.signature=signature; this.lastAt=now; this.live.textContent=announcement(value); return true; }
  }
  return { copy, seconds, model, announcement, render, Announcer };
});
