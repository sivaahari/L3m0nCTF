# Will the $300 Google Cloud free credit be enough?

Written 2026-10-06 after leadership approved the proposed stack but raised a concern about cost. The plan is to run on Compute Engine virtual machines paid from the free trial credit.

**Short answer: probably yes for a lean plan (about $190 to $260), no if the big machines run around the clock for weeks.** The estimate uses list prices, not a quote, and the margin is thin, so two protections below matter as much as the sizing.

## What the credit is

- $300 of credit, valid for **90 days from sign-up**. When the credit is used up or the 90 days pass, the trial billing account **closes and every resource in it stops**, unless the account has been upgraded to a paid account first ([Google's free trial terms](https://docs.cloud.google.com/free)). Upgrading keeps the unused credit and spends it first.
- A trial account may start with a low CPU quota. I could not confirm the exact figure from Google's pages, so check the Quotas page for the chosen region before relying on the layout below. Upgrading to a paid account is also how the quota is raised.

## What we need (from the build design, assumption A2)

| Machine | Size | Used for |
|---------|------|----------|
| App host | 8 vCPU, 16 to 32 GB | The website, database, cache, workers |
| Challenge hosts (2 to 4) | 8 vCPU, 16 GB each | The live challenge containers |
| Staging | 4 vCPU, 16 GB | Rehearsals and tests (day-to-day development stays on a laptop for free) |
| Small ops host | 1 shared vCPU | Monitoring and alerts |

Cloudflare's free plan sits in front, so most traffic never reaches Google.

## The price

An 8 vCPU, 32 GB machine (`e2-standard-8`) lists at about **$0.268 an hour** in the Iowa region ([price list](https://calculator.holori.com/gcp/vm/e2-standard-8)); a 4 vCPU, 16 GB machine is about half of that. The Mumbai region is usually somewhat dearer, so the estimate below adds 20%.

| Item | Hours | Cost (Mumbai, with 20% margin) |
|------|-------|-------------------------------|
| App host, from registration opening to the day after the round (14 days) | 336 | about $108 |
| Two challenge hosts: load test, dress rehearsal, the round and a day either side (about 6 days each) | 288 in total | about $92 |
| Staging machine, switched on only on test days | about 100 | about $16 |
| Disks, one fixed IP address, outgoing traffic, logs | | about $35 |
| **Total** | | **about $250** |

## Ways to bring it down

- **Use cheap interruptible machines (Spot) for the challenge hosts** during the load test and rehearsal. They cost about a third of the normal price and are fine for short tests. Use normal machines for the round itself, because a Spot machine can be taken away.
- **Run the website on a smaller machine until the load test,** then switch to the big one. Resizing means a few minutes of downtime, which is fine before the event.
- **Switch machines off when they are not needed.** A stopped machine costs only its disk.
- **Open registration later** (every extra day of the app host is about $8).
- Keep staging on the laptop's Docker for everything except the final rehearsals.

With Spot challenge hosts for the tests and a small app host before the load test, the same plan comes to roughly **$190**.

## What would break the plan

- Four challenge hosts instead of two, run for weeks: about $300 on their own.
- Leaving everything on from now until the round.
- A traffic surprise: outgoing data is billed per gigabyte. Static files are cached by Cloudflare, so this should stay small, but it is the one cost with no ceiling.

## Two protections (do both before registration opens)

1. **Upgrade the billing account to a paid account while the credit is still unused.** The remaining credit is spent first, and nothing shuts down in the middle of the round if the estimate turns out low or the 90 days run out. This needs a card, and it needs someone to be accountable for the bill.
2. **Set a budget alert** at 50%, 80% and 100% of the intended spend, sent to two people. A budget alert does not stop spending, so someone must watch it during the event.

Also decide **when the 90 days start**: they begin at sign-up. Signing up around the time staging is first needed (late October) covers the round on 28 to 29 November; the finals, if they are in December or January, may fall outside it.

## What I still need

- Which region the machines will be in. Mumbai is closest to most players; Iowa is cheaper.
- Whether the account may be upgraded to a paid account and who owns the bill (decision D10).
