// The crew's release page in a real browser (headless Chrome over the DevTools protocol, Node built-ins only).
// Run by test_release_page_browser.py, which seeds the plan and passes the administrator's session cookie in L3MON_COOKIE.
//   env: L3MON_BASE (default http://localhost:8080), L3MON_COOKIE, L3MON_SHOTS (a folder for two screenshots, optional),
//        L3MON_TOMORROW_IST_EPOCH (the epoch second of tomorrow 09:00 India time, worked out by the test in Python),
//        CHROME (the browser, if it is not in a usual place)
import { checker, launch, sleep, tab as openTab } from './cdp.mjs';

const BASE = process.env.L3MON_BASE || 'http://localhost:8080';
const COOKIE = process.env.L3MON_COOKIE;
const SHOTS = process.env.L3MON_SHOTS;
const TOMORROW = Number(process.env.L3MON_TOMORROW_IST_EPOCH);
const tab = (chrome, opts = {}) => openTab(chrome, { base: BASE, cookie: COOKIE, shots: SHOTS, ...opts });
const { check, finish } = checker();

const chrome = await launch();
try {
  const page = await tab(chrome);
  await page.goto(`${BASE}/admin/l3mon/release`);
  await sleep(2000);
  const text = () => page.run('document.getElementById("release-app").innerText');
  const row = (slug) => `Array.from(document.querySelectorAll("tr")).find((r) => r.children[1] && r.children[1].textContent === ${JSON.stringify(slug)})`;
  const rowText = (slug) => page.run(`(${row(slug)} || {}).innerText || ""`);
  const press = (slug, label) => page.run(`(() => { const b = Array.from(${row(slug)}.querySelectorAll("button")).find((x) => x.textContent.startsWith(${JSON.stringify(label)})); b.click(); return true; })()`);
  const dialogs = () => page.log.dialogs || 0;

  check('the page is called Release control', (await page.run('document.querySelector("h1").textContent')).includes('Release control'));
  check('three channels are drawn', (await page.run('document.querySelectorAll(".rc-channel").length')) === 3);
  const first = await text();
  check('the summary counts what is on air, in the singular for one', /1 programme on air, 4 coming up/.test(first), first.slice(0, 160));
  check('a name that contains markup is plain text and no element was made from it', first.includes('<img src=x onerror=alert(1)>') && (await page.run('document.querySelector("#release-app img") === null')));
  check('the released programme says players see it', (await rowText('padding')).includes('players see it'));
  check('the other says off air and withheld', /off air/.test(await rowText('mango')) && /withheld/.test(await rowText('mango')));
  await page.shot('release-desktop.png');

  let before = dialogs();
  await press('mango', 'Release now');
  await sleep(1500);
  check('Release now asks first, then releases', dialogs() === before + 1 && /released/.test(await rowText('mango')) && /changed\./.test(await text()), (await rowText('mango')));

  // the reason box keeps what is typed across a refresh of the plan, and is cleared once the change it was for is made
  await page.run('document.querySelector("input[aria-label=\\"Reason for the change\\"]").focus()');
  await page.send('Input.insertText', { text: 'typing a reason' });
  await sleep(16500); // the page re-reads the plan every 15 seconds
  check('a refresh of the plan does not take the reason box away or empty it',
    (await page.run('document.activeElement && document.activeElement.getAttribute("aria-label") === "Reason for the change" && document.activeElement.value')) === 'typing a reason');
  await press('logger', 'Release now');
  await sleep(1500);
  const audit = await text();
  check('the reason reaches the audit trail', audit.includes('typing a reason') && audit.includes('release.set') && audit.includes('organiser'), audit.slice(-300));
  check('and is cleared afterwards, so the next change does not reuse it', (await page.run('document.querySelector("input[aria-label=\\"Reason for the change\\"]").value')) === '');

  // a time typed in India becomes the right second
  await press('sauce', 'Schedule');
  await sleep(300);
  const setInput = (v) => page.run(`(() => { const i = document.querySelector("input[type=datetime-local]"); i.value = ${JSON.stringify(v)}; return i.value; })()`);
  const clickSet = () => page.run('(() => { Array.from(document.querySelectorAll("button")).find((b) => b.textContent === "Set").click(); return true; })()');
  const day = (offsetDays) => new Date(Date.now() + 5.5 * 3600e3 + offsetDays * 86400e3).toISOString().slice(0, 10);
  await setInput(`${day(-1)}T09:00`); await clickSet(); await sleep(300);
  check('a time in the past is refused plainly', /still to come/.test(await text()), (await text()).slice(0, 120));
  await setInput(`${day(1)}T09:00`); await clickSet(); await sleep(1500);
  const view = await page.run(`fetch(${JSON.stringify(`${BASE}/api/v1/l3mon/admin/release`)}, { credentials: "same-origin" }).then((r) => r.json())`);
  const sauce = view.data.channels.flatMap((c) => c.programmes).find((p) => p.slug === 'sauce');
  check('09:00 typed in India is stored as exactly that second (09:00 IST is 03:30 UTC)', sauce.state === 'scheduled' && sauce.at === TOMORROW && /09:00 IST$/.test(sauce.at_ist), JSON.stringify(sauce));

  before = dialogs();
  await page.run('(() => { Array.from(document.querySelectorAll("button")).find((b) => b.textContent === "Withhold every channel").click(); return true; })()');
  await sleep(1800);
  check('Withhold every channel asks first and takes everything off air', dialogs() === before + 1 && /0 programmes on air/.test(await text()), (await text()).slice(0, 160));
  check('no script error, no failed request', page.log.errors.length === 0 && page.log.failed.length === 0, JSON.stringify({ errors: page.log.errors, failed: page.log.failed }));
  check('nothing was asked of any other host', page.log.urls.every((u) => u.startsWith(BASE) || u.startsWith('data:')), JSON.stringify(page.log.urls.filter((u) => !u.startsWith(BASE) && !u.startsWith('data:')).slice(0, 5)));

  const phone = await tab(chrome, { width: 390, height: 900, mobile: true });
  await phone.goto(`${BASE}/admin/l3mon/release`);
  await sleep(2000);
  check('at phone width nothing spills sideways and each cell carries its column name',
    (await phone.run('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1')) === true && (await phone.run('document.querySelector("#release-app td").getAttribute("data-label")')) === 'No.');
  await phone.shot('release-phone.png');
} finally {
  await chrome.close();
}
finish();
