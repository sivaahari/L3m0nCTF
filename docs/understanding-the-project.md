# Understanding the platform: what we chose, why, and how to learn it

This page is for the project owner, who is learning full-stack work through this project and wants to know the reason behind every
technical choice. It is written in plain words. It says who decided each thing, what else was possible, why this was picked, and what
it costs. Nothing here is secret: it only collects what the other documents say in one place.

**The honest starting point.** Three kinds of decision were made, and they are not the same:

| Kind | What it covers | Who decided |
|---|---|---|
| **The brief** | "Based on CTFd", online prelims, listed on CTFtime, the cartoon broadcast idea | You and the organisers |
| **The stack** | CTFd plus MariaDB, Redis, nginx, Docker, Cloudflare, Google Cloud | I proposed it, and leadership approved it on 2026-10-06 (decisions D15 and D16; hosting on Google Cloud with the free credit is D10) |
| **The inside** | How the plugins are built, how scoring, the freeze, the board and the comics work | Me, inside the limits above. Written down in the design and the plans (`docs/superpowers/`) |

Feeling that you accepted things without being able to judge them is normal at this stage, and it is fixable. The fix is not to
re-decide everything. It is to understand enough of each choice to ask "why not the other one?" and to know that you could say no.
The table below is built for that question.

## The map: what happens when a player sends a flag

```
Player's browser
  -> Cloudflare            (free: HTTPS, protection against floods, caching of static files)
  -> nginx                 (our own front door: size limits, rate limits, static files, error pages)
  -> CTFd                  (the Python application: accounts, teams, challenges, flags, scoring)
       -> our plugins      (the story, the board, scoring rules, the freeze, release control, CTFtime)
       -> MariaDB          (the database: the permanent record of everything)
       -> Redis            (fast memory: caches, sessions, the "something changed" counter)
```

Everything runs in **Docker containers** that **Docker Compose** starts together (`deploy/`, `docker/`, `tools/compose.sh`). The
public landing page is a separate, plain set of files on Cloudflare (`private/landing`), so it stays up even if the platform is down.

## Each choice, with the alternatives

| Choice | Plain meaning | What else we could have used | Why this one | What it costs us | Look here |
|---|---|---|---|---|---|
| **CTFd 3.8.8 as the engine** | A ready-made CTF website: teams, challenges, flags, scoreboard, admin pages | GZCTF (a different engine), rCTF, a custom build, or CTFd as a hidden API behind a new front end | The brief says "based on CTFd". Building accounts, scoring and admin from nothing in six weeks is not realistic, and 30 authors already know CTFd's challenge format | We live inside its rules. It is older Python code, and we found places where it leaks (see below) | `docs/analysis/03-options-and-recommendation.md` |
| **Python and Flask** | The language and web library CTFd is written in | Nothing: this comes with CTFd | It is CTFd's, so every plugin is Python | Python is slower than Go or Rust, so we cache and keep requests small | `plugins/` |
| **Extend by plugins only** | We never edit CTFd's files; our code sits beside it | Fork CTFd and change it directly | An unmodified CTFd can be upgraded and checked against its own 676 tests. A fork drifts and every upgrade is a merge | Some things are harder (we wrap two of its functions, a startup check stops us loading on any other CTFd version) | `plugins/l3mon_core/versions.py`, `docs/superpowers/specs/2026-10-07-sp3-plugin-layer-design.md` |
| **MariaDB** | The database | PostgreSQL, MySQL, SQLite | CTFd's own recommended setup uses it, so it is the best-tested path | Honestly, PostgreSQL would also work. This choice was made for least surprise, not because MariaDB is better. Its default isolation level caused real bugs we had to design around (see the freeze and the locks) | `docs/superpowers/plans/2026-10-15-sp3-6-freeze.md` |
| **Redis** | A very fast memory store | Memcached, files, or the database only | CTFd already uses it for caches and sessions; we also keep one counter there (the "tick") so pages know when to refresh | One more thing to run and watch. If it is lost, players are signed out | `plugins/l3mon_core/tick.py` |
| **nginx** | The front door in front of CTFd | Caddy, Traefik, or CTFd alone | It serves files quickly, limits abuse before it reaches Python, and is what CTFd's own compose file uses | Its configuration is long and unforgiving | `deploy/nginx/` |
| **Docker and Compose** | Packaging so it runs the same on your laptop and the server | Installing everything by hand, or Kubernetes | Compose is simple enough to understand and repair in a crisis. Kubernetes is far more to learn for a single event | One machine is one point of failure unless we add more | `docker/ctfd/Dockerfile`, `deploy/` |
| **Google Cloud + Cloudflare** | Where it runs, and what stands in front | Another cloud, a university server | Leadership chose Google Cloud and the $300 credit; Cloudflare gives free HTTPS and flood protection | The credit leaves a thin margin (the estimate is about $190 to $260). Someone must own the bill | `docs/research/gcp-cost-estimate.md`, `docs/deploy/platform-deployment.md` |
| **gunicorn with gevent workers** | How CTFd serves many people at once | Plain threads, more processes | It is CTFd's own setting | Gevent is cooperative, so a blocking call can stall everybody. It is why our database code is careful | `docker/` |
| **"Tick" and polling, not live push** | Pages ask every 15 seconds "did anything change?" with one number, and only fetch when it did | WebSockets or Centrifugo (a live-push server) | Far simpler, cheap, works through every proxy, and survives a restart. The approved proposal listed Centrifugo; I did not build it, because polling meets the need | A change can take up to 15 seconds to appear. If you want instant updates, Centrifugo can still be added | `plugins/l3mon_core/tick.py` |
| **Pages drawn by the server, with small scripts** (planned, SP6) | The real theme will be ordinary HTML pages from CTFd with a little JavaScript | A single-page app (React, Vue, Svelte) | Works without JavaScript, stays small, and fits CTFd's own templates | Less "app-like" feel. The approved prototype is plain JavaScript already, so this is a port | `private/platform-ui/` (the prototype) |
| **A static landing page with no framework** | The public page is files and a little JavaScript | WordPress, Next.js | It cannot be hacked through a database, costs nothing to host, and has a 60 KB first-load budget that tests enforce | Everything is hand-made | `private/landing/` |
| **Pictures as WebP, shown by a small player** | The friend's drawings become compressed WebP files, served only to registered players | Video files, GIFs, our earlier drawn-in-code art | A WebP is about 100 to 200 KB against 6 MB for the original PNG. A page-flipping player is simpler and lighter than video, and you chose it | The words are painted into the pictures, so we also keep them as text for screen readers | `plugins/l3mon_story/` |
| **Tests that try to break the code** | Besides ordinary tests, we change one line on purpose and check that some test fails | Ordinary tests only | A test that never fails proves nothing; this shows each rule is really guarded. Independent reviews by a second model add a different pair of eyes | Slow: the full plugin suite takes about half an hour | `docs/deploy/verification-log.md` |
| **A public repo for the platform, a private one for the story** | Code open, story and challenges closed | One repo | The story and flags must stay secret until the event; a scanner fails the build if one slips into the public side | Two places to keep in step | `tools/l3mon/hygiene.py` |

