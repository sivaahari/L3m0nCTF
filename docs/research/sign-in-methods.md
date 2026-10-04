# Sign-in methods: email, Google and CTFtime

Decided on 2026-10-04 by the project owner: players can sign in three ways. This note says what each one is, what it needs, the rules that keep accounts safe, and how it fits the build. The CTFtime details are in [CTFtime OAuth and the live JSON feed](ctftime-oauth-and-live-feed.md). Nothing here contains a secret.

## 1. The three methods

| Method | What the player does | What we receive | What it needs from the organisers | Works before the event is approved? |
|--------|---------------------|-----------------|-----------------------------------|-------------------------------------|
| **Email and password** | Types an email, a username and a password, then clicks the link we email | Whatever the player types | A sending domain with SPF, DKIM and DMARC and a mail provider (decision D13, assumption A4 in the build design) | Yes |
| **Continue with Google** | Clicks the button, picks a Google account, confirms | A stable Google ID, an email address and whether Google has verified it, and a display name | A Google Cloud project owned by an organiser account, a consent screen, a web client and the final domain | Yes |
| **Login with CTFtime** | Clicks the button, confirms on CTFtime's page | CTFtime user ID, name, an email that CTFtime does not guarantee is verified, and the team the player chose | An approved event on CTFtime and a fixed outgoing server address | No: only after CTFtime approves the event |

Email and password always work. Each of the other two buttons appears only when that method is configured, and a failure in one never blocks the others.

## 2. Why Google is worth having

- **It removes the biggest registration risk.** The build design lists "sign-up emails land in spam or are delayed" as a risk. A player who signs in with Google already has a verified address, so the email step is skipped.
- **Fewer fake accounts.** A Google account costs more to fake than a throwaway email address.
- **Faster sign-up** for the thousand-team target: two clicks instead of a form and a mailbox round trip.
- **Cost:** about 2 builder-days on top of the CTFtime plugin, because both share one plugin shell. Recommendation: put Google in scope tier A (it reduces a tier A risk) and keep CTFtime login in tier B (it needs event approval). That choice is the owner's (decision D17).

## 3. How Google sign-in works, in simple terms

1. The player clicks **Continue with Google**. We send the browser to Google with a one-time random value (`state`), a second one-time value (`nonce`) and a PKCE challenge.
2. Google asks the player to choose an account and confirm.
3. Google sends the browser back to our fixed callback address with a one-time code.
4. Our server swaps the code for a signed ID token, checks the signature against Google's published keys, and checks the issuer, audience, expiry, `nonce` and `email_verified`.
5. We find or create the player's account and sign them in. We never see the Google password, and we do not keep any Google token, because we never call a Google service after sign-in. The scopes are only `openid email profile`.

**Google Cloud setup (an organiser does this once).**

- Create a project under an organiser account, not a personal one, so the setup survives people leaving.
- Fill in the OAuth consent screen: app name, a support email, the authorised domain and links to our privacy and terms pages.
- Create an OAuth client of type "Web application" and register the exact callback address, for example `https://<our-domain>/auth/google/callback`. For local development Google accepts a `localhost` address.
- Set the publishing status to **In production** before registration opens. In "Testing" status only a short list of named test users (up to 100) can sign in.
- The basic scopes need no Google security review, but Google's consent-screen rules change, so check the current requirements on the day and allow a few days of margin.
- Store the client ID and secret outside git, in the secrets store.

**One risk to check early.** If student accounts are Google Workspace accounts, the college's Google administrator may block third-party sign-in for them. Email sign-in remains as the fallback, and we should ask the administrator once.

## 4. Rules that keep accounts safe

These are the part that matters most. They apply to Google and CTFtime alike.

1. **One person, one account.** Each provider identity (provider name plus the provider's stable ID) maps to exactly one account, stored in a small identity table. A name or an email address alone never identifies someone.
2. **Never link by an unverified email.** If someone registers with an email and never verifies it, and the real owner later signs in with Google, the two must not merge into the first person's credentials. A provider sign-in links to an existing account by email only when that account's email is already verified. Otherwise the player must sign in to the existing account first and press **Link Google** in settings.
3. **Trust Google's email only when `email_verified` is true.** The CTFtime email is never trusted: such an account starts unverified and goes through the normal "verify your email" gate, and any bracket that depends on a verified email waits until then.
4. **Admins cannot sign in through a third party.** Admin accounts use email and password plus whatever extra protection we set up. This is already scenario S11.
5. **Checks on every callback.** `state` is single-use and tied to the browser session (CTFtime wants 16 random bytes as hex), PKCE and `nonce` are verified, the callback address must match exactly, and the callback is rate limited per address.
6. **Team names stay separate.** Neither provider knows our team name. Team creation stays step 3 of registration. The CTFtime path pre-fills the CTFtime team name as a hint.
7. **Minimum data and a clear notice.** We store the provider, the stable ID, the email and a display name. The privacy notice says what each provider shares, and the Google consent screen links to it.
8. **Official branding.** The Google button uses Google's own button artwork and wording ("Continue with Google" or "Sign in with Google"), as their guidelines require. The CTFtime button is plain text.
9. **Fail soft.** If Google or CTFtime is down, that button shows a short message and email sign-in keeps working. The login page never depends on a third party to render.

## 5. How we build it

- One plugin, `l3mon_auth`, with a small provider interface: build the authorise address for a `state`, exchange a code for an identity, and return `{provider, subject, email, email_verified, display_name, team_hint}`.
- Three providers behind it: Google, CTFtime and a **mock provider** used by tests and development, so nothing in CI talks to the internet.
- Routes `/auth/<provider>/start` and `/auth/<provider>/callback`. After the identity is checked, CTFd's own user creation, session and email-verification flow take over, so its throttles and rules stay as they are.
- Tests with the mock provider: success, bad `state`, expired code, replayed code, unverified email, the account-takeover case from rule 2, an admin refused, a name clash and an oversized field.
- The login and registration pages show the three methods (pages atlas v5, private repo). Without JavaScript the buttons are plain links.

**Effort.** Google adds about 2 builder-days. Google and CTFtime together are about 4 to 5 builder-days instead of 2 to 3 for CTFtime alone. Updated plan: SP8 in the build design.

## 6. What we need from people

| Need | From | By |
|------|------|----|
| A Google Cloud project owned by an organiser account, with the consent-screen details and a support email | An organiser (decision D21) | Before staging goes public |
| The final domain, to register the callback address | Leadership (D13) | The same day it is chosen |
| Approval of the privacy notice text, including what Google and CTFtime share | Organisers | Before registration opens |
| One question to the college's Google administrator: may students use third-party sign-in? | Owner | This week |
| A sending domain and mail provider for email sign-up | Leadership (D10, D13) | By about Oct 14 |

## 7. Open points

- Whether a verified `@cb.students.amrita.edu` address (via Google or email) should set the AMRITA and STUDENTS brackets automatically. Today's plan says yes where the email is verified.
- Whether Google sign-in should be restricted to certain domains. Recommendation: no, the event is open to any team.
- Whether to offer other providers later (GitHub, Discord). Recommendation: no, three methods are enough for the first event.
