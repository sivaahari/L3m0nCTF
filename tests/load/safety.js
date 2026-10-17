// tests/load/safety.js - Safety rails and target allow-list enforcement for k6
// Refuses execution against any host not on the allow-list.

import { check } from 'k6';

const ALLOW_LIST = [
  'localhost',
  '127.0.0.1',
  'localhost:8080',
  '127.0.0.1:8080',
  'host.docker.internal',
  'host.docker.internal:8080',
  'ctfd:8000',
  'nginx:80',
  'nginx:8080',
  'play.l3m0nctf.xyz'
];

export function validateTarget(targetUrl) {
  if (!targetUrl || typeof targetUrl !== 'string') {
    throw new Error(`[SAFETY RAIL REFUSAL] Invalid or missing target URL: ${targetUrl}`);
  }

  // Extract hostname:port from URL
  let host = targetUrl
    .replace(/^https?:\/\//i, '')
    .split('/')[0]
    .toLowerCase();

  const isAllowed = ALLOW_LIST.some(allowed => {
    // the whole host name, with or without its port: a name that merely starts like an allowed one (localhost.evil.example) is not allowed
    return host === allowed.toLowerCase() || host.split(':')[0] === allowed.toLowerCase();
  });

  if (!isAllowed) {
    throw new Error(
      `[SAFETY RAIL REFUSAL] Target host "${host}" is NOT in the allow-list!\n` +
      `Allowed targets: ${ALLOW_LIST.join(', ')}\n` +
      `Aborting load test immediately to prevent accidental impact to unauthorized targets.`
    );
  }

  return true;
}

export function validateAccountPrefix(name) {
  if (!name || !name.startsWith('zzload')) {
    throw new Error(
      `[SAFETY RAIL REFUSAL] Test username "${name}" does NOT start with mandatory prefix "zzload"!\n` +
      `All synthetic load testing accounts must start with "zzload" for automated cleanup isolation.`
    );
  }
  return true;
}
