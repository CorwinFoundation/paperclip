/* Headless pilot driver. SDK success confirms submission, never an answer. */
(() => {
  'use strict';
  let experience, mounted = false, busy = false, fault = false;
  function bounded(operation, ms, maximumMs = 30000) {
    if (!Number.isInteger(ms) || ms < 1 || ms > maximumMs) throw new Error('Invalid deadline');
    let timer;
    // Validate before starting SDK work and attach rejection handling before
    // the operation runs. An invalid send deadline must never submit a prompt.
    return Promise.race([Promise.resolve().then(operation), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Quick operation timed out')), ms);
    })]).finally(() => clearTimeout(timer));
  }
  window.backbondQuickPilot = Object.freeze({
    version: 1,
    async mount({embedUrl, agentId, timeoutMs}) {
      if (mounted) throw new Error('A fresh page is required');
      mounted = true;
      if (typeof agentId !== 'string' || !agentId || typeof embedUrl !== 'string') throw new Error('Invalid target');
      let loaded, failed;
      const ready = new Promise((resolve, reject) => { loaded = resolve; failed = reject; });
      const operation = async () => {
        const context = await QuickSightEmbedding.createEmbeddingContext();
        experience = await context.embedQuickChat(
          {url: embedUrl, container: '#chat', width: '100%', height: '100%',
            framePermissions: {clipboardRead: false, clipboardWrite: false},
            onChange(event) {
              if (event.eventLevel === 'ERROR') { fault = true; failed(new Error('Quick frame failed')); }
            }},
          {agentOptions: {fixedAgentId: agentId},
            promptOptions: {enablePrivateMode: true, showChatHistory: false},
            onMessage(event) {
              // Quick Chat can initialize its supported SDK channel without
              // emitting CONTENT_LOADED. Neither event is an agent answer.
              if (event.eventName === 'CONTENT_LOADED' || event.eventName === 'EXPERIENCE_INITIALIZED') loaded();
              if (event.eventName === 'ERROR_OCCURRED') { fault = true; failed(new Error('Quick content failed')); }
            }});
        await ready;
        if (fault) throw new Error('Quick content failed');
        return {ready: true, version: 1};
      };
      // Attach a handler immediately even when embedding fails before ready.
      ready.catch(() => {});
      // The batch runner allows 60 seconds for mount within its case deadline.
      // Prompt submission keeps the separate 30-second maximum below.
      try { return await bounded(operation, timeoutMs, 60000); }
      catch (_) { fault = true; throw new Error('Quick mount failed'); }
    },
    async send({prompt, timeoutMs}) {
      if (!experience || fault || busy || typeof prompt !== 'string' || !prompt.trim()) throw new Error('Quick is unavailable');
      busy = true;
      try {
        const acknowledgement = await bounded(() => experience.sendPrompt(prompt), timeoutMs);
        if (!acknowledgement || acknowledgement.success !== true || fault) throw new Error('Quick submission failed');
        return {submitted: true};
      } catch (_) {
        fault = true;
        throw new Error('Quick submission status unknown');
      } finally { busy = false; }
    }
  });
})();
