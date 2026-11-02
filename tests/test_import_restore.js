// Run the actual saved-import callback with a small DOM substitute.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/wallet.js', 'utf8');
const elements = new Map();
const element = key => {
  if (!elements.has(key)) elements.set(key, {
    value: '', hidden: false, nextElementSibling: {}, children: [],
    replaceChildren() { this.children = []; },
    append(child) { this.children.push(child); },
    addEventListener(type, handler) { this[type] = handler; }
  });
  return elements.get(key);
};
const state = {};
let preview;
let rendered;
const context = vm.createContext({
  state, pending: null, $: element,
  document: {querySelector: element, createElement: () => element(Symbol())},
  refresh() {}, message() {},
  renderPreview() { rendered = state.preview; },
  api: async path => path === '/api/imports'
    ? {imports: [{draft_id: 'draft-123', created_at: '', confirmed: false}]}
    : {preview, results: [], done: false}
});
vm.runInContext(source.slice(source.indexOf('  function formatAmountText('), source.indexOf('  function formatAmountInput(')) +
  source.slice(source.indexOf('  function showImportMode()'), source.indexOf("  $('#amount-mode').addEventListener")) +
  source.slice(source.indexOf('  async function savedImports()'), source.indexOf("  $('#refresh-imports').addEventListener")), context);
(async () => {
  for (const options of [
    {amount_mode: 'fixed', fixed_amount: 1000000, cashback_percent: '0'},
    {amount_mode: 'excel', cashback_percent: '12.5'},
    {cashback_percent: '7'} // Older drafts default to Excel mode.
  ]) {
    preview = {...options, valid_count: 2};
    await context.savedImports();
    await element('#saved-imports').children[0].click();
    const fixed = options.amount_mode === 'fixed';
    assert.equal(element('#amount-mode').value, fixed ? 'fixed' : 'excel');
    assert.equal(element('#fixed-amount').value, fixed ? '1,000,000' : '');
    assert.equal(element('#fixed-amount-field').hidden, !fixed);
    assert.equal(element('#fixed-amount').required, fixed);
    assert.equal(element('#import-cashback-percent').hidden, fixed);
    assert.equal(element('#import-cashback-percent').required, !fixed);
    assert.equal(element('#import-cashback-percent').value, fixed ? '5' : options.cashback_percent);
    assert.equal(element('label[for="import-cashback-percent"]').hidden, fixed);
    assert.equal(element('#import-cashback-percent').nextElementSibling.hidden, fixed);
    assert.equal(state.preview, preview);
    assert.equal(rendered, preview);
    assert.equal(state.draft, 'draft-123');
    assert.equal(element('#import-preview').hidden, false);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
