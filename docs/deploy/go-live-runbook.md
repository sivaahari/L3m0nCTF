<!-- The copy of the owner's desktop guide, with their own account name replaced. Keep it free of secrets and personal addresses. -->
# Putting L3m0nCTF on the internet: the step-by-step guide (Google Cloud, Cloudflare, the platform)

Written 18 October 2026 for the project owner. Plain words, every command in a box, a **Check** after every step.

**Nothing secret is written in this file and nothing secret may be added to it.** Passwords, keys and the CTFtime secret go in your password manager (the "vault"), never in chat, never in a screenshot, never in the repository.

---

## 0. Read this first (five minutes)

### 0.1 What is being built, and what "done for today" means

```
 Players ──► CLOUDFLARE (free) ──► one Google Cloud VM in Mumbai ──► nginx ─► CTFd ─► MariaDB, Redis
              certificate, cache,    Debian 12, Docker, 4 CPUs        only Cloudflare may connect
              firewall rules         fixed IP address
```

| Milestone | What it means | Needs the VM? |
|---|---|---|
| **A. Registered on CTFtime** | The form is filed (file `01-CTFTIME-REGISTRATION-SHEET.md`). **Do this first, before noon.** | **No** |
| **B. The two CTFtime addresses online** | `https://play.l3m0nctf.xyz/ctftime/standings.json` answers, and `https://play.l3m0nctf.xyz/auth/ctftime/callback` exists (it stays hidden until CTFtime gives its secret). This guide, Parts 2 to 6. | Yes |
| **C. Login with CTFtime switched on** | Needs CTFtime's approval first. Part 7. | Yes |
| **D. Extras (anti-abuse, mail, monitoring)** | Part 8, later in the week. | Yes |

CTFtime does **not** need B before it approves; it needs B before it can *use* the addresses. Parts 2 to 6 take about **2.5 to 3 hours** the first time, most of it waiting for installs.

### 0.2 What I proved on this computer, and what I could not

I merged and tested your teammates' work with the main platform on this laptop (the report is `03-AUDIT-REPORT.md`), and I rehearsed the production set-up locally earlier (a whole production-mode stack with throwaway certificates). **I cannot sign in to your Google Cloud or Cloudflare accounts, and the Google command-line tool is not installed on this laptop**, so the Google and Cloudflare steps below come from their own documentation and from the existing guide (`docs/deploy/platform-deployment.md`); I have not run them. Dashboards move their menus: if a button is not where this guide says, look for the same words nearby. If a command fails, copy the **exact error text** (not any secret) and send it to me or to the AI agent in `04-HANDOVER-PROMPT-FOR-AI-AGENT.md`.

### 0.3 What you need open before you start

| # | Thing | Why |
|---|---|---|
| 1 | A browser signed in to the Google account **YOUR_GOOGLE_ACCOUNT@gmail.com** | Google Cloud |
| 2 | The login of whoever controls **Cloudflare** for `l3m0nctf.xyz` | DNS, certificates, rules. **Ask now who has it** (your earlier notes say the domain was moved to Cloudflare by a superior) |
| 3 | A card for the Google Cloud free trial (if the account has no billing yet) | The VM costs about $190 to $250 for the whole event; the $300 trial credit covers it (cost note: `docs/research/gcp-cost-estimate.md`) |
| 4 | A password manager with a shared vault | Every secret below |
| 5 | The **public IP addresses of the computers the crew will use for /admin** | nginx refuses the admin pages from every other address |

To find an address, on the crew member's computer run these two (one of them may print nothing; that is fine):

```bash
curl -4 -s https://api.ipify.org
curl -6 -s https://api6.ipify.org
```

On 18 October this laptop printed both an IPv4 and an IPv6 address. Mobile and home connections change addresses; Part 6.4 shows how to add or fix an address later in one minute.

### 0.4 Decisions I made so you do not have to wait (change them only for a reason)

| Decision | My choice | Why |
|---|---|---|
| Region | `asia-south1` (Mumbai), zone `asia-south1-a` | Nearest to the players |
| Machine | `e2-standard-4` (4 CPUs, 16 GB) to start; `e2-standard-8` for the load test and the event | I measured about 2.8 CPU cores of CTFd for 300 players at once. Four is right for testing and early registration; resize before the event (Part 9) |
| Disk | 100 GB balanced | Logs are rotated; room for backups |
| What runs on day one | The platform only (CTFd, MariaDB, Redis, nginx) | Fewest moving parts for the CTFtime addresses. The anti-abuse, monitoring and mail pieces are added in Part 8 |
| What does **not** go live yet | The per-team instancer | It is not finished (its router and several limits are still prototype-grade, see the audit). It stays off |
| Admin access | Only from listed addresses **and** Cloudflare Access (second factor) | A stolen admin password alone is not enough |

### 0.5 Names used below (keep them exactly, the later steps reuse them)

