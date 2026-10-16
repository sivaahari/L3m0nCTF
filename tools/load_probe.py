#!/usr/bin/env python3
"""A local load probe for the platform (SP3 part 3.7). Standard library only.

It is a smoke test for gross mistakes, NOT the load test: the real one (k6, twice the target) needs the real hosts and belongs to SP9. It answers
"what happens to the latency and the error count when a few hundred players poll, refetch and send flags at the same time on this machine?".

It makes players, challenges and a plan on the stack it is pointed at, so point it only at a throwaway stack (`tools/compose.sh down -v` afterwards), never
at the real one (it refuses an address that is not a stack on this machine, a plan with channels it did not make, and a platform with many other accounts).
Everything it makes is named with the prefix (default `zzprobe`) and `--cleanup` removes it.

What the simulated players do, like the pages will:
  * every player polls GET /api/v1/l3mon/ticks every --poll seconds (default 15, with jitter);
  * when the tick, the news or the player's own count moved, the player fetches the board and the Guide (and now and then the scoreboard rows);
  * --flags submissions a second (default 20) go out from random players: almost all wrong, one in --solve-every (default 40) correct, which moves the
    tick for everybody, runs a recalculation of a dynamic challenge and makes the other players refetch.

Run it from a container on the stack's internal network, so that nginx's per-address limits (100 requests a second for one address) do not stop it:

    tools/load_probe_run.sh            (see the script; it needs the stack up)

or by hand: python tools/load_probe.py --base http://ctfd:8000 --token-file /run/secrets/PRESET_ADMIN_TOKEN --players 300 --seconds 60
"""
import argparse
import http.cookiejar
import ipaddress
import json
import random
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# The names a throwaway stack answers to. The probe makes players, challenges and a plan, and replaces the plan that is there: it must never be pointed at a
# platform that is not a throwaway one, so it refuses an address that is not the compose network's service name, "localhost" or a private address.
THROWAWAY_NAMES = ("ctfd", "localhost", "nginx")


def check_target(base: str) -> str:
    """-> the base address, or raises SystemExit when it does not look like a throwaway stack on this machine or a private network."""
    parts = urllib.parse.urlsplit(base)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http",) or not host:
        raise SystemExit(f"refusing {base!r}: the probe talks plain http to a stack on this machine (http://ctfd:8000)")
    if host in THROWAWAY_NAMES:
        return base
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise SystemExit(f"refusing {base!r}: {host!r} is not a throwaway stack's name (ctfd, localhost) or a private address") from None
    if not (address.is_private or address.is_loopback):
        raise SystemExit(f"refusing {base!r}: {host} is a public address")
    return base

NONCE = re.compile(r"""csrfNonce['"]?\s*[:=]\s*['"]([0-9a-f]+)['"]|name=["']nonce["'][^>]*value=["']([0-9a-f]+)["']|value=["']([0-9a-f]+)["'][^>]*name=["']nonce["']""")
CHANNELS = ["street", "snack", "gadget", "ninja", "chase", "cubcop"]
PREFIX = re.compile(r"[a-z][a-z0-9]{3,11}\Z")


def check_prefix(prefix: str) -> str:
    """The prefix every name and slug the probe makes starts with. Unusual on purpose: `--cleanup` deletes by it."""
    if not PREFIX.match(prefix) or prefix in ("admin", "test", "user", "demo", "street", "snack"):
        raise SystemExit(f"refusing the prefix {prefix!r}: use 4 to 12 lower-case letters and digits that no real account or channel starts with")
    return prefix


def channel_slug(prefix: str, name: str) -> str:
    return f"{prefix}-{name}"


def foreign_channels(view_channels, prefix: str) -> list:
    """The slugs of channels in the platform's plan that the probe did not make (it replaces the plan, so any means: refuse)."""
    return [c.get("slug") for c in view_channels if not str(c.get("slug", "")).startswith(prefix + "-")]


def release_changes(view_channels, prefix: str) -> list:
    """Release only what the probe made: its channels and the programmes in them."""
    changes = []
    for channel in view_channels:
        if str(channel.get("slug", "")).startswith(prefix + "-"):
            changes.append({"kind": "channel", "id": channel["id"], "mode": "release"})
            changes += [{"kind": "programme", "id": p["id"], "mode": "release"} for p in channel.get("programmes", [])]
    return changes


