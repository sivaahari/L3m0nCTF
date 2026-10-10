# Message to the superiors: registering L3m0nCTF 2026 on CTFtime (draft of 2026-10-10)

Subject: L3m0nCTF 2026: registering the event on CTFtime (what we file, what we need from you, and where the build stands)

Dear Sir/Madam,

We want to list L3m0nCTF 2026 on CTFtime (ctftime.org), the international site where capture-the-flag events are listed and rated. Being listed makes the event visible to teams worldwide, and after the event the participating teams earn CTFtime rating points, which is a strong reason for good teams to join. The listing is filed through a form and approved by hand by CTFtime, and they do not publish how long that takes. The event is 7 weeks away (28 November), so we would like to file as soon as the items below are settled.

## 1. What we will enter on the CTFtime form

- **Event name:** L3m0nCTF 2026
- **Official website:** https://l3m0nctf.xyz/ (live now; it shows the dates in IST and UTC, and the rules and privacy pages are published)
- **Format:** Jeopardy
- **Location:** On-line. The prelims are online. The on-campus finals in Coimbatore for the top teams will be listed as a separate second event later, the way other Indian two-round CTFs do it.
- **Dates:** 28 November 2026, 10:00 to 22:00 IST (04:30 to 16:30 UTC), 12 hours, one day
- **Restrictions:** Open (anyone can play, teams of 1 to 4, with a beginner category)
- **Logo:** https://l3m0nctf.xyz/icon-512.png
- **Description (English, ready to paste):** "L3m0nCTF is the annual flagship capture-the-flag competition of Amrita Vishwa Vidyapeetham, Coimbatore, hosted by TIFAC-CORE in Cyber Security. The online prelims are Jeopardy-style and run for 12 hours (10:00 to 22:00 IST on 28 November 2026). The top teams are invited to on-campus finals in Coimbatore. Challenges span web, pwn, reverse engineering, cryptography, forensics, OSINT and miscellaneous, with extra tracks in AI and LLM security, Web3 and hardware, IoT and RF. This edition is a broadcast themed on the cartoons we grew up with. Open to all, with teams of 1 to 4 players, and a beginner category that teaches the basics."
- **Registration link:** registration opens when the platform goes live at play.l3m0nctf.xyz. Until then the text will say "registration opens soon on l3m0nctf.xyz".

## 2. What we need from you before we can file

1. **A person with a CTFtime account who belongs to an organiser team.** CTFtime requires the submitter to be a member of the organising team. We suggest a TIFAC-CORE team on CTFtime. Amrita Coimbatore already has a presence there (XiomaraCTF, the Anokha fest CTF of 2017 and 2018), so an existing team could be reused. Please tell us who files and under which team name.
2. **An event contact email** that is checked every day (CTFtime may write to it, and teams will too).
3. **Prize details,** or your permission to write "to be announced".
4. **Confirmation of the restrictions:** prelims "Open", finals to be listed later as "Prequalified" or "Invited only".
5. **Your go-ahead to file now.** Please also tell us who at the college receives CTFtime's replies.

## 3. What happens after CTFtime approves

- CTFtime opens a management page for the event. From it we copy two values, the event number (the "client ID") and a secret, into our platform. That switches on "Login with CTFtime", so a player can sign in with their CTFtime team. This only works while the event is upcoming or running on CTFtime, so it can be tested for real only after approval.
- We type two addresses into that page: the sign-in return address `https://play.l3m0nctf.xyz/auth/ctftime/callback`, and the live scoreboard feed `https://play.l3m0nctf.xyz/ctftime/standings.json`. CTFtime reads the feed every minute. The feed is optional.
- **The part that matters most for rating:** after the event we upload the final results (we publish them at `.../ctftime/final-standings.json` once any cheating cases are settled). Without final results teams get no rating points.
- CTFtime matches teams by exact team name, so teams should register with the same name they have on CTFtime. Login with CTFtime does this automatically.
- Teams vote on the event's "weight" during the event and for a week after. Rating points follow once that window closes. A first-time event is capped at weight 25.
- CTFtime may ask for the server's fixed outgoing IP address, because its Cloudflare protection has blocked sign-in requests from unknown servers for other events.

## 4. Decisions needed in parallel, so the live scoreboard and the CTFtime sign-in can work

(Details in `docs/deploy/platform-deployment.md`.)

- Who owns the Google Cloud account and its bill, and may it be upgraded to a paid account? The estimate is about $190 to $250 for the whole event, mostly covered by the $300 starter credit.
- A server with a fixed IP address, and DNS control for `play.l3m0nctf.xyz` (the domain itself is already on Cloudflare).
- An email-sending service for verification emails (the free plans have daily limits, and hundreds of people register on the first day).
- The campus network addresses from which the organisers reach the admin pages, and two named people on call during the 12 hours.

## 5. Where the build stands (10 October 2026)

Done and tested:

- **Public website** at l3m0nctf.xyz on Cloudflare: countdown, rules, privacy, calendar file. It already shows the new 10:00 to 22:00 IST time.
- **Platform foundation:** hardened CTFd 3.8.8 with a database, cache and web server in containers, settings kept as code, automatic backups with a tested restore, and a checking pipeline that runs on every change. Leadership's penetration testers have a ready bundle.
- **Challenge author kit** for our ~30 authors, with nine sample challenges converted.
- **Release control:** the crew decides which challenges go on air and when, with scheduled drops.
- **Scoring:** values that fall as more teams solve, a cost per hint, ties won by whoever got there first, "revoke" and "restore" for a broken challenge, and bonus points with a private message.
- **The player's board** (channels, programmes, flag replies) and **seven drawn cartoon "cold open" comics** that open from each channel for registered players.
- **CTFtime pieces:** the live and final scoreboard feeds, and Login with CTFtime. Login is switched off until CTFtime approves the event.
- **How well it is checked:** about 1,100 automated tests, all of CTFd's own 676 tests, 78 tests on a running copy of the whole system, and an independent security review of each part, with every finding fixed or recorded.

Still ahead:

- **Nothing is deployed publicly yet except the website.** The platform runs on development machines and in testing only. Putting it online depends on the decisions in section 4.
- **Remaining build:** the Guide, Scoreboard and notifications pages (starting now), the scoreboard freeze, a whole-system security audit, the live-server launcher for challenges that need one, anti-abuse protections, sign-in with Google, then monitoring, backups off the machine and the load test.
- **Dates:** feature freeze 21 November, dress rehearsal 27 November, event 28 November.

## 6. Risks to be aware of

- CTFtime's approval time is unknown, so filing early is the safest course.
- The server decisions in section 4 are on the critical path: the platform cannot go live, and CTFtime sign-in cannot be tested, until they are made.
- One routine automated security check is flagging an advisory in a standard third-party image. It is not in our code, and we will refresh that image on the owner's go-ahead.

We can file as soon as items 1 to 5 in section 2 are settled. Please reply with those details, and we will register the event the same day.

Regards,
[Your name]
TIFAC-CORE in Cyber Security, Amrita Vishwa Vidyapeetham, Coimbatore
