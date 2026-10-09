# Putting the platform on `play.l3m0nctf.xyz`: the set-up guide

Written 2026-10-09 for the owner. Plain words. No secrets and no event material are in this file, and none should ever be added.

The landing page (`https://l3m0nctf.xyz`) is already live and has its own guide ([landing-hosting.md](landing-hosting.md)). This guide is for the **platform**: the sign-in, the Channels page, the Guide, the Scoreboard and the crew's pages, which will run at `https://play.l3m0nctf.xyz`.

## 0. Read this first

**What is being deployed.** CTFd 3.8.8 with our plugins and theme, inside Docker containers, on one Google Cloud virtual machine (VM), behind Cloudflare. The participant pages you have been reviewing in `private/platform-ui` are a demo with invented data; they become the real pages when sub-projects SP3 (the plugins) and SP6 (the theme) are finished. Nothing in this guide puts the demo on the internet.

**Three kinds of step.** Each step below carries one of these tags, so you can start the ones that do not wait for the build.

| Tag | Meaning |
|---|---|
| **[Now]** | Needs nothing from the repository. Free or almost free. Start today. |
| **[VM]** | Needs the Google Cloud account. The commands do not depend on anything still to be built. Start when the money decision (section 2) is made. |
| **[After SP9]** | Needs files that do not exist yet (the production settings for the stack). The steps are written so you can read them and prepare, but the exact commands are confirmed when SP9 delivers. |

**What I tested and what I could not.** I can run the stack and Docker on my side. I cannot sign in to your Google Cloud, Cloudflare or email accounts, so those steps come from the vendors' own documentation, read on 2026-10-09, and are **not tried by me**. Dashboards change their menus: if a button is not where the guide says, look for the same words nearby, and tell me what you see. The table at the end of this file lists exactly what I proved.

