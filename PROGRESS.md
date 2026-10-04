# L3m0nCTF 2026: Progress

Last updated: 2026-10-04

## Snapshot

Phase: design, waiting on a technology decision. Tasks 1, 2 and 3 are done (platform option A preferred, story chosen with details kept private, layout direction, shell and all eleven page concepts approved, then a visual polish pass and a pass over the small parts of a complete site). The CTFtime OAuth and live-feed research is also done. The build design for Task 4 is written. The technology choices are a proposal: the project owner cannot approve a stack, so the department's leadership decides (D15), and a four-page proposal was written for them. Nothing is built until they decide; meanwhile work continues only on parts that do not depend on the stack. Prelims are about six weeks out, so the plan below is built backwards from them. **P** is the prelims start; exact dates are not confirmed yet.

## Plan

| When | Focus | Gate |
|------|-------|------|
| P-6 to P-5 | Tasks 1 and 2: platform analysis, CTF survey, five story chains. A minimal public landing page, because CTFtime needs an official URL before it will list us | Story and base-platform decision approved; landing page live |
| P-5 to P-4 | Task 3: layout brainstorm and visual direction. Architecture spec. Author kit v0 (challenge format, Docker template, CI check) so the 30 authors can start | Visual direction approved |
| P-4 to P-2 | Task 4 build: theme, story overlay, instance orchestration, CTFtime feed, anti-cheat, ops. First staging on `pre-deployment` | Staging usable by authors |
| P-2 to P-1 | Load test at 2x target, security review, challenge intake, bug fixing. Feature freeze at P-1 | Load test passes |
| P-1 to P | Dress rehearsal, backups and monitoring, runbooks | Go / no-go |
| P | Prelims | |
| After P | Finalist selection, finals preparation | Finals date not fixed yet |

## Tasks

| # | Task | Status | Output |
|---|------|--------|--------|
| 1 | Analyse CTFd core and last year's platform | done, option A approved | `docs/analysis/` |
| 2 | Survey story-driven CTFs, propose five story chains | done, story chosen | `docs/research/` (public precedent survey); story pitches stay private in the private repo |
| 3 | Layout and UX brainstorm | done: direction, shell and all eleven page concepts approved, polish pass (v3) complete | Private design record and mockups |
| 3a | CTFtime hosting survey and compliance checklist | done | `docs/research/ctftime-requirements.md` |
| 3b | CTFtime OAuth and live JSON feed research | done | `docs/research/ctftime-oauth-and-live-feed.md` |
| 4 | Build and verify the platform | build design drafted with the site essentials; the stack is a proposal awaiting leadership (D15); first plan covers SP0 to SP2 | `docs/superpowers/specs/2026-10-04-platform-build-design.md`, and the leadership proposal PDF in the private repo |

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
| 2026-10-03 | **Base platform: option A.** CTFd 3.8.x as the engine, extended by plugin and theme only, with no core edits |
| 2026-10-03 | A story was chosen from five pitches. Its details stay in the private repo until the event |
| 2026-10-03 | Visuals are code-led: CSS, SVG and generated textures, with a few key illustrations only |
| 2026-10-03 | This repo stays public for platform code. Story, challenges and flags live in the private repo `sivaahari/L3m0nCTF-challenges`, checked out locally at `private/` |
| 2026-10-03 | Layout direction and shell approved (details in the private repo) |
| 2026-10-04 | All eleven page concepts reviewed and approved. A final visual polish pass (v3) was requested and done. Details and mockups are in the private repo |

## Pending decisions

