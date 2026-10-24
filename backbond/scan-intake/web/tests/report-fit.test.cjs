const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../report.js'), 'utf8');
const helper = source.slice(source.indexOf('  function fitNativeReport('), source.indexOf('  async function load()'));

function fixture(width, height, observer = true) {
  let resize;
  const viewport = {style:{}, clientWidth:width, clientHeight:height, append(frame) {this.frame = frame;}};
  const frame = {style:{}, src:'https://api.backbond.ai/approved', sandbox:'',
    addEventListener(event, callback) {assert.equal(event, 'load'); this.loaded = callback;}};
  const context = {document:{createElement() {return viewport;}},
    window:{addEventListener(event, callback) {assert.equal(event, 'resize'); resize = callback;}},
    ResizeObserver:observer ? class {constructor(callback) {this.callback=callback;} observe() {this.callback();}} : undefined};
  vm.runInNewContext(helper + '\nthis.fit = fitNativeReport;', context);
  assert.equal(context.fit(frame), viewport);
  return {viewport, frame, resize};
}

test('a narrow panel contains the complete Letter page and its scrollbar', () => {
  const {frame, viewport} = fixture(400, 700);
  const scale = Number(frame.style.transform.match(/scale\((.+)\)/)[1]);
  assert.equal(parseFloat(frame.style.width) * scale, 400);
  assert.ok(parseFloat(frame.style.width) >= 816 + 20);
  assert.ok(parseFloat(frame.style.height) * scale <= 700);
  assert.equal(viewport.style.overflow, 'hidden');
  assert.equal(frame.src, 'https://api.backbond.ai/approved');
  assert.equal(frame.sandbox, '');
});

test('desktop pages retain normal size and react to panel and height changes', () => {
  const {viewport, frame, resize} = fixture(860, 700);
  assert.equal(frame.style.transform, 'scale(1)');
  assert.equal(frame.style.height, '700px');
  viewport.clientWidth = 320; viewport.clientHeight = 500; resize();
  assert.equal(frame.style.width, '840px');
  assert.ok(parseFloat(frame.style.height) * (320/840) <= 500);
});

test('initially hidden panels recover on frame load without ResizeObserver', () => {
  const {viewport, frame} = fixture(0, 0, false);
  assert.equal(frame.style.width, undefined);
  viewport.clientWidth = 400; viewport.clientHeight = 700; frame.loaded();
  assert.equal(frame.style.width, '840px');
});
