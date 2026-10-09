# The cold-open comic (design and implementation plan)

> **For agentic workers:** work task by task, test first. All Python runs inside the platform image (`l3mon/ctfd:dev`) against CTFd's own test helpers; the player's pure logic is tested in node and the whole player in a real browser (`tests/browser/cdp.mjs`). A security test is only accepted when it has also been **shown failing with its guard switched off**. Opus audits at the end; it does not build.

## In one page (for the owner and the superiors)

**What it is.** When a channel is on air, a button under its caption opens a short comic, the channel's *cold open* (4 to 6 panels, about 30 to 45 seconds): the panels move (slow camera pans, characters sliding in, speech bubbles typing out, a TV-static flash between panels), there is an optional cartoon sound sting for each beat, and it can be read as plain text. It tells a little of the show-world and ends by calling the night-shift crew (the player) to master control. It never says what a challenge is about.

**Who may open it.** Only a registered, signed-in player. A visitor who clicks it is sent to the registration page and brought back afterwards. A channel's comic is available only once the channel has something on air, and never before the broadcast starts. The crew can preview any of them at any time.

**Why a comic drawn in code, not a video file.** It was the owner's choice among options (the earlier notes compare them), and it is the best of the four on the three things asked for:

| | Efficiency | Visual coolness | Security |
|---|---|---|---|
| **Motion comic drawn in code (built)** | one small request on click, about 60 to 120 KB for a whole channel; no video host; works on a phone | TV language the whole platform already speaks: static bursts, scan lines, lower-third captions, camera moves, parallax, typed bubbles | the story is never in a page, script or style sheet; one gated request; art is validated vector art shown as an image, which cannot run code |
| Video file per channel | 3 to 6 MB each, a video-friendly host, captions to make | richest, but the least controllable | a media route to gate and a bigger CSP |
| Hand-made animation by a club | depends on people and time | the best result if it arrives | same as video |
| Generated video | cheap to try | characters drift between clips | terms of each tool to read; not for the shipped story |

A **rendered clip** of the same comic (for the landing page and for sharing) comes next, from the same script, by playing it in a headless browser; nothing in what is built now stands in its way.

## Decisions (and why)

