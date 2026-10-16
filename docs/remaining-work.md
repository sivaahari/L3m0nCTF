# What is left to build, and whether it fits

Written 2026-10-15, updated 2026-10-16 after part 3.7. Dates: the feature freeze in the build plan is **21 November 2026**; the online round is **28 November 2026**. That is 37
days to the freeze and 44 days to the event.

## What "a part" means here

One **part** is a piece the size of part 3.5 (the Guide, the scoreboard, the bell): designed, built test first, broken on purpose to check the tests,
reviewed by a second model, verified on a rebuilt stack, written up and committed. In practice a part is about one working day of calendar time,
because most of it is waiting for long test runs (the full plugin suite alone is half an hour), and about **0.3 to 0.5 million tokens**. This
session's own work (the pictures and the finale gate, the landing page frames, and the review fixes of the scoreboard freeze) used about 0.65 million,
which was a part and a half.

## What is left

| Piece | What it is | Parts | What it needs from you or leadership | If time runs short |
|---|---|---|---|---|
| 3.7 | **Done 2026-10-16.** An audit of the whole plugin layer (SP3), a speed check, and the decision on the database isolation level. It found and fixed two real defects by measuring and three medium ones by the audit; the isolation level stays; and it found that the production host must be sized (decision D27, below) | 0 | nothing | done |
| SP6 | The real theme: every player page on CTFd (sign-in and registration, channels and the programme panel, scoreboard, the Guide, bell, team pages, settings, error pages, account gates). The approved prototype exists, so this is a port, but it is the largest piece | 5 to 6 | nothing; the pages are approved | build in this order: sign-in and registration, channels and panel, scoreboard and Guide, the rest. Cut the live wall and extra text modes |
| SP4 | Per-team instances for web and pwn challenges, with a flag that is different for each team | 3 to 4 | challenge hosts to test on; decision D2 (build our own thin instancer, which I recommend); last year's authors' answer if we reuse their code | web challenges only first; TCP later |
| SP5 | Anti-abuse: verified email, a CAPTCHA, limits, a record of suspicious flag sharing for the crew to review | 2 | a Cloudflare Turnstile key | verification, limits and the evidence record only |
| SP7 | The rest of the story engine: the finale button on the page, an automatic air time, a small editor | 1 to 2 | the finale rules (the trigger and the 100 percent meter are built) | skip the editor |
| SP8 | Continue with Google | 1 to 2 | a Google Cloud project and sign-in client (D21); the college's answer on third-party sign-in | drop it: email and CTFtime sign-in already work |
| SP9 | Operations: monitoring, off-server backups, a status page, a mail relay, the runbooks, a load-test script, the dress rehearsal | 3 | the server, the domain, a mail service; this is mostly your side | keep health checks, backups and runbooks; cut the dashboards |
| End | Rebuild the penetration-test bundle, the final audits, the last documents | 1 to 2 | the testers' feedback | |
| **Total** | | **16 to 21 parts** | | **about 11 parts** if we keep only the first option in each "if time runs short" |

Not counted: SP10 (the finals), which waits until after the prelims.

## Does it fit?

**In time: yes, with room.** 16 to 21 parts is 16 to 21 working days, against 36 days to the freeze. The room is real but it is not unlimited, because
three things outside my control can stop the work: a decision that arrives late (the instancer, the freeze, the finale), a server that is not ready
for the load test and the dress rehearsal, and the manual testing by the superiors, which needs a working staging site about **10 days before the
freeze** (by about 10 November) so that what they find can still be fixed.

**In tokens: I cannot see your weekly allowance, so I cannot promise this part.** The plan is roughly **7 to 9 million tokens** in full, or about 5
million for the short version. If your week allows that much, one week is enough for everything; if it allows about half, one week finishes the
short version's core (the theme's main pages, instances for web challenges, the basic anti-abuse) and a second week finishes the rest, which
still lands well before the freeze. The Usage page of the app shows what a week allows; send me the number and I will turn this into a calendar.

## The size of the host (found by part 3.7, for leadership: PROGRESS D27)

Part 3.7 measured what the platform costs per request, on the development laptop (4 CPUs, shared with the test program that played the players). The
numbers are in [the plan of part 3.7](superpowers/plans/2026-10-16-sp3-7-audit-and-speed.md). In plain words:

- A signed-in page of plain CTFd costs about **11 to 15 ms** of one processor core. Our board and Guide cost **37 to 42 ms** each, the tick **20 ms**. Averaged over the event's mix, **37 ms a request**, so one core serves about **27 requests a second**.
- A player makes about **0.25 requests a second** (the tick every 15 seconds, the board and the Guide when something changed, an occasional scoreboard, flags). That gives:

| Players at the same time | Requests a second | Cores of CTFd needed (at 37 ms) | Measured |
|---|---|---|---|
| 300 | 75 | 2.8 | yes: the laptop was at its limit, p95 about 1 to 2 seconds |
| 600 | 150 | 5.5 | no, scaled |
| 1000 | 250 | 9 | no, scaled (the build design's lower target) |
| 1600 | 400 | 15 | no, scaled (the build design's upper target) |

The database adds about a sixth of that, and Redis and nginx a little. Cloud cores may be faster or slower than this laptop's; the k6 test on the real host (SP9) gives the real figure. **Leadership has to decide how many players the first event should carry, and so the host's size (cores, not memory).** The choice also decides how much of the Google Cloud credit the event uses (`docs/research/gcp-cost-estimate.md`).

If the real test says the host is too small, there are four ways to need fewer cores, in the order I would try them (the gains are estimates, not measurements):

1. **Poll every 30 seconds instead of 15** (a setting in the pages, SP6): about half the CPU, at the price of a change taking up to 30 seconds to show. The build design asked for 5 seconds for the scoreboard, so this is a decision about freshness.
2. **Remember the board and the Guide for a studio for a few seconds**, keyed by the tick: three players of one studio then cost one: about a third less.
3. **Fewer database statements for the board and the Guide** (they read the same programmes three times): perhaps a fifth less.
4. **A faster database driver in the image:** perhaps a fifth less; a change to the image, so it needs the full checks again.

## What would put the date at risk

1. **The challenges.** About 30 authors must deliver roughly 150 to 200 challenges. The platform can be perfect and the event still thin. This needs a deadline and a weekly check, starting now.
2. **Hosting, and its size.** Nothing has been deployed yet. The set-up guide (`docs/deploy/platform-deployment.md`) is ready; the sooner a server exists, the sooner the load test and the dress rehearsal can happen. Part 3.7 measured what a request costs (about 37 ms of CPU in CTFd on average): **how many players the first event can carry depends on the number of cores of the host, and leadership has to choose it (D27)**. The next section has the numbers.
3. **CTFtime approval.** Its lead time is unknown; the registration message is written (`docs/ctftime-registration-message.md`).
4. **Late decisions.** The scoreboard freeze, the instancer, and Google sign-in each wait on somebody else.

## The order I would build in

The theme's first pages together with web instances, because the superiors need something
to click through and the authors need somewhere to test; then anti-abuse; then Google sign-in if the college allows it; then operations in
parallel with your hosting work; and the finishing parts last.
