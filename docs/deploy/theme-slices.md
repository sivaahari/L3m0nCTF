# The CTFd theme, page by page

The platform's own look (the approved design) reaches CTFd through one theme folder, `ui/theme/l3mon`. The design lives in a private
repository, **`L3m0nCTF-ui`** (UI only: no challenges, no story; since 22 October 2026 it is the home of the prototype), and its
prototype builds the pages. A script there converts the prototype's finished templates and bundles into the theme. Nothing is typed
by hand except a few small adapter files and the plugin's page modules.

## How it works

- **The exporter** (`platform-ui/tools/export-theme.mjs`, in the `L3m0nCTF-ui` repository) builds the prototype into a scratch
  folder, converts the templates of the registered pages from the prototype's small template language to Jinja, copies the
  hashed bundles those pages use, and writes:
  - `ui/theme/l3mon/templates/_<page>.html`, the converted page, with a first line that says it is generated;
  - `ui/theme/l3mon/static/` the hashed bundles, and `bundles.json` which names them. The file is not called `manifest.json`:
    CTFd reads that name as its own asset manifest for the theme, and every core page would fail if ours had it.
  It refuses, and writes nothing, if a template uses anything outside the subset, an inline script or style, an event attribute,
  a `javascript:` address, or a page it cannot find.
- **The adapters** (hand-written, in `ui/theme/l3mon/templates/`): `login.html` for the sign-in page, and `errors/<code>.html`
  for the error codes. Each sets the values the converted page reads (from CTFd's request and session, and from the theme plugin),
  then includes the converted page. Adapters hold no page markup.
- **The plugin** (`plugins/l3mon_theme/`) gives the theme the values only CTFd knows: the bundle addresses, the sign-in state of
  the viewer, the CTFtime sign-in switch, the event name, the build, the event website's address (`site_url`) and the route table
  (`routes`). Each is read only when a page of ours asks for it, so a page that is not ours costs no database statement. **A page
  adds its own values and hooks in its own module, `plugins/l3mon_theme/pages/<page>.py`** (`registry.value`, `install(app)`):
  the plugin imports every module of that folder by itself, so nobody edits a shared file. `values.py` (the shared values) and
  `registry.py` belong to the lead engineer. `pages/login.py` carries a sign-in's `next` address from the form into the address,
  where CTFd reads it.
- **CTFd's fallback**: a template the theme does not have is taken from CTFd's own `core` theme (`THEME_FALLBACK`, on by default).
  So the theme can grow one page at a time.
- **The image**: `docker/ctfd/Dockerfile` copies `ui/theme/l3mon/` into `/opt/CTFd/CTFd/themes/l3mon/` and the plugins into the
  image. Nothing else is needed.

## How to regenerate

From the `L3m0nCTF-ui` repository, with the working tree you want to export **committed** (the first line of each generated file
names the commit it was made from):

```bash
cd L3m0nCTF-ui/platform-ui
node tools/export-theme.mjs --out ../../L3m0nCTF/ui/theme/l3mon
```

Run it twice and compare: the two runs must match byte for byte (`git diff --stat` shows the result, and the exporter's own test
checks it).

**Generated files and merges.** The hashed bundles and `bundles.json` change whenever any page's style or script changes, so two
branches that both regenerate will conflict in them. That is expected. **Resolve such a conflict by regenerating, never by hand:**
merge the `L3m0nCTF-ui` branches first, then run the exporter on the merged result and commit its output.

## How to add the next page

1. In `L3m0nCTF-ui`: add `platform-ui/theme-pages/<page>.json` (`{"page": "<page>", "css": "css.app", "js": "js.<page>"}`: one
   file per page, no shared list), the page's template and scripts as the prototype has them, and the exporter's equivalence test
   for the page (in `platform-ui/tests/export-theme.test.mjs` or a new test file of its own).
2. If the page needs new values from CTFd, write them in `plugins/l3mon_theme/pages/<page>.py` with `@registry.value(...)`
   (lazily, with a safe default), and document the keys in the prototype's `docs/template-contract.md`.
3. Write the adapter in `ui/theme/l3mon/templates/` under the name CTFd uses for that page (for example `register.html`).
   Links go through `l3.routes` and `l3.site_url`, never typed paths of pages that do not exist yet.
4. Add tests: the exporter's equivalence test, a Python test for the page through CTFd (in `plugins/l3mon_theme/tests/`), and an
   integration check (in `tests/integration/test_theme.py`).
5. Regenerate, run the tests below, and only then rebuild the image.

## What must be checked before the generated files are committed

- The exporter's tests pass: `cd L3m0nCTF-ui/platform-ui && node --test --test-concurrency=1 tests/export-theme.test.mjs`.
- The prototype's own suite still passes: `node --test --test-concurrency=1 "tests/*.test.mjs"`.
- The theme's Python tests pass, with the working-tree theme and plugins mounted (no image rebuild needed):

  ```bash
  env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme
  ```
- The generated files regenerate identically (run the exporter twice and compare).
- No text on a platform page says "prototype", "stand-in", "mock" or "DIRECTOR" (the Python tests check this).
- The sign-in page and the error pages have no inline script, style, or event attribute (the Python tests check this).

## What is not in this slice

Pages other than the sign-in page and the error codes 403, 404, 429 and 500 still come from CTFd's own theme. Later slices add their
pages through the same steps above.
