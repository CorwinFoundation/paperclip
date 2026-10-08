(function (root, factory) {
  const api = factory(root.BackBondScanLifecycle, root.BackBondScanProgress);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.BackBondScanPage = api;
})(typeof globalThis === "object" ? globalThis : this, function (lifecycle, progress) {
  "use strict";

  function mount(options) {
    const opts = options || {};
    const buttons = Array.from(document.querySelectorAll(opts.startSelector || "[data-backbond-scan-start]"));
    const panel = document.querySelector(opts.progressSelector || "[data-backbond-scan-progress]");
    const storage = opts.storage || window.sessionStorage;
    const key = opts.storageKey || "backbond.active-scan";
    let state = lifecycle.restore(storage, key);
    let timer = null;

    function paint(raw) {
      const view = lifecycle.view(state);
      buttons.forEach((button) => {
        button.disabled = view.disabled || view.action === "progress";
        button.textContent = view.label;
        button.hidden = view.action === "progress" && !view.disabled;
      });
      if (panel) panel.hidden = !view.showProgress;
      if (panel && raw) progress.render(panel, raw, opts.links);
      lifecycle.save(storage, key, state);
    }

    function reset() {
      stop();
      state = lifecycle.initial();
      paint();
    }

    async function poll() {
      if (!state.statusToken) return;
      try {
        const raw = await opts.getProgress(state.statusToken);
        state = lifecycle.reduce(state, {type: "status", status: raw.state, updated_at: raw.updated_at});
        paint(raw);
        if (lifecycle.TERMINAL.has(state.state)) stop();
      } catch (_) {
        state = lifecycle.reduce(state, {type: "poll_failed"});
        paint();
      }
    }

    function stop() { if (timer) clearInterval(timer); timer = null; }
    function watch() { stop(); poll(); timer = setInterval(poll, opts.pollMs || 2000); }
    async function reconcile(error) {
      try {
        const accepted = await opts.reconcileAcceptance(error || {outcomeUnknown: true});
        state = accepted ? lifecycle.reduce(state, {type: "accepted", status: accepted.state || accepted.status || "queued", statusToken: accepted.scan_id || accepted.status_token, updated_at: accepted.updated_at}) : lifecycle.reduce(state, {type: "not_accepted"});
        paint(); if (accepted) watch();
      } catch (reconcileError) {
        if (opts.onError) opts.onError(reconcileError);
        paint();
      }
    }
    async function start(event) {
      event.preventDefault();
      if (lifecycle.ACTIVE.has(state.state)) return;
      state = lifecycle.reduce(state, {type: "submit"}); paint();
      try {
        const accepted = await opts.startScan(event.currentTarget);
        state = lifecycle.reduce(state, {type: "accepted", status: accepted.state || "queued", statusToken: accepted.scan_id, updated_at: accepted.updated_at});
        paint(); watch();
      } catch (error) {
        if (!error || !error.outcomeUnknown) {
          state = lifecycle.reduce(state, {type: "submit_failed", message: error && error.message});
          paint();
          if (opts.onError) opts.onError(error);
          return;
        }
        state = lifecycle.reduce(state, {type: "ambiguous_start", statusToken: error.scan_id}); paint();
        await reconcile(error);
      }
    }
    buttons.forEach((button) => button.addEventListener("click", start));
    paint();
    if (lifecycle.ACTIVE.has(state.state) && state.statusToken) watch();
    else if (lifecycle.TERMINAL.has(state.state) && state.statusToken) poll();
    else if (state.state === "unconfirmed" && opts.reconcileAcceptance) reconcile();
    return { poll, stop, reset, state: () => state };
  }
  return { mount };
});
