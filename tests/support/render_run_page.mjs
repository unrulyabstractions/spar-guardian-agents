// The run page rendered under node with the API's answer on stdin: its text on stdout, one line per text node. The DOM
// here is what the page's own code calls, and no browser is needed.
//
//   node tests/support/render_run_page.mjs < detail.json
import { readFileSync } from 'node:fs';

class StubNode {
  constructor() { this.childNodes = []; }
  appendChild(child) { this.childNodes.push(child); return child; }
  lines() { return this.childNodes.flatMap(child => child.lines()); }
}

class StubText extends StubNode {
  constructor(text) { super(); this.text = text; }
  lines() { return [this.text]; }
}

class StubElement extends StubNode {
  constructor() { super(); this.dataset = {}; this.classList = { add() {}, remove() {}, toggle() {} }; this.style = {}; }
  set textContent(text) { this.childNodes = [new StubText(text)]; }
  setAttribute() {}
  addEventListener() {}
}

globalThis.Node = StubNode;
globalThis.document = { createElement: () => new StubElement(), createElementNS: () => new StubElement(),
  createTextNode: text => new StubText(text), title: '' };
const detail = readFileSync(0, 'utf8');
globalThis.fetch = async () => ({ ok: true, status: 200, text: async () => detail });
const { viewRun } = await import('../../src/ui/static/app/view_run.js');
const page = await viewRun({ run: JSON.parse(detail).run }, { mounted() {} });
process.stdout.write(page.lines().join('\n'));
