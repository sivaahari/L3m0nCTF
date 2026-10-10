# Hosting L3m0nCTF on CTFtime: requirements and what we must build

Survey of what CTFtime requires of an organiser and of the platform, read from CTFtime's own pages on 2026-10-03, plus real events as evidence. Every requirement ends up in the checklist in section 9, mapped to a backlog item.

**Method and limits.** Primary sources: [For organizers](https://ctftime.org/for-organizers), [OAuth2 configuration](https://ctftime.org/for-organizers/oauth/), [JSON scoreboard feed](https://ctftime.org/json-scoreboard-feed), [FAQ](https://ctftime.org/faq/), [Rating formula](https://ctftime.org/rating-formula/), the [API page](https://ctftime.org/api/) and the [upcoming events list](https://ctftime.org/event/list/upcoming). Real events read for evidence: bi0sCTF 2024 and 2025, Pragyan CTF 2026, XiomaraCTF and the InCTF series. The event submission form and the management interface sit behind a login and could not be read. Anything only visible there is listed as unknown in section 10.

## 1. How listing works

1. **A CTFtime account.** Sign-in is through Twitter, Facebook, Google or GitHub only.
2. **An organiser team.** The submitter must be a member of the organiser team, and if the team does not exist it must be registered first.
3. **Submit the event through the form** at `ctftime.org/event/mail/`. It is an email-style form, so approval is **manual**. The information must be in English.
4. **Approval.** CTFtime then provides a **management interface** for your team, where you edit the event details yourself. The OAuth2 client secret is also issued there.
5. **During the event.** Teams mark that they will participate. Public weight voting runs from the start of the event to a week after.
6. **After the event.** The final scoreboard must be provided. Rating points for a new event are awarded once the voting window ends, about a week after.

**The submission needs an official site URL, so the public site must exist first.** That makes the website the critical-path item (see section 11).

## 2. Eligibility rules

| Rule | Source | L3m0nCTF |
|------|--------|----------|
| Team competitions only. An event that implies individual participation cannot be listed | For organizers | Fine. Allow one-person teams, as Pragyan CTF 2026 does ("max 4 members or individual") |
| An event lasting more than 5 days gets no rating points | For organizers | Prelims are 12 hours |
| Organisers of events without a scoreboard, or that never provide the final scoreboard, get no rating points | For organizers | We publish a public scoreboard and a final export |
| Event text must be in English | For organizers | The landing page, the event description and the whole platform are in plain English |

## 3. Restrictions, and the two rounds

CTFtime's event filters are: **Location** (On-line, On-site) and **Restrictions** (Open, Prequalified, Academic, High-school, Invited only, Individual). The format list includes Jeopardy, Attack-Defense and Hack-quest.

- **Prelims:** online, Jeopardy, **Open**. Keeping it open is what makes a rating plausible.
- **Finals:** Jeopardy-style, on campus, invited teams. This is naturally **Prequalified** or **Invited only**.
- Restrictions do not decide rating, the weight does. On the upcoming list, prequalified finals include SAS CTF Finals at 49.50 and Srdnlen CTF Finals at 25.00, while several Indian finals show 0.00 pending votes (H7CTF Finals, Hacker's Gambit Round 2). A two-round Indian CTF, Hacker's Gambit 2026, is listed as two separate events: an [online qualifier](https://ctftime.org/event/3380) and a prequalified [Grand Finale](https://ctftime.org/event/3381). That is the same shape as ours. **Ask CTFtime** (section 10).
- That precedent's official URL is a third-party event page, so the listing does not need our own domain. A public page with the event details is enough to submit.
- If we mark our teams "Academic" on CTFtime, that only concerns CTFtime's team flag (university students only). It does not change how our event is listed.

## 4. Listing fields

| Field | Value for L3m0nCTF | Needs |
|-------|--------------------|-------|
| Full event and CTF name | "L3m0nCTF 2026" | Settled name |
| Logo link | A hosted PNG or SVG | Public asset URL, from the landing site |
| Start and finish (UTC) | Prelims 28 November 2026, 10:00 to 22:00 IST = 04:30 to 16:30 UTC, 12 h (changed 2026-10-10) | Exact start and finish time in UTC |
| Official site URL | The public landing page | **Live site before submission** |
| Format | Jeopardy | |
| Online or on-site | Prelims on-line. Finals on-site, Coimbatore | |
| Organiser team name | A CTFtime team the submitter belongs to | Register one (an existing Amrita Coimbatore team may exist) |
| Prizes, description | From the landing page | Prize details |

Amrita Coimbatore already has a CTFtime presence: **XiomaraCTF** (the Anokha fest CTF, 2017 and 2018, weight 17.80 each), and a cybersecurity club team. Reuse the organiser account where sensible.

## 5. Scoreboard: the JSON feed

Spec: [json-scoreboard-feed](https://ctftime.org/json-scoreboard-feed). CTFtime polls the feed every 60 seconds.

| Field | Type | Notes |
|-------|------|-------|
| `standings` | array, required | One object per team |
| `pos` | integer | Position. Calculated from `score` if omitted |
| `team` | string | **Team name.** Unicode must be escaped as `\uXXXX` |
| `score` | number | Up to 4 decimal places. Required if `pos` is omitted |
| `tasks` | array of strings, optional | Task names. If present, every team needs `taskStats` |
| `taskStats` | object | `{ "<task>": { "points": n, "time": unix } }` |
| `lastAccept` | integer | Unix time of the last solve |
| `outward` | boolean | A guest team playing outside the overall standings |

Rules we will implement:
- **Public and unauthenticated**, cached for 30 to 60 seconds, so CTFtime polling can never load the app.
- Include only teams with **score above zero**. CTFtime's `total_teams` counts only teams that scored.
- Exclude hidden, banned and staff teams. Use the same tie-break as our scoreboard so positions match the site.
- Respect the **freeze**: the feed shows the frozen standings until we unfreeze.
- **Only list challenges that are publicly visible.** An existing CTFd plugin lists every visible challenge, including ones meant to stay hidden until a dependency is solved. Our secret and finale challenges must never appear in the feed.
- A **final export** (same format) after the freeze lifts, kept as a file for the record.
- In real events, CTFtime shows `Place | Team | CTF points | Rating points`, for example bi0sCTF 2025 with 624 teams. Organisers appear in some scoreboards as a team named "organizers", and organisers earn rating points for their own events. Optional for us.

The organiser page says the real-time feed feature is "in progress", and the feed page warns against implementing "maximal" feeds before an official announcement. So ship the minimal feed, be ready to hand over the final scoreboard in the same format, and see [CTFtime OAuth and the live feed](ctftime-oauth-and-live-feed.md) for the full plan, including the second (capture log) feed.

## 6. Team identity and names

The feed carries team **names only**, with no CTFtime team ID. Attribution therefore depends on **exact name matching** against CTFtime teams and their aliases. The FAQ's advice to use Aliases for alternate names points the same way. This is inferred from the format and the FAQ, and needs confirming (section 10).

What we build:
- A team-name hint at creation: "Use your CTFtime team name, or an alias, to be rated."
- Unique team names, compared case-insensitively, with Unicode preserved and escaped correctly in the feed.
- An optional **CTFtime team URL or ID** field on the team profile, and a "verified via CTFtime" badge when the OAuth flow is used.

## 7. CTFtime as an OAuth2 provider

Docs: [OAuth2 configuration](https://ctftime.org/for-organizers/oauth/).

| Item | Value |
|------|-------|
| Grant type | Authorization Code |
| Scopes | `profile:read` (alias `profile`), `team:read` (alias `team`), space-delimited |
| Authorize endpoint | `https://oauth.ctftime.org/authorize` |
| Token endpoint | `https://oauth.ctftime.org/token` |
| User endpoint | `https://oauth.ctftime.org/user` |
| Client ID | The CTFtime **event ID**, so it exists only after approval |
| Client secret | From the event management interface |
| Callback | Our own URL, registered for the event |

CTFtime says its server API is compatible with CTFd.io's. CTFd core only ships a provider for MajorLeagueCyber. A pull request and a fork add CTFtime, but we will write our own plugin rather than depend on either.

Caveats, from how rCTF handles it:
- A CTFtime login carries no email address, so email verification and **email-based bracket rules cannot apply**. We must apply bracket eligibility another way.
- A CTFtime account needs normal abuse controls like any other.
- It cannot be tested until the event is approved, so it stays **optional**, with ordinary registration as the fallback.

## 8. Rating and weight

- **Formula (2017 onward):** `E_rating = ((points_coef + place_coef) × weight) / (1 / (1 + team_place / total_teams))`. Only a team's **10 best results in a year** count.
- `total_teams` is the number of teams that scored above zero. `best_points` is the winner's points.
- **Weight** comes from public votes or is set by CTFtime admins. It depends on the tasks and the organisation level.
- **Voting:** open from the event start to a week after. Voters are members of last year's top-50 teams and of teams that scored above zero. Since 2021 a team must have played at least two events, and a player must have joined the team before the event. **Organisers and the winning team cannot vote.**
- **Cap:** first-time events, and events under 25, are limited to a vote weight of **25**. Otherwise the cap is last year's weight times 1.5 (times 2 for attack-defence). If an organiser runs several events in their first year, all of them are capped at 25.
- **Evidence:** the InCTF series' weights ran 25.00 (2017), 25.00 (2018), 37.00 (2019), 53.95 (2020) and 70.41 (2021), which matches the cap rule. XiomaraCTF held 17.80. Pragyan CTF 2026 shows 1.84.

We cannot buy weight. A stable platform, a timely and correct scoreboard, fair play and good challenges are what earn votes.

## 9. Compliance checklist

| # | Requirement | What we build | Backlog |
|---|-------------|---------------|---------|
| C1 | Public official site with event details, in English, with a logo URL | Landing page: name, dates in UTC and IST, format, rules, prizes, registration, scoreboard link, contact. Open Graph tags and schema.org `Event` markup | B11, new B19 |
| C2 | Team-based event | Team mode, one-person teams allowed, no individual scoring | B2 |
| C3 | Public scoreboard and a final one | Anonymous scoreboard page, plus a final export | B12 |
| C4 | JSON feed in the specified format | `/ctftime/scoreboard.json`: `standings`, `tasks`, `taskStats`, `lastAccept`, `outward`, Unicode escaping, cached 30 to 60 s, scored teams only, hidden items excluded, freeze respected | B12 |
| C5 | Exact team-name matching | Case-insensitive unique names, a name hint, an optional CTFtime ID field | B6, new B20 |
| C6 | CTFtime OAuth2 (optional) | Plugin with Authorization Code, scopes `profile` and `team`, team linking, abuse controls, bracket rules that do not rely on email | New B21 |
| C7 | Duration of 5 days or less | Prelims 24 h | none |
| C8 | Tasks and writeups on CTFtime (requested of organisers) | Export a tasks list (name, category, points, tags, a non-spoiler description) after the event | New B22 |
| C9 | Capacity for a CTFtime-sized audience | Plan for 1,000 teams (see below) | B4 |

**Capacity update.** Real CTFtime-listed events: bi0sCTF 2025 had 624 teams, bi0sCTF 2024 had 293, and Pragyan CTF 2026 (an Indian college CTF) had 892. The engineering target in the options document moves from 600 teams to **1,000 teams**.

## 10. Unknowns to put to CTFtime

Use the [feedback](https://ctftime.org/feedback) form or the contact route:
1. The lead time and the approval time for a new event, given we want a listing about 3 to 4 weeks before the prelims.
2. Whether the live JSON feed is supported today, or whether only a final scoreboard is accepted.
3. Whether a Prequalified or Invited-only finals can be listed and rated, and whether it can sit in the same series as the prelims.
4. How to represent the organiser team on the scoreboard (the "organizers" row).
5. The exact rule for matching scoreboard names to teams and aliases.
6. When the OAuth2 client credentials are available.

## 11. Timeline (P = prelims start)

| When | Step |
|------|------|
| Now (P-6 weeks) | Create the CTFtime account and organiser team. Stand up a minimal public landing page with event details |
| P-5 | Submit the event. Send the questions in section 10 |
| P-4 | Approval expected, with a management interface. Get the OAuth client secret |
| P-3 | Feed live on staging. Test the feed format against the spec. OAuth plugin tested if credentials exist |
| P-1 | Dress rehearsal covers the feed, the freeze and the final export |
| P | Prelims. The public scoreboard and the feed are live |
| P+1 week | Voting window closes. Final scoreboard already provided |
| P+2 weeks | Team merging closes. Tasks and writeups posted |