| ID | Decision | Needed for |
|----|----------|------------|
| D2 | Instancer: adopt (`ctfd-chall-manager`, `ctfd-whale`) or build thin on Docker hosts. Needs hosting info and permission to reuse last year's code | Task 4 |
| D5 | Ask last year's plugin authors for permission to reuse their code | Task 4 |
| D8 | Do the finals continue the same story as Part 2, or start a fresh episode | Story |
| D9 | Confirm the cartoon pool excludes mythology and religious figures (inferred from an unselected option) | Story |
| D10 | Hosting and budget, registration and eligibility, team size and brackets, who owns DevOps, finalist count, sponsors | Task 4 |
| D11 | Create a CTFtime account (social login) and the organiser team. Which Amrita team will be the organiser? (An existing Amrita Coimbatore team may be reusable.) Only a team member can do this | CTFtime listing |
| D12 | Send CTFtime the open questions in section 10 of the CTFtime survey (lead time, live feed, finals listing, organiser row, name matching, OAuth timing) | CTFtime listing |
| D13 | Confirm the official domain and logo for the public landing page, and the confirmed prelims dates in UTC. A public page with the event details is enough to submit to CTFtime, so an interim page works | CTFtime listing, landing page |
| D14 | Put the OAuth and feed questions (section 6 of the OAuth and live-feed research) to CTFtime on their issue tracker, and send our stable server IP for the token-endpoint allow-list once it is known | CTFtime OAuth, feed |
| D15 | (leadership) Approve the proposed stack in the build design (`docs/superpowers/specs/2026-10-04-platform-build-design.md`), including Centrifugo as the realtime gateway, S3-compatible file storage and Cloudflare in front. The project owner cannot approve a stack, so a four-page plain-language proposal PDF was written for leadership (private repo, `proposal/`). Decision requested by 2026-10-09 | Task 4 |
| D16 | (leadership, covered by D15) Pulling official container images and packages from their registries (list in section 11 of the build design). Nothing is pulled before D15 | Task 4 |
| D17 | Accept the scope tiers in the build design and the rule that the freeze date wins over features | Task 4 |
| D18 | Allow parallel sub-agents, each in its own git worktree, for independent sub-projects (SP0, SP1, SP2 first) | Task 4 schedule |

## Findings and issues

