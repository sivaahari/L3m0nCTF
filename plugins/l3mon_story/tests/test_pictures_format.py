"""The picture stories' file format (version 2): a comic made of painted pictures whose words are part of the picture.

A version 2 bundle carries no art and no speech bubbles. It names its pictures (`pics`: id -> size, bytes and checksum; the file itself is
`<id>.webp` in the channel's own folder, so nothing in the data is ever a path), shows each in a layer (`pic`), and writes the painted words
again as plain text (`lines`) for the transcript, a screen reader and the page that needs no script. `flip` says the viewer turns the pages.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_story
"""
import copy
import json

import pytest

from CTFd.plugins.l3mon_story import format as fmt

SHA = "a" * 64


def pic(n=1):
    return {"w": 1600, "h": 893, "bytes": 120_000 + n, "sha256": f"{n:x}" * 64 if n < 16 else SHA}


def bundle(panels=2):
    return {
        "v": 2, "slug": "street", "title": "Moth Hour", "kicker": "CH 01 · Street", "lang": "en", "flip": True,
        "panels": [
            {
                "id": f"p{i + 1}", "ms": 9000, "enter": "static" if i == 0 else "slide", "alt": f"Picture {i + 1}: a round sun over a dark field.",
                "layers": [{"pic": f"p{i + 1}", "x": 0, "y": 0, "w": 100, "h": 100, "z": 0, "from": {"x": 0, "y": 0, "s": 1.0}, "to": {"x": -1, "y": 0, "s": 1.05}}],
                "lines": [
                    {"kind": "caption", "text": "A round sun, low."},
                    {"kind": "say", "who": "Host", "text": "Good evening, crew."},
                ],
                "sfx": [{"cue": "whoosh", "at": 0}],
            }
            for i in range(panels)
        ],
        "pics": {f"p{i + 1}": pic(i + 1) for i in range(panels)},
    }


def problems(obj):
    return [str(p) for p in fmt.validate_bundle(obj)]


def mutate(fn, panels=2):
    obj = copy.deepcopy(bundle(panels))
    fn(obj)
    return obj


def test_a_good_picture_bundle_has_no_problems():
    assert fmt.validate_bundle(bundle()) == []


def test_a_good_picture_bundle_survives_strict_json():
    obj, found = fmt.load_bundle(json.dumps(bundle()))
    assert found == [] and obj == bundle()


def test_the_flip_flag_is_optional():
    obj = bundle()
    del obj["flip"]
    assert fmt.validate_bundle(obj) == []


def test_the_painted_words_are_optional():
    obj = bundle()
    for panel in obj["panels"]:
        del panel["lines"]
    assert fmt.validate_bundle(obj) == []


