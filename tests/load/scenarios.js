// tests/load/scenarios.js - k6 load test suite for L3m0nCTF 2026
// Implements registration bursts, tick polling, flag submission bursts, and scoreboard reads.
// Safety rails enforce target allow-list validation before any request is made.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { validateTarget, validateAccountPrefix } from './safety.js';

// Consider only 5xx or network drops as failed requests; 401/403/404/400 are valid app responses
http.setResponseCallback(http.expectedStatuses(200, 201, 204, 301, 302, 304, 400, 401, 403, 404));

const TARGET = __ENV.TARGET_URL || 'http://localhost:8080';
validateTarget(TARGET);

const PROFILE = __ENV.PROFILE || 'laptop'; // 'laptop' (scaled) or 'full' (2x target)

// Scenario configuration
let optionsConfig = {};

if (PROFILE === 'full') {
  // Production 2x target profile (needs high-spec distributed runner, cannot run on laptop)
  optionsConfig = {
    scenarios: {
      registration_burst: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          { duration: '30s', target: 500 },
          { duration: '60s', target: 2000 },
          { duration: '30s', target: 0 },
        ],
        exec: 'registrationBurst',
      },
      tick_polling: {
        executor: 'constant-vus',
        vus: 3000,
        duration: '2m',
        exec: 'tickPolling',
      },
      flag_submissions: {
        executor: 'constant-arrival-rate',
        rate: 140,
        timeUnit: '1s',
        duration: '2m',
        preAllocatedVUs: 200,
        maxVUs: 500,
        exec: 'flagSubmissions',
      },
      scoreboard_reads: {
        executor: 'constant-vus',
        vus: 500,
        duration: '2m',
        exec: 'scoreboardReads',
      },
    },
    thresholds: {
      http_req_failed: ['rate<0.001'], // < 0.1% errors
      'http_req_duration{type:read}': ['p(95)<300', 'p(99)<1000'], // p95 < 300ms, p99 < 1s
      'http_req_duration{type:flag}': ['p(95)<500'],
    },
  };
} else {
  // Scaled laptop profile: safe for local dev execution
  optionsConfig = {
    scenarios: {
      tick_polling: {
        executor: 'constant-vus',
        vus: 15,
        duration: '15s',
        exec: 'tickPolling',
      },
      flag_submissions: {
        executor: 'constant-arrival-rate',
        rate: 10,
        timeUnit: '1s',
        duration: '15s',
        preAllocatedVUs: 10,
        maxVUs: 30,
        exec: 'flagSubmissions',
      },
      scoreboard_reads: {
        executor: 'constant-vus',
        vus: 5,
        duration: '15s',
        exec: 'scoreboardReads',
      },
    },
    thresholds: {
      http_req_failed: ['rate<0.01'], // < 1% on local dev
      'http_req_duration{type:read}': ['p(95)<500'],
    },
  };
}

export const options = optionsConfig;

const HOST_HEADER = __ENV.HOST_HEADER || '';

function reqHeaders(custom = {}) {
  const h = Object.assign({}, custom);
  if (HOST_HEADER) {
    h['Host'] = HOST_HEADER;
  }
  return h;
}

// Scenario 1: Registration Burst
export function registrationBurst() {
  const vuId = __VU;
  const iterId = __ITER;
  const username = `zzload_reg_${vuId}_${iterId}`;
  validateAccountPrefix(username);

  const res = http.get(`${TARGET}/register`, {
    headers: reqHeaders(),
    tags: { type: 'read' },
  });
  check(res, {
    'register page loaded': (r) => r.status === 200,
  });
  sleep(1);
}

// Scenario 2: Tick Polling & Board Refetch
export function tickPolling() {
  const tickRes = http.get(`${TARGET}/api/v1/l3mon/ticks`, {
    headers: reqHeaders({ 'Accept': 'application/json' }),
    tags: { type: 'read' },
  });

  check(tickRes, {
    'tick poll succeeded': (r) => r.status === 200,
  });

  // When tick returns, simulate client conditional refetch
  if (Math.random() < 0.25) {
    const boardRes = http.get(`${TARGET}/api/v1/l3mon/board`, {
      headers: reqHeaders({ 'Accept': 'application/json' }),
      tags: { type: 'read' },
    });
    check(boardRes, {
      'board fetch status': (r) => r.status === 200 || r.status === 304 || r.status === 403,
    });
  }

  // 15s interval with jitter
  sleep(12 + Math.random() * 6);
}

// Scenario 3: Flag Submissions Burst
export function flagSubmissions() {
  const payload = JSON.stringify({
    challenge_id: 1,
    submission: `test_flag_${Math.random().toString(36).substring(7)}`,
  });

  const params = {
    headers: reqHeaders({
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    }),
    tags: { type: 'flag' },
  };

  const res = http.post(`${TARGET}/api/v1/challenges/attempt`, payload, params);
  check(res, {
    'flag submission processed (not starved by 503)': (r) => r.status !== 503,
  });
}

// Scenario 4: Scoreboard Reads
export function scoreboardReads() {
  const res = http.get(`${TARGET}/api/v1/l3mon/scoreboard`, {
    headers: reqHeaders({ 'Accept': 'application/json' }),
    tags: { type: 'read' },
  });

  check(res, {
    'scoreboard read status': (r) => r.status === 200 || r.status === 304,
  });

  sleep(5 + Math.random() * 5);
}

// Custom JSON summary exporter
export function handleSummary(data) {
  const summaryOut = {
    'stdout': textSummary(data, { indent: ' ', enableColors: true }),
  };
  const summaryFile = __ENV.SUMMARY_PATH || '/scripts/summary.json';
  summaryOut[summaryFile] = JSON.stringify(data, null, 2);
  return summaryOut;
}

function textSummary(data) {
  let lines = [];
  lines.push('=== L3m0nCTF 2026 k6 Load Test Summary ===');
  lines.push(`Target: ${TARGET} | Profile: ${PROFILE}`);
  if (data.metrics.http_reqs) {
    lines.push(`Total Requests : ${data.metrics.http_reqs.values.count}`);
    lines.push(`Req Rate       : ${data.metrics.http_reqs.values.rate.toFixed(2)} req/s`);
  }
  if (data.metrics && data.metrics.http_req_duration && data.metrics.http_req_duration.values) {
    const d = data.metrics.http_req_duration.values;
    const p50 = d['p(50)'] || d.med || d.avg || 0;
    const p95 = d['p(95)'] || d.max || 0;
    const p99 = d['p(99)'] || d.max || 0;
    lines.push(`Duration (p50) : ${p50.toFixed(2)} ms`);
    lines.push(`Duration (p95) : ${p95.toFixed(2)} ms`);
    lines.push(`Duration (p99) : ${p99.toFixed(2)} ms`);
  }
  if (data.metrics && data.metrics.http_req_failed && data.metrics.http_req_failed.values) {
    const rate = data.metrics.http_req_failed.values.rate || 0;
    lines.push(`Error Rate     : ${(rate * 100).toFixed(2)} %`);
  }
  lines.push('==========================================');
  return lines.join('\n');
}