| ID | Date | Finding | Status |
|----|------|---------|--------|
| F-001 | 2026-10-03 | Upstream CTFd latest is 3.8.8 (released 2026-10-02), Apache-2.0 | recorded |
| F-002 | 2026-10-03 | Last year's pain points, per the organisers: load and outages, challenge hosting, cheating and abuse, look and authoring | drives task 1 |
| F-003 | 2026-10-03 | Docker Desktop's engine was not running on the dev machine. On 2026-10-04 it is running (4 CPUs and about 7.7 GB allotted, Compose v5), so local stacks and reduced-scale load tests are possible. Real load tests still need the staging hosts | resolved locally |
| F-004 | 2026-10-03 | This repo is public. Story pitches, flags and challenge sources must stay out of it. Story material lives in a gitignored `private/` folder until a private home is chosen | open |
| F-005 | 2026-10-03 | `pip-audit` on CTFd 3.8.8's pins: 73 advisories in 10 of 66 packages (Pillow, Werkzeug, cryptography, urllib3 and others). Several are bumpable now | tracked as B14 |
| F-006 | 2026-10-03 | CTFd rate-limits login, register, team-join, email confirm, password reset and SSE per client IP. Flag submission is per account. A campus NAT makes the IP limits and IP-based abuse rules misfire | designed around in B6, B15 |
| F-007 | 2026-10-03 | CTFd clears the standings and challenge caches on every flag submission (correct, partial and wrong, re-read in source on 2026-10-04), and dynamic scoring commits the challenge row per solve. Both are load-test targets | invalidation verified in source, impact to test in B4 |
| F-008 | 2026-10-03 | Last year's deployment: one worker, nginx serving every asset through Python with no caching, Docker socket mounted in a root web container, spawned containers without resource limits | informs B1, B5 |
| F-009 | 2026-10-03 | Last year's public repo history contains database backups and dumps and a cookie jar. Details given privately to the project owner | owner to purge |
| F-010 | 2026-10-03 | Capacity: P0 backlog is roughly 30 to 36 working days and P1 roughly 25 to 30 more, against about 30 available. Needs adopt-over-build and scope cuts. Update 2026-10-04: with the full theme, the sub-project estimates total about 58 to 75 builder-days and tier A alone about 37 to 46, against about 25 weekdays to a 2026-11-07 freeze | see doc 03 section 4 and build design section 7 |
| F-011 | 2026-10-03 | Survey: story-as-flavour on an open board is the dominant pattern at scale (Cyber Apocalypse 2024: about 13,000 players, 5,730 teams, 67 challenges). No CTF themed on Indian cartoons and no shared progress meter across all teams was found | informs task 2 |
| F-012 | 2026-10-03 | InCTF 2026 (Amrita with team bi0s) uses the "Operation VAJRA" space and cyber-warfare theme. Our story should avoid that territory | informs task 2 |
| F-013 | 2026-10-03 | CTFtime rating needs a team-based event of at most 5 days and a scoreboard in its JSON feed format (`standings`, optional `tasks` and `taskStats`, polled every 60 s) | tracked as B12 |
| F-014 | 2026-10-03 | CTFtime listing is a manual form behind a social login, needs an official site URL first, and publishes no lead time. The public landing page is on the critical path | B19, P0 |
| F-015 | 2026-10-03 | CTFtime-listed events draw big fields: Pragyan CTF 2026 had 892 teams, bi0sCTF 2025 had 624. The capacity target moves from 600 to 1,000 teams | doc 03 updated |
| F-016 | 2026-10-03 | The CTFtime feed identifies teams by name only, so results are matched by exact name or alias. First-time events are capped at weight 25. Only teams scoring above zero count. Hidden challenges must be kept out of the public feed | B12, B20 |
| F-017 | 2026-10-03 | CTFtime OAuth2 can only be used after approval (client ID is the event ID). CTFd core has no CTFtime provider. It stays optional | B21 |
| F-018 | 2026-10-03 | CTFtime has two feeds (standings, and a capture log polled with `?lastId=`), warns against "maximal" feeds, and has called the real-time feature "in progress" since about 2016. No running event I checked shows a live scoreboard. The final results upload is what matters for rating. Plan: static file, minimal feed first | B12a to B12c |
| F-019 | 2026-10-03 | CTFtime OAuth gotchas: it works only for upcoming or running events, the token endpoint has returned 403 until the server IP was allow-listed, and over-long `state` values failed. Stock CTFd's OAuth needs `email`, sends no `redirect_uri` and rate-limits per IP, so we write a plugin | B21, B23 |
| F-020 | 2026-10-03 | A two-round Indian CTF (Hacker's Gambit 2026) is listed on CTFtime as two events, an online qualifier and a prequalified finale, with a third-party page as its official URL. Prequalified finals can carry weight (SAS CTF Finals 49.50) | informs the listing |
| F-021 | 2026-10-04 | CTFd 3.8.8's flag-submission endpoint answers some problems with 403 or 429 and a JSON body ("another submission is already being processed", "no tries left"). The challenge panel must show any JSON reply from the submit call inline, never as a page error. A busy server should answer 503, not 403 | in the error contract, build design section 5 |

## Change log

| Date | Change |
|------|--------|
| 2026-10-03 | Repository initialised on `pre-deployment` with README and this tracker |
| 2026-10-03 | Added Task 1 analysis: `docs/analysis/01-ctfd-core.md`, `02-last-year-fork.md`, `03-options-and-recommendation.md` |
| 2026-10-03 | Added Task 2 precedent survey: `docs/research/ctf-story-survey.md`. Story pitches written to the gitignored `private/story/` |
| 2026-10-03 | Created the private repo `sivaahari/L3m0nCTF-challenges`, checked out at `private/`, and pushed the story pitches and decision record to its `pre-deployment` branch |
| 2026-10-03 | Recorded the approvals: platform option A, story chosen, code-led visuals, private challenges repo. Started Task 3 |
| 2026-10-03 | Layout direction and shell approved (details in the private repo). Added the CTFtime survey `docs/research/ctftime-requirements.md`; raised the capacity target to 1,000 teams and added backlog items B19 to B22 |
| 2026-10-03 | Presented eleven page concepts (startup, login, registration, teams, scoreboard, programme, text mode, error slates, live wall) with performance and delivery guardrails. Mockups and decisions are in the private repo |
| 2026-10-03 | Added `docs/research/ctftime-oauth-and-live-feed.md`: what CTFtime's OAuth2 and JSON feeds really offer, a plan for a static live feed and a "Login with CTFtime" plugin, and the questions to put to CTFtime. Backlog B12 split into B12a to B12c, B21 raised to P1, B23 added |
| 2026-10-04 | Page concepts approved. Visual polish pass (v3) done and checked on all eleven pages at 375, 414, 640, 820 and 1180 px with no horizontal overflow. Mockup stored in the private repo |
| 2026-10-04 | Re-read CTFd 3.8.8's submission path: the standings and challenge caches are cleared on wrong submissions too, not only on solves. Docs 01, README and F-007 corrected |
| 2026-10-04 | Drafted the build design: architecture, eleven sub-projects (SP0 to SP10), calendar to a 2026-11-14 prelims, scope tiers, end-to-end verification definition, risks and decisions D15, D16 |
| 2026-10-04 | Thought through the small parts of a complete site and added them to the build design: an error contract (HTML slate for pages, JSON for API calls, inline messages for flag replies), busy mode in three levels (503 for overload, not 403), account gates, info and discovery pages, support and operations parts, with owners and tiers. Added backlog item B24. Estimates now total about 64 to 82 builder-days (tier A about 42 to 52). Four new end-to-end scenarios (S13 to S16) |
| 2026-10-04 | The project owner cannot approve a stack, so the stack is now a proposal for leadership. Wrote a four-page plain-language proposal PDF with diagrams, screenshots and a dedicated CTFtime login and live-scores section (private repo, `proposal/`). Pages atlas v4 adds the twelve slates and a small-parts tab (private repo) |
