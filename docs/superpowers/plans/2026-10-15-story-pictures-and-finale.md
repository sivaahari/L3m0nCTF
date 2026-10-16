# The painted cold opens, the finale, and the pictures on the landing page (implementation plan and record)

> Built test first, one task at a time, with the mutation checks and the independent review of the verification section.

**Goal.** Replace the cold-open comics drawn in code with the pictures a friend of the organisers painted (six frames for each of seven channels and six for the finale,
words painted into them), shown to registered players as pages they turn, with a finale that opens when the broadcast is complete, and put the opening frame of each
channel and the six frames of the test card on the public landing page.

**Owner's answers (2026-10-15).** Wait for the missing Mighty Street set (it arrived the same day); use the Chase Club pictures as they are (the organisers carry any risk of a
complaint; the note is in the private story folder); the finale plays on the platform for registered players when the on-air meter reaches 100 percent or the crew
triggers it, never on the landing page before the end; the landing page gets each channel's opening frame (setting only) in the left wing "Now showing" and the six
test-card frames as a strip, and the full comics stay behind sign-in because each channel's last frame hints at its theme.

## Decisions

1. **Format version 2, beside version 1.** The format (`plugins/l3mon_story/format.py`) accepts `v: 1` (drawn art, speech bubbles) and `v: 2` (painted pictures). A version 2 bundle has `pics` (id -> width, height, bytes, SHA-256), layers that name a picture (`pic`), the painted words again as `lines` (kind, who, text) for the transcript, a screen reader and the page without script, and `flip` (the person turns the pages). It has no `art` and no `bubbles`; version 1 has no `pics`, `lines` or `flip`; an unknown key is an error in both, as before. The drawn sources were removed from the private repository; the engine still reads version 1 (its checks and tests stay).
2. **A picture file is never named by data.** `pics.<id>` is a file `pics/<slug>/<id>.webp` in the story folder; the id is `[a-z0-9_]`, so nothing in a bundle is a path. Each picture is 320 to 4096 pixels a side and at most 400 KB, a plain still WebP.
3. **The store proves each picture.** `store.py` checks when it looks at the folder, and again when it sends a picture: the folders (`pics` and the channel's) are real folders and not links, the file is a regular file and not a link, its size is the bundle's, its SHA-256 is the bundle's, and it starts like a WebP. A story with any picture failing is not offered at all; a file swapped after the last look is never sent; unchanged pictures are not read again at every look (file times, sizes and numbers decide); names that are not ids never reach the disk.
4. **One gate for the story and its pictures.** `GET /api/v1/l3mon/story/<slug>/pic/<id>` has exactly the story's gate (a visitor 401; a channel not on air, an unknown story, an unknown picture, a malformed name and a time before the start all give the same 404; the crew can preview), answers `image/webp` with a strong tag made from the file's checksum, `private, max-age=300`, `nosniff` and `same-origin`, and 304 on a match. The page `/story/<slug>` also lists the pictures for a browser that runs no script.
5. **The finale (`finale.py`).** The story `finale` belongs to no channel. For a registered player it is available when the Guide's on-air meter, as that player sees it, reads 100 percent, or when the crew's setting `l3mon_finale_open` is on; before the start nothing opens it; the crew can preview it any time. The meter is worked out only when a finale file exists and the trigger is off, so a platform without one pays nothing. The release plan refuses a channel called `finale` (`l3mon_release/plan.py`), so no channel can be shown the finale's button. It appears in the list of stories (`GET /api/v1/l3mon/story`) when it is open; the page's button comes with the theme (SP6).
6. **The player plays a picture story as pages.** The same player (`comic.js`) shows each picture as a full-screen layer with the old slow push, the arrival effect and sound (off until chosen); a story with `flip` never advances by itself (no play button; space, the right arrow, a click or a swipe turns the page; the left arrow, Home, End, the dots and a swipe back also work); the next and the previous picture are fetched ahead of time; the window and the tag say "Finale" for the finale; the transcript and the live region carry the painted words.
7. **The build (`python -m l3mon story build`)** reads `channels/<slug>.json` and `pictures/<slug>/<id>.webp`, works out each picture's size from the file's own header, bytes and SHA-256, refuses anything that is not a plain still WebP (animations, cut-short or padded files, links, wrong sizes), copies the pictures the scripts use to `out/pics/<slug>/`, and writes nothing unless every channel passes. The checks of the bundle itself are the server's own file.
8. **The landing page** (`private/landing`): `config/site.json` lists seven channels (`showing`) and six test-card frames (`intro`), the files are in `config/frames/`, the build checks each (a plain still WebP, 320 to 1600 wide, at most 120 KB), names it from its content, publishes it under `/frames/` with a year of caching, and stops before writing anything if one is wrong. Two new sections (the test-card strip, then "Now showing" cards) and the left wing of the hero (which now lists the seven channels with their opening frames) are drawn from it. First load stays inside the 60 KB budget (37.5 KB gzip); the pictures load lazily.

## Measured

| What | Number |
|---|---|
| The friend's frames | 48 PNG of 5 to 7 MB (about 290 MB) became 48 WebP of 55 to 231 KB (6.7 MB in all) |
| The landing page's frames | 7 opening frames at 480 px (8 to 23 KB each) and 6 test-card frames at 880 px (19 to 60 KB each), 349 KB in all, all lazy |
| Landing first load | 37.5 KB gzip (it was 34.6) of the 60 KB budget |

## Tests (all written before the code they test)

- `plugins/l3mon_story/tests/test_pictures_format.py` (every refusal of the version 2 format with the place named, the good file, version 1 refusing the new keys), `test_pictures_store.py` (each refusal for its own reason, swaps and links after the look, names that never reach the disk, rescans), `test_pictures_gate.py` (who and when for a picture, the headers, the 304, the same 404, the page for a browser without script), `test_finale_gate.py` (shut, open by the trigger, open by the meter at 100 percent, not by a meter below it or a meter switched off, before the start, the crew's preview, no work done without a finale file).
- `tools/tests/test_story_pictures_build.py` (the header reader against hand-made files of every kind and a real encoder, every refusal of the build, the same bytes every time).
- `tests/browser/story_player_check.mjs` (82 checks in a real browser: the picture story as a dialog, the gated addresses and the fetch ahead, no play button, no advance by itself, button, key and swipe turns, a slip of the finger is a click, the transcript and the strip, the finale's tag and end card, nothing refused by the policy) and `story_timeline.test.mjs`.
- `private/landing/tests/frames.test.mjs` (the header reader, the config, the cards and the strip, every build refusal, no half-built site) and the landing's end-to-end run (529 of 529 checks).

## The independent review and what changed (2026-10-16)

A second model read a frozen copy cold and measured it (eleven reasons for a picture to be unavailable compared byte for byte; 200,000 fuzzed WebP headers; a real touch screen in Chrome; the landing page on four screens). **No high finding, 3 medium, 6 low, 9 notes.** The gate held (every way of asking for a picture got the same 404 as the story itself), no story data became markup, and the policy `img-src 'self'` without `data:` was enough for painted pictures. Each finding was reproduced as a failing test first, then fixed, then broken on purpose.

| # | Finding | Fix |
|---|---|---|
| M1 | A channel whose slug was `finale` was listed (and given a Cold-open button) while the finale was shut, because the channel rule listed it before the finale rule | The release plan refuses a channel called `finale`; as a second line of defence the channel rule never lists it and `panels_of('finale')` is empty |
| M2 | A finger swipe never turned the page: without `touch-action` the browser cancelled the pointer as it began to pan (the project's check only used mouse drags) | `touch-action: pan-y pinch-zoom` on the screen, only the first finger counts, and the browser check now sends real touch events (a swipe each way, a tap, a drag up, a pinch) |
| M3 | The build never emptied its output folder, so a pulled story kept being served; a failed write left it half written; the slug `nul` crashed it on Windows | The build writes into a folder beside the output and moves it in only when all of it is there (what an earlier build wrote is replaced, anything else is left alone); a failed write says so and changes nothing; names Windows keeps for devices are refused on every system; the manifest is written as bytes so it is the same on every system |
| L1 | A pipe swapped in for a picture between the look and the open made the server wait for ever, holding the store's lock | Files are opened without blocking and the opened descriptor is checked (`fstat`), for pictures and for story files |
| L2 | Ids, slugs, languages, checksums and file names accepted a final line break (`$` matches before it), and serving such a picture crashed with a 500 | The patterns end with `\Z`; tests for each |
| L3 | The finale gate built the studio's whole Guide on every request, nine times for one viewing, when everybody opens at once | The gate asks for the meter alone (`guide.meter`) and keeps the answer of a studio for five seconds (never the crew's trigger, which is read every time); the table is bounded |
| L4 | The landing page's left-wing thumbnails were fetched at once on every screen, even where the wing is hidden (155 KB on a phone) | The attribute `loading` is set before the address; a phone fetches no frame before it scrolls to one |
| L5 | A bundle's texts could start or end with invisible separators and control characters the shared rule trims | A text that the rule would trim is refused |
| L6 | The WebP checks were weaker than documented: junk, a lying chunk size, a canvas that disagrees with the picture, animation chunks with the flag off, hidden metadata; and the server never read the header | One reader (`format.webp_info`) walks the whole file for the build, the server (which now also compares the picture's size with the story's) and, ported, the landing page's build |
| Notes | Real words in public tests; the finale's page title and closed notice said "cold open"; painted words close to the frame's edge; two workers can disagree for up to 20 s | Made-up words in the public tests; wording fixed for the finale; the push over each picture is gentler (3.5 percent, no downward drift); the 20 s is accepted and documented |

Accepted and recorded, not changed: the finale opens at 99.5 percent (the meter is rounded to two places, as the Guide shows it); an existing story that is not on air costs a few more queries than an unknown name (about 0.4 ms); pictures are `private, max-age=300` with `Vary: Cookie`, so a shared campus computer relies on the browser honouring `Vary`; `OPTIONS` answers for every name.

## Not done

The finale's button on the page (SP6), a crew control for the finale trigger beyond CTFd's own settings page, and a rendered clip of a comic. The CSP of the real theme must allow images from its own address (`img-src 'self'`): the landing page already does.
