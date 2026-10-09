# What is hostable today, what is not, and what we need to go live

Written 2026-10-07 for the owner. Plain words; no event material.

## What can be put on `l3m0nctf.xyz` today

| Piece | Hostable now? | What it needs |
|-------|---------------|---------------|
| **Landing page** (`private/landing/dist`) | **Yes.** It is a folder of static files. Any static host or one small nginx container serves it. The pentest bundle already runs it as a container. | The domain pointing at the host, a certificate, and the real facts in `landing/config/site.json` (registration link when it opens, **the sign-in link `links.login`, `https://play.l3m0nctf.xyz/login`, once the platform is up: until it is set the landing page has no way to the platform**, contact email, sponsors, the CTFtime link once it exists). It stays in the private repo until launch. |
| **Platform, the engine** (this repo: hardened CTFd image, nginx, MariaDB, Redis, backup and restore, the CTFtime feed plugin) | **Yes, as a plain CTFd.** `deploy/compose` runs the whole stack on one VM. It is what the pentest bundle packages. It looks and behaves like stock CTFd plus our security hardening. | A Google Cloud VM, the production checklist (`production-checklist.md`), real secrets, DNS and TLS. |
| **Platform, the participant pages** (`private/platform-ui`) | **No.** It is a demo: a mock server with invented teams, fake flags and pretend sign-in providers, kept in memory. It is the approved *design and contract*, not something to put on the internet. | It has to be rebuilt on CTFd: SP3 (the plugin that serves the board, Guide, ticks, notifications and the crew's release control from real data) and SP6 (the theme made from the prototype's templates, styles and scripts). |

So the honest answer to "can I host what I have been reviewing": the landing page yes, the demo no. The demo's pages and rules carry over to the real build, and the board you approved will look the same.

**Step by step:** [platform-deployment.md](platform-deployment.md) is the set-up guide for the platform side (Google Cloud, Cloudflare, email, sign-in), with the parts that can start today marked.

## What is still to build before the real participant site exists

1. **SP3, the plugin layer** (next): the board, Guide, **scoreboard (TRP ratings)**, ticks and **notifications** as CTFd endpoints with the same JSON as the prototype (the Guide also needs each solve and hint to remember **which member** made it, for the per-member shares; and every message must say **TRP**, television rating points, not points); **release control** (the crew chooses which channels and challenges are on air and when, with a scheduler and an admin page); contract tests that compare the real answers with the prototype's.
2. **SP6, the theme**: the approved pages as a CTFd theme.
3. SP4 the instance launcher, SP5 anti-abuse, SP7 the story layer, SP8 sign-in with Google and CTFtime, SP9 monitoring, backups off the machine and the load test.

## What we need from you (and when)

**Needed now, to start SP3 without guessing**

- **The release plan.** Roughly which challenges (or channels) go on air at the start and at which hours after that. A first draft is enough; the plugin takes it as a table the crew can edit until the last minute.
- **Scoring rules.** Fixed points or points that drop as more teams solve (dynamic)? Cost of hints? How ties are broken? A bonus for the first solve? Does the scoreboard freeze for the last hour (the prototype assumes yes)? And one new question: if a challenge that teams have already solved has to be pulled back because it is broken, do those teams keep the points (the prototype does), does the challenge's value go away for everyone while it is hidden, or does the crew get a button to void those solves?
- **Team rules.** Maximum team size (the prototype assumes 4), who may register (college only or open), how many teams to plan for (we plan for 250 and keep room for 1,000).
- **The crew.** Who needs an administrator account, and from which network or VPN they will reach `/admin` (it is closed to everyone else).

**Needed in the next two weeks, to stage the site on the real domain**

- **DNS for `l3m0nctf.xyz`**: either the registrar login or a Cloudflare account that controls it, so we can point `l3m0nctf.xyz` (landing) and `play.l3m0nctf.xyz` (platform) at the server and issue certificates.
- **Google Cloud**: the project and billing account (who pays after the free credit), and the region. We suggest Mumbai (`asia-south1`) for players in India. Plus permission to create two VMs and a storage bucket for backups.
- **Email for verification and password-reset messages**: a sending service and the right to add its records (SPF and DKIM) to the domain. Without it nobody can finish registering.
- **A Google sign-in app** (OAuth client) created under the project, with `https://play.l3m0nctf.xyz` as its address. The CTFtime sign-in needs the event to be approved on CTFtime first.
- **Final text**: rules (the draft is up), eligibility, prizes, sponsors, contact address, privacy wording, social links.

**Needed later**

- The real challenges from the 30 authors through the private repo and its checks; the real flags are set only at deploy.
- The story text for the story meter (private).
- The **cold-open comics**: seven are drawn and compiled in the private repository (`private/story/`; the owner's questions are at the end of `private/story/bible/cold-opens.md`). Before the round, `python -m l3mon story build` compiles them to a folder of files that is copied to the server's read-only `story` volume. The plan's channel slugs must be the story slugs (`test-card`, `street`, `snack`, `gadget`, `ninja`, `chase`, `cubcop`); a channel with no matching file simply has no comic.
- A staging dress rehearsal date, and who is on call during the 24 hours.
- Whether the count of challenges still to come ("12 coming up") should be visible to players, or hidden (the prototype shows it).

## The flag format

Every flag is `L3m0n{...}`. The author kit and both repositories' scanners refuse the old `L3m0nCTF{` form.
