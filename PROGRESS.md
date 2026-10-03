# L3m0nCTF 2026: Progress

Last updated: 2026-10-03

## Snapshot

Phase: research and design (tasks 1 and 2). Prelims are about six weeks out, so the plan below is built backwards from them. **P** is the prelims start; exact dates are not confirmed yet.

## Plan

| When | Focus | Gate |
|------|-------|------|
| P-6 to P-5 | Tasks 1 and 2: platform analysis, CTF survey, five story chains | Story and base-platform decision approved |
| P-5 to P-4 | Task 3: layout brainstorm and visual direction. Architecture spec. Author kit v0 (challenge format, Docker template, CI check) so the 30 authors can start | Visual direction approved |
| P-4 to P-2 | Task 4 build: theme, story overlay, instance orchestration, CTFtime feed, anti-cheat, ops. First staging on `pre-deployment` | Staging usable by authors |
| P-2 to P-1 | Load test at 2x target, security review, challenge intake, bug fixing. Feature freeze at P-1 | Load test passes |
| P-1 to P | Dress rehearsal, backups and monitoring, runbooks | Go / no-go |
| P | Prelims | |
| After P | Finalist selection, finals preparation | Finals date not fixed yet |

## Tasks

| # | Task | Status | Output |
|---|------|--------|--------|
| 1 | Analyse CTFd core and last year's platform | in progress | `docs/analysis/` |
| 2 | Survey story-driven CTFs, propose five story chains | in progress | `docs/research/` (public precedent survey); story pitches stay private |
| 3 | Layout and UX brainstorm | waiting on 2 | |
| 4 | Build and verify the platform | waiting on 3 | |

## Decisions

| Date | Decision |
|------|----------|
| 2026-10-03 | Prelims around mid-November, 24 hours, online, Jeopardy-style |
| 2026-10-03 | Finals are Jeopardy-style over the internet with finalists on campus, so one deployment serves both rounds and no offline/LAN build is needed. Finals length and finalist count are open |
| 2026-10-03 | Cartoon pool is made-in-India toons plus imported India-iconics. Mythology and religious figures are out |
| 2026-10-03 | Homage cast: original characters with parody names, original art |
| 2026-10-03 | Story is an overlay on an open board: every challenge stays independently solvable, solves unlock lore and move a progress meter |
| 2026-10-03 | Niche tracks in scope: AI/LLM security, blockchain/Web3, hardware/IoT/RF |
| 2026-10-03 | Work goes to `pre-deployment` first; `main` only after testing and approval |

## Findings and issues

| ID | Date | Finding | Status |
|----|------|---------|--------|
| F-001 | 2026-10-03 | Upstream CTFd latest is 3.8.8 (released 2026-10-02), Apache-2.0 | recorded |
| F-002 | 2026-10-03 | Last year's pain points, per the organisers: load and outages, challenge hosting, cheating and abuse, look and authoring | drives task 1 |
| F-003 | 2026-10-03 | Docker Desktop's engine is not running on the dev machine; it is needed for local runs and load tests | open |
| F-004 | 2026-10-03 | This repo is public. Story pitches, flags and challenge sources must stay out of it. Story material lives in a gitignored `private/` folder until a private home is chosen | open |

## Change log

| Date | Change |
|------|--------|
| 2026-10-03 | Repository initialised on `pre-deployment` with README and this tracker |
