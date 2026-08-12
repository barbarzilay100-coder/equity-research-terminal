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
// flag two names as a personal watchlist to exercise the watchlist filter (test-only, not committed)
const __WATCH = ['MSFT','AAPL'];
window.DATA.companies.forEach(c=>{ if(__WATCH.includes(c.ticker)) c.watch=true; });
window.eval(inline + '\n;window.__setShowAll=v=>{SHOWALL=v;paintHead();paintScreener();};'
  + '\n;window.__setWatch=v=>{WATCH_ONLY=v;paintScreener();};');

const $ = id => window.document.getElementById(id);
const DB = window.DATA;

check('data loaded with 100+ companies', DB && DB.companies && DB.companies.length >= 100);
const pubN = DB.companies.filter(c=>!c.watch).length;
const watchN = DB.companies.filter(c=>c.watch).length;
check('footer shows public coverage count', $('footcount').textContent === String(pubN));

// market overview (default view)
check('market view rendered', $('view-market').innerHTML.includes('Market Overview'));
check('heatmap has sector cells', $('view-market').querySelectorAll('.heatcell').length >= 5);
check('insider-flow leaderboard rendered', $('view-market').innerHTML.includes('Smart Money'));
check('deal radar board rendered', $('view-market').innerHTML.includes('Deal Radar'));
check('deal radar has clickable rows', $('view-market').innerHTML.includes('openFilings('));

// screener
window.showView('screener');
check('screener rows = public universe', $('scrBody').querySelectorAll('tr').length === pubN);
window.eval("document.getElementById('scrMin').value='0.85'; paintScreener();");
const strong = $('scrBody').querySelectorAll('tr').length;
check('strong-only filter narrows results', strong > 0 && strong < DB.count);
window.eval("document.getElementById('scrMin').value='0';");
window.__setShowAll(true);
check('all-columns toggle widens table', $('scrHead').querySelectorAll('th').length === 12);
window.__setShowAll(false);

// --- personal watchlist filter ---
const watchSet = new Set(DB.companies.filter(c=>c.watch).map(c=>c.ticker));  // real watchlist names + the two injected above
check('watchlist carries the injected names', __WATCH.every(t => watchSet.has(t)));
check('PUB and WATCH partition the universe', pubN + watchN === DB.companies.length);
check('watchlist toggle control present', !!$('scrWatch'));
window.__setWatch(true);
const wRows = [...$('scrBody').querySelectorAll('tr')];
check('watchlist view shows exactly the flagged names', wRows.length === watchN);
check('every watchlist row is a flagged name',
  wRows.every(tr => watchSet.has(tr.querySelector('td').textContent.trim())));
check('injected names show up in the watchlist',
  __WATCH.every(t => wRows.some(tr => tr.querySelector('td').textContent.trim() === t)));
window.__setWatch(false);
const pRows = [...$('scrBody').querySelectorAll('tr')];
check('public screener excludes every watch name',
  pRows.length === pubN && pRows.every(tr => !watchSet.has(tr.querySelector('td').textContent.trim())));

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
['financials', 'valuation', 'technicals', 'smartmoney', 'filings'].forEach(t => {
  window.reportTab(t);
  const el = $('rt-' + t);
  check('sub-tab renders: ' + t, el && el.style.display === 'block' && el.innerHTML.length > 100);
});
check('lazy charts drawn on demand', (window.__chartsDrawn || 0) >= 2);

// SEC filings / deal radar
const evCov = DB.companies.filter(c => c.events && c.events.list && c.events.list.length).length;
check('EDGAR events cover 100+ companies', evCov >= 100);
const radarT = DB.companies.find(c =>
  ((c.events && c.events.list) || []).some(e => ['ma', 'merger', 'activist', 'stake'].includes(e.c)));
window.openFilings(radarT.ticker);
check('openFilings jumps to Filings sub-tab',
  window.document.querySelector('#report .subtab.active').dataset.rt === 'filings');
check('filings table lists classified filings', $('rt-filings').querySelectorAll('tbody tr').length > 0);
check('filing rows link to sec.gov',
  (($('rt-filings').querySelector('tbody a') || {}).href || '').startsWith('https://www.sec.gov/Archives/edgar/data/'));

// as-reported reconciliation (only once build_sec has run; a fresh clone has no `sec` yet)
const secCo = DB.companies.find(c => c.sec && c.sec.revenue != null);
if (secCo) {
  window.openResearch(secCo.ticker);
  window.reportTab('financials');
  const fin = $('rt-financials').innerHTML;
  check('reconciliation table rendered', fin.includes('As Reported vs. Vendor Data'));
  check('reconciliation links to the filing on sec.gov',
    fin.includes('https://www.sec.gov/Archives/edgar/data/'));
  const secCov = DB.companies.filter(c => c.sec && c.sec.revenue != null).length;
  check('as-reported figures cover 100+ companies', secCov >= 100);
}

// search box path
$('ticker').value = 'Coinbase';
window.run();
check('search by company name resolves', $('report').innerHTML.includes('COIN'));

console.log(`\n${passes} passed, ${failures} failed`);
process.exit(failures ? 1 : 0);
