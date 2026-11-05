// End-to-end UI check: loads each of the 7 screens in headless Chromium against a running server, exercises the main action
// on three of them, asserts no console errors / failed requests / missing content, and saves screenshots to docs/screenshots.
// Usage: python -m orbit.api.app &  then  node scripts/e2e_ui.js   (Playwright required)
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const BASE = process.env.ORBIT_URL || 'http://localhost:8000';
const must = (c, m) => { if (!c) { console.error('FAIL:', m); process.exitCode = 1; } else console.log('ok  :', m); };
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1360, height: 900 } });
  const errs = [], bad = [];
  p.on('console', m => m.type() === 'error' && errs.push(m.text())); p.on('pageerror', e => errs.push(String(e)));
  p.on('response', r => r.url().includes('/api/') && r.status() >= 400 && bad.push(r.status() + ' ' + r.url()));
  await p.goto(BASE); await p.waitForSelector('.kpi');
  must(await p.locator('.banner').innerText().then(t => /simulated/i.test(t)), 'simulated-data banner visible');
  const screens = [['Control Tower', 'Priority actions'], ['Demand Intelligence', 'Model comparison'], ['Inventory', 'Stockout'], ['Supplier Intelligence', 'Terms history'],
    ['Replenishment Planner', null], ['Scenario Simulator', null], ['AI Assistant', null]];
  for (let i = 0; i < screens.length; i++) {
    const [name, text] = screens[i];
    await p.click(`nav.side button:text-is("${name}")`);
    if (name === 'Replenishment Planner') { await p.click('text=Generate plan'); await p.waitForSelector('text=Recommended orders'); must((await p.locator('tbody tr').count()) > 5, 'planner returns order rows'); }
    else if (name === 'Scenario Simulator') { await p.click('text=Run 4 scenarios'); await p.waitForSelector('text=Budget $10k'); await p.waitForSelector('text=Historical policy backtest'); must(await p.locator('text=not realised commercial savings').count() > 0, 'backtest labelled as simulation'); }
    else if (name === 'AI Assistant') { await p.click('text=What needs attention today?'); await p.waitForSelector('.msg.a'); must(/\[E1\]/.test(await p.locator('.msg.a').first().innerText()), 'assistant answer carries citations'); }
    else await p.waitForSelector(`text=${text}`);
    await p.waitForTimeout(600);
    must(await p.locator('main h2').innerText() === name, `${name} screen renders`);
    await p.screenshot({ path: `docs/screenshots/${i + 1}_${name.toLowerCase().replace(/ /g, '_')}.png`, fullPage: true });
  }
  await p.setViewportSize({ width: 390, height: 800 }); await p.click('nav.side button:text-is("Control Tower")');
  must(await p.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 2), 'no horizontal page scroll at phone width');
  must(errs.length === 0, 'no console errors ' + JSON.stringify(errs)); must(bad.length === 0, 'no failed API requests ' + JSON.stringify(bad));
  await b.close();
})();
