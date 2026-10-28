# Moving the platform to another Google account, exactly as it is

Use this when the Google Cloud project that runs the platform has to be left (a billing problem, a change of account) and the same platform, with the same data and the same settings, has to run in another project. Nothing about the site changes for visitors except that the server's address changes behind Cloudflare.

**The rule for every step that touches Google Cloud: ask the project owner first and wait for a clear yes.** A step that only runs on this computer needs no yes.

## 0. What lives where

| Where | What | In this move |
|---|---|---|
| GitHub | the code and the release tag (`release-2026-10-22` is what runs today) | nothing to do |
| Cloudflare | DNS, the origin certificate, the client-certificate check, the cache rules, the admin second factor, the landing page | nothing, except the two DNS records get the new address (Part 5) |
| **Only on the old machine** | the database and the uploaded files; the secrets in `.secrets/`; the story volume; `config/event.toml`; `deploy/compose/generated/`; the folder `/etc/l3mon` (settings, admin list, TLS files with the origin key) | **saved first, in one file (Part 1)** |
| The old Google project | the project, the fixed IP, the firewall rules, the VM | cannot be moved: made again, with the same names and sizes (Part 3) |

The two secrets that matter most are `SECRET_KEY` (every sign-in link and invite is signed with it) and `FLAG_HMAC_SECRET` (every team's flag is made from it). They are in the saved file; the new machine uses the same ones, so nothing already issued stops working. Everybody who was signed in has to sign in again (the cache is cleared).

## 1. Save the old machine (do this first, whatever else is undecided)

The two scripts are in `tools/` of the repository's newer commits; the release the machine runs does not have them yet, so copy them over (they are not "changes": the saved summary ignores them). On your computer, in the repository folder, with the old project's account active:

```bash
gcloud compute scp tools/export-state.sh tools/import-state.sh l3mon-app:/home/<your-vm-user>/L3m0nCTF/tools/ --zone asia-south1-a --tunnel-through-iap
gcloud compute ssh l3mon-app --zone asia-south1-a --tunnel-through-iap
```

(`gcloud compute scp` needs the full remote path, not `~`, and on Windows no wildcard: type the exact file name the script printed. `<your-vm-user>` is what the prompt shows, for example `l3m0nctf_gmail_com`.) On the VM:

```bash
cd ~/L3m0nCTF && chmod +x tools/export-state.sh tools/import-state.sh
tools/export-state.sh --encrypt
```

It asks for a passphrase twice (not shown): **choose one and keep it in your password manager; without it the file cannot be opened.** It saves the database and the uploaded files (through `tools/backup.sh`), the story volume, the secrets, the settings that are not in git, `/etc/l3mon`, and which release is running, writes **one** file `backups/l3mon-state-<host>-<time>.tar.enc` (AES-256) next to a `.sha256`, and prints no secret. Then, from your computer:

```bash
gcloud compute scp l3mon-app:/home/<your-vm-user>/L3m0nCTF/backups/<the-file-name-it-printed>.tar.enc ./ --zone asia-south1-a --tunnel-through-iap
gcloud compute scp l3mon-app:/home/<your-vm-user>/L3m0nCTF/backups/<the-file-name-it-printed>.tar.enc.sha256 ./ --zone asia-south1-a --tunnel-through-iap
sha256sum -c <the-file-name-it-printed>.tar.enc.sha256
```

**Check:** `OK`. Keep the file (and a second copy on another disk) where only you can open it: it holds every player's email and the platform's secrets. Do not put it in the repository or in a chat.

## 2. Stop the old machine (only after Part 1 is checked)

```bash
cd ~/L3m0nCTF && set -a; . /etc/l3mon/production.env; set +a; tools/compose.sh down
gcloud compute instances stop l3mon-app --zone asia-south1-a
```

`down` keeps the data on the disk; a stopped VM costs only its disk. The VM has deletion protection on: it cannot be deleted by mistake.

## 3. The new Google project (the project owner logs in once; every command is asked about first)

Keep the old account separate with its own gcloud configuration:

```bash
gcloud config configurations create l3mon-new
gcloud auth login            # opens the browser: sign in with the NEW account
```

Then follow the hosting guide's Part 2 **with the same names and sizes** (`go-live-runbook.md`: 2.3 project and billing and services, 2.4 budget and CPU quota, 2.5 network and firewall, 2.6 service account, fixed address and VM). Only these differ: the project id (Google keeps old ids, so it will not be the same: use a new one and write it in `go-live-progress.md`), and **the fixed address, which is new: write it down**. Keep: region `asia-south1`, zone `asia-south1-a`, network `l3mon-net`, VM `l3mon-app`, address `l3mon-app-ip`, `e2-standard-4`, Debian 12, 100 GB balanced, OS Login on, shielded VM, no scopes, deletion protection, SSH only through IAP, port 443 only from Cloudflare's list.

If Google refuses the VM for quota, ask for the quota in 2.4 first (a new account often starts low).

## 4. Build the new machine (on the new VM)

1. Prepare the machine as in 2.8 of the hosting guide (updates, time, Docker).
2. Check out **the same release** and bring the two scripts across, as in Part 1 (`gcloud compute scp` to the new VM, which is in the new project):

   ```bash
   git clone https://github.com/sivaahari/L3m0nCTF.git && cd L3m0nCTF && git fetch --tags && git checkout release-2026-10-22
   ```

3. Bring the saved file across (from your computer, with the new account active), then put everything back. `place` checks the file's checksums, refuses a different commit, and refuses to overwrite anything already there (add `--force` only when you mean it):

   ```bash
   gcloud compute scp <the-file-name>.tar.enc l3mon-app:/home/<new-vm-user>/ --zone asia-south1-a --tunnel-through-iap
   tools/import-state.sh place ~/<the-file-name>.tar.enc
   ```

4. The same steps as the hosting guide's 5.6 and 5.7 (the script prints them, with the right image name):

   ```bash
   python3 tools/update_cloudflare_ips.py
   docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:release-2026-10-22 . && tools/verify-image.sh l3mon/ctfd:release-2026-10-22
   set -a; . /etc/l3mon/production.env; set +a
   (cd tools && python3 -m l3mon deploy check)     # must end with 0 blocked
   tools/compose.sh up -d --wait
   ```

5. Put the data back (the platform is up and empty at this point):

   ```bash
   tools/import-state.sh restore ~/<the-file-name>.tar.enc
   ```

   It replaces the database and the uploaded files from the backup inside (with the checksums checked first), refills the story volume, and prints the numbers that must match the old machine's: users, studios, challenges, uploaded files.

6. **Check on the machine itself**, before anybody is sent here:

   ```bash
   tools/compose.sh ps                                  # ctfd, db, cache, nginx: healthy
   tools/compose.sh exec -T ctfd python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/readyz').status)"    # 200
   ```

## 5. Switch the visitors over (the project owner, in Cloudflare)

Cloudflare, zone `l3m0nctf.xyz`, **DNS**: change the content of the two A records `play` and `crew` from the old address to the **new** address. Leave them **Proxied (orange cloud)**. Change nothing else: the origin certificate, Authenticated Origin Pulls, the cache rules and Access are by host name and keep working because the new machine has the same files.

Then repeat the outside checks of the 22 October entry in `verification-log.md` (from a laptop): `GET /healthcheck` 200, `GET /readyz` 200, `GET /login` 200 with our title and the secure cookie, `GET /no-such-page` our 404, `GET /ctftime/standings.json` 200, `GET /admin` from an address not on the list 403, a visit to the new VM's own address bypassing Cloudflare gets no answer. Then sign in as an administrator and look at the users and the studios.

## 6. Afterwards

- **CTFtime:** if the registration gave CTFtime the server's outgoing address (it may ask for it), send them the new one. The feed and callback addresses are host names and do not change.
- Write the new project id and address into `go-live-progress.md` and `verification-log.md` (a new entry: what moved, when, the checks).
- The old project: once the billing account is settled, delete the old VM and its address (`l3mon-app` has deletion protection: switch it off first) or leave them; nothing in the repository points at them.
- Keep the saved file until the event is over.

## If the old machine is already gone and nothing was saved

The platform can still be rebuilt from the repository: follow the hosting guide from Part 2 with new secrets (`python3 -m l3mon secrets generate`), issue a new Cloudflare origin certificate, and start empty. What is lost: every account and studio made so far, uploaded files, the story volume (rebuild it from the private story repository with `python -m l3mon story build` and copy it in), and the administrators' settings. Everybody registers again. Nothing else changes.

## If the new machine does not come up

The old VM is only stopped, not deleted: start it (`gcloud compute instances start l3mon-app ...`, with the old account), start the platform (`tools/compose.sh up -d --wait`), and set the two DNS records back to the old address. Do not do this if the old project's billing is suspended: Google may not let it start.

## How this was proved

The two scripts have their own tests (`tools/tests/test_state_move.py`: what is saved, byte-identical secrets, nothing printed, a damaged or foreign file refused, a different commit refused, nothing overwritten without `--force`, the passphrase, the story and the database restore called as they must be; each promise broken on purpose and caught). The whole move was also rehearsed with Docker on the development computer at the live release: an old platform with users, a studio and its members, a page, an uploaded file and a story folder was saved with `--encrypt`, destroyed (containers, volumes and the folder), and a new machine made from a clean clone got it all back from the one file: the same users, studio, page, uploaded file (same checksum), story files (same checksums) and all 16 secrets (same fingerprints). Not rehearsed, because it needs the real accounts: the Google project creation and the Cloudflare switch.