REFUSALS = [
    ("a version nobody defined", lambda b: b.update(v=3), "v"),
    ("a version that is a boolean", lambda b: b.update(v=True), "v"),
    ("a version that is the drawn one", lambda b: b.update(v=1), "pics"),
    ("art in a picture bundle", lambda b: b.update(art={"x": "<svg/>"}), "art"),
    ("no pictures", lambda b: b.pop("pics"), "pics"),
    ("an empty list of pictures", lambda b: b.update(pics={}), "pics"),
    ("pictures that are not an object", lambda b: b.update(pics=[]), "pics"),
    ("too many pictures", lambda b: b.update(pics={f"q{i}": pic(1) for i in range(13)}), "at most 12 pictures"),
    ("a picture id with a path in it", lambda b: b["pics"].update(**{"../x": pic(1)}), "the id must be lower-case letters, digits and underscores"),
    ("a picture id with capitals", lambda b: b["pics"].update(P9=pic(1)), "the id must be lower-case letters, digits and underscores"),
    ("a picture that is not an object", lambda b: b["pics"].update(p1="x.webp"), "pics.p1"),
    ("a picture with a file name of its own", lambda b: b["pics"]["p1"].update(file="../../etc/passwd"), "pics.p1.file"),
    ("a picture with an unknown key", lambda b: b["pics"]["p1"].update(url="http://x"), "pics.p1.url"),
    ("a picture without a checksum", lambda b: b["pics"]["p1"].pop("sha256"), "pics.p1.sha256"),
    ("a checksum in capitals", lambda b: b["pics"]["p1"].update(sha256="A" * 64), "pics.p1.sha256"),
    ("a checksum that is too short", lambda b: b["pics"]["p1"].update(sha256="a" * 63), "pics.p1.sha256"),
    ("a checksum with a non-hex letter", lambda b: b["pics"]["p1"].update(sha256="g" * 64), "pics.p1.sha256"),
    ("a checksum that is not text", lambda b: b["pics"]["p1"].update(sha256=12), "pics.p1.sha256"),
    ("a picture with no size in bytes", lambda b: b["pics"]["p1"].pop("bytes"), "pics.p1.bytes"),
    ("a picture over the size limit", lambda b: b["pics"]["p1"].update(bytes=fmt.LIMITS["pic_bytes"] + 1), "pics.p1.bytes"),
    ("a picture of no bytes", lambda b: b["pics"]["p1"].update(bytes=0), "pics.p1.bytes"),
    ("a size in bytes that is not whole", lambda b: b["pics"]["p1"].update(bytes=1000.5), "pics.p1.bytes"),
    ("a size in bytes that is a boolean", lambda b: b["pics"]["p1"].update(bytes=True), "pics.p1.bytes"),
    ("a width that is too small", lambda b: b["pics"]["p1"].update(w=100), "pics.p1.w"),
    ("a width that is huge", lambda b: b["pics"]["p1"].update(w=100000), "pics.p1.w"),
    ("a width that is not whole", lambda b: b["pics"]["p1"].update(w=640.5), "pics.p1.w: must be a whole number"),
    ("a height that is not whole", lambda b: b["pics"]["p1"].update(h=360.5), "pics.p1.h: must be a whole number"),
    ("a width that is a boolean", lambda b: b["pics"]["p1"].update(w=True), "pics.p1.w: must be a whole number"),
    ("a height that is missing", lambda b: b["pics"]["p1"].pop("h"), "pics.p1.h"),
    ("a height that is not a number", lambda b: b["pics"]["p1"].update(h="893"), "pics.p1.h"),
    ("a layer without a picture", lambda b: b["panels"][0]["layers"][0].pop("pic"), "panels[0].layers[0].pic"),
    ("a layer that points at nothing", lambda b: b["panels"][0]["layers"][0].update(pic="ghost"), "panels[0].layers[0].pic"),
    ("a layer picture id with a path in it", lambda b: b["panels"][0]["layers"][0].update(pic="../p1"), "panels[0].layers[0].pic"),
    ("a layer picture id that is not text", lambda b: b["panels"][0]["layers"][0].update(pic=1), "panels[0].layers[0].pic"),
    ("a layer that also names art", lambda b: b["panels"][0]["layers"][0].update(art="x"), "panels[0].layers[0].art"),
    ("a picture that no layer shows", lambda b: b["pics"].update(spare=pic(2)), "pics.spare"),
    ("speech bubbles", lambda b: b["panels"][0].update(bubbles=[{"kind": "say", "text": "hi", "x": 1, "y": 1, "w": 20}]), "panels[0].bubbles"),
    ("a flip that is not true or false", lambda b: b.update(flip="yes"), "flip"),
    ("lines that are not a list", lambda b: b["panels"][0].update(lines="hello"), "panels[0].lines"),
    ("too many lines", lambda b: b["panels"][0].update(lines=[{"kind": "say", "text": "x"}] * 9), "panels[0].lines"),
    ("a line that is not an object", lambda b: b["panels"][0].update(lines=["hello"]), "panels[0].lines[0]"),
    ("a line with an unknown key", lambda b: b["panels"][0]["lines"][0].update(x=3), "panels[0].lines[0].x"),
    ("a line kind nobody defined", lambda b: b["panels"][0]["lines"][0].update(kind="scream"), "panels[0].lines[0].kind"),
    ("a line without a kind", lambda b: b["panels"][0]["lines"][0].pop("kind"), "panels[0].lines[0].kind"),
    ("a line without text", lambda b: b["panels"][0]["lines"][0].update(text=""), "panels[0].lines[0].text"),
    ("a line with markup", lambda b: b["panels"][0]["lines"][0].update(text="hi <img src=x>"), "panels[0].lines[0].text"),
    ("a line that is too long", lambda b: b["panels"][0]["lines"][0].update(text="x" * 161), "panels[0].lines[0].text"),
    ("a line with a direction mark", lambda b: b["panels"][0]["lines"][0].update(text="a‮b"), "panels[0].lines[0].text"),
    ("a line with a line break", lambda b: b["panels"][0]["lines"][0].update(text="a\nb"), "panels[0].lines[0].text"),
    ("a speaker name that is too long", lambda b: b["panels"][0]["lines"][1].update(who="x" * 25), "panels[0].lines[1].who"),
    ("a speaker name with markup", lambda b: b["panels"][0]["lines"][1].update(who="<b>"), "panels[0].lines[1].who"),
    ("a slug with a new line at the end", lambda b: b.update(slug="street\n"), "slug"),
    ("a language with a new line at the end", lambda b: b.update(lang="en\n"), "lang"),
    ("a checksum with a new line at the end", lambda b: b["pics"]["p1"].update(sha256="a" * 64 + "\n"), "pics.p1.sha256"),
    ("a picture id with a new line at the end", lambda b: b["pics"].update(**{"p1\n": pic(1)}), "the id must be lower-case letters"),
    ("a panel id with a new line at the end", lambda b: b["panels"][0].update(id="p1\n"), "panels[0].id"),
    ("a title that ends in an invisible line separator", lambda b: b.update(title="Moth Hour\u2028"), "title"),
    ("a title that starts with a control character", lambda b: b.update(title="\x1fMoth Hour"), "title"),
    ("a title that ends in a next-line character", lambda b: b.update(title="Moth Hour\x85"), "title"),
    ("a title with a space at the start", lambda b: b.update(title=" Moth Hour"), "title"),
    ("a kicker with an ideographic space at the end", lambda b: b.update(kicker="CH 01\u3000"), "kicker"),
    ("a speaker name that ends in a file separator", lambda b: b["panels"][0]["lines"][1].update(who="Host\x1c"), "panels[0].lines[1].who"),
    ("alt text that ends in a line separator", lambda b: b["panels"][0].update(alt="A round sun.\u2028"), "panels[0].alt"),
    ("a line of text that ends in a space", lambda b: b["panels"][0]["lines"][0].update(text="A round sun, low. "), "panels[0].lines[0].text"),
]


