"""The programme grid: a TV programme guide as markup (SP3 part 3.5), the body of `GET /api/v1/l3mon/guide/epg`.

A row for each channel, a column for each difficulty (warm-up to insane), a block for each programme the viewer may see: its name, what it is
worth now, filled in when the studio has solved it, a link to its page. A programme the viewer may not see (not released yet, pulled back, or
waiting for a prerequisite) is **not in it at all**: the channel's row only says how many are still to come, and not even that when the crew
has switched the count off (`l3mon_show_coming_count`). Before the start there is no grid.

The grid is first made as plain data (`model`), whose signature (`signature`) names what this studio sees. The Guide carries it as `epg_sig`
and the open page fetches the grid again only when it changes. The markup is made from `templates/l3mon_board/epg_row.html` with
autoescaping on, so a name is text whatever it holds.
"""
import base64
import hashlib
import json

from CTFd.plugins.l3mon_board import markup
from CTFd.plugins.l3mon_core.settings import show_coming_count

TIERS = (("warmup", "Warm-up"), ("easy", "Easy"), ("medium", "Medium"), ("hard", "Hard"), ("insane", "Insane"))
TIER = {key: (number, label) for number, (key, label) in enumerate(TIERS, start=1)}
CATEGORY_LABELS = {
    "web": "Web", "crypto": "Crypto", "forensics": "Forensics", "osint": "OSINT", "pwn": "Pwn", "reverse": "Reverse", "hardware": "Hardware",
    "rf": "RF", "ai": "AI", "web3": "Web3", "misc": "Misc",
}  # a category the list does not know is spoken as it is written


def model(plan, viewer):
    """-> None before the start; otherwise one dict for each channel: id, no (its number), name, sponsor, coming, blocks."""
    if plan.before:
        return None
    coming_counted = show_coming_count()
    out = []
    for channel in plan.channels:
        every = plan.by_channel.get(channel.id, [])
        blocks = [
            {
                "slug": programme.slug, "name": challenge.name, "category": challenge.category, "difficulty": programme.difficulty,
                "value": int(challenge.value or 0), "solved": challenge.id in viewer.solved,
            }
            for programme, challenge in every
            if challenge.id in plan.shown
        ]
        out.append({
            "id": channel.id, "no": channel.position, "name": channel.name,
            "sponsor": (channel.sponsor_name or "") if channel.kind == "sponsored" else "",
            "coming": (len(every) - len(blocks)) if coming_counted else 0, "blocks": blocks,
        })
    return out


def signature(grid) -> str:
    """16 characters that change when the grid does for this viewer, or `none` before the start."""
    if grid is None:
        return "none"
    raw = json.dumps(grid, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")[:16]


def _label(block) -> str:
    """The words a block says aloud: "Hush Alley, Forensics, hard, 300 TRP, not solved"."""
    _, tier = TIER.get(block["difficulty"], (3, block["difficulty"]))
    category = CATEGORY_LABELS.get(block["category"], block["category"])
    return f"{block['name']}, {category}, {tier.lower()}, {block['value']} TRP, {'solved' if block['solved'] else 'not solved'}"


def html(grid) -> str:
    """The channel rows one after another; empty before the start."""
    if grid is None:
        return ""
    rows = []
    for channel in grid:
        tiers = {number: [] for number in range(1, len(TIERS) + 1)}
        for block in channel["blocks"]:
            number = TIER.get(block["difficulty"], (3, ""))[0]
            tiers[number].append({
                "slug": block["slug"], "name": block["name"], "value": block["value"], "cls": "is-solved" if block["solved"] else "is-open", "label": _label(block),
            })
        shown = len(channel["blocks"])
        rows.append(markup.render(
            "epg_row.html",
            row={
                "id": channel["id"], "class_no": channel["no"], "no": f"{channel['no']:02d}", "name": channel["name"],
                "meta": f"{shown} on air" if shown else "Off air for now",
                "soon": f"{channel['coming']} coming up" if channel["coming"] > 0 else "",
                "ad": f"Sponsored by {channel['sponsor']}" if channel["sponsor"] else "",
                "t1": tiers[1], "t2": tiers[2], "t3": tiers[3], "t4": tiers[4], "t5": tiers[5],
            },
        ))
    return "".join(rows)
