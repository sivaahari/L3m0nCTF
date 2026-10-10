# L3m0nCTF 2026

Platform, infrastructure and documentation for **L3m0nCTF**, the annual flagship Capture-The-Flag event of Amrita Vishwa Vidyapeetham, Coimbatore Campus, hosted by TIFAC-CORE in Cyber Security.

> **Status: the build is under way.** The platform foundation (SP0) is done and tested: the hardened CTFd 3.8.8 image, our plugin skeleton, the Docker Compose stack, backup and restore with a rehearsed drill, and a CI pipeline. Live status and the decision log are in [PROGRESS.md](PROGRESS.md); every check that was run is recorded in the [verification log](docs/deploy/verification-log.md).

## The event

| | |
|---|---|
| Edition | 2026 |
| Theme | Popular Indian cartoons, told through an original "homage" cast (parody names, original art) |
| Prelims | Online, Jeopardy-style, 12 hours, **28 November 2026, 10:00 to 22:00 IST** (changed on 2026-10-10; it was 24 hours from 09:00 IST) |
| Finals | Jeopardy-style for teams selected from the prelims; platform hosted online, finalists on campus |
| Scale target | 250+ teams, 150 to 200 challenges across both rounds, about 30 challenge authors |
| Rating | Listed on [CTFtime](https://ctftime.org/) |
| Tracks | Web, pwn, reverse, crypto, forensics, misc, OSINT, plus AI/LLM security, blockchain/Web3 and hardware/IoT/RF |

## What we are building

A CTFd-derived platform that keeps what CTFd already does well (teams, scoring, admin, REST API, challenge authoring workflow) and fixes what hurt last year: load and outages, challenge hosting, cheating and abuse, and a look that is hard to customise. It should not look like CTFd. It carries a story overlay: challenges stay independently solvable, and every solve unlocks lore and moves a progress meter toward the event's ultimate goal.

## Work plan

| # | Task | Status |
|---|------|--------|
| 1 | Analyse CTFd core and last year's platform | done, option A approved |
| 2 | Survey story-driven CTFs, propose five story chains | done, story chosen (details private) |
| 3 | Layout and UX brainstorm | done: direction, shell and all eleven page concepts approved (details private) |
| 4 | Build and verify the platform | in progress: SP0 foundation done (see below); the sign-in, challenge and board pages, the story overlay, the instancer and the CTFtime sign-in and feed follow |

## Findings

Task 1 (platform analysis) is written up in [docs/analysis](docs/analysis):

- [CTFd 3.8.x: how it works and where it strains](docs/analysis/01-ctfd-core.md)
- [Last year's platform: what was built and what hurt](docs/analysis/02-last-year-fork.md)
- [Platform options and recommendation](docs/analysis/03-options-and-recommendation.md) (option A, CTFd 3.8.x extended, approved)

Headlines:

- Upstream CTFd is at 3.8.8 (2026-10-02, Apache-2.0). It is a sound engine to extend. The strains sit around it: single-worker defaults, a scoreboard cache cleared on every flag submission, IP-keyed rate limits, no event stream and no per-team instances.
- Last year's fork left core untouched and shipped four themes and seven plugins. It ran one worker behind an nginx that served every asset through Python, and gave the web container the Docker socket.
- Recommendation: keep CTFd as the engine, extend it by plugin and theme only, and build a hardened deployment, an external instancer, an event stream and an abuse layer around it.

Task 2 (story research) has a public precedent survey in [docs/research](docs/research):

- [Story-driven CTFs: a survey](docs/research/ctf-story-survey.md). About 25 events and formats, the patterns the field has converged on, lessons for an open-board overlay, and the gaps where an original story can stand out.
- [Hosting on CTFtime: requirements and what we must build](docs/research/ctftime-requirements.md). How listing works, the eligibility rules, the JSON scoreboard feed, OAuth2, rating and weight, a compliance checklist mapped to backlog items, and the questions to put to CTFtime.
- [CTFtime OAuth and the live JSON feed](docs/research/ctftime-oauth-and-live-feed.md). What CTFtime offers and what is real, what stock CTFd does, the plan for a static live feed and a "Login with CTFtime" plugin, operational gotchas, and the questions to ask CTFtime.
- [Sign-in methods: email, Google and CTFtime](docs/research/sign-in-methods.md). The three ways to sign in, what each needs, the rules that keep accounts safe, and how the plugin is built.
- The five story pitches contain the plot, so they stay out of this public repo.

Task 4 (build) starts from a design spec:

- [Platform build design](docs/superpowers/specs/2026-10-04-platform-build-design.md). Architecture, sub-projects SP0 to SP10, the small parts of a complete site (error contract, busy mode, account gates, info pages), build calendar, scope tiers, how "verified end to end" is defined, risks and the decisions needed. The technology is a proposal: the department's leadership decides.
- A four-page plain-language proposal for leadership, with diagrams and screenshots, lives in the private repo because the screenshots show the unreleased visual theme.
- A challenge author kit (templates, a one-command checker, CI and guides for the roughly 30 authors) is built in the private repo, because it holds challenge sources. Nine challenges the owner wrote earlier were adapted to it.

## Run it and check it

- [Running the platform on your own computer](docs/deploy/local.md): from a fresh clone to a working stack, day-to-day commands, every check and what it proves.
- [Dependency upgrades and the advisories that remain](docs/security/dependency-bumps.md): what we upgraded in the official CTFd image, what could not be upgraded and why, and what stops each remaining advisory.
- [Verification log](docs/deploy/verification-log.md): a dated record of every check that was run, with its command and result.
- [Go-live inputs](docs/deploy/go-live-inputs.md): what is hostable today, what is not, and what we need from the owner to go live.
- [CTFtime endpoints](docs/ctftime-endpoints.md): the addresses and values for the CTFtime registration form.

```
docker/ctfd/      the platform image (official CTFd 3.8.8 + upgraded libraries + our plugin and theme)
plugins/          our CTFd plugins            ui/theme/    our CTFd theme
deploy/           Docker Compose stack, nginx, MariaDB and Redis settings
tools/            the settings and secrets tool, the hygiene scanner, backup and restore, image checks
tests/            integration tests of the running stack and the restore drill
config/           the public example of the event settings
docs/             research, analysis, the build design, plans, security notes, deployment notes
```

## Repository conventions

- Every change lands on `pre-deployment` first. `main` only receives work that has been tested there and approved.
- This repository is public. It must never contain flags, challenge solutions, secrets, database dumps or unreleased story material. Those live in the private repository `sivaahari/L3m0nCTF-challenges`, which is checked out locally at `private/` (ignored here).