**Never send me** a password, an API key, a private key, a client secret, a recovery code or a card number. Send names, addresses that are public anyway (the VM's IP address, the project ID, the bucket name), screenshots with the secrets covered, and error messages.

## 1. The picture

```
 Players ──https──►  CLOUDFLARE (free plan)                       GOOGLE CLOUD, Mumbai
                       - certificate for visitors                ┌───────────────────────────────────┐
                       - cache only the theme's files            │ one VM  (Debian 12, Docker)       │
                       - firewall rules, rate limit              │                                   │
                       - Authenticated Origin Pulls ──https────► │  nginx :443 ─► CTFd ─► MariaDB     │
                                                                 │                   └──────► Redis   │
 Crew ─────https───►  CLOUDFLARE ACCESS (email code, 2nd factor) │                                   │
        (admin host)          └──────────────────────────────────► (the same nginx, admin host name)   │
                                                                 └───────────────┬───────────────────┘
 CTFd ──mail──► email service (SPF, DKIM, DMARC set in Cloudflare DNS)           │ encrypted backups
 CTFtime ◄── feed from /ctftime/                                                 ▼ (a bucket the VM can write but not read)
```

Why each part is there:

- **Cloudflare** hides the VM's address from casual attackers, absorbs floods, and serves visitors' HTTPS. It is free.
- **The VM firewall** lets only Cloudflare reach the web port, and only Google's tunnel reach the admin (SSH) port. Nobody else can even knock.
- **Authenticated Origin Pulls** make nginx refuse any request that does not carry a certificate that *our* Cloudflare account presents. Without it, any Cloudflare customer could aim traffic at our VM through Cloudflare.
- **Cloudflare Access** puts a second factor (a code sent to the crew member's email) in front of the admin pages, so a stolen admin password or cookie is not enough.
- **The backup bucket** can be written to by the VM but not read from it, so a thief of the VM cannot read old backups.

## 2. Decisions needed before money moves

You cannot approve technical choices; leadership does. These are the questions to put to them, with my recommendation each time. Defaults apply if there is no answer.

| # | Question | Recommendation | Why |
|---|---|---|---|
| 1 | Who owns the Google Cloud bill, and may the account be upgraded to a paid account? (D10) | Yes, upgrade, with one named owner | The $300 credit lasts 90 days from sign-up, then the account closes. An upgraded account spends the unused credit first and nothing stops mid-round |
| 2 | Region | Mumbai (`asia-south1`) | Closest to most players. About 20% dearer than Iowa; included in the estimate |
| 3 | Email service for verification and password-reset mail | See the table in step A3. I suggest **Brevo, paid Starter for one month**, or **Amazon SES** | The free plans cap how many emails go out per day. On the day registration opens, hundreds of people need their email at once |
| 4 | From which networks does the crew reach the admin pages? | A short list of fixed addresses (campus), *plus* Cloudflare Access | The checklist needs explicit addresses and no private ranges (see `production-checklist.md`, section 2) |
| 5 | Who is on call during the 24 hours, and who may start and stop the VM? | Two named people with the Google permissions in step B6 | Someone must be able to reach it at 03:00 |
| 6 | Where does the crew's admin page live? | `crew.l3m0nctf.xyz` (the name is only a proposal) | A separate name lets Cloudflare Access cover the whole admin surface, including its API |

## 3. Money and the 90-day clock

From [the cost estimate](../research/gcp-cost-estimate.md): about **$190 to $250** for the whole plan if machines are switched off when not needed. The clock of 90 days starts when the Google Cloud trial account is created, so **do the free steps (Part A) first and create the trial account only when a VM is needed** (about late October for a staging rehearsal). That keeps the credit covering 28 to 29 November and the weeks before it.

| Machine | When it runs | Rough cost (Mumbai, with margin) |
|---|---|---|
| App host (`e2-standard-4` while testing, `e2-standard-8` for the load test and the round) | Staging days, then from registration opening to the day after the round | about $108 for 14 days at the big size |
| Challenge hosts | Later (SP4); they have their own guide | about $92 for two |
| Disks, the fixed address, outgoing traffic | Always | about $35 |

Stopping a VM stops its hourly charge; the disk costs a little. **Stop the VM whenever you are not testing.**

---

# Part A. Things you can do today **[Now]**

## A1. Secure the accounts (30 minutes)

1. Turn on **two-step verification** on every account that touches the event: Cloudflare, Hostinger, GitHub, Google, the email service, and the password manager.
2. Make **one shared vault** in a password manager that two or three crew members can open (Bitwarden or similar; leadership picks). Everything in this guide that is a secret goes there and nowhere else: not in chat, not in a file in the repository, not in a screenshot.
3. Save in the vault: recovery codes for each account above, the Cloudflare login, and (later) every key the steps below create.

**Check:** a second crew member can open the vault, and each account asks for a second step when you sign in from a new browser.

## A2. The DNS plan for `l3m0nctf.xyz`

DNS is edited in **Cloudflare** now (the nameservers moved there), not in Hostinger. This is the finished plan; each row is created at the step named.

| Name | Type | Points to | Proxied (orange cloud) | Created at |
|---|---|---|---|---|
| `l3m0nctf.xyz` | Worker custom domain | the landing page | yes | done |
| `play` | A | the VM's fixed address | **yes** | C5 |
| `crew` | A | the same address | **yes** | C5 |
| mail records (SPF, DKIM, DMARC) | TXT / CNAME | from the email service | **no** (grey cloud, "DNS only") | A3 |
| `www` | CNAME | `l3m0nctf.xyz` | yes | optional, your call |

## A3. Email: so that "verify your address" works

CTFd is set to make every player verify their email, so **no email, no registration**. This is the longest lead time of anything in this guide, so start it first.

1. **Choose a service** (decision 3). Numbers below are from vendor and review pages read on 2026-10-09; confirm them on the vendor's own pricing page before paying.

   | Service | What it gives | The catch |
   |---|---|---|
   | Brevo, free | 300 emails a day | Daily cap. 300 sign-ups on the first day is the limit |
   | Brevo, Starter (about $9 a month) | 5,000 emails a month, no daily cap | One month's fee. Simple SMTP |
   | Resend, free | 3,000 a month, 100 a day, one domain | The daily cap is too low for a rush |
   | Amazon SES | About $0.10 per 1,000 emails | New accounts start in a "sandbox" (200 a day, only to verified addresses). Leaving it needs a request that AWS says takes about a day. Needs an AWS account and card |

   How many emails: we plan for 250 teams and keep room for 1,000. At four players a team that is up to 4,000 verification emails, most of them in the first days, plus resets.

2. Create the account, add the domain `l3m0nctf.xyz`, and let the service show you its **DNS records** (SPF, DKIM, sometimes a return-path). Add each in Cloudflare under **DNS → Records**. Turn the cloud grey (**DNS only**) on every one of them.
   - There may be only **one** SPF record (a TXT record starting `v=spf1`). If one exists, add the new `include:` to it; do not create a second.
3. Add a DMARC record: type TXT, name `_dmarc`, content `v=DMARC1; p=none; rua=mailto:dmarc@l3m0nctf.xyz`. (`p=none` only watches. Moving to `quarantine` is a decision for after the rehearsal.)
4. Create **SMTP credentials** (a host, a port, a username and a password, or an API key). Put them in the vault. They are used later (Part D).
5. Optional, free and useful: **Cloudflare Email Routing** (Cloudflare dashboard → Email → Email Routing) gives you addresses such as `contact@l3m0nctf.xyz` that forward to a real mailbox. It also answers the open question of the landing page's contact address: send me the address and I put it in the footer. Email Routing adds its own MX and SPF records; when you add the sender's SPF record, merge them into the one record.

**Check:** the service's dashboard shows the domain "verified" or "authenticated". The real test comes in Part D (a message to a Gmail and an Outlook inbox, with SPF, DKIM and DMARC all "pass" in the message source).

## A4. "Continue with Google" **[Now]**, built later in SP8

The Google project for sign-in can be made **without billing**, so it does not start the 90-day clock.

1. Sign in at <https://console.cloud.google.com> with an account the crew controls (not a personal one that will leave). Create a project, for example `l3monctf-2026-auth`. Do **not** link billing.
2. Open **Google Auth Platform** (Google has renamed this from "OAuth consent screen", so look for either). Choose **External**. Fill in: app name `L3m0nCTF`, a support email, the authorised domain `l3m0nctf.xyz`, and the privacy page `https://l3m0nctf.xyz/privacy/`.
3. Create a client: **Clients → Create client → Web application**. Authorised JavaScript origin: `https://play.l3m0nctf.xyz`. Authorised redirect URI: `https://play.l3m0nctf.xyz/auth/google/callback`. This path comes from our sign-in research and may change when SP8 is built; adding another address later costs nothing.
4. Copy the **client ID** (not secret; fine to send me) and the **client secret** (vault only).
5. Before the event, set the publishing status to **In production**. While it says "Testing", only the test users you list (a small number) can sign in. The basic scopes we use (`openid email profile`) should not need Google's review; read the screen for any warning, and tell me if it asks for one.
6. Ask the college's Google administrator whether students may use third-party sign-in (D21). If it is blocked for them, ordinary email sign-in still works.

## A5. CTFtime **[Now]**

1. Create the CTFtime account (it uses a social login) and the **organiser team** (D11). Only a team member can do this; an existing Amrita team may be reusable.
2. The event can be submitted once it has a public page with the details; the landing page is that page. The sign-in with CTFtime needs the event approved first; the fixed IP address (step B4) goes to CTFtime later (D14).

## A6. Cloudflare Zero Trust (Access) **[Now]**

1. In the Cloudflare dashboard open **Zero Trust**. The first time, it asks for a team name (any short name) and a plan: choose **Free** (free for up to 50 users). It may ask for a payment method even for the free plan; it does not charge.
2. Under **Integrations → Identity providers** (older name: Settings → Authentication) add **One-time PIN**. Cloudflare then emails a 6-digit code to the person signing in; no mail server of ours is involved.
3. Write down the **crew's email addresses** (2 to 3 people). They are the only ones who will be allowed in. The application itself is created at step C7, when the admin host exists.

---

# Part B. The Google Cloud side **[VM]**

Do this when the money decision is made and a VM is needed. The commands use the `gcloud` tool (install from <https://cloud.google.com/sdk/docs/install>, then `gcloud auth login`). **I could not run these**, so if one fails, send me the exact message.

Pick the names once and keep them. Below they are `PROJECT_ID` (for example `l3monctf-2026`), the region `asia-south1`, the zone `asia-south1-a`.

## B1. Account, project, billing, alerts

1. Create (or have the bill owner create) the Google Cloud **free trial** at <https://cloud.google.com/free>. It needs a card. The $300 credit lasts 90 days.
2. Create the project and link billing:
   ```bash
   gcloud projects create PROJECT_ID --name="L3m0nCTF 2026"
   gcloud billing accounts list
   gcloud billing projects link PROJECT_ID --billing-account=BILLING_ACCOUNT_ID
   gcloud config set project PROJECT_ID
   gcloud services enable compute.googleapis.com iap.googleapis.com storage.googleapis.com logging.googleapis.com monitoring.googleapis.com
   ```
3. **Upgrade to a paid account** (console: Billing → the banner "Activate full account"), while the credit is still unused (decision 1).
4. **Budget alerts**: console → Billing → **Budgets & alerts → Create budget**. Amount about $250, alerts at 50%, 80% and 100%, sent to **two** people. A budget alert does not stop spending; someone has to read it.
5. **Check the CPU quota**: console → IAM & Admin → **Quotas**, filter "CPUs" and `asia-south1`. A new account can start low; the app host needs 8 CPUs at the big size. If it is lower, ask for an increase now (it can take a day or two) or upgrading (step 3) often raises it.

**Check:** `gcloud config get-value project` prints the project; the console shows the budget and the quota.

## B2. A private network with its own firewall

The default network comes with open rules. A custom one starts closed.

```bash
gcloud compute networks create l3mon-net --subnet-mode=custom
gcloud compute networks subnets create l3mon-asia-south1 --network=l3mon-net --region=asia-south1 --range=10.10.0.0/24

# SSH only through Google's tunnel (Identity-Aware Proxy), never from the open internet
gcloud compute firewall-rules create l3mon-allow-iap-ssh --network=l3mon-net --direction=INGRESS --action=ALLOW \
  --rules=tcp:22 --source-ranges=35.235.240.0/20 --target-tags=l3mon-app

# The web port, only from Cloudflare. The list is generated from the repository's own file
# (run these two lines in the repository folder on your own computer, in Git Bash):
python tools/update_cloudflare_ips.py        # refresh deploy/nginx/snippets/cloudflare-realip.conf first
CF_V4=$(grep -E '^set_real_ip_from [0-9.]+/' deploy/nginx/snippets/cloudflare-realip.conf | sed 's/set_real_ip_from //; s/;//' | paste -sd, -)
gcloud compute firewall-rules create l3mon-allow-cloudflare-https --network=l3mon-net --direction=INGRESS --action=ALLOW \
  --rules=tcp:443 --source-ranges="$CF_V4" --target-tags=l3mon-app
```

The VM gets an IPv4 address only, so only the IPv4 ranges are needed (the one-liner above prints the 15 current ones; I ran it here and it works).

**Check:** console → VPC network → Firewall shows exactly those two rules for `l3mon-net`, and nothing allowing `0.0.0.0/0`.

## B3. A service account with no powers

The VM acts as this account. If someone breaks into the VM, the account gives them nothing.

```bash
gcloud iam service-accounts create l3mon-app-vm --display-name="L3m0nCTF app VM (no roles)"
```

Do **not** grant it any role. (The backup bucket in B7 gets one narrow permission on that bucket only, later.)

## B4. The fixed address

```bash
gcloud compute addresses create l3mon-app-ip --region=asia-south1
gcloud compute addresses describe l3mon-app-ip --region=asia-south1 --format="value(address)"
```

The second command prints the address. It is public by nature and safe to send me. It is also what CTFtime may ask for (D14). A reserved address that is not attached to a running VM is billed a small hourly fee, so release it when everything is torn down.

## B5. The VM

Start with the smaller size while building; resizing later is a few minutes of downtime, which is fine before the event.

```bash
gcloud compute instances create l3mon-app \
  --zone=asia-south1-a --machine-type=e2-standard-4 \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=100GB --boot-disk-type=pd-balanced \
  --network=l3mon-net --subnet=l3mon-asia-south1 --address=l3mon-app-ip \
  --tags=l3mon-app \
  --service-account=l3mon-app-vm@PROJECT_ID.iam.gserviceaccount.com --no-scopes \
  --metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE \
  --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
  --deletion-protection
```

What the options do: Debian 12 is supported until mid-2028; `no-scopes` and the empty service account keep Google's own APIs out of reach of the VM; OS Login ties SSH to Google accounts (no loose keys); `block-project-ssh-keys` stops a key added to the project from working here; Shielded VM protects the boot; `deletion-protection` makes an accidental delete fail.

For the load test and the round: stop the VM, then
`gcloud compute instances set-machine-type l3mon-app --zone=asia-south1-a --machine-type=e2-standard-8`, then start it. Resize back down after the event.

## B6. Who may get in

Each crew member needs two permissions. Replace the address and repeat for each person:

```bash
gcloud projects add-iam-policy-binding PROJECT_ID --member="user:crew@example.org" --role="roles/compute.osAdminLogin"
gcloud projects add-iam-policy-binding PROJECT_ID --member="user:crew@example.org" --role="roles/iap.tunnelResourceAccessor"
# the person who may start and stop machines (decision 5):
gcloud projects add-iam-policy-binding PROJECT_ID --member="user:crew@example.org" --role="roles/compute.instanceAdmin.v1"
```

Then connect:

```bash
gcloud compute ssh l3mon-app --zone=asia-south1-a --tunnel-through-iap
```

**Check:** the connection works through the tunnel, and `ssh` straight to the public address from your own computer fails (the firewall blocks it).

## B7. Prepare the VM

Run these on the VM, after connecting.

1. Update, and make security updates automatic:
   ```bash
   sudo apt-get update && sudo apt-get -y upgrade
   sudo apt-get -y install unattended-upgrades git python3 chrony
   ```
2. Check the clock. Flag times depend on it:
   ```bash
   chronyc tracking      # "Leap status: Normal" and a small "System time" offset
   timedatectl           # "System clock synchronized: yes"
   ```
3. Install Docker from Docker's own repository (the current official steps are at <https://docs.docker.com/engine/install/debian/>; check them, because they change):
   ```bash
   sudo apt-get install -y ca-certificates curl
   sudo install -m 0755 -d /etc/apt/keyrings
   sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
   sudo chmod a+r /etc/apt/keyrings/docker.asc
   echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
   sudo apt-get update
   sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   sudo usermod -aG docker "$USER"      # then log out and in again
   docker compose version               # must print v2 or newer
   ```
   Being in the `docker` group is the same as being an administrator of the VM. Only crew members who already have the admin permission in B6 should be in it.
4. Check the disk and memory: `df -h /` (about 100 GB) and `free -h`. Container logs are rotated by the stack (5 files of 50 MB per service), so they cannot fill the disk.
5. **Not by hand:** the production checklist also needs a rule that stops containers from reaching Google's metadata server, a limit on what the platform container may reach on the internet, and encrypted off-host backups. Those are written, tested and shipped by SP9 as scripts. Do not improvise them from the internet; a rule that is wrong is worse than a missing one because it looks like protection.

## B8. The backup bucket (create it now, connect it in SP9)

```bash
gcloud storage buckets create gs://BUCKET_NAME --location=asia-south1 --uniform-bucket-level-access --public-access-prevention
```

Pick a hard-to-guess name (bucket names are global). The VM's account will get **only** the permission to create objects in this one bucket (`roles/storage.objectCreator`), so it can write a backup but not list, read or delete one. Reading and restoring is done by a crew member with their own login. SP9 adds the permission together with the encryption step, because an unencrypted backup in a bucket is a liability (it holds every player's email and API token).

---

# Part C. Cloudflare in front of the VM

These steps can be done as soon as the VM's address exists (B4). Where a step makes a secret, put it in the vault.

## C1. The origin certificate (so Cloudflare can talk to nginx over HTTPS)

1. Cloudflare dashboard → the zone `l3m0nctf.xyz` → **SSL/TLS → Origin Server → Create Certificate**.
2. Choose **Generate private key and CSR with Cloudflare**, key type **RSA (2048)**. Hostnames: `play.l3m0nctf.xyz` and `crew.l3m0nctf.xyz` (remove the `*` entries so nothing else is covered). Validity: your choice; 15 years is the default and avoids renewals.
3. **Create.** The page shows the certificate and the private key **once**. Copy each into the vault as `origin.pem` and `origin.key`. If you close the page without saving the key, delete the certificate and make a new one.

Visitors never see this certificate; they see Cloudflare's own. It only secures the second hop, from Cloudflare to our VM, and browsers do not trust it, which is expected.

## C2. Turn the strict mode on

1. **SSL/TLS → Overview**: set the mode to **Full (strict)**. (Do this only after the origin certificate exists and the VM serves it, or `play` shows error 526. Until the VM is up, leave the mode alone; nothing points at the VM yet.)
2. **SSL/TLS → Edge Certificates**: switch **Always Use HTTPS** on, and set **Minimum TLS Version** to 1.2.
3. HSTS is added by nginx itself, and only after the proof in the checklist (a forged header must not earn it). Leave Cloudflare's HSTS switch off.

## C3. Authenticated Origin Pulls with our own certificate

This is the zone-level version: the certificate is made by us, so only *our* Cloudflare zone can present it. I ran these exact commands with OpenSSL 3.5 on 2026-10-09 and checked that the result verifies as a client certificate.

1. On your own computer (not the VM), in an empty folder:
   ```bash
   openssl genrsa -aes256 -out rootca.key 4096                              # asks for a passphrase: vault
   openssl req -x509 -new -key rootca.key -sha256 -days 1826 -subj "/CN=l3m0nctf.xyz origin pulls" -out rootca.crt
   openssl req -new -nodes -newkey rsa:4096 -keyout cert.key -out cert.csr -subj "/CN=cloudflare-origin-pull.l3m0nctf.xyz"
   printf 'basicConstraints=CA:FALSE\n' > cert.v3.ext
   openssl x509 -req -in cert.csr -CA rootca.crt -CAkey rootca.key -CAcreateserial -out cert.crt -days 400 -sha256 -extfile cert.v3.ext
   openssl verify -purpose sslclient -CAfile rootca.crt cert.crt            # must print: cert.crt: OK
   ```
   `rootca.key` and `cert.key` go in the vault, then **delete them from the computer**. `rootca.crt` is public (it goes on the VM). `cert.crt` and `cert.key` are what Cloudflare presents.
   The leaf is valid for 400 days, which covers the event with room. Cloudflare warns 30 and 14 days before a certificate expires if you switch on its certificate alerts (Notifications).
2. Cloudflare → **SSL/TLS → Origin Server → Authenticated Origin Pulls**. In the **Zone-level** section choose **Upload certificate**, paste `cert.crt` and `cert.key` (the leaf, not the root), and save. Do **not** switch the toggle on yet.
3. Keep `rootca.crt`: it goes to the VM as `/etc/l3mon/tls/cloudflare-origin-pull-ca.pem` in Part D, and nginx will check every request against it.
4. After Part D, when nginx is serving on 443 and the pages load through Cloudflare, switch the **Zone-level** toggle **On**. Then tighten nginx from "optional" to "on". Ask me; the stack will ship in the safe order.

What nginx will do, proved on the stack's own pinned nginx image on 2026-10-09 with a test certificate made by these same commands: with the right client certificate the request is answered (`200`); a certificate from any other authority gets `400 The SSL certificate error`; no certificate gets `400 No required SSL certificate was sent`.

## C4. The cache rules

Cloudflare must not store anything with a player's session in it, and it ignores a page's own instructions about that. So the rule is: cache nothing except the theme's static files.

Cloudflare → **Caching → Cache Rules → Create rule**, two rules, each with its own condition so they never overlap:

| # | If the request matches | Then |
|---|---|---|
| 1 | Custom filter expression: `not starts_with(http.request.uri.path, "/themes/")` | **Bypass cache** |
| 2 | Custom filter expression: `starts_with(http.request.uri.path, "/themes/")` | **Eligible for cache**; edge TTL "use cache-control header if present, bypass if not" |

**Check (in Part D):** two different signed-in sessions request `/login` and `/api/v1/challenges`: neither response carries `cf-cache-status: HIT`. A file under `/themes/` does.

## C5. The DNS records

Cloudflare → **DNS → Records → Add record**:

| Type | Name | Content | Proxy status |
|---|---|---|---|
| A | `play` | the address from B4 | Proxied (orange) |
| A | `crew` | the same address | Proxied (orange) |

If you create these before the VM serves anything, the pages show a Cloudflare error (521): that is expected and harmless.

## C6. Basic firewall and rate-limit rules

The free plan allows **5 custom rules** and **1 rate-limiting rule** (check the numbers in your dashboard). nginx has its own limits inside the VM; Cloudflare's job is to stop floods before they arrive.

1. **Security → Security rules → Create rule** (the menu is called "WAF" in some versions). Rule one: if `http.host eq "play.l3m0nctf.xyz" and starts_with(http.request.uri.path, "/admin")` → **Block**. The admin pages live on the other host name only.
2. **Rate limiting rule** (one): if `http.host eq "play.l3m0nctf.xyz" and http.request.method eq "POST" and http.request.uri.path in {"/login" "/register"}` → block after about 20 requests a minute from one address. (The free plan may offer only a short fixed window, such as 10 seconds; use the nearest choice it gives.) Add `and not ip.src in {the campus addresses}`, because a hundred players behind one campus address would otherwise block each other. nginx already limits these paths per address as well.
3. Leave **Bot Fight Mode** off until the rehearsal has shown it does not block real players on the campus network.
4. Know where **Under Attack Mode** is (Security → Settings). It is the emergency switch for a flood; the runbook that SP9 writes covers when to use it.

## C7. Cloudflare Access on the admin host

1. Zero Trust → **Access → Applications → Add an application → Self-hosted**.
2. Application domain: `crew.l3m0nctf.xyz`, no path (so every page and every API call on that host is covered). Session duration: 8 hours.
3. Add a policy: action **Allow**, rule **Emails** → the crew's addresses from A6. Nothing else.
4. Save.

**Check:** open `https://crew.l3m0nctf.xyz/admin` in a private window. You get Cloudflare's login page, an address that is not on the list gets "That account does not have access", and one that is on the list receives a code by email. The code expires after 10 minutes.

Remember the limit of Access: it works only if every request goes through Cloudflare. That is what the VM firewall (B2) and Authenticated Origin Pulls (C3) guarantee, so all three are needed together.

---

# Part D. Putting the platform on the VM **[After SP9]**

## D0. What exists today and what arrives

| Piece | Today | Arrives with |
|---|---|---|
| The CTFd image (hardened, our plugins) and `tools/compose.sh` | Built and tested (`docs/deploy/local.md`) | already there |
| The stack for one computer (nginx on plain HTTP, 127.0.0.1 only) | Works | already there |
| **Production settings**: nginx on 443 with the origin certificate and the client-certificate check; the admin host name; `L3MON_SECURE_COOKIES=true`; mail settings; no preset admin token | **Not built** | SP9 |
| The metadata block, the egress limits, encrypted off-host backups, monitoring, the status page | **Not built** | SP9 |
| Real pages (Channels, Guide, Scoreboard, crew pages) | The demo only | SP3 and SP6 |

So the commands below are the **intended sequence**. The first five run unchanged today on any machine (that is how the stack is tested); the production file and the certificate mounting are added by SP9, and I will update this section with the exact lines and re-test it on a second VM before the rehearsal.

## D1. Get the code

```bash
git clone https://github.com/sivaahari/L3m0nCTF.git && cd L3m0nCTF
git checkout <the release tag I give you>
```

Deploy a tag, never a moving branch, so you can always say exactly what is running.

## D2. Make the secrets (on the VM, never on a laptop)

```bash
cd tools && python3 -m l3mon secrets generate --dir ../.secrets && cd ..
```

This writes new random secrets into `.secrets/` and prints nothing. It refuses to overwrite existing ones. Then:

1. Open each file and copy it into the vault (`cat .secrets/FLAG_HMAC_SECRET` and so on; do not paste them into chat). **`FLAG_HMAC_SECRET` matters most: losing it during the round changes every team's flag.**
2. Delete the file `.secrets/PRESET_ADMIN_TOKEN`. Production never uses it.
3. The folder must be readable only by the deploy user (`chmod 700 .secrets`).

## D3. The event settings

```bash
cp config/event.example.toml config/event.toml
```

Check the file: the name, the two dates (09:00 IST on 28 November is `2026-11-28T03:30:00Z`), `platform_host = "play.l3m0nctf.xyz"`, `size_max = 4`, `verify_emails = true`. Then turn it into the settings the stack reads:

```bash
cd tools && python3 -m l3mon config render ../config/event.toml --out ../deploy/compose/generated && cd ..
```

## D4. Production values that are not in the repository

| What | Where it goes | Notes |
|---|---|---|
| Organisers' addresses | a file **outside** the repository, for example `/etc/l3mon/admin-nets.conf`, named by `ADMIN_NETS_FILE` | Start from `deploy/nginx/snippets/admin-nets.production.example.conf`. Explicit addresses only, no private range; a test refuses the file otherwise |
| The origin certificate and key | `/etc/l3mon/tls/origin.pem` and `origin.key` | Key readable by root and the nginx group only. From the vault |
| The Cloudflare client-certificate authority | `/etc/l3mon/tls/cloudflare-origin-pull-ca.pem` | That is `rootca.crt` from C3, public |
| Mail settings | the host, port, username and password from A3 | CTFd reads `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USEAUTH`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_TLS` or `MAIL_SSL`, and `MAILFROM_ADDR` (I read these names from CTFd 3.8.8 in our image). The password goes in as a secret file, not in the admin panel, because a value typed into the panel is stored in the database and so in every backup |
| `L3MON_SECURE_COOKIES=true` | the production settings | Makes the session cookie `__Host-session` with `Secure` |
| The Cloudflare address list | `python3 tools/update_cloudflare_ips.py`, then read the diff | Run before every deploy |

## D5. Build and check the image

```bash
docker build -f docker/ctfd/Dockerfile -t l3mon/ctfd:<tag> .
tools/verify-image.sh l3mon/ctfd:<tag>        # about 30 seconds; must pass
```

## D6. Start

```bash
L3MON_ENV=production tools/compose.sh up -d --wait
tools/compose.sh ps                            # every service "healthy"
```

(`L3MON_ENV=production` picks up the production file SP9 adds. Today only `dev` exists.)

## D7. First sign-in and the crew's accounts

1. Open `https://crew.l3m0nctf.xyz/admin`, pass Cloudflare Access, sign in as `organiser` with the password from `.secrets/PRESET_ADMIN_PASSWORD`.
2. Create **one named admin account per crew member** (two or three). Give each a strong password and keep `organiser` as the emergency account, its password only in the vault.
3. Admin tokens for scripts are made inside CTFd, with an expiry. There is no fixed token in production.

## D8. The tests that prove it

Run each and write the result in [verification-log.md](verification-log.md):

| Test | How | Passes when |
|---|---|---|
| The page loads through Cloudflare | open `https://play.l3m0nctf.xyz` | the sign-in page, a valid certificate |
| The VM refuses a direct request | `curl -k https://<VM address>/` from your own computer | the connection is refused or times out (the firewall) |
| Another Cloudflare account cannot reach it | after C3 step 4 is done: a request without our client certificate | `400 No required SSL certificate was sent` |
| The cache rule | request `/login` and a file under `/themes/` twice | `/login`: no `HIT`; the theme file: `HIT` |
| The cookie | sign in, look at the response | `Set-Cookie: __Host-session=...; Secure; HttpOnly; SameSite=Lax` |
| The admin pages are closed | open `https://play.l3m0nctf.xyz/admin` | blocked; the same on the admin host without Access: the Access login |
| The real visitor address reaches nginx | read the access log (`tools/compose.sh logs nginx`) | your own public address, not a Docker or Cloudflare one |
| A forged header earns nothing | send `X-Forwarded-Proto: https` | no HSTS in the answer |
| Email | register a test account with a Gmail and an Outlook address | the message arrives in the inbox, not in spam, and "Show original" lists SPF, DKIM and DMARC as `PASS` |
| Backup and restore | `tools/backup.sh`, then restore on a **second** VM | the drill finishes in under 15 minutes |

The rest of the list (the VM's account, the egress test, the secrets copy, the load test) is [production-checklist.md](production-checklist.md); the platform is ready for the public when every line there has its proof in the log.

---

# Part E. Updating, rolling back, switching off

**Update.** `git fetch --tags && git checkout <new tag>`, build the image with a new tag, then `L3MON_ENV=production tools/compose.sh up -d --wait --force-recreate`. Keep the previous two image tags on the VM.

**Roll back.** Check out the previous tag and start it the same way. Take a backup before every update (`tools/backup.sh`); a database change is not undone by an older image.

**Change freeze.** After the feature freeze on **2026-11-21** only approved fixes go in, each with the checks above. The run of the full CI workflow by hand happens before the freeze and the day before the round.

**Switch off to save the credit.** `gcloud compute instances stop l3mon-app --zone=asia-south1-a` between test days. After the event: take the final backup, keep a copy of the database and the logs off the VM, stop the VM, and later delete it, release the fixed address (`gcloud compute addresses delete l3mon-app-ip --region=asia-south1`), and delete the project when nothing in it is needed.

---

# Part F. A suggested order and dates

Dates are proposals; the online round is 28 to 29 November.

| When | What | Who |
|---|---|---|
| This week | A1, A2, A3 (choose the service, add the DNS records), A5, A6; decisions 1 to 6 put to leadership | you |
| By Oct 16 | A4 (Google project and client); email verified | you |
| About Oct 25 | B1 (start the trial and upgrade it), B2 to B7, C1, C3 (certificates), C5 | you, with me on the other end |
| SP9 delivery (before Nov 1) | The production file, the hardening scripts; I update Part D and re-test it | me |
| About Nov 1 | Part D on the staging VM; Part C switches on in the order the guide gives | both |
| Nov 10 | Load test on the big size (needs B5 resize) | both |
| Nov 21 | Feature freeze | |
| Nov 27 | Dress rehearsal, restore drill, every alert fired once | both |
| Nov 28, 09:00 IST (03:30 UTC) | Start | |

---

# Part G. If something goes wrong

| What you see | Usual cause | What to do |
|---|---|---|
| Cloudflare **521** | The VM is off, nginx is down, or the firewall blocks Cloudflare | Start the VM; `tools/compose.sh ps`; check rule `l3mon-allow-cloudflare-https` and that the list is current |
| Cloudflare **522** or **523** | The firewall blocks Cloudflare, or the address is wrong | Compare the `A` record with the address from B4 |
| Cloudflare **525** or **526** | The origin certificate is missing, wrong, or does not cover the host name | Check `/etc/l3mon/tls/` and the host names in C1; set the mode back to Full while you fix it |
| `400 No required SSL certificate was sent` for real players | The Authenticated Origin Pulls toggle is off, or the mode is not Full | Turn the toggle on and set the mode to Full (strict); reload nginx |
| `/admin` shows 403 on the admin host | Your address is not in the organisers' file | Add it to the file named by `ADMIN_NETS_FILE`; reload nginx |
| Cloudflare Access never sends the code | The mail filter or a typo in the address | Check the spam folder; allow `noreply@notify.cloudflare.com` |
| Players say the verification email never came | The service's daily cap, a wrong DNS record, or the address in spam | The service's dashboard shows sent, bounced and blocked; fix the cap or the record |
| `gcloud compute ssh` fails | You lack the two permissions in B6, or the tunnel rule is missing | Check B6 and rule `l3mon-allow-iap-ssh` |
| The clock is wrong | The time service stopped | `chronyc tracking`; `sudo systemctl restart chrony` |
| A bill alert arrives | A big VM is running, or traffic grew | Stop what is not needed (Part E); the budget alert does not stop spending itself |

---

# What I proved, and what I could not

| Claim | How it was checked on 2026-10-09 |
|---|---|
| The client-certificate commands in C3 produce a certificate that verifies as a client certificate | Ran them with OpenSSL 3.5 and `openssl verify -purpose sslclient` |
| nginx on the stack's pinned image, set to require the client certificate, answers 200 for ours and 400 for a certificate from another authority or none | Ran it in Docker with a test server certificate and these certificates |
| CTFd 3.8.8 reads the mail settings from the environment under the names in D4 | Read the configuration code inside our image |
| The Cloudflare address list one-liner in B2 prints the 15 IPv4 ranges | Ran it |
| Cloudflare's zone-level Authenticated Origin Pulls steps, the Origin CA steps, Access with One-time PIN, the free-plan limits, the email services' limits | Read in the vendors' documentation and review pages. **Not tried by me**; the limits change, so confirm them in your dashboard |
| The `gcloud` commands, the Google console paths, Docker's install steps on Debian 12, the quota behaviour of a new account | From the vendors' documentation and my knowledge. **Not run by me.** Send me the exact message of anything that fails and I correct the guide |
| Part D as a whole on a real VM | **Not possible yet**: it needs the production file from SP9. I will run it end to end on a second VM before the rehearsal and update this file |

## Sources

- Cloudflare: [zone-level Authenticated Origin Pulls](https://developers.cloudflare.com/ssl/origin-configuration/authenticated-origin-pull/set-up/zone-level/), [Origin CA certificates](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/), [One-time PIN](https://developers.cloudflare.com/cloudflare-one/identity/idp-integration/one-time-pin), [self-hosted applications (Access)](https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/self-hosted-apps)
- Google Cloud: [free trial](https://cloud.google.com/free), [Docker on Debian](https://docs.docker.com/engine/install/debian/)
- CTFd: [configuration](https://docs.ctfd.io/docs/deployment/configuration)
- In this repository: [production-checklist.md](production-checklist.md), [local.md](local.md), [go-live-inputs.md](go-live-inputs.md), [the cost estimate](../research/gcp-cost-estimate.md), [the sign-in research](../research/sign-in-methods.md)