## Where CTFd itself surprised us (why we wrote so many rules)

CTFd is good, but not built for a story, a freeze that hides everything, or hidden challenges. Measuring it found real leaks, which
the plugins now close. Examples: a hidden challenge could be named through its solves, files, shares and score history; the
notification list was open to anyone; after a freeze a dynamic challenge's value kept falling and told everybody that someone had
solved; challenge ratings could do the same. Each is a test with a number in the verification log. This is the main reason the
project has more code than "a theme and a few plugins" suggests, and it is why testing and reviews take so long.

## Where I would like you to push back

These are the choices with the weakest reasons, so they deserve your questions:

1. **MariaDB over PostgreSQL.** Chosen for convenience. Ask if you want to compare.
2. **How much defensive code the freeze needed.** It is correct and tested, but it is more machinery than a simple "hide the scoreboard" switch. Leadership has not decided whether to use a freeze at all.
3. **Centrifugo in the approved proposal.** I replaced it with polling. If leadership expects live push, tell them it is not there.
4. **The cost margin on Google Cloud.** Thin. The two protections in the cost estimate must be in place before registration opens.
5. **The server-drawn theme as a bet.** It is the right size for the time we have, but it is a bet until SP6 is built.
6. **The amount of verification.** It makes the platform safer and the work slower. We can lower it for the last parts if time runs short, but I would not lower it for scoring, the freeze, or anything that decides who wins.

## How to learn it: a path of about a week, an hour a day

You do not need to read code to start. Do these in order. Each step ends with something you can say in your own words.

1. **Run it on your laptop** (`docs/deploy/local.md`). Start the stack, open the site, sign in, solve a test challenge. You should be able to say what each of the four containers (CTFd, the database, the cache and nginx) does.
2. **Follow one flag.** Read the sequence diagram in `docs/superpowers/specs/2026-10-04-platform-build-design.md` (section 5, "A solve"). Then open a browser's developer tools (the Network tab), send a flag, and find the request. You should be able to say which parts touched the database and which touched Redis.
3. **Read one small plugin from top to bottom.** Start with `plugins/l3mon_story/` (the comics): `store.py` reads files, `api.py` decides who may see them, `assets/comic.js` plays them. You should be able to say why a visitor gets one answer and a player another.
4. **Read one test, then break the code.** Open `plugins/l3mon_story/tests/test_pictures_gate.py`, change one line in `api.py` (for example let a visitor through), run the test, and watch it fail. This is the most useful exercise in the project: it teaches how the guard and the test hold each other up.
5. **Read the scoring and the freeze.** `plugins/l3mon_scoring/values.py` (what a challenge is worth), then `freeze.py` and the plan `docs/superpowers/plans/2026-10-15-sp3-6-freeze.md`. This is the hardest part of the project; do not worry if it takes more than one sitting.
6. **Read the deployment guide** (`docs/deploy/platform-deployment.md`) and ask yourself what you would do if the server stopped during the event. The answer is the runbook we still have to write (SP9).

Some words that will help: *request and response* (what a browser and a server say to each other), *route* (an address the server answers),
*database transaction* (a group of changes that all happen or none do), *cache* (a saved answer so the work is not done twice),
*reverse proxy* (nginx standing in front), *container* (a packaged program with its own small world), *ORM* (the library that turns
database rows into Python objects), *idempotent* (doing it twice has the same effect as once).

## How we will talk about it

Pick any row of the table, or any file, and ask "why not the other one?" or "show me where this happens". I will show the alternative,
what it would change, and what it would cost, and you decide whether you would change it. If a choice cannot be explained plainly,
that is a sign it was not chosen well, and I will say so.
