# Putting the landing page on `l3m0nctf.xyz`, and changing it afterwards

Written 2026-10-08 for the owner. Plain words. No secrets, no event material.

## Where things are today

| Piece | Where it is | Notes |
|---|---|---|
| The landing page | Cloudflare, as a Worker named `l3m0nctf-test`, at `https://l3m0nctf-test.cb-sc-u4cys24055.workers.dev/` | Live and checked: all 16 files identical to the build, the strict security headers, the 404 page and the countdown work |
| The domain `l3m0nctf.xyz` | Hostinger (the owner's superior shared access to it) | The domain name is registered there. That does **not** force the page to be hosted there |
| What is still missing | The domain pointing at the page | One change at Hostinger (the nameservers), then one in Cloudflare |

There are two ways to finish. **Route A is the one to use**: the page is already live on Cloudflare, it is free, it copes with a rush of visitors, and it gives the page its security headers without any extra work. Route B puts the files on a Hostinger hosting plan instead, and only makes sense if the superior already pays for one.

## Step 0: accept the access at Hostinger (both routes)

1. Open the email Hostinger sent to your personal address about the shared access, and click the link to accept.
2. If you have no Hostinger account under that email yet, Hostinger asks you to create one first. Use the same email, then turn on **two-step verification** in your profile: this login now controls the event's domain.
3. After you accept, the superior's account shows up in your hPanel (Hostinger's dashboard). Switch to it from the account menu at the top of hPanel.
4. What you can do depends on the role the superior chose. Hostinger's **Collaborator** role can manage a domain's DNS. It cannot buy anything, transfer or unlock a domain, or add payment methods. If a button below is greyed out, ask the superior to give you the **Admin** role for the domain, or to do that one step himself. Do not ask for his password.

## Route A (recommended): domain at Hostinger, page on Cloudflare

### A1. In Cloudflare (your account)
1. Click **Add → Connect a domain**. Enter `l3m0nctf.xyz` and choose the **Free** plan.
2. Cloudflare copies the domain's existing DNS records. Keep the ones you recognise, especially mail (MX) records and anything that starts with `v=spf1`, `google-site-verification` or `_dmarc`.
3. Cloudflare shows **two nameservers** ending in `.ns.cloudflare.com`. Copy both. Leave this page open.

### A2. In Hostinger (hPanel, in the superior's account)
1. In the left sidebar click **DNS**, and select `l3m0nctf.xyz`. (You can also go to **Domains** and click **Manage** next to it.)
2. Open the **DNS / Nameservers** tab. If a **DNSSEC** switch is shown and it is on, turn it off first.
3. Click **Change Nameservers**. Choose the option that points the domain elsewhere ("Change nameservers", not "Use Hostinger nameservers"). Enter the two Cloudflare nameservers (up to four boxes; two is right), and click **Save**.
4. Hostinger says the change can take up to 24 hours. It usually takes minutes to a few hours. From now on, DNS is edited in Cloudflare, not at Hostinger.

### A3. Back in Cloudflare
1. Wait until the domain shows **Active**. (Cloudflare also emails you.)
2. Go to **Workers & Pages → l3m0nctf-test → Settings → Domains & Routes → Add → Custom Domain**. Enter `l3m0nctf.xyz` and click **Add Custom Domain**. Cloudflare creates the DNS record and the HTTPS certificate itself. If an old record already exists for that exact name, delete it first.
3. Optional: add `www.l3m0nctf.xyz` the same way.
4. Under **SSL/TLS → Edge Certificates**, switch on **Always Use HTTPS**.
5. Send `https://l3m0nctf.xyz/` to Claude. The same checks as before are run on it (files, headers, 404, countdown, share card).
6. Afterwards, switch off the old `workers.dev` address in the same Domains & Routes section, so the site has one address.

## Route B (only if a Hostinger hosting plan exists): files on Hostinger

This needs a Hostinger **web hosting** plan on the superior's account, and the plan has to be shared with you too (sharing only the domain gives DNS, not the files).

