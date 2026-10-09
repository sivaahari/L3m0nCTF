/* Release control: the crew's page.
 * Reads and writes only through /api/v1/l3mon/admin/release. Everything on the page is drawn with text nodes, never as markup,
 * so a channel or programme whose name contains markup stays plain text. Times are typed and shown in India (IST, UTC+5:30). */
(function () {
  'use strict';

  var IST_OFFSET_MINUTES = 330;
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var REFRESH_MS = 15000;

  var app = document.getElementById('release-app');
  if (!app) return;
  var api = app.getAttribute('data-api');
  var nonce = (window.init && window.init.csrfNonce) || '';
  var statusLine = document.getElementById('release-status');
  var plan = null;
  var busy = false;
  var editing = null; // 'kind:id' of the entry whose schedule box is open
  var shell = null; // the parts of the page that are built once and kept: the summary, the reason box and the buttons above the plan

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
    statusLine.className = isError ? 'rc-error' : '';
  }

  function istText(epoch) {
    var d = new Date((epoch + IST_OFFSET_MINUTES * 60) * 1000);
    var hh = ('0' + d.getUTCHours()).slice(-2);
    var mm = ('0' + d.getUTCMinutes()).slice(-2);
    return d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()] + ' ' + d.getUTCFullYear() + ', ' + hh + ':' + mm + ' IST';
  }

  function istInputValue(epoch) {
    return new Date((epoch + IST_OFFSET_MINUTES * 60) * 1000).toISOString().slice(0, 16);
  }

  function istToEpoch(value) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || '');
    if (!m) return null;
    return Math.floor(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) / 1000) - IST_OFFSET_MINUTES * 60;
  }

  function problems(body) {
    if (!body || !body.errors) return 'The request was refused.';
    return Object.keys(body.errors).map(function (field) { return field + ': ' + body.errors[field].join(' '); }).join(' | ');
  }

  function send(method, payload) {
    var options = { method: method, credentials: 'same-origin', headers: { 'Accept': 'application/json', 'Content-Type': 'application/json', 'CSRF-Token': nonce } };
    if (payload) options.body = JSON.stringify(payload);
    return fetch(api, options).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (body) {
        if (!response.ok || !body || body.success !== true) throw new Error(problems(body));
        return body.data;
      });
    });
  }

  function change(changes) {
    if (busy) return;
    busy = true;
    editing = null;
    say('Saving…', false);
    send('PUT', { changes: changes, reason: shell ? shell.reasonBox.value : '' }).then(function (data) {
      plan = data;
      if (shell) shell.reasonBox.value = ''; // a reason belongs to the change it was typed for, never to the next click
      var r = data.result;
      say(r.changed === 0 ? 'Nothing needed changing.' : r.changed + ' changed. ' + r.shown + ' now on air, ' + r.hidden + ' pulled back.', false);
      busy = false;
      render();
    }).catch(function (error) {
      busy = false;
      say(error.message, true);
      render();
    });
  }

  function load() {
    if (busy || editing) return;
    send('GET').then(function (data) {
      plan = data;
      if (!statusLine.className) say('', false);
      render();
    }).catch(function (error) { say(error.message, true); });
  }

  function chip(entry) {
    var label = entry.state === 'scheduled' ? 'scheduled for ' + entry.at_ist : entry.state;
    var kind = entry.state === 'released' ? 'badge-success' : entry.state === 'scheduled' ? 'badge-info' : 'badge-secondary';
    return node('span', { 'class': 'badge ' + kind, text: label });
  }

  function actions(kind, item) {
    var key = kind + ':' + item.id;
    var box = node('div', { 'class': 'rc-actions' });
    box.appendChild(node('button', { type: 'button', 'class': 'btn btn-sm btn-success', text: 'Release now' }));
    box.appendChild(node('button', { type: 'button', 'class': 'btn btn-sm btn-outline-secondary', text: 'Withhold' }));
    box.appendChild(node('button', { type: 'button', 'class': 'btn btn-sm btn-outline-info', text: 'Schedule…' }));
    var buttons = box.querySelectorAll('button');
    buttons[0].addEventListener('click', function () {
      if (!window.confirm('Put "' + item.name + '" on air now? Players will see it at once, and it cannot be unseen.')) return;
      change([{ kind: kind, id: item.id, mode: 'release' }]);
    });
    buttons[1].addEventListener('click', function () { change([{ kind: kind, id: item.id, mode: 'withhold' }]); });
    buttons[2].addEventListener('click', function () { editing = key; render(); });
    if (editing === key) {
      var input = node('input', { type: 'datetime-local', 'class': 'form-control form-control-sm', 'aria-label': 'Release time in India (IST)', value: istInputValue(plan.now + 600) });
      var set = node('button', { type: 'button', 'class': 'btn btn-sm btn-primary', text: 'Set' });
      var cancel = node('button', { type: 'button', 'class': 'btn btn-sm btn-link', text: 'Cancel' });
      set.addEventListener('click', function () {
        var at = istToEpoch(input.value);
        if (at === null || at <= plan.now) { say('Choose a time in India that is still to come.', true); return; }
        change([{ kind: kind, id: item.id, mode: 'schedule', at: at }]);
      });
      cancel.addEventListener('click', function () { editing = null; render(); });
      box.appendChild(node('span', { 'class': 'rc-when' }, [input, set, cancel]));
    }
    return box;
  }

  function channelBlock(ch) {
    var rows = ch.programmes.map(function (p) {
      var cls = p.on_air ? 'rc-onair' : 'rc-off';
      return node('tr', {}, [
        node('td', { 'data-label': 'No.', text: String(p.number) }),
        node('td', { 'data-label': 'Slug', text: p.slug }),
        node('td', { 'data-label': 'Challenge', text: p.name }),
        node('td', { 'data-label': 'Setting' }, [chip(p)]),
        node('td', { 'data-label': 'Players', 'class': cls, text: p.visible ? 'players see it' : p.on_air ? 'on air (CTFd is catching up)' : 'off air' }),
        node('td', { 'data-label': 'Change' }, [actions('programme', p)])
      ]);
    });
    var table = node('table', { 'class': 'table table-sm' }, [
      node('thead', {}, [node('tr', {}, ['No.', 'Slug', 'Challenge', 'Setting', 'Players', 'Change'].map(function (h) { return node('th', { text: h }); }))]),
      node('tbody', {}, rows)
    ]);
    var head = node('header', {}, [
      node('h2', { text: 'CH ' + ch.position + ' · ' + ch.name + (ch.kind === 'sponsored' ? ' (sponsored by ' + (ch.sponsor_name || '?') + ')' : '') }),
      chip(ch),
      node('span', { 'class': ch.on_air ? 'rc-onair' : 'rc-off', text: ch.on_air ? 'channel on air' : 'channel off air' }),
      actions('channel', ch)
    ]);
    return node('section', { 'class': 'rc-channel' }, [head, ch.programmes.length ? table : node('p', { 'class': 'p-3 mb-0', text: 'No programmes on this channel yet.' })]);
  }

  function buildShell() {
    var reasonBox = node('input', { type: 'text', 'class': 'form-control form-control-sm', maxlength: '200', placeholder: 'Reason (kept in the audit trail)', 'aria-label': 'Reason for the change' });
    var refresh = node('button', { type: 'button', 'class': 'btn btn-sm btn-outline-primary', text: 'Refresh' });
    refresh.addEventListener('click', function () { editing = null; load(); });
    var panic = node('button', { type: 'button', 'class': 'btn btn-sm btn-danger', text: 'Withhold every channel' });
    panic.addEventListener('click', function () {
      if (!window.confirm('Take every channel off air for players?')) return;
      change(plan.channels.map(function (ch) { return { kind: 'channel', id: ch.id, mode: 'withhold' }; }));
    });
    var summary = node('p', { 'class': 'rc-summary' });
    var body = node('div', {});
    while (app.firstChild) app.removeChild(app.firstChild);
    app.appendChild(statusLine);
    app.appendChild(node('div', { 'class': 'rc-bar' }, [summary, reasonBox, refresh, panic]));
    app.appendChild(body);
    shell = { summary: summary, reasonBox: reasonBox, body: body };
  }

  function render() {
    if (!plan) return;
    if (!shell) buildShell(); // built once: a poll only replaces what is below, so the reason box keeps its text and its focus
    var counts = plan.counts;
    shell.summary.textContent = 'Now ' + istText(plan.now) + ' · broadcast ' + plan.phase.state + ' · ' + counts.on_air +
      (counts.on_air === 1 ? ' programme' : ' programmes') + ' on air, ' + counts.coming + ' coming up';
    var loose = plan.loose.length ? node('section', { 'class': 'rc-loose' }, [
      node('h2', { 'class': 'h5', text: 'Not on any channel (so not on air)' }),
      node('ul', {}, plan.loose.map(function (c) { return node('li', { text: c.name }); }))
    ]) : null;
    var audit = node('section', { 'class': 'rc-audit' }, [
      node('h2', { 'class': 'h5', text: 'Latest changes' }),
      node('table', { 'class': 'table table-sm' }, [
        node('thead', {}, [node('tr', {}, ['When', 'Who', 'What', 'Where', 'Detail'].map(function (h) { return node('th', { text: h }); }))]),
        node('tbody', {}, plan.audit.map(function (a) {
          return node('tr', {}, [node('td', { text: istText(a.at) }), node('td', { text: a.actor }), node('td', { text: a.action }), node('td', { text: a.target }), node('td', { text: a.detail })]);
        }))
      ])
    ]);
    var children = (plan.channels.length ? plan.channels.map(channelBlock) : [node('p', { text: 'There are no channels yet. Load the plan first (PUT /api/v1/l3mon/admin/programmes).' })]).concat([loose, audit]);
    while (shell.body.firstChild) shell.body.removeChild(shell.body.firstChild);
    children.forEach(function (child) { if (child) shell.body.appendChild(child); });
  }

  load();
  window.setInterval(load, REFRESH_MS);
}());