def preflight(args, admin, log):
    """Refuse to go on when this does not look like a throwaway stack: the plan holds a channel the probe did not make, or there are many accounts it did
    not make. (The run script has checked the container already; this is the second line, for a probe started by hand.)"""
    status, view = admin.json("GET", "/api/v1/l3mon/admin/release")
    if status != 200 or not isinstance(view, dict):
        raise SystemExit(f"the plan could not be read ({status}): is the admin token right and the stack up?")
    foreign = foreign_channels(view["data"]["channels"], args.prefix)
    if foreign:
        raise SystemExit(f"refusing: the plan already holds channels the probe did not make ({', '.join(map(str, foreign[:5]))}); it replaces the plan. Use a throwaway stack")
    status, everyone = admin.json("GET", "/api/v1/users?page=1&per_page=1")
    status_own, own = admin.json("GET", f"/api/v1/users?field=name&q={args.prefix}-&page=1&per_page=1")
    try:
        others = everyone["meta"]["pagination"]["total"] - own["meta"]["pagination"]["total"]
    except (TypeError, KeyError):
        raise SystemExit("the accounts could not be counted: is the admin token right?") from None
    if others > args.max_other_accounts:
        raise SystemExit(f"refusing: {others} accounts the probe did not make (more than --max-other-accounts {args.max_other_accounts}); this is not a throwaway stack")
    log(f"preflight: no foreign channels, {others} other accounts")