1. **Panels are layered SVG pictures shown as images, with the words and the motion added by the player.** An SVG shown through an image element cannot run script, load another file or touch the page, which is the strongest sandbox a browser offers for art, and it keeps the content authors' tools simple (any SVG editor, or code). The art is also *checked* when the story is built and again when the server loads it (an allow-list of shapes, gradients, patterns and a few filters; no script, no style sheet, no outside address, no image or font fetch, no event handler).
2. **One gated request per channel.** `GET /api/v1/l3mon/story/<slug>` returns the whole bundle (script, art, text). A visitor gets 401; a channel not on air, an unknown channel and a time before the start all get the same 404; a signed-in account that is not allowed to see the board yet (suspended, email not verified) is refused as everywhere else. Nothing about a story is in the board answer except the number of panels of a channel whose story is available (`channels[].cold_open`), because the button needs it; the board still never carries a field called `story`.
3. **The engine is in the public repository; the story is not.** The plugin ships the validator, the gate, the player and a small made-up sample. The real scripts and art live in the private repository and are compiled to files that the server reads from a folder it is given (`L3MON_STORY_DIR`, read-only, filled at deployment). The server never writes it and holds no story in its database.
4. **The words are real text.** Speech bubbles and captions are text nodes in the page (selectable, translatable, readable by a screen reader); there is a plain-text transcript, and with "reduce motion" on (or no script) the comic is a static strip of panels with no animation and no sound.
5. **Sound is made in the browser with the Web Audio interface** (a dozen short synthesised stings: no audio files, nothing to host), off until the player turns it on, never on its own.
6. **Nothing hidden.** The comic is a visible button, never a hidden trigger. No easter eggs.
7. **Mythology as tropes, no religion** (the owner's rule): a thunder-wielding hero, a trickster fox, a hundred-eyed night watchman. Never a figure that is worshipped today, a holy text, a prayer or a ritual. Every name is a made-up working title to be cleared before public use.

## Format (version 1)

A compiled **bundle** is one JSON file per channel, `<slug>.json`:

```json
{
  "v": 1, "slug": "street", "title": "Show title", "kicker": "CH 01 · Mighty Street", "lang": "en",
  "panels": [
    {
      "id": "p1", "ms": 6000, "enter": "static", "alt": "A plain description of the picture, for people who cannot see it.",
      "layers": [ {"art": "street_wide", "x": 0, "y": 0, "w": 100, "h": 100, "z": 0, "from": {"x": 0, "y": 0, "s": 1.05}, "to": {"x": -3, "y": 0, "s": 1.15}} ],
      "bubbles": [ {"kind": "say", "who": "Pip", "text": "Sixty seconds!", "x": 55, "y": 8, "w": 36, "tail": "bl", "at": 900} ],
      "sfx": [ {"cue": "whoosh", "at": 0} ]
    }
  ],
  "art": { "street_wide": "<svg ...>...</svg>" }
}
```

Layer and bubble positions are percentages of the panel (16:9). `from` and `to` are the layer's slow move over the panel's time (x and y as a percentage of the panel, `s` the scale). `kind` is `say`, `think`, `shout`, `caption` or `sfx` (a big comic sound-word). `enter` is how the panel arrives: `cut`, `static`, `slide` or `pop`. `at` and `ms` are milliseconds. `cue` is one of the stings listed in the validator.

**Limits** (so a bad file can never hurt a browser or a worker): at most 12 panels, 12 layers a panel, 6 bubbles a panel, 4 cues a panel; a bubble is 1 to 160 characters of plain text; a panel stays 2 to 20 seconds; each art piece is at most 80 KB and the bundle at most 600 KB; every number is bounded; every key is on the allow-list (an unknown key is an error, so a typo never passes silently).

## File structure

| File | Responsibility |
|---|---|
| `plugins/l3mon_story/format.py` | `validate_bundle(obj)` -> list of problems; `check_svg(text)`; the limits and the allow-lists |
| `plugins/l3mon_story/store.py` | reads the folder, validates, caches by file time; `available(slug_list)` |
| `plugins/l3mon_story/api.py` | `GET /api/v1/l3mon/story`, `GET /api/v1/l3mon/story/<slug>`, the page `GET /story/<slug>`, the player's assets |
| `plugins/l3mon_story/assets/comic.js`, `comic.css`, `sounds.js` | the player (no framework, no outside request) |
| `plugins/l3mon_story/templates/l3mon_story/story.html` | the page shell: the transcript as text, the player, the registration prompt |
| `plugins/l3mon_story/tests/story_world.py` | a made-up bundle builder and a world for the tests (no sample folder ships) |
| `plugins/l3mon_board/board.py` | `channels[].cold_open` through `hooks.cold_open` (set by this plugin) |
| `tools/l3mon/story.py` + `python -m l3mon story build SRC OUT` | compile and check a story folder (the private repo's content) |
| `tests/browser/story_player_check.mjs`, `test_story_player_browser.py` | the player in a real browser |
| `private/story/` | the bible, the art, the scripts, the build output (private repository) |

## Tasks

1. **The format and its checks** (`format.py`, `tools/l3mon/story.py`): the allow-lists; a valid sample passes; every limit, every unknown key, every forbidden SVG construct (script, style, foreignObject, image, event handler, outside `href`, `javascript:`, `data:`, entities, a DOCTYPE, a processing instruction) is refused with the field named; the same check runs in the build and at load.
2. **The store and the gate** (`store.py`, `api.py`): visitor 401; unknown, not on air, and before the start all give one 404; the crew can preview; a broken file never takes a request down (the story is simply unavailable and the log says why); ETag and 304; `private, no-cache`; nothing about a story in a public route; the board's `cold_open` count; the list of available stories.
3. **The page** (`story.html`): a visitor is sent to `/register?next=/story/<slug>`; a player gets the shell with the transcript as text.
4. **The player** (`comic.js`): a timeline with play, pause, next, previous, skip and replay; typed bubbles; layer moves; entrance effects; the static burst; a text strip when reduced motion is asked for; sound stings, off by default; keyboard (arrows, space, escape, S for sound, T for the transcript); focus kept inside while it is open and returned on close; an announcement for each panel.
5. **The art and the stories** (private repo): the bible for every channel, the characters, the scripts; compiled and checked.
6. **Integration**: the button in the demo's board (the owner can see it), the made-up sample in the preview, the registration redirect for a visitor, a stack test through nginx.
7. **Record and audit**: docs, the verification log, an independent Opus audit; each finding fixed test first.

## What changed while building (2026-10-09 and 10)

The plan was followed task by task, test first. These things were found by doing the work and are now part of the design:

1. **No sample folder ships.** The tests build their own made-up bundles (`tests/story_world.py`, `test_format.py`), and the real ones are built from the private repository. A folder of files nobody serves would only be one more thing to keep safe.
2. **The player is four small files, not three.** `timeline.js` holds the pure part (which layers and bubbles are on screen at a time `t`) so it can be tested in plain Node (10 tests) without a browser; `comic.js` is the DOM, `sounds.js` the synthesised cues (no audio file exists), `comic.css` the look. A layer can say where it turns (`pivot`).
3. **Bubbles were painted under the pictures** in the first real browser run, and the keyboard focus was lost when the button that held it was hidden. Fixed with stacking contexts in the CSS, a focus keeper, an idempotent `close`, Escape heard at the document, and a bigger hit area for the dots (2.3 rem) so a thumb can reach them. All of it is now a check in the browser run.
4. **The player is checked under the platform's own content security policy** (no inline script, no inline style, nothing from another host): the host page of the browser run sends that policy, a recorder counts every refusal (`securitypolicyviolation`), and a control shows the recorder does see a refused inline script. The run is 49 of 49 checks, including a phone width, reduced motion (a still strip, no moving pictures) and a story whose text and pictures are hostile (shown as text; nothing runs).
5. **The store has a test file of its own** (`test_store.py`, 24 tests). The first mutation run (40 deliberate breaks, one at a time) caught 28 and missed 12: seven in the format checks and five in the store. Each miss was a test that was too weak, not a missing guard: the same picture was refused by a second rule, so a broken first rule hid behind it. The fixes: every guard is now tested **for its own reason** (an exact message, with only one problem in the picture, 34 cases), the strict-JSON tests use an otherwise good bundle, and the store is tested directly with a link, a folder, a named pipe (which would make the server wait forever on `open`), an oversize file, a file with another channel's slug, a file that fails the checks (logged once), names that are not channels, a folder that is not usable, the 20-second rescan, and a name that is not a slug. After the fixes all 40 are caught.
6. **Two guards turned out to be doubled on purpose and are tested as such:** an attribute not on the allow-list is refused by the allow-list and again by the catch-all at the end of the attribute checks. A test now proves that every allowed attribute has its own rule, so the catch-all can never be the one that decides, and the exact-message cases prove the allow-list itself.
7. **Not built here, said plainly:** the button on the demo's Channels page (the real button belongs to the platform's own pages; `cold_open: {panels}` is already in the board's answer, and the preview server `private/story/tools/serve-preview.mjs` shows the real bundles in the real player); the rendered clip (route B needs a video encoder, which is not installed here); a story for the Sponsored Break (its words stay the sponsor's).