@pytest.mark.parametrize("label,change,where", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_every_bad_picture_bundle_is_refused_with_the_place_named(label, change, where):
    found = problems(mutate(change))
    assert any(where in line for line in found), (label, found)


def test_a_picture_bundle_with_a_good_change_still_passes():
    """The refusal list is only worth something if the same edits, made well, are accepted."""
    obj = bundle()
    obj["pics"]["p1"].update(bytes=fmt.LIMITS["pic_bytes"], w=4096, h=4096)
    obj["panels"][0]["lines"][0].update(text="x" * 160)
    obj["panels"][0]["lines"] = [{"kind": kind, "text": "ok"} for kind in fmt.KINDS] + [{"kind": "say", "who": "x" * 24, "text": "ok"}] * 3
    assert fmt.validate_bundle(obj) == []


def test_version_one_does_not_accept_the_picture_keys():
    old = {
        "v": 1, "slug": "test-card", "title": "Moth Hour", "kicker": "CH 00", "lang": "en",
        "panels": [{"id": "p1", "ms": 4000, "alt": "Bars.", "layers": [{"art": "bars", "x": 0, "y": 0, "w": 100, "h": 100}]}],
        "art": {"bars": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 9"><rect width="16" height="9"/></svg>'},
    }
    assert fmt.validate_bundle(old) == []
    for key, value in (("pics", {"p1": pic(1)}), ("flip", True)):
        assert any(key in line for line in problems({**old, key: value})), key
    with_lines = copy.deepcopy(old)
    with_lines["panels"][0]["lines"] = [{"kind": "say", "text": "x"}]
    assert any("lines" in line for line in problems(with_lines))
    with_pic = copy.deepcopy(old)
    with_pic["panels"][0]["layers"][0]["pic"] = "p1"
    assert any("pic" in line for line in problems(with_pic))


def test_the_picture_limits_are_what_the_plan_says():
    assert fmt.LIMITS["pics"] == 12
    assert fmt.LIMITS["pic_bytes"] == 400_000
    assert fmt.LIMITS["pic_px"] == (320, 4096)
    assert fmt.LIMITS["lines"] == 8
    assert fmt.VERSIONS == (1, 2)
