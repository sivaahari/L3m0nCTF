# Story-driven CTFs: a survey

What has already been done with narrative in CTFs, so the story we choose for L3m0nCTF builds on proven mechanics and adds something new. This document is public and contains no event material.

## Method and limits

- Sources: web search (a US-only index) plus primary pages where reachable: SANS, Hack The Box, NorthSec, CMU's Entertainment Technology Center, GitHub, CTFtime. Items marked *(search summary)* come from search snippets only and were not confirmed on a primary page.
- Some pages could not be fetched: the Hackvent site (DNS error), one Dark Reading article (HTTP 403), one CTF-design blog (redirect loop). Those events are described from secondary sources only.
- About 25 events and formats are covered. That is broad, not exhaustive. Indian and wider Asian events are thinner than they should be, and additions are welcome.
- "No precedent found" means none turned up in this survey. It does not prove none exists.

## 1. Catalogue

### Story as flavour on an open board (the dominant pattern at scale)

| Event | What the story does | Notes | Source |
|-------|---------------------|-------|--------|
| Hack The Box Cyber Apocalypse, 2021 to 2025 | A new sci-fi or fantasy plot each year: aliens attack on Earth Day (2021), "Intergalactic Chase" (2022, 181 countries *(search summary)*), "The Cursed Mission" (2023), "The Fray" run by KORP with five factions (2024), "Tales from Eldoria", an online RPG taken over by an AI called Helios (2025, 21 to 26 March) | 2024: nearly 13,000 players, 5,730 teams, 67 challenges, eight teams solved all. The plot is flavour text and the factions are cosmetic | [2024 recap](https://hackthebox.com/blog/cyber-apocalypse-2024-event-recap), [2025 event](https://hackthebox.com/events/cyber-apocalypse-2025), [2022 recap](https://www.hackthebox.com/blog/cyber-apocalypse-2022-event-recap), [2021](https://darkreading.com/cyber-risk/10k-hackers-defend-the-planet-against-extraterrestrials) |
| SekaiCTF | Anime references inside individual challenges, no overarching story | Fandom flavour only | [CTFtime](https://ctftime.org/ctf/769) |

### Story revealed in acts or scenes

| Event | What the story does | Notes | Source |
|-------|---------------------|-------|--------|
| picoCTF 2013, "Toaster Wars" | Five scenes with more than four problems each *(search summary)* | picoCTF has a different storyline each year. 6,000 participants in 2013, 39,000 by 2019 | [Wikipedia](https://en.wikipedia.org/wiki/PicoCTF) |
| picoCTF 2014, "Daedalus" (CMU Entertainment Technology Center) | A robotics expert is kidnapped and his son investigates across five acts. Breaking into each piece of encrypted evidence uncovers part of the true story. Acts 4 and 5 are optional deeper investigation | The clearest precedent for "solve to reveal story" | [CMU ETC](https://projects-old.etc.cmu.edu/daedalus/?p=14) |
| SANS CyberStart Game, 2014 to 2024 | The player is an agent of a fictional agency working across bases (HQ, Moon, Forensics) | 200+ challenges, levels unlock progressively | [CyberStart](https://cyberstart.com/game/) |
| Google CTF Beginners Quest | Challenges tied together by a spy story. 2021 had 18 tasks and no scoreboard | Stress-free, story-first | [Gynvael](https://gynvael.coldwind.pl/?id=743) |

### In-world exploration

| Event | What it does | Notes | Source |
|-------|--------------|-------|--------|
| SANS Holiday Hack Challenge and KringleCon | An avatar world with objectives and terminals. NPCs and conference talks give hints | Non-linear. A "CTF-Mode" skips the narrative. 2025 had 15+ micro-challenges of 10 to 15 minutes plus 3 to 4 capstones, and "cohorts" with their own live scoreboard | [SANS](https://www.sans.org/mlp/holiday-hack-challenge-2022) |
| Pwn Adventure 3 (Ghost in the Shellcode 2015) | An intentionally vulnerable open-world MMORPG | Hack the game client, network and logic | [Vector 35](https://vector35.com/HackingGames) |
| Google CTF Hackceler8 (2022, 2023) | Esports-style finals for the top 8 teams. Flags come from abusing a custom game's mechanics. 2023 cast the teams as "Mew" against the evil "rA.Ibbit" *(search summary)* | Held in Tokyo in 2023 | [Google blog](https://security.googleblog.com/2022/06/game-on-2022-google-ctf-is-here.html), [C4T BuT S4D tooling](https://github.com/C4T-BuT-S4D/hackceler8-2023) |

### Spatial map

| Event | What it does | Notes | Source |
|-------|--------------|-------|--------|
| Facebook CTF (FBCTF), open-sourced 2016 | A world-map gameboard where teams capture countries by capturing flags | Jeopardy and King-of-the-Hill modes. Archived | [GitHub](https://github.com/facebookarchive/fbctf), [Help Net Security](https://www.helpnetsecurity.com/?p=48255) |

### Time-gated and daily

| Event | What it does | Notes | Source |
|-------|--------------|-------|--------|
| Hackvent and Hacky Easter (Hacking-Lab) | Daily challenges through December, and 24 "eggs" at Easter. Easter eggs are QR codes scanned in a mobile app *(search summary)* | Gamification through a mobile app | [Hackvent write-ups](https://mobeigi.com/blog/category/security/capture-the-flag/hacking-lab/hackvent/2024) |
| TryHackMe Advent of Cyber, 2023 and 2024 | A daily story. The **Side Quest** challenges are locked behind keycards hidden inside tasks of the main event | A working "hidden key" pattern on an open board | [Side Quest 2024](https://tryhackme.com/room/adventofcyber24sidequest) |
| CyberStudents Advent of CTF | Daily challenges 1 to 25 December | | [CTFtime](https://ctftime.org/ctf/1218) |

### Meta and finale

| Event | What it does | Notes | Source |
|-------|--------------|-------|--------|
| NorthSec (Montreal), 2013 to 2025 | Every edition has a theme that drives the storyline, the hardware badge and the challenges. Flags are part of a plot unravelled over 48 hours. 2025 ("Cruiseship CVSS Bonsecours", a heist) introduced the first **metatrack** that combines several tracks into one final track | 600 to 680 on-site players in 2024 and 2025. Physical tracks, costume contest. 2026 reportedly used a solarpunk universe with four story characters, 41 tracks and 161 flags *(search summary)* | [Past editions](https://nsec.io/past-editions/) |
| Flare-On (Mandiant) | About 11 reverse-engineering challenges over six weeks, with finisher prizes | Single-player. Whether challenges strictly unlock in order was not confirmed | [Mandiant blog](https://cloud.google.com/blog/topics/threat-intelligence/announcing-ninth-flareon-challenge/) |

### Investigation and mystery

| Event | What it does | Notes | Source |
|-------|--------------|-------|--------|
| Immersive Labs Halloween murder mystery | Five labs, each revealing part of who did it (SQL injection, nested archives, DNA forensics in Autopsy, template injection) | Linear series | [Immersive Labs](https://www.immersivelabs.com/resources/blog/halloween-can-you-solve-the-murder-mystery-in-our-new-series) |

### Precedents for our niche tracks

| Track | Event | What it shows | Source |
|-------|-------|---------------|--------|
| AI/LLM | Lakera Gandalf | Eight levels, each adding a defence, from none to an LLM output filter. Over 40 million prompts. A ready-made "ladder" format | [Lakera](https://lakera.ai/blog/who-is-gandalf) |
| Web3 | Paradigm CTF, DownUnderCTF | A private forked chain per team, started on demand, so solutions do not leak. Reference infrastructure exists | [Zellic](https://www.zellic.io/blog/how-to-create-an-ethereum-ctf-challenge), [DownUnderCTF](https://github.com/DownUnderCTF/eth-challenge-infra) |
| Hardware/IoT | Ph0wn | An on-site only smart-device CTF with physical gear (drones, cameras, consoles) | [CTFtime](https://ctftime.org/ctf/220) |

### Culture and pop-culture

| Item | Relevance | Source |
|------|-----------|--------|
| Alternate reality games: Cicada 3301, Ben Drowned | Genre precedent for "lost media" storytelling and puzzle trails. Not CTFs | [Wikipedia](https://en.wikipedia.org/wiki/Alternate_reality_game) |
| TryHackMe "Pickle Rick" | A single beginner room themed on Rick and Morty. The nearest pop-culture CTF precedent found | [Walkthrough](https://mayillikestotech.medium.com/pickle-rick-a-rick-and-morty-themed-ctf-tryhackme-walkthrough-be1e2328adb2) |

### Indian context

| Event | Notes | Source |
|-------|-------|--------|
| InCTF (Amrita Vishwa Vidyapeetham with team bi0s) | India's first CTF. The 2026 theme is "Operation VAJRA": a legacy orbital system tries to retake control of modern infrastructure. Online phases in April and May, offline finals in Bangalore in June, teams of 3 to 5, a ₹5,00,000 prize pool | [Internshala](https://internshala.com/competitions/inctf-2026/) |
| Pragyan CTF (NIT Trichy) | Student-run and open to the world. No narrative noted | [CTFtime](https://ctftime.org/event/3058/) |
| BackdoorCTF | Online Jeopardy. No narrative details found | |

InCTF is run by Amrita's other campus, so our story should stay clear of a space and cyber-warfare theme.

## 2. Patterns the field has converged on

| Pattern | Examples | Cost | Fit with an open board |
|---------|----------|------|------------------------|
| Flavour-text narrative | Cyber Apocalypse, most themed CTFs | Lowest | Native |
| Solve-to-reveal acts | picoCTF 2014, CyberStart | Medium (writing and art) | Good, if reveals are per team and optional |
| In-world exploration | Holiday Hack, Pwn Adventure | High (a whole game) | Poor for 250+ teams in 24 hours |
| Spatial map | FBCTF | Medium (one map) | Good |
| Time-gated or daily | Hackvent, Advent of Cyber | Low | Partial: gating conflicts with "open board", but timed lore drops work |
| Hidden keys and side quests | Advent of Cyber Side Quest | Low | Good: reward curiosity without blocking anyone |
| Meta-track or finale | NorthSec 2025 | Medium | Good: gives every solve a purpose |
| Physical and on-site | NorthSec badges, Ph0wn | High | Only for finalists on campus |
| Investigation | Immersive Labs | Medium (writing) | Good: evidence cards fit an open board |
| Escalating ladder | Gandalf | Low | Good for the AI/LLM track |
| Persona and faction | CyberStart, Cyber Apocalypse 2024 | Low | Good: identity without mechanics |

## 3. Lessons we can use

- **Narrative helps but is optional.** One organiser's view is that a story is not required, yet can lift a CTF from adequate to great ([Hats Off Security](https://hatsoffsecurity.com/2020/05/27/how-to-create-a-good-security-ctf/)). SANS lets players switch the story off. We should too.
- **State the technical facts plainly.** Creators assume players know the flag format. Over-explain format, case and time stamps (Hats Off Security).
- **Keep difficulty honest.** Suggested mixes: teaching 35% easy, 35% medium, 25% hard, 5% extreme. Conference-style 10/25/40/25 (Hats Off Security).
- **Playtest with two internal reviewers and ship a reference `solve.py`.** Do not change a challenge after any team has solved it. Release hints publicly, not privately. Use a weekend, 24 to 48 hours, with the tiebreak on the time of the last scoring solve ([rCTF, Running a successful CTF](https://rctf.osec.io/meta/running-a-successful-ctf/)).
- **Plan the CTFtime submission early.** Rating needs a scoreboard, in the [JSON feed format](https://ctftime.org/json-scoreboard-feed), for team-based events of at most five days ([CTFtime for organisers](https://ctftime.org/for-organizers)).
- **Players publish write-ups.** Treat them as feedback and publicity, not a leak (Hats Off Security).

## 4. What we found no precedent for

- **A CTF themed on Indian cartoons.** The nearest pop-culture CTF found is a single TryHackMe room.
- **A shared progress meter across all teams in a CTF.** Community-goal bars are an established mechanic in games, such as the community bar in Coin Master and Diablo 4's community challenge ([example](https://support.coinmastergame.com/hc/en-us/articles/24439148690578-What-is-the-Community-Challenge)), so it is proven with players, but none turned up in CTFs.
- **Per-team story variants to defeat spoiler sharing**, and **a story meter that reconstructs a cryptographic secret** (for example, secret-sharing fragments earned by solves). Both are new to this survey.
- **A broadcast-schedule structure** where categories are channels. Advent calendars are the nearest relative.

These gaps are where an original story can stand out. The story pitches use them.
