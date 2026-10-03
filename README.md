# L3m0nCTF 2026

Platform, infrastructure and documentation for **L3m0nCTF**, the annual flagship Capture-The-Flag event of Amrita Vishwa Vidyapeetham, Coimbatore Campus, hosted by TIFAC-CORE in Cyber Security.

> **Status: research and design.** There is no platform code yet. Live status and the decision log are in [PROGRESS.md](PROGRESS.md).

## The event

| | |
|---|---|
| Edition | 2026, tentatively November |
| Theme | Popular Indian cartoons, told through an original "homage" cast (parody names, original art) |
| Prelims | Online, Jeopardy-style, 24 hours, around mid-November |
| Finals | Jeopardy-style for teams selected from the prelims; platform hosted online, finalists on campus |
| Scale target | 250+ teams, 150 to 200 challenges across both rounds, about 30 challenge authors |
| Rating | Listed on [CTFtime](https://ctftime.org/) |
| Tracks | Web, pwn, reverse, crypto, forensics, misc, OSINT, plus AI/LLM security, blockchain/Web3 and hardware/IoT/RF |

## What we are building

A CTFd-derived platform that keeps what CTFd already does well (teams, scoring, admin, REST API, challenge authoring workflow) and fixes what hurt last year: load and outages, challenge hosting, cheating and abuse, and a look that is hard to customise. It should not look like CTFd. It carries a story overlay: challenges stay independently solvable, and every solve unlocks lore and moves a progress meter toward the event's ultimate goal.

## Work plan

| # | Task | Status |
|---|------|--------|
| 1 | Analyse CTFd core and last year's platform | in progress |
| 2 | Survey story-driven CTFs, propose five story chains | in progress |
| 3 | Layout and UX brainstorm | waiting on task 2 |
| 4 | Build and verify the platform | waiting on task 3 |

## Findings

Findings are linked here as they land. Baseline facts so far:

- Upstream CTFd's latest release is 3.8.8 (2026-10-02), licensed Apache-2.0.

## Repository conventions

- Every change lands on `pre-deployment` first. `main` only receives work that has been tested there and approved.
- This repository is public. It must never contain flags, challenge solutions, secrets, database dumps or unreleased story material.
