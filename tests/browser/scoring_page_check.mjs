// The crew's scoring page in a real browser (headless Chrome over the DevTools protocol, Node built-ins only).
// Run by test_scoring_page_browser.py, which seeds three studios and three challenges and passes the administrator's session cookie.
//   env: L3MON_BASE (default http://localhost:8080), L3MON_COOKIE, L3MON_SHOTS (a folder for two screenshots, optional),
//        L3MON_WORLD (JSON: {dyn, fix, markup, studio}: the names of the three challenges and of the studio used for the bonus),
//        CHROME (the browser, if it is not in a usual place)
import { checker, launch, sleep, tab as openTab } from './cdp.mjs';

const BASE = process.env.L3MON_BASE || 'http://localhost:8080';
const COOKIE = process.env.L3MON_COOKIE;
const SHOTS = process.env.L3MON_SHOTS;
const WORLD = JSON.parse(process.env.L3MON_WORLD);
const tab = (chrome, opts = {}) => openTab(chrome, { base: BASE, cookie: COOKIE, shots: SHOTS, ...opts });
const { check, finish } = checker();

const REASON = `Fix & "quotes" 'single' &amp; done`;

const chrome = await launch();
try {
  const page = await tab(chrome);
  await page.goto(`${BASE}/admin/l3mon/scoring`);
  await sleep(2500);

  const text = () => page.run('document.getElementById("scoring-app").innerText');
  const status = () => page.run('document.getElementById("scoring-status").textContent');
  const row = (name) => `Array.from(document.querySelectorAll("#scoring-app tr")).find((r) => { const c = r.querySelector('td[data-label="Challenge"]'); return c && c.textContent === ${JSON.stringify(name)}; })`;
  const rowText = (name) => page.run(`(${row(name)} || {}).innerText || ""`);
  const press = (name, label) => page.run(`(() => { const b = Array.from(${row(name)}.querySelectorAll("button")).find((x) => x.textContent === ${JSON.stringify(label)}); if (b.disabled) return "disabled"; b.click(); return "clicked"; })()`);
  const pressTop = (label) => page.run(`(() => { const b = Array.from(document.querySelectorAll("#scoring-app button")).find((x) => x.textContent === ${JSON.stringify(label)}); b.click(); return true; })()`);
  const typeInto = async (selector, value) => {
    await page.run(`(() => { const i = document.querySelector(${JSON.stringify(selector)}); i.focus(); i.select(); return true; })()`);
    await page.send('Input.insertText', { text: value });
  };
  const view = () => page.run(`fetch(${JSON.stringify(`${BASE}/api/v1/l3mon/admin/scoring`)}, { credentials: "same-origin" }).then((r) => r.json()).then((j) => j.data)`);
  const reasonBox = '#scoring-app input[aria-label="Reason"]';

  check('the page is called Scoring', (await page.run('document.querySelector("h1").textContent')).includes('Scoring'));
  check('the three challenges are drawn', (await rowText(WORLD.dyn)) !== '' && (await rowText(WORLD.fix)) !== '' && (await rowText(WORLD.markup)) !== '');
  check('a challenge named with markup is plain text and no element was made from it',
    (await page.run('document.querySelector("#scoring-app img") === null')) && (await rowText(WORLD.markup)).includes(WORLD.markup));
  const first = await rowText(WORLD.dyn);
  check('a dynamic value that is not its formula\'s is flagged with the formula\'s answer', /500 TRP/.test(first) && /formula says 495 TRP/.test(first), first);
  check('the summary counts the differing values', /1 with a value that is not its formula/.test(await text()), (await text()).slice(0, 200));
  await page.shot('scoring-desktop.png');

  let before = page.log.dialogs;
  await pressTop('Recalculate values');
  await sleep(1500);
  check('Recalculate puts the value right without asking, and says so', page.log.dialogs === before && /1 value put right/.test(await status()) && /495 TRP/.test(await rowText(WORLD.dyn)) && !/formula says/.test(await rowText(WORLD.dyn)), await status());

  before = page.log.dialogs;
  await press(WORLD.dyn, 'Set aside solves');
  await sleep(600);
  check('setting solves aside with no reason is refused on the page, without a dialog', page.log.dialogs === before && /Write the reason first/.test(await status()), await status());

  await typeInto(reasonBox, REASON);
  await press(WORLD.dyn, 'Set aside solves');
  await sleep(1800);
  check('with a reason it asks first, naming the challenge', page.log.dialogs === before + 1 && page.log.messages[page.log.messages.length - 1].includes(WORLD.dyn), JSON.stringify(page.log.messages.slice(-1)));
  check('and warns that players can still see the programme, so a studio could solve it again at once', /visible to players now/.test(page.log.messages[page.log.messages.length - 1]) && /withhold it first/.test(page.log.messages[page.log.messages.length - 1]), JSON.stringify(page.log.messages.slice(-1)));
  check('it sets the solves aside and says what the programme is worth now', /3 solves of ".*" set aside\. It is worth 500 TRP now \(was 495\)/.test(await status()), await status());
  const afterRevoke = await rowText(WORLD.dyn);
  check('the row shows three set aside, and Put back is available', /\b3\b.*Put back/s.test(afterRevoke) && (await page.run(`!Array.from(${row(WORLD.dyn)}.querySelectorAll("button")).find((b) => b.textContent === "Put back").disabled`)), afterRevoke);
  check('the reason is listed exactly as typed, quotes and ampersands and all', (await text()).includes(REASON));
  check('the reason box is empty again, so the next click cannot reuse it', (await page.run(`document.querySelector(${JSON.stringify(reasonBox)}).value`)) === '');
  const afterView = await view();
  check('the server agrees: three open records, the audit line names the administrator', afterView.voids.filter((v) => v.outcome === 'open').length === 3 && afterView.audit[0].action === 'scoring.revoke' && afterView.audit[0].actor.length > 0, JSON.stringify(afterView.audit[0]));

  before = page.log.dialogs;
  await press(WORLD.dyn, 'Put back');
  await sleep(1800);
  check('Put back asks first and restores all three', page.log.dialogs === before + 1 && /3 restored, 0 skipped, 0 superseded/.test(await status()) && /495 TRP/.test(await rowText(WORLD.dyn)), await status());

  check('and Put back shows the line the studios will read (the standard one, as no reason was typed)', page.log.messages[page.log.messages.length - 1].includes('The crew put your solve back; it counts again.'), JSON.stringify(page.log.messages.slice(-1)));

  // the bonus form
  const chooseStudio = () => page.run(`(() => { const s = document.querySelector('#scoring-app select[aria-label="Studio"]'); const o = Array.from(s.options).find((x) => x.textContent === ${JSON.stringify(WORLD.studio)}); s.value = o.value; s.dispatchEvent(new Event("change")); return s.value !== ""; })()`);
  const fillBonus = (trp, message) => page.run(`(() => { document.querySelector('#scoring-app input[aria-label="TRP"]').value = ${JSON.stringify(trp)}; document.querySelector('#scoring-app input[aria-label="Message for the studio"]').value = ${JSON.stringify(message)}; return true; })()`);
  check('the studio list has the seeded studio', await chooseStudio());
  check('the member list offers the whole studio first and the members after', (await page.run(`Array.from(document.querySelector('#scoring-app select[aria-label="Member"]').options).map((o) => o.textContent)`))[0].startsWith('The whole studio'));

  await fillBonus('50', '<b>bold</b>');
  before = page.log.dialogs;
  await pressTop('Give bonus');
  await sleep(1500);
  check('a message with markup is refused plainly and nothing is given', page.log.dialogs === before + 1 && /may not contain < or >/.test(await status()) && (await view()).bonuses.length === 0, await status());

  await fillBonus('0', 'ok');
  before = page.log.dialogs;
  await pressTop('Give bonus');
  await sleep(300);
  check('0 TRP is refused on the page before anything is asked', page.log.dialogs === before && /whole number from -1000 to 1000/.test(await status()), await status());

  await fillBonus('50', 'Found a bug (login page)');
  before = page.log.dialogs;
  await page.run(`(() => { const b = Array.from(document.querySelectorAll("#scoring-app button")).find((x) => x.textContent === "Give bonus"); b.click(); b.click(); return true; })()`);
  await sleep(2000);
  const afterBonus = await view();
  check('a double click asks once and gives one bonus', page.log.dialogs === before + 1 && afterBonus.bonuses.length === 1, `dialogs +${page.log.dialogs - before}, bonuses ${afterBonus.bonuses.length}`);
  check('the bonus is listed with its private message, its title and who gave it', /Bonus \+50 TRP/.test(await text()) && (await text()).includes('Found a bug (login page)') && afterBonus.bonuses[0].given_by.length > 0);
  check('the form is emptied after a bonus', (await page.run(`document.querySelector('#scoring-app input[aria-label="Message for the studio"]').value`)) === '');

  // a refresh keeps what is being typed
  await typeInto(reasonBox, 'typing a reason');
  await fillBonus('7', 'half-written message');
  await pressTop('Refresh');
  await sleep(1500);
  check('Refresh keeps the reason and the half-written bonus', (await page.run(`document.querySelector(${JSON.stringify(reasonBox)}).value`)) === 'typing a reason' &&
    (await page.run(`document.querySelector('#scoring-app input[aria-label="Message for the studio"]').value`)) === 'half-written message' &&
    (await page.run(`document.querySelector('#scoring-app select[aria-label="Studio"]').selectedOptions[0].textContent`)) === WORLD.studio);

  check('no script error, no failed request', page.log.errors.length === 0 && page.log.failed.length === 0, JSON.stringify({ errors: page.log.errors, failed: page.log.failed }));
  check('nothing was asked of any other host', page.log.urls.every((u) => u.startsWith(BASE) || u.startsWith('data:')), JSON.stringify(page.log.urls.filter((u) => !u.startsWith(BASE) && !u.startsWith('data:')).slice(0, 5)));
  check('no word of the page says points', !/\bpoints?\b/i.test(await text()));

  const phone = await tab(chrome, { width: 390, height: 900, mobile: true });
  await phone.goto(`${BASE}/admin/l3mon/scoring`);
  await sleep(2500);
  check('at phone width nothing spills sideways and each cell carries its column name',
    (await phone.run('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1')) === true && (await phone.run('document.querySelector("#scoring-app td").getAttribute("data-label")')) === 'Challenge');
  await phone.shot('scoring-phone.png');
} finally {
  await chrome.close();
}
finish();