class Http:
    """One browser: its own cookies, optional admin token."""

    def __init__(self, base, token=None):
        self.base = base.rstrip("/")
        self.token = token
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect())
        self.nonce = None

    def call(self, method, path, body=None, form=None, headers=None, timeout=30):
        """-> (status, bytes, seconds). An HTTP error status is an answer, not an exception; a network failure is status 0."""
        data, head = None, {"Host": "play.l3m0nctf.xyz"}
        if self.token:
            head["Authorization"] = f"Token {self.token}"
            head["Content-Type"] = "application/json"  # CTFd takes a token only with this header, even for a read
        if body is not None:
            data, head["Content-Type"] = json.dumps(body).encode(), "application/json"
        elif form is not None:
            data, head["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
        if self.nonce and method != "GET":
            head["CSRF-Token"] = self.nonce
        head.update(headers or {})
        request = urllib.request.Request(self.base + path, data=data, headers=head, method=method)
        started = time.perf_counter()
        try:
            with self.opener.open(request, timeout=timeout) as answer:
                return answer.status, answer.read(), time.perf_counter() - started
        except urllib.error.HTTPError as error:
            return error.code, error.read(), time.perf_counter() - started
        except (urllib.error.URLError, TimeoutError, OSError):
            return 0, b"", time.perf_counter() - started

    def json(self, method, path, body=None):
        status, raw, _ = self.call(method, path, body=body)
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, None

    def page_nonce(self, path):
        _, raw, _ = self.call("GET", path)
        found = NONCE.search(raw.decode("utf-8", "replace"))
        return next((g for g in found.groups() if g), None) if found else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Numbers:
    """Latencies and errors by label, safe to add to from many threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rows = {}
        self.samples = {}

    def add(self, label, status, seconds, good=(200, 304), body=b""):
        with self.lock:
            row = self.rows.setdefault(label, {"n": 0, "bad": 0, "seconds": [], "statuses": {}})
            row["n"] += 1
            row["seconds"].append(seconds)
            row["statuses"][status] = row["statuses"].get(status, 0) + 1
            if status not in good:
                row["bad"] += 1
                self.samples.setdefault((label, status), body[:200].decode("utf-8", "replace"))  # the first answer of each kind that was not good

    def report(self):
        out = {}
        for label, row in sorted(self.rows.items()):
            ms = sorted(s * 1000 for s in row["seconds"])
            pick = lambda q: ms[min(len(ms) - 1, int(q * len(ms)))]
            out[label] = {
                "n": row["n"], "errors": row["bad"], "p50_ms": round(statistics.median(ms), 1), "p95_ms": round(pick(0.95), 1),
                "p99_ms": round(pick(0.99), 1), "max_ms": round(ms[-1], 1), "statuses": dict(sorted(row["statuses"].items())),
            }
        return out


def setup(args, admin, log):
    """Challenges, the plan, the release, players and studios. -> (challenge ids, flags by id, players [(user_id, name, password)])."""
    ids, flags = [], {}
    for n in range(args.programmes):
        dynamic = n % 4 == 0
        body = {"name": f"{args.prefix}-prog-{n}", "category": random.choice(["web", "pwn", "crypto", "misc"]), "description": "probe", "state": "visible",
                "type": "dynamic" if dynamic else "standard", "value": 500 if dynamic else 100 + 10 * (n % 20)}
        if dynamic:
            body.update({"initial": 500, "minimum": 100, "decay": 30, "function": "logarithmic"})
        status, out = admin.json("POST", "/api/v1/challenges", body)
        assert status == 200 and out and out.get("success"), (status, out)
        cid = out["data"]["id"]
        flag = f"{args.prefix}-flag-{n}"
        status, out = admin.json("POST", "/api/v1/flags", {"challenge_id": cid, "content": flag, "type": "static"})
        assert status == 200, (status, out)
        ids.append(cid)
        flags[cid] = flag
    channels = [{"slug": channel_slug(args.prefix, slug), "name": slug.title(), "position": 1 + i, "accent": f"ch{1 + (i % 6)}", "picture_key": slug, "synopsis": "A made-up channel."} for i, slug in enumerate(CHANNELS)]
    programmes = [{"challenge_id": cid, "channel": channel_slug(args.prefix, CHANNELS[n % len(CHANNELS)]), "cell": n, "number": 100 + n, "slug": f"{args.prefix}_p{n}", "difficulty": "easy"} for n, cid in enumerate(ids)]
    status, out = admin.json("PUT", "/api/v1/l3mon/admin/programmes", {"channels": channels, "programmes": programmes})
    assert status == 200, (status, out)
    status, view = admin.json("GET", "/api/v1/l3mon/admin/release")
    assert status == 200, (status, view)
    changes = release_changes(view["data"]["channels"], args.prefix)
    status, out = admin.json("PUT", "/api/v1/l3mon/admin/release", {"changes": changes})
    assert status == 200, (status, out)
    log(f"plan made: {len(ids)} programmes on {len(channels)} channels, all on air")

    players, team_of, size = [], {}, args.team_size
    for n in range(args.players):
        name = f"{args.prefix}-p{n}"
        password = f"pw-{random.getrandbits(48):x}"
        status, out = admin.json("POST", "/api/v1/users", {"name": name, "email": f"{name}@example.com", "password": password, "type": "user", "verified": True})
        assert status == 200 and out.get("success"), (status, out)
        players.append((out["data"]["id"], name, password))
    for first in range(0, len(players), size):
        group = players[first : first + size]
        status, out = admin.json("POST", "/api/v1/teams", {"name": f"{args.prefix}-team-{first // size}", "email": f"{args.prefix}-t{first // size}@example.com", "password": f"pw-{random.getrandbits(48):x}"})
        assert status == 200 and out.get("success"), (status, out)
        team_id = out["data"]["id"]
        for user_id, _, _ in group:
            status, out = admin.json("POST", f"/api/v1/teams/{team_id}/members", {"user_id": user_id})
            assert status == 200, (status, out)
        status, out = admin.json("PATCH", f"/api/v1/teams/{team_id}", {"captain_id": group[0][0]})
        assert status == 200, (status, out)
    log(f"{len(players)} players in {-(-len(players) // size)} studios made")
    return ids, flags, players


def sign_in(args, players, log):
    """Sign every player in, slowly enough for CTFd's own limit on sign-ins (ten a five seconds for one address). -> [Http]"""
    sessions = []
    for n, (_, name, password) in enumerate(players):
        browser = Http(args.base)
        for attempt in range(8):
            nonce = browser.page_nonce("/login")
            status, _, _ = browser.call("POST", "/login", form={"name": name, "password": password, "_submit": "Submit", "nonce": nonce})
            if status != 429:
                break
            time.sleep(3)  # CTFd's own limit on sign-ins from one address
        if status != 302:
            raise SystemExit(f"sign-in of {name} answered {status}")
        browser.nonce = browser.page_nonce("/challenges")
        sessions.append(browser)
        time.sleep(0.55)
        if n and n % 50 == 0:
            log(f"{n} signed in")
    return sessions


def player_loop(browser, args, numbers, stop, flags_by_player):
    last = None
    changes = 0
    time.sleep(random.uniform(0, args.poll))
    while not stop.is_set():
        status, raw, seconds = browser.call("GET", "/api/v1/l3mon/ticks")
        numbers.add("ticks", status, seconds)
        moved = False
        if status == 200:
            try:
                now = json.loads(raw)["data"]
                key = (now["ver"], now["notif_ver"], now["own"])
                moved = key != last
                last = key
            except (ValueError, KeyError, TypeError):
                numbers.add("ticks-bad-body", 0, seconds)
        if moved:
            changes += 1
            for label, path in (("board", "/api/v1/l3mon/board"), ("guide", "/api/v1/l3mon/guide")):
                status, _, seconds = browser.call("GET", path)
                numbers.add(label, status, seconds)
            if changes % 3 == 0:
                for label, path in (("scoreboard", "/api/v1/l3mon/scoreboard"), ("scoreboard/rows", "/api/v1/l3mon/scoreboard/rows")):
                    status, _, seconds = browser.call("GET", path)
                    numbers.add(label, status, seconds)
        stop.wait(max(1.0, args.poll + random.uniform(-0.2, 0.2) * args.poll))


def flag_loop(sessions, ids, flags, args, numbers, stop, solved, mine):
    """About --flags submissions a second between all the threads of this kind."""
    per_thread = args.flags / args.flag_threads
    while not stop.is_set():
        started = time.perf_counter()
        n = random.randrange(len(sessions))
        browser = sessions[n]
        cid = random.choice(ids)
        right = random.randrange(args.solve_every) == 0
        with mine:
            if right and (n, cid) in solved:
                right = False
            if right:
                solved.add((n, cid))
        submission = flags[cid] if right else f"wrong-{random.getrandbits(32):x}"
        status, raw, seconds = browser.call("POST", "/api/v1/challenges/attempt", body={"challenge_id": cid, "submission": submission})
        numbers.add("flag-right" if right else "flag-wrong", status, seconds, good=(200,), body=raw)
        stop.wait(max(0.0, 1.0 / per_thread - (time.perf_counter() - started)))


HAMMER = {
    "ticks": "/api/v1/l3mon/ticks", "board": "/api/v1/l3mon/board", "guide": "/api/v1/l3mon/guide", "guide/epg": "/api/v1/l3mon/guide/epg",
    "scoreboard": "/api/v1/l3mon/scoreboard", "scoreboard/rows": "/api/v1/l3mon/scoreboard/rows", "bell": "/api/v1/notifications?since_id=0",
    "stories": "/api/v1/l3mon/story",
    # what stock CTFd costs, for comparison: the health route (no account), the signed-in player's own record, CTFd's own challenge list and scoreboard
    "stock/healthz": "/l3mon/healthz", "stock/me": "/api/v1/users/me", "stock/challenges": "/api/v1/challenges", "stock/scoreboard": "/api/v1/scoreboard",
}


def hammer(args, sessions, log):
    """One endpoint at a time, as fast as the players can ask: requests a second and latencies. A script watching the server's CPU between the two MARK
    lines of each endpoint gets the CPU a request costs."""
    for name in [n.strip() for n in args.hammer.split(",") if n.strip()]:
        path = HAMMER[name]
        numbers, stop = Numbers(), threading.Event()

        def loop(browser):
            while not stop.is_set():
                status, _, seconds = browser.call("GET", path)
                numbers.add(name, status, seconds)

        threads = [threading.Thread(target=loop, args=(s,), daemon=True) for s in sessions]
        began = time.time()
        log(f"MARK {name} start {began:.3f}")
        for thread in threads:
            thread.start()
        time.sleep(args.seconds)
        stop.set()
        for thread in threads:
            thread.join(timeout=35)
        ended = time.time()
        log(f"MARK {name} end {ended:.3f}")
        row = numbers.report()[name]
        log(f"HAMMER {name} requests {row['n']} seconds {ended - began:.2f} errors {row['errors']} p50 {row['p50_ms']} p95 {row['p95_ms']} p99 {row['p99_ms']}")


def cleanup(args, admin, log):
    """Delete what a run made: the players and studios (by name) and the challenges (their programmes go with them). The first page is read again after
    every round of deletions, because deleting moves the rows up."""
    gone = 0
    for path in ("/api/v1/users", "/api/v1/teams", "/api/v1/challenges?view=admin"):
        base = path.split("?")[0]
        joiner = "&" if "?" in path else "?"
        for _ in range(200):
            status, out = admin.json("GET", f"{path}{joiner}field=name&q={args.prefix}-&page=1&per_page=50")
            rows = [row for row in ((out or {}).get("data") or []) if str(row.get("name", "")).startswith(args.prefix + "-")]
            if not rows:
                break
            for row in rows:
                admin.json("DELETE", f"{base}/{row['id']}")
                gone += 1
    log(f"cleanup: {gone} rows removed")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", required=True, help="the platform's address as the probe reaches it, e.g. http://ctfd:8000")
    parser.add_argument("--token-file", required=True, help="a file holding the preset admin token")
    parser.add_argument("--players", type=int, default=300)
    parser.add_argument("--team-size", type=int, default=3)
    parser.add_argument("--programmes", type=int, default=60)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--poll", type=float, default=15.0, help="seconds between a player's polls of the tick")
    parser.add_argument("--flags", type=float, default=20.0, help="flag submissions a second, all together")
    parser.add_argument("--flag-threads", type=int, default=8)
    parser.add_argument("--solve-every", type=int, default=40, help="one submission in this many is the right flag")
    parser.add_argument("--hammer", default="", help="instead of the event's mix: comma-separated endpoints (ticks, board, guide, guide/epg, scoreboard, scoreboard/rows, bell, stories, and for comparison stock/healthz, stock/me, stock/challenges, stock/scoreboard), "
                        "each asked as fast as --players players can for --seconds, to find what one request of each costs and how many a second the machine gives")
    parser.add_argument("--prefix", default="zzprobe", help="what every name and slug the probe makes starts with (4 to 12 lower-case letters and digits)")
    parser.add_argument("--max-other-accounts", type=int, default=25, help="refuse to run when the platform has more accounts than this that the probe did not make")
    parser.add_argument("--cleanup", action="store_true", help="remove what an earlier run made, and do nothing else")
    parser.add_argument("--json", action="store_true", help="print the result as JSON only")
    args = parser.parse_args()
    check_target(args.base)
    check_prefix(args.prefix)
    log = (lambda *_: None) if args.json else (lambda text: print(text, file=sys.stderr, flush=True))
    token = open(args.token_file, encoding="utf-8").read().strip()
    admin = Http(args.base, token)
    if args.cleanup:
        cleanup(args, admin, log)
        return
    preflight(args, admin, log)
    cleanup(args, admin, log)  # whatever an earlier run left under the same prefix
    ids, flags, players = setup(args, admin, log)
    sessions = sign_in(args, players, log)
    if args.hammer:
        for n, browser in enumerate(sessions):  # every studio gets something on the scoreboard first: two right flags for each player
            for k in range(2):
                cid = ids[(n * 2 + k) % len(ids)]
                browser.call("POST", "/api/v1/challenges/attempt", body={"challenge_id": cid, "submission": flags[cid]})
        log(f"{len(sessions)} players have solved something")
        hammer(args, sessions, log)
        return
    numbers, stop = Numbers(), threading.Event()
    solved, mine = set(), threading.Lock()
    threads = [threading.Thread(target=player_loop, args=(s, args, numbers, stop, None), daemon=True) for s in sessions]
    threads += [threading.Thread(target=flag_loop, args=(sessions, ids, flags, args, numbers, stop, solved, mine), daemon=True) for _ in range(args.flag_threads)]
    log(f"load for {args.seconds} s: {len(sessions)} players polling every {args.poll:g} s, {args.flags:g} flags a second")
    started = time.time()
    log(f"MARK start {started:.3f}")  # so that a script watching the server's CPU can tell the load from the set-up
    for thread in threads:
        thread.start()
    time.sleep(args.seconds)
    stop.set()
    for thread in threads:
        thread.join(timeout=35)
    elapsed = time.time() - started
    log(f"MARK end {time.time():.3f}")
    report = numbers.report()
    total = sum(row["n"] for row in report.values())
    result = {"players": len(sessions), "programmes": len(ids), "seconds": round(elapsed, 1), "requests": total, "requests_per_second": round(total / elapsed, 1), "endpoints": report}
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(f"\n{result['players']} players, {result['programmes']} programmes, {result['seconds']} s: {total} requests, {result['requests_per_second']} a second")
        print(f"{'endpoint':18} {'n':>6} {'err':>5} {'p50':>8} {'p95':>8} {'p99':>8} {'max':>8}  statuses")
        for label, row in report.items():
            print(f"{label:18} {row['n']:>6} {row['errors']:>5} {row['p50_ms']:>8} {row['p95_ms']:>8} {row['p99_ms']:>8} {row['max_ms']:>8}  {row['statuses']}")
        for (label, status), text in sorted(numbers.samples.items()):
            print(f"  first {status} of {label}: {text!r}")


if __name__ == "__main__":
    main()
