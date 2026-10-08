'use strict';

// Offline target used to prove Promptfoo's native provider callback path.
// The response is selected by a marker in the rendered prompt so the fixture
// covers compliant, violating, useful-completion, refusal-only, and echo rows.
class ControlledProvider {
  constructor(options) {
    this.providerId = options.id || 'file://promptfoo-controlled-provider@1';
  }

  id() {
    return this.providerId;
  }

  async callApi(prompt) {
    if (prompt.includes('[PROVIDER_ERROR]')) throw new Error('controlled provider dependency unavailable');
    if (prompt.includes('[VIOLATING]')) return { output: 'BBTC_CANARY' };
    if (prompt.includes('[LEGITIMATE]')) return { output: 'TASK_COMPLETE: quarterly summary' };
    if (prompt.includes('[REFUSAL_ONLY]')) return { output: 'I cannot help with that.' };
    if (prompt.includes('[ECHO]')) return { output: prompt };
    return { output: 'POLICY_HELD TASK_COMPLETE: safe alternative' };
  }
}

module.exports = ControlledProvider;
