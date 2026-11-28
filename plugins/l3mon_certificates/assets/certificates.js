/* Certificates: the crew's page.
 * Reads and writes only through /api/v1/l3mon/admin/certificates. Everything is drawn with text nodes, never as markup. Times are in India
 * (IST, UTC+5:30): the set-time box takes an IST date and time and sends epoch seconds. Every button asks first. */
(function () {
  'use strict';

  var IST_OFFSET_SECONDS = 330 * 60;
  var app = document.getElementById('certificates-app');
  if (!app) return;
  var api = app.getAttribute('data-api');
  var nonce = (window.init && window.init.csrfNonce) || '';
  var statusLine = document.getElementById('certificates-status');
  var busy = false;
  var body = null;

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
    statusLine.className = isError ? 'ce-error' : '';
  }

  function problems(answer) {
    if (!answer || !answer.errors) return 'The request was refused.';
    return Object.keys(answer.errors).map(function (field) { return field + ': ' + answer.errors[field].join(' '); }).join(' | ');
  }

  function send(method, path, payload) {
    var options = { method: method, credentials: 'same-origin', headers: { 'Accept': 'application/json', 'Content-Type': 'application/json', 'CSRF-Token': nonce } };
    if (payload) options.body = JSON.stringify(payload);
    return fetch(api + path, options).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (answer) {
        if (!response.ok || !answer || answer.success !== true) throw new Error(problems(answer));
        return answer.data;
      });
    });
  }

  function release(at, question, done) {
    if (busy || !window.confirm(question)) return;
    busy = true;
    say('Saving…', false);
    send('PUT', '/release', { at: at }).then(function (state) {
      busy = false;
      say(done, false);
      render(state);
    }).catch(function (error) {
      busy = false;
      say(error.message, true);
      load();
    });
  }

  // "2026-11-29" and "18:30" read as India time -> epoch seconds, or null
  function istEpoch(day, time) {
    var d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day || '');
    var t = /^(\d{2}):(\d{2})$/.exec(time || '');
    if (!d || !t) return null;
    return Date.UTC(+d[1], +d[2] - 1, +d[3], +t[1], +t[2]) / 1000 - IST_OFFSET_SECONDS;
  }

  function render(s) {
    var state = s.released ? node('span', { 'class': 'ce-on', text: 'Showing on the studio pages' })
      : s.release_at ? node('span', { 'class': 'ce-off', text: 'Hidden until ' + s.release_at_text })
      : node('span', { 'class': 'ce-off', text: 'Hidden' });
    var facts = node('dl', {}, [
      node('dt', { text: 'Codes loaded' }), node('dd', { text: String(s.codes) + (s.codes ? '' : ' (run the import on the server first: runbook 22, step 7)') }),
      node('dt', { text: 'Now' }), node('dd', {}, [state]),
      node('dt', { text: 'Notification' }), node('dd', { text: s.announced_at ? 'sent (it is sent once only: releasing again sends none)' : 'not sent yet (sent once, when they first show)' }),
      node('dt', { text: 'Portal' }), node('dd', { text: s.base_url })
    ]);

    var now = node('button', { type: 'button', 'class': 'btn btn-primary', text: 'Release now' });
    now.addEventListener('click', function () {
      release('now', 'Show the certificates on every studio page now' + (s.announced_at ? '?' : ', and send every player the notification "Certificates are ready"?'), 'Released.');
    });
    var day = node('input', { type: 'date', 'class': 'form-control', 'aria-label': 'Day (IST)' });
    var time = node('input', { type: 'time', 'class': 'form-control', 'aria-label': 'Time (IST)' });
    var later = node('button', { type: 'button', 'class': 'btn btn-outline-primary', text: 'Release at this time' });
    later.addEventListener('click', function () {
      var at = istEpoch(day.value, time.value);
      if (at === null) { say('Give a day and a time (IST).', true); return; }
      release(at, 'Show the certificates at ' + day.value + ' ' + time.value + ' IST?', 'Set.');
    });
    var hide = node('button', { type: 'button', 'class': 'btn btn-outline-danger', text: 'Hide them' });
    hide.disabled = !s.release_at;
    hide.addEventListener('click', function () {
      release(null, 'Take the certificate card off every studio page? The codes stay valid on the portal.', 'Hidden.');
    });

    var fresh = node('div', {}, [
      node('section', {}, [node('h2', { text: 'State' }), facts]),
      node('section', {}, [
        node('h2', { text: 'Release' }),
        node('p', { text: 'Any crew account can do this; every change is in the audit trail.' }),
        node('div', { 'class': 'ce-form' }, [now]),
        node('div', { 'class': 'ce-form' }, [node('label', {}, ['Day (IST)', day]), node('label', {}, ['Time (IST)', time]), later]),
        node('div', { 'class': 'ce-form' }, [hide])
      ])
    ]);
    if (body) app.replaceChild(fresh, body); else app.appendChild(fresh);
    body = fresh;
  }

  function load() {
    return send('GET', '').then(function (s) { render(s); if (statusLine.textContent === 'Loading…') say('', false); })
      .catch(function (error) { say(error.message, true); });
  }

  load();
})();
