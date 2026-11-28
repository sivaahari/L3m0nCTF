# 22. The participant verification portal (verify.l3m0nctf.xyz)

Every person who took part in L3m0n CTF 2026 gets one code, printed on their certificate with a QR code. Anyone (an employer, a college) can type
the code at **https://verify.l3m0nctf.xyz** or scan the QR code and see who the certificate was issued to.

| Code | Who | Shown on the page |
|---|---|---|
| `L3M0N26-ORG-xxxxx` | an organiser (the crew's list of names) | Event Name "L3m0n CTF 2026", Role "Organizer", Name, Issue Date |
| `L3M0N26-PAR-xxxxx` | a member of a studio that scored **above 0 TRP** in the online qualifiers | Event Name "L3m0n CTF 2026 Qualifiers", Role "Participant", Username, Studio Name, Issue Date |
| `L3M0N26-ACH-xxxxx` | a member of a **top-3** studio of the finals | Event Name "L3m0n CTF 2026 Finals", Role "Winner", Username, Studio Name, Studio Rank (1st-3rd), Issue Date |
| `L3M0N26-APP-xxxxx` | a member of any other finals studio | Event Name "L3m0n CTF 2026 Finals", Role "Participant", Username, Studio Name, Studio Rank (4th and up), Issue Date |

The five characters of every code the tool makes come from 21 consonants (no vowels: no I/O to mistake for 1/0, no accidental words); they are
unique across all categories. **Organiser codes are the exception:** the organisers' codes were handed out before this tool existed and are
permanent, so an ORG code may hold any five capital letters or digits (section 2). PAR, ACH and APP codes are never written by hand: the tool
draws them when the codes are issued, after the qualifiers (section 3) and after the finals (section 4).
**One code per person:** a qualifier who reaches the finals ends with the finals code only (the qualifier code's page then says "replaced" and links to
the new one). How codes reach people: qualifiers see theirs on the platform's studio page (step 7 below: loaded with one command, released by any crew account);
finalists get printed certificates.

## How it works (and why it can run for years)

The portal is a **static website**. `python -m l3mon verify build` turns the private credentials database into one ready-made page per code; nginx
serves the files read-only (`deploy/nginx/verify/verify.conf`, overlay `verify`). There is no application, no login and nothing that writes on the
server, the pages carry a strict Content-Security-Policy, and the same folder can later be served by any static host (a tiny VM, or a Cloudflare
Worker like the landing page) without changing a line. A withdrawn code's page names nobody.

Files: `tools/l3mon/verify/` (codes, database, issue rules, QR encoder, site builder, `export_prelims.py`), `tools/ops/verify-publish.sh`,
`deploy/nginx/verify/verify.conf`, `deploy/nginx/snippets/verify-headers.conf`, `deploy/compose/compose.verify.yml`; tests `tools/tests/test_verify_*.py`.

## PRIVATE data: never in this repository

- the **credentials database** (`credentials.sqlite`): the only record of the codes. Keep it on the owner's laptop **and** a backup copy (it cannot
  be re-made: codes are random). It holds real organiser names, usernames and studio names.
- the **organiser list** (one full name per line), the **prelims export**, the **finals results**, the **sponsor folder** (`sponsors.json` and logos).

