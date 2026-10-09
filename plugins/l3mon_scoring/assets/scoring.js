/* Scoring: the crew's page.
 * Reads and writes only through /api/v1/l3mon/admin/scoring. Everything on the page is drawn with text nodes, never as markup, so a
 * challenge, a studio or a message that contains markup stays plain text. Times are shown in India (IST, UTC+5:30). */
(function () {
  'use strict';

  var IST_OFFSET_MINUTES = 330;
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var REFRESH_MS = 30000;

  var app = document.getElementById('scoring-app');
  if (!app) return;
  var api = app.getAttribute('data-api');
  var nonce = (window.init && window.init.csrfNonce) || '';
  var statusLine = document.getElementById('scoring-status');
  var data = null;
  var busy = false;
  var shell = null; // the parts that are built once and kept: the summary, the reason box and the bonus form

  function node(tag, props, kids) {
    var n = document.createElement(tag);
    Object.keys(props || {}).forEach(function (k) {
      if (k === 'text') n.textContent = props[k];
      else if (k === 'class') n.className = props[k];
      else n.setAttribute(k, props[k]);
    });
    (kids || []).forEach(function (kid) {
      if (kid !== null && kid !== undefined && kid !== false) n.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    });
    return n;
  }

  function say(text, isError) {
    statusLine.textContent = text;
    statusLine.className = isError ? 'sc-error' : '';
  }

  function istText(epoch) {
    if (epoch === null || epoch === undefined) return '';
    var d = new Date((epoch + IST_OFFSET_MINUTES * 60) * 1000);
    var hh = ('0' + d.getUTCHours()).slice(-2);
    var mm = ('0' + d.getUTCMinutes()).slice(-2);
    return d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()] + ' ' + d.getUTCFullYear() + ', ' + hh + ':' + mm + ' IST';
  }

  function problems(body) {
    if (!body || !body.errors) return 'The request was refused.';
    return Object.keys(body.errors).map(function (field) { return field + ': ' + body.errors[field].join(' '); }).join(' | ');
  }

  function send(method, path, payload) {
    var options = { method: method, credentials: 'same-origin', headers: { 'Accept': 'application/json', 'Content-Type': 'application/json', 'CSRF-Token': nonce } };
    if (payload) options.body = JSON.stringify(payload);
    return fetch(api + path, options).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (body) {
        if (!response.ok || !body || body.success !== true) throw new Error(problems(body));
        return body.data;
      });
    });
  }

  function load() {
    if (busy) return Promise.resolve();
    return send('GET', '').then(function (fresh) {
      data = fresh;
      render(); // a refresh never touches the status line: what the last action said stays until the next one speaks
    }).catch(function (error) { say(error.message, true); });
  }

  // One change at a time: busy is set before anything else, so a second click while the first is on its way does nothing.
  function act(path, payload, done) {
    if (busy) return;
    busy = true;
    say('Saving…', false);
    send('POST', path, payload).then(function (result) {
      busy = false;
      say(done(result), false);
      return load();
    }).catch(function (error) {
      busy = false;
      say(error.message, true);
      return load();
    });
  }

  function table(headings, rows) {
    return node('table', { 'class': 'table table-sm' }, [
      node('thead', {}, [node('tr', {}, headings.map(function (h) { return node('th', { text: h }); }))]),
      node('tbody', {}, rows)
    ]);
  }

  function cells(headings, values) {
    return node('tr', {}, values.map(function (v, i) {
      return node('td', { 'data-label': headings[i] }, v === null || v === undefined ? [] : [typeof v === 'string' || typeof v === 'number' ? String(v) : v]);
    }));
  }

  function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }

  function reasonText() { return shell.reasonBox.value.trim(); }

  // ---- values -------------------------------------------------------------------------------------------------------------------

  function valuesSection() {
    var heads = ['Challenge', 'Kind', 'Worth now', 'Starts → floor', 'Studios', 'Check', 'Set aside', 'Change'];
    var rows = data.challenges.map(function (c) {
      var curve = c.type === 'dynamic' ? c.initial + ' → ' + c.floor + ' over ' + plural(c.decay, 'solve') : '—';
      var studios = String(c.solves) + (c.held > c.solves ? ' (' + c.held + ' held)' : '');
      var check = c.value === c.wanted ? 'ok' : node('span', { 'class': 'sc-differs', text: 'formula says ' + c.wanted + ' TRP' });
      var revoke = node('button', { type: 'button', 'class': 'btn btn-sm btn-outline-danger', text: 'Set aside solves' });
      var restore = node('button', { type: 'button', 'class': 'btn btn-sm btn-outline-success', text: 'Put back' });
      if (c.held === 0) revoke.setAttribute('disabled', 'disabled');
      if (c.open_voids === 0) restore.setAttribute('disabled', 'disabled');
      revoke.addEventListener('click', function () {
        if (busy) return; // a second click while the first is on its way asks nothing and does nothing
        var reason = reasonText();
        if (!reason) { say('Write the reason first: every studio that loses a solve reads it.', true); shell.reasonBox.focus(); return; }
        var warning = c.state === 'visible'
          ? ' It is visible to players now, so a studio can solve it again at once. If it is broken, withhold it first on the Release control page.'
          : '';
        if (!window.confirm('Set aside every solve of "' + c.name + '"? ' + plural(c.held, 'studio') + ' lose their TRP for it now and are told why. Nothing is deleted; you can put them back.' + warning)) return;
        act('/revoke', { challenge_id: c.id, reason: reason }, function (r) {
          shell.reasonBox.value = '';
          return plural(r.voided, 'solve') + ' of "' + r.name + '" set aside. It is worth ' + r.value_after + ' TRP now (was ' + r.value_before + ').';
        });
      });
      restore.addEventListener('click', function () {
        if (busy) return;
        var reason = reasonText();
        var told = reason ? '"' + reason + '"' : 'the standard line "' + data.restore_note + '"';
        if (!window.confirm('Put back the solves set aside for "' + c.name + '"? The studios are told ' + told + '.')) return;
        act('/restore', reason ? { challenge_id: c.id, reason: reason } : { challenge_id: c.id }, function (r) {
          shell.reasonBox.value = '';
          return r.restored + ' restored, ' + r.skipped + ' skipped, ' + r.superseded + ' superseded. "' + r.name + '" is worth ' + r.value_after + ' TRP now (was ' + r.value_before + ').';
        });
      });
      return cells(heads, [c.name, c.type, c.value + ' TRP', curve, studios, check, c.open_voids || '—', node('div', { 'class': 'sc-actions' }, [revoke, restore])]);
    });
    return node('section', {}, [
      node('h2', { text: 'What each programme is worth' }),
      node('p', { 'class': 'sc-muted', text: 'A dynamic programme is worth the same to every studio that solved it, and falls as more studios solve it. "Held" counts every solve, including studios that are banned or hidden; only the others move the value.' }),
      data.challenges.length ? table(heads, rows) : node('p', { text: 'There are no challenges yet.' })
    ]);
  }

  // ---- the bonus form (built once) ----------------------------------------------------------------------------------------------

  function buildBonusForm() {
    var studio = node('select', { 'class': 'form-control form-control-sm', 'aria-label': 'Studio' });
    var member = node('select', { 'class': 'form-control form-control-sm', 'aria-label': 'Member' });
    var trp = node('input', { type: 'number', min: '-1000', max: '1000', step: '1', 'class': 'form-control form-control-sm', 'aria-label': 'TRP' });
    var message = node('input', { type: 'text', maxlength: '200', 'class': 'form-control form-control-sm', 'aria-label': 'Message for the studio' });
    var give = node('button', { type: 'button', 'class': 'btn btn-sm btn-primary', text: 'Give bonus' });
    var form = node('section', {}, [
      node('h2', { text: 'Give a bonus' }),
      node('p', { 'class': 'sc-muted', text: 'TRP from -1000 to 1000, never 0. The message is private: only that studio and the crew read it. The studio ranking sees only the title, such as "Bonus +50 TRP".' }),
      node('div', { 'class': 'sc-form' }, [
        node('label', {}, ['Studio', studio]),
        node('label', {}, ['Member', member]),
        node('label', {}, ['TRP', trp]),
        node('label', { 'class': 'sc-message' }, ['Message', message]),
        give
      ])
    ]);
    studio.addEventListener('change', fillMembers);
    give.addEventListener('click', function () {
      if (busy) return;
      var teamId = parseInt(studio.value, 10);
      var amount = Number(trp.value);
      if (!teamId) { say('Choose a studio.', true); return; }
      if (!trp.value || !Number.isInteger(amount) || amount === 0 || Math.abs(amount) > 1000) { say('TRP must be a whole number from -1000 to 1000, and not 0.', true); return; }
      if (!message.value.trim()) { say('Write the message: the studio reads it.', true); return; }
      var name = studio.options[studio.selectedIndex].textContent;
      if (!window.confirm('Give "' + name + '" ' + (amount > 0 ? '+' : '') + amount + ' TRP? The message is private to that studio.')) return;
      var payload = { team_id: teamId, trp: amount, message: message.value.trim() };
      if (member.value) payload.user_id = parseInt(member.value, 10);
      act('/bonus', payload, function (r) {
        message.value = '';
        trp.value = '';
        return r.title + ' given.';
      });
    });
    shell.form = { studio: studio, member: member, section: form, teamsKey: '' };
    return form;
  }

  function fillMembers() {
    var team = null;
    var chosen = parseInt(shell.form.studio.value, 10);
    data.teams.forEach(function (t) { if (t.id === chosen) team = t; });
    var keep = shell.form.member.value;
    while (shell.form.member.firstChild) shell.form.member.removeChild(shell.form.member.firstChild);
    shell.form.member.appendChild(node('option', { value: '', text: 'The whole studio (on the captain\'s account)' }));
    ((team && team.members) || []).forEach(function (m) { shell.form.member.appendChild(node('option', { value: String(m.id), text: m.name })); });
    shell.form.member.value = keep;
    if (shell.form.member.value !== keep) shell.form.member.value = '';
  }

  function fillStudios() {
    var key = JSON.stringify(data.teams);
    if (shell.form.teamsKey === key) return; // the studios have not changed: leave the choice (and what is typed) alone
    shell.form.teamsKey = key;
    var keep = shell.form.studio.value;
    while (shell.form.studio.firstChild) shell.form.studio.removeChild(shell.form.studio.firstChild);
    shell.form.studio.appendChild(node('option', { value: '', text: 'Choose a studio…' }));
    data.teams.forEach(function (t) {
      shell.form.studio.appendChild(node('option', { value: String(t.id), text: t.name + (t.banned ? ' (banned)' : t.hidden ? ' (hidden)' : '') }));
    });
    shell.form.studio.value = keep;
    if (shell.form.studio.value !== keep) shell.form.studio.value = '';
    fillMembers();
  }

  // ---- the lists ----------------------------------------------------------------------------------------------------------------

  function voidsSection() {
    var heads = ['Set aside', 'Programme', 'Studio', 'Member', 'Reason', 'Outcome', 'By', 'Put back'];
    var rows = data.voids.map(function (v) {
      return cells(heads, [istText(v.voided_at), v.challenge, v.team, v.user || '(gone)', v.reason, v.outcome, v.voided_by || '(gone)',
        v.restored_at ? istText(v.restored_at) + (v.restored_by ? ' by ' + v.restored_by : '') : '']);
    });
    return node('section', {}, [node('h2', { text: 'Solves set aside (newest 50)' }), data.voids.length ? table(heads, rows) : node('p', { 'class': 'p-3', text: 'Nothing has been set aside.' })]);
  }

  function bonusesSection() {
    var heads = ['Given', 'Studio', 'Member', 'Scope', 'Title', 'Message', 'By'];
    var rows = data.bonuses.map(function (b) {
      return cells(heads, [istText(b.given_at), b.team, b.user, b.scope === 'team' ? 'whole studio' : 'one member', b.title, b.message, b.given_by || '(gone)']);
    });
    return node('section', {}, [node('h2', { text: 'Bonuses (newest 30)' }), data.bonuses.length ? table(heads, rows) : node('p', { 'class': 'p-3', text: 'No bonus has been given.' })]);
  }

  function auditSection() {
    var heads = ['When', 'Who', 'What', 'Where', 'Detail'];
    var rows = data.audit.map(function (a) { return cells(heads, [istText(a.at), a.actor, a.action, a.target, a.detail]); });
    return node('section', {}, [node('h2', { text: 'Latest scoring changes' }), data.audit.length ? table(heads, rows) : node('p', { 'class': 'p-3', text: 'No scoring change has been made yet.' })]);
  }

  // ---- the page -----------------------------------------------------------------------------------------------------------------

  function buildShell() {
    var reasonBox = node('input', { type: 'text', 'class': 'form-control form-control-sm', maxlength: '500', placeholder: 'Reason (the studios read it when you set a solve aside)', 'aria-label': 'Reason' });
    var refresh = node('button', { type: 'button', 'class': 'btn btn-sm btn-outline-primary', text: 'Refresh' });
    refresh.addEventListener('click', function () { load(); });
    var recalc = node('button', { type: 'button', 'class': 'btn btn-sm btn-outline-secondary', text: 'Recalculate values' });
    recalc.addEventListener('click', function () {
      act('/recalculate', {}, function (r) { return r.changed.length ? plural(r.changed.length, 'value') + ' put right.' : 'Every value was already right.'; });
    });
    var summary = node('p', { 'class': 'sc-summary' });
    var top = node('div', {});
    var bottom = node('div', {});
    shell = { reasonBox: reasonBox, summary: summary, top: top, bottom: bottom };
    var form = buildBonusForm();
    say('', false); // "Loading the scores…" has done its job
    while (app.firstChild) app.removeChild(app.firstChild);
    app.appendChild(statusLine);
    app.appendChild(node('div', { 'class': 'sc-bar' }, [summary, reasonBox, refresh, recalc]));
    app.appendChild(top);
    app.appendChild(form);
    app.appendChild(bottom);
  }

  function fill(box, children) {
    while (box.firstChild) box.removeChild(box.firstChild);
    children.forEach(function (child) { box.appendChild(child); });
  }

  function render() {
    if (!data) return;
    if (!shell) buildShell(); // built once: a refresh only replaces the tables, so the reason, the choice and the message typed are kept
    var differ = data.challenges.filter(function (c) { return c.value !== c.wanted; }).length;
    shell.summary.textContent = 'Now ' + istText(data.now) + ' · ' + plural(data.challenges.length, 'challenge') + ' · ' +
      (differ ? differ + ' with a value that is not its formula\'s' : 'every value is its formula\'s');
    fill(shell.top, [valuesSection()]);
    fillStudios();
    fill(shell.bottom, [voidsSection(), bonusesSection(), auditSection()]);
  }

  load();
  window.setInterval(load, REFRESH_MS);
}());
