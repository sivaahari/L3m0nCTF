# Message to the superiors: registering L3m0nCTF 2026 on CTFtime (updated 2026-10-17)

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

## 5. Where the build stands (17 October 2026)

**The two addresses CTFtime will ask for are built and tested. What is missing is a server to put them on, and filing the event does not need one.**

- **The live scoreboard feed** (`/ctftime/standings.json`) and the final results file are built, in CTFtime's own format, with the platform's real standings and its freeze, and tested inside the platform and through the web server.
- **Login with CTFtime** is built and tested against a stand-in for CTFtime. It stays off until CTFtime approves the event and gives us the event number and the secret; only then can it be tried for real.
- **The production web server set-up is built and was tried** on 17 October with throwaway certificates: a complete production-mode copy of the platform started, answered only to Cloudflare's certificate, kept its session cookie secure, served the feed with no cookie, and sent the CTFtime sign-in to CTFtime with the right return address. A computer-run checklist (`deploy check`) tells the person deploying what is still missing, and never shows a secret.
- **Public website** at l3m0nctf.xyz on Cloudflare: countdown, rules, privacy, calendar file, and (being updated now) the cartoon pictures.
- **The platform itself:** release control (the crew decides what is on air and when), scoring that falls as more teams solve, hints that cost, bonuses and voids, the board, the Guide, the scoreboard, the notification bell, the scoreboard freeze, and the painted cold-open comics and the finale. The whole of this back end (SP3) is finished, measured and audited.
- **How well it is checked:** over 3,000 automated checks, all of CTFd's own 676 tests on our image, a complete system run from nothing before every step, deliberate breakage of the code to prove the checks notice, and an independent security review of each part by a second model, with every finding fixed or written down.

Still ahead:

- **A server.** Nothing but the website is online. The platform runs on development machines. Going online needs the decisions in section 4. **The size of the server is a decision for you:** we measured that the platform needs about 2.8 processor cores for 300 players at once, about 9 for 1,000 and about 15 for 1,600 (`docs/remaining-work.md`).
- **The pages players see** (sign-in, channels, Guide, scoreboard, notifications) on the real platform: the approved design exists as a demo and is being rebuilt on CTFd. Then anti-abuse protections, challenge launchers for web and pwn challenges, sign-in with Google, monitoring, off-machine backups and the load test.
- **Dates:** feature freeze 21 November, dress rehearsal 27 November, event 28 November.

## 6. Risks to be aware of

- CTFtime's approval time is unknown, so filing early is the safest course. Approval must come before we can test the sign-in for real.
- The server decisions in section 4 are on the critical path for everything players touch, and for the live feed.
- The number of players the first event can carry depends on the size of the server (section 5).
- One routine automated security check is flagging an advisory in a standard third-party image. It is not in our code, and we will refresh that image on the owner's go-ahead.

We can file as soon as items 1 to 5 in section 2 are settled. Please reply with those details, and we will register the event the same day.

Regards,
[Your name]
TIFAC-CORE in Cyber Security, Amrita Vishwa Vidyapeetham, Coimbatore