Suggested place on the laptop: a folder outside every repository, e.g. `D:\l3m0nCTF\private-verify\` (and its backup).

**The Issue Dates (the organisers' decision of 27 Nov 2026):** organisers (ORG) **2026-12-12**, finals winners (ACH) and finals participants (APP)
**2026-12-12**, qualifier participants (PAR) **2026-11-28**. Every `--date` below uses them. The organisers' codes were issued with their date on
27 Nov 2026; a code keeps the date it was issued with, whatever a later run says.

## 1. Make the database (once)

```bash
cd tools
python -m l3mon verify init D:/l3m0nCTF/private-verify/credentials.sqlite     # makes the folder too if it is not there
```

Names may hold any character; the tool writes UTF-8 when its output goes to a file or a pipe, so `verify list > list.txt` is safe.

## 2. Organisers

```bash
python -m l3mon verify issue D:/l3m0nCTF/private-verify/credentials.sqlite --date 2026-12-12 --org D:/path/to/org.txt
```

Re-running with a longer list issues codes only to the new names; a removed name is reported, never deleted (use `withdraw`).

**Permanent organiser codes.** A line may end in the organiser's own code, after a tab or spaces: `Full Name<TAB>L3M0N26-ORG-XXXXX`. That code is
stored exactly as written (any five capital letters or digits) and never changes; a line with only a name gets a code drawn by the tool. Load the
organisers **before** the qualifier codes are issued, so that no drawn code can take a permanent code's five characters (the run would stop and
name it). The run stops, and writes nothing, when the same code is on two lines, when a code is already held by someone else, or when a person
already holds a different code (a code is permanent: withdraw the old one first if the list is right). Mind the look-alikes in these codes
(`O` and `0`, `I` and `1`): the page matches the code exactly, so print them in a font that tells them apart.

## 3. Qualifier participants (after the qualifiers end, 28 Nov 22:00 IST, and after any Revoke/Restore)

The export runs **read-only** inside the platform container on the VM (**ask the owner first**, rule R3):

```bash
# on the laptop: copy the export script up
# (the gcloud configuration and project as in the owner's private notes, with CLOUDSDK_CORE_PROJECT set as for tools/ops/gate.sh)
gcloud compute scp tools/l3mon/verify/export_prelims.py <platform VM>:/tmp/export_prelims.py --zone=<zone> --tunnel-through-iap --quiet
# on the VM
cd ~/L3m0nCTF && set -a && . /etc/l3mon/production.env && set +a
docker exec -i "$(tools/compose.sh ps -q ctfd)" bash -c "source /usr/local/lib/l3mon-env.sh; python -" < /tmp/export_prelims.py > /tmp/prelims-export.txt
# back on the laptop: fetch it, then DELETE both copies on the VM (rm /tmp/prelims-export.txt /tmp/export_prelims.py)
```

It lists every member of every studio the public scoreboard ranks (above 0 TRP; hidden and banned studios, banned or hidden accounts and crew
accounts are left out), between two marker lines. Then:

```bash
python -m l3mon verify issue D:/l3m0nCTF/private-verify/credentials.sqlite --date 2026-11-28 --prelims D:/path/to/prelims-export.txt
python -m l3mon verify list  D:/l3m0nCTF/private-verify/credentials.sqlite     # look before publishing
```

## 4. Finals (after 12 Dec)

The finals system's export format is **not decided yet**. Until it is, the tool reads a CSV with `username,studio,rank` and optionally `user_id`
(the platform's user id, which matches a qualifier exactly; without it the username is matched, ignoring case):

```bash
python -m l3mon verify issue D:/l3m0nCTF/private-verify/credentials.sqlite --date 2026-12-12 --finals D:/path/to/finals.csv
```

Ranks 1-3 become ACH (Winner), 4 and up APP; two studios cannot share a rank. The holders' qualifier codes are marked replaced in the same run.

**Who is who.** A player is recognised by either the platform user id or the username (Unicode-normalised, spaces and case ignored). When the
two point at two different people already in the database, the run stops and changes nothing: fix the input and run again.

## 5. Build and look

```bash
python -m l3mon verify build D:/l3m0nCTF/private-verify/credentials.sqlite D:/l3m0nCTF/private-verify/site --sponsors D:/l3m0nCTF/private-verify/sponsors
```

Pack it **in PowerShell** (Windows' own tar; Git Bash's GNU tar reads `D:/...` as a remote machine called "D"):

```powershell
tar -czf D:\l3m0nCTF\private-verify\verify-site.tgz -C D:\l3m0nCTF\private-verify\site .
```

`sponsors.json` is `[{"name": "CyberDefenders", "file": "cyberdefenders.png"}, ...]` in the order of the crawl; logos are PNG, SVG, WebP or JPEG
(300 KB at most; an SVG must be a plain drawing). The build refuses anything else and says why.

## 6. Publish (the VM: ask the owner first)

Once only, first: **deploy a release that contains the portal** (the usual release procedure, with its backup and fingerprint). The overlay file,
the nginx block, the `verify` rate-limit zone, the include in the production template and `tools/ops/verify-publish.sh` all arrive with it; on an
older checkout `tools/compose.sh` stops with "there is no overlay 'verify'".

**The first time, in this order** (publishing BEFORE nginx gets the block means nginx never serves an empty folder):

1. From the laptop, copy the archive up (same gcloud configuration as in step 3):
   `gcloud compute scp D:\l3m0nCTF\private-verify\verify-site.tgz <platform VM>:/tmp/verify-site.tgz --zone=<zone> --tunnel-through-iap --quiet`
2. On the VM, make the folder and publish into it:
   ```bash
   sudo mkdir -p /etc/l3mon/verify/site && sudo chown "$USER" /etc/l3mon/verify/site
   cd ~/L3m0nCTF && bash tools/ops/verify-publish.sh /tmp/verify-site.tgz /etc/l3mon/verify/site && rm /tmp/verify-site.tgz
   ```
3. In `/etc/l3mon/production.env` (with `sudo`): add `,verify` to the `L3MON_OVERLAYS` line, and add the line `L3MON_VERIFY_SITE_DIR=/etc/l3mon/verify/site`.
4. Check, then give nginx the block (nginx is recreated; a few seconds):
   ```bash
   set -a; . /etc/l3mon/production.env; set +a
   (cd tools && python3 -m l3mon deploy check)          # the line "verification portal" must be OK, with the number of certificates built
   tools/compose.sh up -d --wait nginx
   ```

**Every later publish:** step 1, then on the VM `cd ~/L3m0nCTF && bash tools/ops/verify-publish.sh /tmp/verify-site.tgz && rm /tmp/verify-site.tgz`
(the folder comes from `L3MON_VERIFY_SITE_DIR`). No restart: the script copies the files into the served folder.

**Cloudflare (the owner):** a DNS record `verify` -> the platform VM's address (the same address as `play`), **proxied** (orange cloud). An explicit
record wins over the instancer's wildcard `*`. The nginx block answers only the exact name `verify.l3m0nctf.xyz` (Cloudflare's
origin-pull certificate is shared by all its customers, so a looser name would serve the portal under anybody's zone); another domain means
editing its one `server_name` line. The origin certificate already covers `*.l3m0nctf.xyz`, and Authenticated Origin Pulls applies to the
whole zone. **Access:** the portal must stay public. If the crew Access application ever carries the wildcard `*.l3m0nctf.xyz` (the strict instancer
set-up, docs/deploy/instancer.md), add a **Bypass** application for `verify.l3m0nctf.xyz`, exactly as for `play`: the most specific hostname wins.
Optional: a Cloudflare rate-limit rule for `verify.l3m0nctf.xyz` (nginx already allows 60 requests a minute per address).

## 7. The qualifiers' certificates on the platform (after step 6)

Each qualifier participant sees a **"Your certificate"** card at the top of their own studio page (`/team`): their code, a QR code of the
certificate's address and an "Open your certificate" button. Nobody sees anybody else's code. Nothing shows until a crew account releases
the certificates, and every player then gets **one** notification, "Certificates are ready" (a short pop-up and the bell, never the
blocking announcement). Plugin: `plugins/l3mon_certificates`.

1. On the laptop, write the platform's list (the VALID qualifier codes, by platform player id; it is PRIVATE: usernames and codes):
   ```bash
   python -m l3mon verify export-platform D:/l3m0nCTF/private-verify/credentials.sqlite D:/l3m0nCTF/private-verify/certificates.json
   ```
2. Copy it up (same gcloud configuration as in step 3) and look first: without `--apply` nothing is written, it only says what would change
   (how many new, changed, removed; player ids with no account; players who renamed themselves, who keep their code):
   ```bash
   gcloud compute scp D:\l3m0nCTF\private-verify\certificates.json <platform VM>:/tmp/certificates.json --zone=<zone> --tunnel-through-iap --quiet
   # on the VM
   cd ~/L3m0nCTF && set -a && . /etc/l3mon/production.env && set +a
   docker exec -i "$(tools/compose.sh ps -q ctfd)" bash -c "source /usr/local/lib/l3mon-env.sh; cd /opt/CTFd; python -m CTFd.plugins.l3mon_certificates.loader" < /tmp/certificates.json
   ```
3. If it is right, the same command with `loader --apply`, then **delete the copy**: `rm /tmp/certificates.json`. The platform's table is
   now exactly the list (one line in the audit trail, `certificates.import`). Importing releases nothing.
4. Release: crew host, **Admin -> Certificates** (`/admin/l3mon/certificates`): **Release now**, or a day and time (IST) and **Release at
   this time**. Any crew account can; every change is in the audit trail (`certificates.release`, with who did it). **Hide them** takes the
   card off every studio page at once; the codes stay valid on the portal. The notification is sent once only, ever: releasing again after
   hiding sends none.
5. Check: sign in as a test player that has a code and open `/team`: the card shows that player's code, the QR code opens the portal's page.

After a later `issue`, `withdraw` or the finals run, repeat 1-3: a withdrawn code, or a qualifier code replaced by a finals code, leaves the
platform at the next import (the finalist then has no card: their certificate is printed).

**Things to know (the independent review of 27 Nov 2026):**
- **A player who is in no studio sees no card.** The card is on the studio page, and `/team` sends a player without a studio to the join page.
  A qualifier who left or was removed from their studio still gets the notification, but has nowhere to see the code: give it to them by
  hand from `verify list`, or they can find it on the portal only through the code itself.
- **Import only into the platform the qualifiers ran on.** The list is matched by player id. On a rebuilt or restored database a player id
  could belong to a different person, who would then see somebody else's code. In the dry run, a large "renamed since the export" count is
  the warning sign: stop and ask.
- **A release set for later sends its notification only when codes are loaded.** If the table is empty at that moment, nothing is sent and
  the one notification is kept for when codes are imported. A notification that keeps failing is retried by each worker at most every 30 s.
- The import names no code or username in any error: a database error shows only its kind (for example `IntegrityError`).
- **The card's QR code is a picture of its own** (`/api/v1/l3mon/certificate/qr.svg`): it answers only the signed-in player's own code, only
  while released (404 otherwise, never somebody else's), and is never cached. Drawn inside the page it put the studio page over its size
  budget. A future Content-Security-Policy on the platform needs `img-src 'self'` for it (no `data:` images are needed).

## Check that it works

```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://verify.l3m0nctf.xyz/   # 200 and no redirect (a redirect to cloudflareaccess.com means Access caught it)
curl -s -o /dev/null -w "%{http_code}\n" https://verify.l3m0nctf.xyz/L3M0N26-PAR-BBBBB  # 404 (no such code)
curl -s -D - -o /dev/null https://verify.l3m0nctf.xyz/<a real code> | grep -i "content-security-policy\|x-robots-tag"
```

Then open a real code in a browser and scan its QR code with a phone.

## Withdraw a code

```bash
python -m l3mon verify withdraw D:/l3m0nCTF/private-verify/credentials.sqlite L3M0N26-PAR-XXXXX --reason "why (kept private)"
```

Then build and publish again. The page then says the certificate was withdrawn, without any name. A routine re-run never gives that person a new
code. To correct a certificate (a wrong rank, a misspelt name), withdraw the old code and run `issue` with the corrected list and
`--reissue <old code>`: only then is a new code issued to that person.

## Keeping it alive for years

A certificate is only worth something while its code can be checked. These are the things that can end that, and who looks after each. The
dates are the ones known in November 2026: check them again when this runbook is next used.

| What | Ends when | What to do, and who |
|---|---|---|
| The domain `l3m0nctf.xyz` | at its registrar's expiry date (the owner's notes; November 2027 when this was written) | renew every year, or set it to renew by itself, **well before** the date (owner, with the club mailbox that holds the registrar account). If the domain lapses, every printed QR code dies with it. |
| The platform VM (where the portal runs today) | when the VM is stopped after the events, or the cloud credit ends | **move the portal first** (next section), then stop the VM. Decide the permanent host before the finals. |
| Cloudflare's origin certificate on the VM | valid to 2041 | nothing until then; a new host gets its own. |
| The credentials database | if the only copy is lost (codes are random: it cannot be made again) | keep it in at least **two** places: the owner's laptop and an encrypted copy somewhere else the owner controls. Copy it again after every `issue` or `withdraw`. |
| The built site | (it can always be rebuilt from the database and the sponsor folder) | keep the last `verify-site.tgz` next to the database backup anyway: it is the fastest way back. |
| The people who know how | when the crew changes | this runbook; hand it on with the database. |

## Moving the portal off this VM (before the VM is ever stopped)

The published folder is the whole website. Any static host that can map `/<CODE>` to `c/<CODE>.html`, answer unknown addresses with `404.html`
(status 404) and send the headers of `deploy/nginx/snippets/verify-headers.conf` will do:

- **a small VM with nginx** and `deploy/nginx/verify/verify.conf` as it is (the least work, but a machine to keep paying for and patching);
- **Cloudflare (Pages, or a Worker like the landing page)**: free and with no server to look after, which suits "forever". It needs a small rule
  file for the `/<CODE>` mapping and the headers; to be prepared when the permanent host is decided.

Point the `verify` DNS record at the new host, check it with "Check that it works" above, and only then stop the old one.
