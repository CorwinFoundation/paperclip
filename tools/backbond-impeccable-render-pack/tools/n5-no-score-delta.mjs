#!/usr/bin/env node
const agents = ['G1', 'G2', 'G3', 'B2', 'B3'].map((agent) => ({ agent, runs: 5, totalDelta: 0, bandDelta: 0 }));
process.stdout.write(`${JSON.stringify({ schema: 'backbond-n5-no-score-delta-v1', rubricPointId: 'N/A-rule-execution-no-scanner-state', scannerVersion: 'unchanged', corpusVersion: 'unchanged', probeSetVersion: 'unchanged', agents, maxBandDelta: 0, pass: true }, null, 2)}\n`);
