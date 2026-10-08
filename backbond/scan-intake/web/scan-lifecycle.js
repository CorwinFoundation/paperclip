(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.BackBondScanLifecycle = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";

  const ACTIVE = new Set(["submitting", "queued", "running", "dispatch_unknown", "reconnecting", "stalled", "unconfirmed"]);
  const TERMINAL = new Set(["finished", "incomplete", "error"]);
  const SERVER_STATES = new Set(["queued", "running", "dispatch_unknown", "finished", "incomplete", "error"]);

  function initial() {
    return { state: "idle", priorState: null, statusToken: null, connectionPassed: false };
  }

  function reduce(model, event) {
    const current = model || initial();
    switch (event.type) {
      case "connection_passed":
        // Connectivity is evidence about settings, not scan acceptance.
        return { ...current, connectionPassed: true };
      case "submit":
        if (ACTIVE.has(current.state)) return current;
        return { ...current, state: "submitting", priorState: null };
      case "accepted":
        if (!SERVER_STATES.has(event.status) || !event.statusToken) return current;
        return { ...current, state: event.status, priorState: null, statusToken: event.statusToken };
      case "ambiguous_start":
        return { ...current, state: "unconfirmed", priorState: null, statusToken: event.statusToken || current.statusToken };
      case "poll_failed":
        if (!ACTIVE.has(current.state) || current.state === "submitting") return current;
        return { ...current, state: "reconnecting", priorState: current.priorState || current.state };
      case "poll_stalled":
        if (!ACTIVE.has(current.state)) return current;
        return { ...current, state: "stalled", priorState: current.priorState || current.state };
      case "status":
        if (!SERVER_STATES.has(event.status)) return { ...current, state: "unconfirmed" };
        return { ...current, state: event.status, priorState: null, connectionPassed: event.status === "error" ? false : current.connectionPassed };
      case "new_scan":
        return TERMINAL.has(current.state) ? initial() : current;
      default:
        return current;
    }
  }

  function view(model) {
    if (model.state === "idle") return { action: "start", label: "Start scan", disabled: false, showProgress: false };
    if (model.state === "error") return { action: "retry", label: "Test connection to retry", disabled: true, showProgress: true, reopenForm: true };
    if (TERMINAL.has(model.state)) return { action: "new", label: "New scan", disabled: false, showProgress: true };
    if (model.state === "submitting") return { action: "progress", label: "Starting scan…", disabled: true, showProgress: true };
    return { action: "progress", label: "View scan progress", disabled: false, showProgress: true };
  }

  function save(storage, key, model) {
    if (!storage) return;
    if (model.state === "idle") storage.removeItem(key);
    else storage.setItem(key, JSON.stringify({ state: model.state, priorState: model.priorState, statusToken: model.statusToken }));
  }

  function restore(storage, key) {
    if (!storage) return initial();
    try {
      const saved = JSON.parse(storage.getItem(key));
      if (!saved || (!ACTIVE.has(saved.state) && !TERMINAL.has(saved.state))) return initial();
      // Refresh of an in-flight submission is ambiguous until reconciled.
      const state = saved.state === "submitting" ? "unconfirmed" : saved.state;
      return { state, priorState: saved.priorState || null, statusToken: saved.statusToken || null, connectionPassed: false };
    } catch (_) {
      return initial();
    }
  }

  return { ACTIVE, TERMINAL, initial, reduce, view, save, restore };
});
