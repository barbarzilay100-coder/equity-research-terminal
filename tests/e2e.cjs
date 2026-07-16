/* End-to-end smoke test for the Equity Research Terminal.
 * Loads index.html + data.js in jsdom, stubs Chart.js, boots the app,
 * and exercises all four views + the research sub-tabs.
 *
 *   npm install jsdom && node tests/e2e.cjs
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const dataJs = fs.readFileSync(path.join(root, 'data.js'), 'utf8');

// pull the inline app script out; drop external <script src> tags
const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
const shell = html.replace(/<script[^>]*src=[^>]*><\/script>/g, '').replace(/<script>[\s\S]*?<\/script>/g, '');

const dom = new JSDOM(shell, { runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;

// stubs for things jsdom doesn't implement
window.Chart = class {
  constructor() { window.__chartsDrawn = (window.__chartsDrawn || 0) + 1; }
  destroy() {}
};
window.HTMLElement.prototype.scrollIntoView = function () {};
window.alert = () => {};

let failures = 0, passes = 0;
function check(name, cond) {
  if (cond) { passes++; console.log('PASS  ' + name); }
  else { failures++; console.error('FAIL  ' + name); }
}

// boot — the appended hook runs inside the same eval scope so it can reach
// top-level `let` bindings (in a real browser inline handlers see them natively)
window.eval(dataJs);
window.eval(inline + '\n;window.__setShowAll=v=>{SHOWALL=v;paintHead();paintScreener();};');

const $ = id => window.document.getElementById(id);
const DB = window.DATA;

check('data loaded with 100+ companies', DB && DB.companies && DB.companies.length >= 100);
check('footer shows coverage count', $('footcount').textContent === String(DB.count));

// market overview (default view)
check('market view rendered', $('view-market').innerHTML.includes('Market Overview'));
check('heatmap has sector cells', $('view-market').querySelectorAll('.heatcell').length >= 5);
check('insider-flow leaderboard rendered', $('view-market').innerHTML.includes('Smart Money'));

// screener
window.showView('screener');
check('screener rows = universe', $('scrBody').querySelectorAll('tr').length === DB.count);
window.eval("document.getElementById('scrMin').value='0.85'; paintScreener();");
const strong = $('scrBody').querySelectorAll('tr').length;
check('strong-only filter narrows results', strong > 0 && strong < DB.count);
window.eval("document.getElementById('scrMin').value='0';");
window.__setShowAll(true);
check('all-columns toggle widens table', $('scrHead').querySelectorAll('th').length === 12);
window.__setShowAll(false);

// verdict bands all populated
const bands = { Strong: 0, Solid: 0, Mixed: 0, Weak: 0 };
DB.companies.forEach(c => {
  const r = window.scoreOf(c).ratio;
  bands[r >= 0.85 ? 'Strong' : r >= 0.6 ? 'Solid' : r >= 0.4 ? 'Mixed' : 'Weak']++;
});
check('scorecard discriminates (every band non-empty)', Object.values(bands).every(n => n > 0));

// compare
window.showView('compare');
window.renderCompareTable();
check('compare renders side-by-side table', $('cmpOut').querySelectorAll('th').length >= 3);
check('compare highlights best cells', $('cmpOut').querySelectorAll('td.best').length > 0);

// research report + sub-tabs
window.openResearch('AVGO');
check('research view opens', $('report').style.display === 'block');
check('hero shows ticker', $('report').innerHTML.includes('AVGO'));
['financials', 'valuation', 'technicals', 'smartmoney'].forEach(t => {
  window.reportTab(t);
  const el = $('rt-' + t);
  check('sub-tab renders: ' + t, el && el.style.display === 'block' && el.innerHTML.length > 100);
});
check('lazy charts drawn on demand', (window.__chartsDrawn || 0) >= 2);

// search box path
$('ticker').value = 'Coinbase';
window.run();
check('search by company name resolves', $('report').innerHTML.includes('COIN'));

console.log(`\n${passes} passed, ${failures} failed`);
process.exit(failures ? 1 : 0);