| Name | Value |
|---|---|
| Project id | `l3monctf-2026` (add digits if Google says it is taken) |
| Network | `l3mon-net` |
| VM | `l3mon-app` |
| Fixed address | `l3mon-app-ip` |
| Platform host | `play.l3m0nctf.xyz` |
| Admin host | `crew.l3m0nctf.xyz` |
| Release to deploy | the tag `release-2026-10-18` (a snapshot of the tested code) |

---

## 1. Register the event on CTFtime (do this before anything else)

Open `01-CTFTIME-REGISTRATION-SHEET.md` and follow it. It takes 15 to 30 minutes and needs no server.

---

## 2. Google Cloud (about 60 minutes)

### 2.1 Sign in, and make sure billing exists

1. Open <https://console.cloud.google.com> and make sure the top-right avatar shows **YOUR_GOOGLE_ACCOUNT@gmail.com**.
2. Open <https://console.cloud.google.com/billing>.
   - If you see a **billing account** (it says "Free trial" or shows a name), note its name and go to 2.2.
   - If you see nothing, or a banner to start a free trial: choose **Start free trial / Activate**, choose country **India**, and enter the card. **The $300 credit lasts 90 days from now**, which covers 28 November.
3. **Upgrade the trial account** to a paid account (billing page, banner **"Activate full account"** or **Upgrade**) so the machines never stop in the middle of the round. The unused credit is spent first. *This is a money decision (D10): whoever owns the card agrees to it.*

**Check:** the billing page lists one billing account, status active.

### 2.2 Open Cloud Shell (a terminal inside your browser)

Cloud Shell is a free Linux terminal that is already signed in as you and already has the `gcloud` tool, `git`, `python3` and `openssl`. It is the easiest way, because nothing has to be installed on your laptop.

1. Open <https://shell.cloud.google.com> (or click the `>_` icon at the top right of the console). Accept the prompts.
2. Type each block below into it. Cloud Shell forgets its settings when it restarts, so **if you come back later, paste the first block again.**

```bash
export PROJECT_ID=l3monctf-2026
export REGION=asia-south1
export ZONE=asia-south1-a
```

### 2.3 Create the project, link billing, switch on the services

```bash
gcloud projects create $PROJECT_ID --name="L3m0nCTF 2026"
```

If it says the id **already exists**, choose another (`export PROJECT_ID=l3monctf-2026-7`, and use it everywhere after), then repeat.

```bash
gcloud config set project $PROJECT_ID
gcloud billing accounts list
```

The second command prints a line like `0X0X0X-0X0X0X-0X0X0X  My Billing Account  True`. Copy the first column (the id) and use it here:

```bash
gcloud billing projects link $PROJECT_ID --billing-account=PASTE_THE_ID_HERE
gcloud services enable compute.googleapis.com iap.googleapis.com storage.googleapis.com logging.googleapis.com monitoring.googleapis.com
```

(The last command takes up to two minutes.)

**Check:** `gcloud config get-value project` prints your project id, and `gcloud billing projects describe $PROJECT_ID --format="value(billingEnabled)"` prints `True`.

### 2.4 A budget alert, and the CPU quota (in the console)

1. <https://console.cloud.google.com/billing> then **Budgets & alerts** then **Create budget**. Name `l3monctf`, amount **250** (US dollars; Google bills in dollars or rupees depending on the account, use the amount in your currency that is about $250), alerts at **50%, 80% and 100%**, send to **two people**. A budget alert does not stop spending; someone has to read it.
2. <https://console.cloud.google.com/iam-admin/quotas> then filter: `CPUs` and region `asia-south1`. The limit must be **at least 8**. A brand-new account can start lower; if it is lower, press **Edit quotas** and ask for 8 now (upgrading the account in 2.1 often raises it by itself).

### 2.5 The private network and the firewall

The default network comes with open doors. This one starts closed and opens exactly two.

```bash
gcloud compute networks create l3mon-net --subnet-mode=custom
gcloud compute networks subnets create l3mon-asia-south1 --network=l3mon-net --region=$REGION --range=10.10.0.0/24
```

SSH only through Google's tunnel (IAP), never from the open internet:

```bash
gcloud compute firewall-rules create l3mon-allow-iap-ssh --network=l3mon-net --direction=INGRESS --action=ALLOW --rules=tcp:22 --source-ranges=35.235.240.0/20 --target-tags=l3mon-app
```

