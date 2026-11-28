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
the new one). How codes reach people: qualifiers see theirs on the platform's studio page (a later platform release, switched on by the organiser);
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

## 5b. PDF certificates (the qualifiers' downloadable certificate)

With `--certificate`, the build also prints one PDF certificate per valid code of a category, filled in from the database: the holder's username,
the studio, the code, the Issue Date and a QR code back to the code's page. The page then has a **Download PDF** button, and the file is at
`https://verify.l3m0nctf.xyz/<CODE>/certificate.pdf`. Only valid codes get one: a withdrawn or replaced code has no PDF (404).

```bash
python -m l3mon verify build D:/l3m0nCTF/private-verify/credentials.sqlite D:/l3m0nCTF/private-verify/site --sponsors D:/l3m0nCTF/private-verify/sponsors --certificate PAR
```

`--certificate PAR` alone uses the design that ships with the tool (`tools/l3mon/verify/certificates/participant.svg`). For the organisers' own
design: `--certificate PAR=D:/l3m0nCTF/private-verify/certificate-par.svg`. The same option works for ORG, ACH and APP with a design of their own.

**Needs Chrome, Chromium or Edge on the machine that builds** (it prints the PDFs headless, with the network cut off; one that is not found by
itself is named with `L3MON_CHROME=<path>`). About half a second per certificate, four at a time. A rebuild prints only the certificates whose
design or details changed; the others are reused. The same input gives the same bytes.

**Changing the design** (the organisers decide it; nothing in the code changes):

1. Make it in Figma, Inkscape or Illustrator at the paper size (A4 landscape: 297 x 210 mm; in Figma a 1123 x 794 frame).
2. Where a value goes, type its placeholder as text: `{{USERNAME}}`, `{{STUDIO}}`, `{{CODE}}`, `{{DATE}}` (28 November 2026), and if wanted
   `{{EVENT}}`, `{{ROLE}}`, `{{RANK}}`, `{{NAME}}` (an organiser's full name, else the username), `{{URL}}`. `{{CODE}}` and the holder are required.
3. Draw a rectangle where the QR code goes and name it **`qr`** (its fill does not matter; the QR code replaces it).
4. **Alignment** of a value of another length: SVG's own `text-anchor` is followed. Figma fixes positions instead, so name the text layer with
   **center** (or centre, middle) to keep it centred, or **right** to keep its right edge; otherwise it keeps its left edge. A long value shrinks to
   fit the page (or the layer's `data-max-width`, in the design's units, when it has one).
5. Export as **SVG with "Outline text" OFF** (outlined text has no placeholders left; the build says so). Fonts: either common system fonts, or
   embedded in the SVG as a `data:` address; pictures (a background exported from Canva, a logo) also only as `data:` (Figma's "embed" export
   does that). The build refuses scripts and anything loaded from the network, and names the reason.
6. Build with `--certificate PAR=<the file>` and **open two or three PDFs** (a short and a long username) before publishing.

Keep the design file with the private files (it is not personal data, but it belongs with the database and the sponsor folder).

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

## Check that it works

```bash
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://verify.l3m0nctf.xyz/   # 200 and no redirect (a redirect to cloudflareaccess.com means Access caught it)
curl -s -o /dev/null -w "%{http_code}\n" https://verify.l3m0nctf.xyz/L3M0N26-PAR-BBBBB  # 404 (no such code)
curl -s -D - -o /dev/null https://verify.l3m0nctf.xyz/<a real code> | grep -i "content-security-policy\|x-robots-tag"
```

With PDF certificates: `curl -s -o /dev/null -w "%{http_code} %{content_type}\n" https://verify.l3m0nctf.xyz/<a real PAR code>/certificate.pdf`
gives `200 application/pdf`.

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