1. In hPanel: **Websites**, add the domain to the hosting plan, and wait until it points at Hostinger. Hostinger's free SSL certificate is installed from the website's dashboard.
2. Open the website's **Dashboard → Files → File Manager → `public_html`**.
3. Upload the **contents** of the `dist` folder (the 16 files, not the folder itself) so `index.html` sits directly in `public_html`. Easiest: zip the contents, upload the zip, right-click it and choose **Extract**.
4. Hostinger's servers do not read Cloudflare's `_headers` file. The page then loses its strict security headers unless an `.htaccess` file with the same rules is added. Claude prepares that file on request; do not go live on Hostinger without it.

## Changing the website after it is live

Everything the page says about the event (dates, registration link, contact email, sponsors, news lines) is in one file, `private/landing/config/site.json`. The pages come from `private/landing/src`. A change is always the same four moves: **edit, build, check, upload**.

### Route A: Cloudflare (what to run on your computer)

One-time set-up (about 5 minutes):
1. In the folder `private/landing`, there is a file `wrangler.jsonc`. It says: the Worker is called `l3m0nctf-test`, the files are in `dist`, and a wrong address shows the 404 page.
2. Run `npx wrangler login`. A browser tab opens, you approve it in your Cloudflare account. No password or key is ever typed into a terminal or sent to Claude.

Each change:
1. Edit `config/site.json` (or ask Claude to). 
2. Run `node tools/build.mjs`. It rebuilds `dist` and refuses to finish if a size budget or a safety check fails.
3. Run `node tools/serve.mjs --no-build` and open `http://localhost:8080/` to look at it.
4. Run `npx wrangler deploy`. Cloudflare only uploads the files that changed. The new version is live in seconds.
5. If something is wrong, open **Workers & Pages → l3m0nctf-test → Deployments** in Cloudflare and roll back to the previous version.

If you would rather not use a terminal: ask Claude to make the change and tell you; the upload can also be done in the Cloudflare dashboard by uploading the rebuilt `dist` folder again.

### Route B: Hostinger
- **File Manager:** upload the changed files into `public_html` (they overwrite the old ones).
- **FTP:** in hPanel, **Files → FTP Accounts**, create an account limited to `public_html`, and use it in an FTP program such as FileZilla (port 21, or 22 for SFTP).
- **Git:** in the website's dashboard, **Advanced → Git**, connect GitHub, pick the branch and `public_html`. Every push to that branch then deploys by itself. Hostinger overwrites `public_html` on each deploy, so never edit files there by hand.

## Rules that keep the page safe

- Only the built `dist` folder goes online. Never upload the repository, the `private` folder, or any file with a password in it.
- After every deploy, load the page once and check the countdown, the Rules page and a wrong address (the 404 page).
- Keep two-step verification on for Hostinger, Cloudflare and GitHub. Remove people's access when they stop helping with the event.
- Changing the nameservers moves all of the domain's DNS to Cloudflare, so mail records must be copied over first (step A1.2). If the domain sends or receives email, test that after the switch.

## Sources

- [How to change nameservers at Hostinger](https://www.hostinger.com/support/1696789-how-to-change-nameservers-at-hostinger/)
- [How to use the Domains section in hPanel](https://support.hostinger.com/en/articles/6940479-how-to-use-the-domains-section-in-hpanel)
- [How to share access to your account at Hostinger](https://www.hostinger.com/support/1583777-how-to-share-access-to-your-account-at-hostinger/)
- [Hostinger File Manager](https://www.hostinger.com/support/4548688-basic-actions-in-the-file-manager-in-hostinger/), [Git deployment](https://www.hostinger.com/support/1583302-how-to-deploy-a-git-repository-in-hostinger/), [FTP accounts](https://support.hostinger.com/en/articles/1583246-how-to-create-additional-ftp-accounts-on-hpanel)
- [Cloudflare Workers: custom domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/), [static assets headers](https://developers.cloudflare.com/workers/static-assets/headers/), [static assets get started](https://developers.cloudflare.com/workers/static-assets/get-started/)