The web port, only from Cloudflare (this fetches Cloudflare's current list of 15 address ranges):

```bash
CF_V4=$(curl -s https://www.cloudflare.com/ips-v4/ | paste -sd, -)
echo "$CF_V4"
```

The second line must print a comma-separated list of about 15 ranges starting `173.245.48.0/20`. If it is empty, stop and retry. Then:

```bash
gcloud compute firewall-rules create l3mon-allow-cloudflare-https --network=l3mon-net --direction=INGRESS --action=ALLOW --rules=tcp:443 --source-ranges="$CF_V4" --target-tags=l3mon-app
```

**Check:** `gcloud compute firewall-rules list --filter="network:l3mon-net" --format="table(name,sourceRanges.list():label=FROM,allowed[].map().firewall_rule().list():label=ALLOW)"` shows exactly those two rules, and nothing that allows `0.0.0.0/0`.

### 2.6 A service account with no powers, a fixed address, and the VM

If someone breaks into the VM, this account gives them nothing.

```bash
gcloud iam service-accounts create l3mon-app-vm --display-name="L3m0nCTF app VM (no roles)"
```

Do **not** give it any role. Now the fixed address:

```bash
gcloud compute addresses create l3mon-app-ip --region=$REGION
gcloud compute addresses describe l3mon-app-ip --region=$REGION --format="value(address)"
```

The second command prints the address. **Write it down.** It is public by nature (it will appear in DNS) and is what CTFtime may ask for later. Now the VM:

```bash
gcloud compute instances create l3mon-app \
  --zone=$ZONE --machine-type=e2-standard-4 \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=100GB --boot-disk-type=pd-balanced \
  --network=l3mon-net --subnet=l3mon-asia-south1 --address=l3mon-app-ip \
  --tags=l3mon-app \
  --service-account=l3mon-app-vm@$PROJECT_ID.iam.gserviceaccount.com --no-scopes \
  --metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE \
  --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
  --deletion-protection
```

(If it complains about the quota, see 2.4. If it complains the zone is out of capacity, use `--zone=asia-south1-b` here and in every later command, and tell me.)

What the options do: Debian 12 is supported until 2028; `--no-scopes` plus the empty account keep Google's own APIs out of reach of the VM; OS Login ties SSH to your Google account; `block-project-ssh-keys` stops stray keys; Shielded VM protects the boot; `--deletion-protection` makes an accidental delete fail.

### 2.7 Let yourself in, and connect

Give your account the two permissions that SSH through the tunnel needs (the project's creator already has most of this, but being explicit costs nothing):

```bash
gcloud projects add-iam-policy-binding $PROJECT_ID --member="user:YOUR_GOOGLE_ACCOUNT@gmail.com" --role="roles/compute.osAdminLogin"
gcloud projects add-iam-policy-binding $PROJECT_ID --member="user:YOUR_GOOGLE_ACCOUNT@gmail.com" --role="roles/iap.tunnelResourceAccessor"
```

Connect (the first time it offers to create an SSH key: press Enter for the defaults, empty passphrase):

```bash
gcloud compute ssh l3mon-app --zone=$ZONE --tunnel-through-iap
```

**Check:** you see a prompt like `l3m0nctf_gmail_com@l3mon-app:~$`. Everything in Part 2.8 and Part 5 runs **on the VM** (this prompt). To leave it, type `exit`. A direct `ssh` to the public address from your laptop must fail (the firewall blocks it); that is correct.

### 2.8 Prepare the VM (about 15 minutes, on the VM)

```bash
sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get -y install unattended-upgrades git python3 ca-certificates curl
```

The clock matters (flag times depend on it). Google's Debian images already use Google's time server; just check:

```bash
timedatectl | grep -i "synchronized"
chronyc tracking | head -3
```

`System clock synchronized: yes` is what you want. Now install Docker from Docker's own repository (the official steps; they are at <https://docs.docker.com/engine/install/debian/> if a command changes):

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Log out and in again so the group takes effect:

```bash
exit
```

then reconnect with the `gcloud compute ssh ...` command of 2.7 (paste the three `export` lines of 2.2 first if Cloud Shell restarted), and check:

```bash
docker compose version
docker run --rm hello-world
df -h /
free -h
python3 --version
```

**Check:** `docker compose version` prints v2 or newer, `hello-world` prints "Hello from Docker!", `df` shows about 100 GB, Python is 3.11 or newer.

*Being in the `docker` group is the same as being the administrator of this machine. Only add crew members who are already trusted with it.*

---

## 3. Cloudflare (about 45 minutes)

Do these in the Cloudflare dashboard for the zone **l3m0nctf.xyz**. **The order matters**: nginx on the VM accepts **only** requests that carry *our* client certificate, so Authenticated Origin Pulls must be on *before* any visitor arrives.

### 3.1 The origin certificate (secures the hop from Cloudflare to the VM)

1. Dashboard, zone `l3m0nctf.xyz`, **SSL/TLS**, **Origin Server**, **Create Certificate**.
2. Choose **Generate private key and CSR with Cloudflare**, key type **RSA (2048)**. Hostnames: `play.l3m0nctf.xyz` and `crew.l3m0nctf.xyz` (remove any `*` entry). Validity 15 years.
3. **Create.** The page shows the certificate and the private key **once**. Copy each into the vault under the names `origin.pem` and `origin.key`. Keep the page open until Part 5.3, where you paste them onto the VM. (If you close it without saving the key, delete the certificate and make a new one.)

### 3.2 Our own client-certificate authority (proves a request came through *our* Cloudflare zone)

Without this, any Cloudflare customer could aim traffic at your VM through Cloudflare. Run this in **Cloud Shell** (not on the VM), in an empty folder. It asks for a passphrase once: choose a strong one and put it in the vault.

```bash
mkdir -p ~/aop && cd ~/aop
openssl genrsa -aes256 -out rootca.key 4096
openssl req -x509 -new -key rootca.key -sha256 -days 1826 -subj "/CN=l3m0nctf.xyz origin pulls" -out rootca.crt
openssl req -new -nodes -newkey rsa:4096 -keyout cert.key -out cert.csr -subj "/CN=cloudflare-origin-pull.l3m0nctf.xyz"
printf 'basicConstraints=CA:FALSE\n' > cert.v3.ext
openssl x509 -req -in cert.csr -CA rootca.crt -CAkey rootca.key -CAcreateserial -out cert.crt -days 400 -sha256 -extfile cert.v3.ext
openssl verify -purpose sslclient -CAfile rootca.crt cert.crt
```

**Check:** the last line must print `cert.crt: OK`.

Now look at the three files you need and put each in the vault (select the text on screen and copy; do not paste it in any chat):

```bash
cat rootca.crt      # public: this one goes to the VM (Part 5.3) as cloudflare-origin-pull-ca.pem
cat cert.crt        # public: this one goes to Cloudflare (3.3)
cat cert.key        # SECRET: goes to Cloudflare (3.3) and the vault
```

Then wipe the secrets from Cloud Shell (keep `rootca.crt`, you will copy it to the VM in a moment):

```bash
shred -u rootca.key cert.key cert.csr
```

*The `rootca.key` passphrase and the `rootca.key` file are only needed again if you ever have to make a new client certificate (in 400 days). You deleted the file from Cloud Shell; keep it in the vault only if you want that option. If you did not save `rootca.key` in the vault before running `shred`, you can simply repeat this whole step later and upload the new `cert.crt` to Cloudflare and the new `rootca.crt` to the VM.*

### 3.3 Upload the client certificate to Cloudflare and switch Authenticated Origin Pulls ON

1. Dashboard, zone, **SSL/TLS**, **Origin Server**, section **Authenticated Origin Pulls**. In the **Zone-level** part choose **Upload certificate** (or "Use custom certificates").
2. Paste `cert.crt` (the leaf) and `cert.key`. Save.
3. **Switch the zone-level toggle ON.**

### 3.4 The strict mode

1. **SSL/TLS**, **Overview**: set the mode to **Full (strict)**.
2. **SSL/TLS**, **Edge Certificates**: switch **Always Use HTTPS** on; **Minimum TLS Version** 1.2. Leave Cloudflare's HSTS off (nginx adds its own).

### 3.5 The DNS records

**DNS**, **Records**, **Add record** (do both):

| Type | Name | Content | Proxy status |
|---|---|---|---|
| A | `play` | the fixed address from 2.6 | **Proxied (orange cloud)** |
| A | `crew` | the same address | **Proxied (orange cloud)** |

Until the VM serves something, `https://play.l3m0nctf.xyz` shows a Cloudflare **521** error. That is expected and harmless.

### 3.6 The cache rule (so Cloudflare never stores anyone's session)

**Caching**, **Cache Rules**, **Create rule**, two rules:

| # | If the request matches (custom filter expression) | Then |
|---|---|---|
| 1 | `not starts_with(http.request.uri.path, "/themes/")` | **Bypass cache** |
| 2 | `starts_with(http.request.uri.path, "/themes/")` | **Eligible for cache**; edge TTL: use the origin's cache-control, bypass if none |

### 3.7 Firewall and rate-limit rules (free plan: 5 custom rules, 1 rate-limit rule)

1. **Security**, **Security rules**, **Create rule** (menu "WAF" in some versions). Rule one: if `http.host eq "play.l3m0nctf.xyz" and starts_with(http.request.uri.path, "/admin")` then **Block**. *(Admin lives on the other host name only. Skip this rule if you skip Access in 3.8: then admin must be reached on the `play` host.)*
2. **Rate limiting rule** (one): if `http.host eq "play.l3m0nctf.xyz" and http.request.method eq "POST" and http.request.uri.path in {"/login" "/register"}` then block after about 20 requests per minute (or the nearest choice the free plan offers) from one address. nginx limits these paths too. A hundred players behind one campus address must not block each other, so do **not** make it stricter.
3. Leave **Bot Fight Mode** off until the rehearsal has shown it does not block campus players.
4. Know where **Under Attack Mode** is (Security, Settings): it is the emergency switch for a flood.

### 3.8 Cloudflare Access on the admin host (the second factor)

1. In the dashboard open **Zero Trust**. First time: choose a team name and the **Free** plan (it may ask for a card; it does not charge for under 50 users).
2. **Settings / Authentication**: add the login method **One-time PIN** (Cloudflare emails a 6-digit code).
3. **Access, Applications, Add an application, Self-hosted.** Domain `crew.l3m0nctf.xyz`, no path (so every page and every API call on that host is covered). Session 8 hours.
4. Policy: action **Allow**, rule **Emails**: the crew's addresses (two or three people). Nothing else. Save.

*Access only works because every request has to come through Cloudflare, which the firewall (2.5) and Authenticated Origin Pulls (3.3) guarantee. All three belong together.*

---

## 4. Put the code on the VM (about 10 minutes, on the VM)

```bash
cd ~
git clone https://github.com/sivaahari/L3m0nCTF.git
cd L3m0nCTF
git fetch --tags
git checkout release-2026-10-18
git log -1 --format='%h %s' | cut -c1-100
```

**Always deploy a tag, never a moving branch,** so you can say exactly what is running. If `git checkout release-2026-10-18` says the tag does not exist, ask me or the AI agent: it means the release was not tagged yet.

---

## 5. Set up the platform (about 30 minutes, on the VM)

### 5.1 Make the secrets

```bash
cd ~/L3m0nCTF/tools && python3 -m l3mon secrets generate --dir ../.secrets && cd ..
chmod 700 .secrets
rm -f .secrets/PRESET_ADMIN_TOKEN
ls .secrets
```

This writes new random secrets and prints only their names. **Production never uses `PRESET_ADMIN_TOKEN`** (it is a development shortcut), so it is deleted. (If you ever run `secrets generate --only-missing` later to add a new secret, it will make that file again: delete it again, because `deploy check` refuses to start while it exists.)

Now **save every one of them in the vault** (do not paste into chat). Show a file only on this screen, copy it to the vault, then clear the screen:

```bash
cat .secrets/PRESET_ADMIN_PASSWORD
```

Do the same for `.secrets/SECRET_KEY`, `.secrets/DATABASE_PASSWORD`, `.secrets/DATABASE_ROOT_PASSWORD`, `.secrets/REDIS_PASSWORD` and `.secrets/FLAG_HMAC_SECRET` (this last one matters most: losing it during the round would change every team's flag). Then `clear`. The files named `CTFTIME_CLIENT_SECRET`, `MAIL_PASSWORD`, `DISCORD_WEBHOOK_URL`, `TURNSTILE_SECRET` and `GOOGLE_CLIENT_SECRET` are **empty on purpose**: an empty one means "that feature is off".

### 5.2 The event settings

```bash
cp config/event.example.toml config/event.toml
grep -n "platform_host\|start\|end\|size_max\|verify_emails\|client_id" config/event.toml
```

The example already has the right values: `platform_host = "play.l3m0nctf.xyz"`, start `2026-11-28T04:30:00Z`, end `2026-11-28T16:30:00Z`, `size_max = 4`, `verify_emails = true`, `client_id = ""`. Then turn it into the settings the stack reads:

```bash
cd tools && python3 -m l3mon config render ../config/event.toml --out ../deploy/compose/generated && cd ..
```

### 5.3 The certificates

```bash
sudo install -d -m 0750 -o "$USER" -g 101 /etc/l3mon/tls
sudo tee /etc/l3mon/tls/origin.pem > /dev/null
```

The second command waits: **paste the origin certificate** from 3.1 (from `-----BEGIN CERTIFICATE-----` to `-----END CERTIFICATE-----`), press **Enter**, then **Ctrl-D**. Then the key:

```bash
sudo tee /etc/l3mon/tls/origin.key > /dev/null
```

Paste the **private key** from 3.1, Enter, Ctrl-D. Then the client-certificate authority (from Cloud Shell: `cat ~/aop/rootca.crt` there, copy, paste here):

```bash
sudo tee /etc/l3mon/tls/cloudflare-origin-pull-ca.pem > /dev/null
```

Paste `rootca.crt`, Enter, Ctrl-D. Then set who may read them. You (the deploy user) keep them so the computer's own check in 5.7 can read them, and the nginx container, which runs as group 101, may read them too; nobody else may:

```bash
sudo chown "$USER":101 /etc/l3mon/tls/*
sudo chmod 640 /etc/l3mon/tls/*
ls -l /etc/l3mon/tls
```

**Check:** three files, owner your user name, group `101`, mode `-rw-r-----`. The check in 5.7 verifies their content.

### 5.4 Who may open /admin

nginx shows the admin pages **only** to addresses listed in a file outside the repository. Make it with your crew's addresses from 0.3 (explicit addresses only: never a private range like `10.x`, `172.16.x` or `192.168.x`, which the checker refuses):

```bash
sudo tee /etc/l3mon/admin-nets.conf > /dev/null <<'EOF'
# the addresses the crew opens /admin from (one per line, ending in " 1;")
203.0.113.10 1;
EOF
```

**Replace `203.0.113.10` with your real address(es)**, one per line: an IPv4 as `157.1.2.3 1;` and an IPv6 network as `2409:40f4:2014:466c::/64 1;` (use the first four groups of your IPv6 address followed by `::/64`, because home and mobile IPv6 addresses change inside their /64). Use `sudo nano /etc/l3mon/admin-nets.conf` to edit it. Part 6.4 shows the easiest way to get an address right.

### 5.5 The shell settings file

```bash
sudo tee /etc/l3mon/production.env > /dev/null <<'EOF'
L3MON_ENV=production
L3MON_IMAGE_TAG=release-2026-10-18
TLS_DIR=/etc/l3mon/tls
ADMIN_NETS_FILE=/etc/l3mon/admin-nets.conf
EXTRA_HOST=crew.l3m0nctf.xyz
EOF
sudo chmod 644 /etc/l3mon/production.env
```

Nothing secret is in this file. (`L3MON_OVERLAYS` is added in Part 8; leave it out today.)

### 5.6 Refresh Cloudflare's address list and build the image

```bash
python3 tools/update_cloudflare_ips.py
ls -l deploy/nginx/snippets/cloudflare-realip.conf
docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:release-2026-10-18 .
tools/verify-image.sh l3mon/ctfd:release-2026-10-18
```

The first command creates the file nginx uses to trust Cloudflare's address ranges (it is not stored in the repository, and `deploy check` refuses to start without it). The build takes **3 to 6 minutes**. `verify-image.sh` takes about 30 seconds and **must end with `All 32 checks passed`**.

### 5.7 The computer's own check, then start

```bash
set -a; . /etc/l3mon/production.env; set +a
cd tools && python3 -m l3mon deploy check && cd ..
```

It must end with **`0 blocked`**. A `todo` line about mail is expected today (the mail service is Part 8). A `BLOCKED` line says exactly what is wrong (a missing file, a key that everybody can read, the example admin addresses...). Fix it and run it again. It never prints a secret.

Then start:

```bash
tools/compose.sh up -d --wait
tools/compose.sh ps
```

The first start creates the database and takes about **two minutes**. **Check:** every service is `healthy` (four of them: `ctfd`, `db`, `cache`, `nginx`).

---

## 6. Check it from the outside (15 minutes)

### 6.1 The simple checks (from your laptop, any terminal that has `curl`)

| What | Command | You should see |
|---|---|---|
| The platform answers | `curl -sI https://play.l3m0nctf.xyz/healthcheck` | `HTTP/2 200` |
| It speaks HTTPS with HSTS | `curl -sI https://play.l3m0nctf.xyz/login` | `200`, and a `strict-transport-security` line |
| **The CTFtime live feed** | `curl -s https://play.l3m0nctf.xyz/ctftime/standings.json` | `{"standings":[]}` (empty until the event starts) |
| The CTFtime sign-in is hidden for now | `curl -sI https://play.l3m0nctf.xyz/auth/ctftime` | `404` (until Part 7) |
| The admin pages are closed to strangers | `curl -sI https://play.l3m0nctf.xyz/admin` | `403` or Cloudflare's block page |
| The VM refuses a direct visit | `curl -sk --max-time 8 https://VM_ADDRESS/` (the address from 2.6) | no answer, a timeout or a refusal |
| No client certificate, no answer | `curl -sk --resolve play.l3m0nctf.xyz:443:VM_ADDRESS https://play.l3m0nctf.xyz/` | timeout (the firewall) |
| The cookie is the strict one | `curl -sI https://play.l3m0nctf.xyz/login` and look for `set-cookie` | `__Host-session=...; Secure; HttpOnly; SameSite=Lax` |

Also open `https://play.l3m0nctf.xyz` in a browser: you get the sign-in page with a valid certificate.

### 6.2 Cache and real addresses

Request `/login` twice: the answer must **not** carry `cf-cache-status: HIT`. A file under `/themes/` should become `HIT` on the second request. On the VM, `tools/compose.sh logs --tail 20 nginx` shows each request with the visitor's **real** address (yours), not a Cloudflare or Docker one.

### 6.3 First sign-in to the admin area

1. Open `https://crew.l3m0nctf.xyz/admin` in a **private window**. Cloudflare Access asks for an email; only an address on your list gets a code. Enter the code.
2. Sign in as user `organiser`; the password is in the vault (`PRESET_ADMIN_PASSWORD`). If you see **403** here, your address is not in the admin list yet: do 6.4.
3. Create **one named admin account per crew member** (two or three) with strong passwords, and keep `organiser` as the emergency account (its password only in the vault).

### 6.4 If /admin says 403: add your address in one minute

On the VM:

```bash
tools/compose.sh logs --tail 30 nginx | grep " /admin" | tail -3
```

The first number in the line is the address nginx saw. Add it to `/etc/l3mon/admin-nets.conf` (as `ADDRESS 1;`), then:

```bash
tools/compose.sh restart nginx
```

### 6.5 Save the result

Write the date, the release tag, and the outputs of 6.1 in `docs/deploy/verification-log.md` (or send them to me): that is the record that it worked.

---

## 7. After CTFtime approves the event (switching Login with CTFtime on)

CTFtime sends an approval email and opens the event's management page. Then:

1. On that page type the two addresses exactly (see `01-CTFTIME-REGISTRATION-SHEET.md`, Step 4), and copy the **client ID** (the event number) and the **client secret**.
2. On the VM, put the number in `config/event.toml`:

   ```bash
   cd ~/L3m0nCTF && nano config/event.toml
   ```

   Change `client_id = ""` to `client_id = "THE_NUMBER"` (keep the quotes). Save (Ctrl-O, Enter, Ctrl-X). Render again:

   ```bash
   cd tools && python3 -m l3mon config render ../config/event.toml --out ../deploy/compose/generated && cd ..
   ```
3. Put the **secret** into its file (paste it, Enter, Ctrl-D; it must be the secret and nothing else):

   ```bash
   tee .secrets/CTFTIME_CLIENT_SECRET > /dev/null
   chmod 644 .secrets/CTFTIME_CLIENT_SECRET
   ```
4. Recreate the platform container so it reads them:

   ```bash
   set -a; . /etc/l3mon/production.env; set +a
   tools/compose.sh up -d --wait --force-recreate --no-deps ctfd
   ```
5. Check from outside: `curl -sI https://play.l3m0nctf.xyz/auth/ctftime` now answers **302** with a `location` pointing at `oauth.ctftime.org`. Try the sign-in with a real CTFtime account.
6. If CTFtime's token request is refused (403), send CTFtime the VM's **outgoing** IP address (the fixed address from 2.6 is also the outgoing one for this VM).

---

## 8. Later this week: the extra pieces (each is optional and independent)

Add them one at a time. Each is switched on by adding its name to `L3MON_OVERLAYS` in `/etc/l3mon/production.env` and then running `tools/compose.sh up -d --wait`. If one misbehaves, take its name out again and run the same command: the platform keeps working without it.

### 8.1 Anti-abuse and "Continue with Google": the overlay `identity`

- The abuse rules (disposable email addresses, look-alike team names, a limit on flag guesses per team, an evidence page for the crew at `/admin/l3mon/guard`) work as soon as the overlay is on. They need **no keys**.
- **CAPTCHA**: create a Turnstile widget in Cloudflare (Turnstile, Add widget, domain `play.l3m0nctf.xyz`), put the **site key** in `/etc/l3mon/production.env` as `TURNSTILE_SITEKEY=...` and the **secret key** in `.secrets/TURNSTILE_SECRET`. Empty means off.
- **Google sign-in**: create the OAuth client (`docs/deploy/identity.md`, Google part: redirect address `https://play.l3m0nctf.xyz/auth/google/callback`, publishing status **In production**), put the client id in `production.env` as `GOOGLE_CLIENT_ID=...` and the secret in `.secrets/GOOGLE_CLIENT_SECRET`.
- The campus networks: `L3MON_GUARD_CAMPUS_NETS=a.b.c.d/n,...` in `production.env`, so address-based evidence ignores the campus.

### 8.2 Mail and monitoring: the overlay `ops`

- **Mail** is what lets players finish registering (every account is verified by email). Choose a service (`docs/deploy/platform-deployment.md`, A3: Brevo or Amazon SES) and add its DNS records in Cloudflare (grey cloud). **With the `ops` overlay, CTFd sends to a queueing relay on the internal network and the relay sends to the provider**, so registration never waits for the provider and a provider outage only delays mail. You therefore configure the **relay**, not CTFd: put these lines in `/etc/l3mon/production.env` (no secret among them) and the provider's password in `.secrets/MAIL_PASSWORD`:

  ```
  UPSTREAM_SMTP_HOST=smtp.the-provider.example
  UPSTREAM_SMTP_PORT=587
  UPSTREAM_SMTP_USER=the-login-name
  UPSTREAM_SMTP_TLS=true
  MAILFROM_ADDR=no-reply@l3m0nctf.xyz
  RATE_LIMIT_PER_MINUTE=30
  RATE_LIMIT_PER_DAY=300
  ```

  **Set `RATE_LIMIT_PER_DAY` to what the provider allows** (the relay's own default is 500, which would slow registration down on the first day: a 250-team event can need about 1,000 verification mails; a free plan allows 300 a day, so for the first day buy the provider's paid month). Messages over the limit wait in the queue and go out later; `.../metrics` on the relay shows the queue length. The `[mail]` section of `event.toml` is only for running **without** the `ops` overlay.
- **Monitoring**: Prometheus, Grafana, Loki and alerts. Grafana is reachable only through a tunnel from your laptop: `gcloud compute ssh l3mon-app --zone=$ZONE --tunnel-through-iap -- -L 3000:localhost:3000`, then open <http://localhost:3000> (admin password in `.secrets/GRAFANA_ADMIN_PASSWORD`). Alerts go to Discord when `.secrets/DISCORD_WEBHOOK_URL` holds a webhook address; empty means they stay in the alert manager.
- Details, runbooks and the backup scripts: `docs/deploy/operations.md` and `docs/runbooks/`.

### 8.3 What stays OFF for now

**The per-team instancer** (overlay `instancer`). Its first version runs in a development mode and its web router is still a prototype. Do not put it on the VM. The challenge hosts it needs are a separate step in November.

---

## 9. Money: what costs what, and how to save it

| Item | Cost | Save by |
|---|---|---|
| The VM `e2-standard-4` | about 15 US cents an hour | **Stop it whenever you are not testing**: `gcloud compute instances stop l3mon-app --zone=$ZONE` (start: `... start ...`). A stopped VM costs only its disk |
| Resize for the load test and the event | `e2-standard-8` about double | `gcloud compute instances stop l3mon-app --zone=$ZONE`, then `gcloud compute instances set-machine-type l3mon-app --zone=$ZONE --machine-type=e2-standard-8`, then start. Resize back after the event |
| The fixed address | a small hourly fee when **not attached** to a running VM | Delete it after the event (`gcloud compute addresses delete l3mon-app-ip --region=$REGION`) |
| Disk, outgoing traffic | about $35 for the whole plan | nothing to do |

After the event: take the final backup (`tools/backup.sh`), copy the database backup and logs off the VM, stop the VM, later delete it and the address.

**Before each change on the VM, take a backup:** `cd ~/L3m0nCTF && tools/backup.sh` (it writes `backups/<time>/`; treat it like a password file).

---

## 10. If something goes wrong

| What you see | Usual cause | What to do |
|---|---|---|
| Cloudflare **521** | The VM is off, nginx is down, or the firewall blocks Cloudflare | Start the VM; `tools/compose.sh ps`; compare the firewall rule with Cloudflare's list (2.5) |
| Cloudflare **522 / 523** | Firewall or wrong address | Compare the `A` record with the address from 2.6 |
| Cloudflare **525 / 526** | The origin certificate is missing, wrong, or does not cover `play`/`crew` | Check `/etc/l3mon/tls/` and the host names in 3.1; set the mode to Full (not strict) while you fix it |
| **400 No required SSL certificate was sent** for real visitors | Authenticated Origin Pulls is off, or its certificate is not the one that matches `cloudflare-origin-pull-ca.pem` | Turn the zone-level toggle on (3.3); check that `rootca.crt` on the VM is the authority that signed the `cert.crt` you uploaded |
| `/admin` shows 403 | Your address is not in the list | 6.4 |
| `deploy check` says BLOCKED | See its own words | It names the file or setting |
| `docker build` fails on a download | A temporary network problem | Run it again |
| A container is `unhealthy` | A setting or secret problem | `tools/compose.sh logs --tail 100 ctfd` (secrets are never logged) |
| `gcloud compute ssh` fails | Missing permission or the tunnel rule | 2.7, and the rule `l3mon-allow-iap-ssh` in 2.5 |
| The clock is wrong | The time service stopped | `chronyc tracking`; `sudo systemctl restart chrony` |
| The Cloud Shell forgot your settings | It restarted | Paste the three `export` lines of 2.2 again |

**If you are stuck for more than 15 minutes:** stop, copy the exact text of the error and what you ran, and give them to the AI agent with the prompt in `04-HANDOVER-PROMPT-FOR-AI-AGENT.md`. Do not paste any secret.

---

## 11. The list to tick (one page)

- [ ] CTFtime form filed (time: ______), confirmation email kept
- [ ] 2.1 Billing active, upgraded to a paid account, owner of the card agrees (D10)
- [ ] 2.4 Budget alert to two people; CPU quota at least 8
- [ ] 2.5 Two firewall rules and nothing else open
- [ ] 2.6 VM created; fixed address written down: ______________
- [ ] 2.7, 2.8 SSH works through the tunnel; Docker installed
- [ ] 3.1 Origin certificate saved in the vault
- [ ] 3.2, 3.3 Client certificate made, uploaded, Authenticated Origin Pulls **ON**
- [ ] 3.4 Full (strict) and Always HTTPS
- [ ] 3.5 `play` and `crew` records, proxied
- [ ] 3.6 to 3.8 Cache rules, firewall rules, Access
- [ ] 4 Code at `release-2026-10-18`
- [ ] 5.1 Secrets in the vault (especially `FLAG_HMAC_SECRET`)
- [ ] 5.3, 5.4 Certificates and admin addresses in place
- [ ] 5.6 Image built; **32 checks passed**
- [ ] 5.7 `deploy check` shows **0 blocked**; all four services healthy
- [ ] 6.1 Every check in the table gave the right answer; the feed answers
- [ ] 6.3 Named admin accounts created
- [ ] **CTFtime approved, Part 7 done, the sign-in redirects to CTFtime**
- [ ] Part 8 pieces switched on and tried, one at a time
