"""The cold-open comic's file format: what a good bundle is, and every way a bad one is refused.

The art is the sensitive part, so it is tested both ways: a list of pictures that must pass (shapes, gradients, a pattern, a filter, text, one
level of <use>) and a longer list of constructions that must each be refused, one test line per attack. A test that only shows the good
file passing proves nothing about the guard, so the list of refusals is the point.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_story
"""
import copy
import json
import time

import pytest

from CTFd.plugins.l3mon_story import format as fmt

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90">'
GOOD_SVG = HEAD + '<rect width="160" height="90" fill="#223"/><circle cx="80" cy="45" r="20" fill="#fc3"/></svg>'


def bundle():
    return {
        "v": 1, "slug": "test-card", "title": "Please Stand By", "kicker": "CH 00 · Test Card", "lang": "en",
        "panels": [
            {
                "id": "p1", "ms": 5000, "enter": "static", "alt": "Colour bars with a small round sun in the middle.",
                "layers": [{"art": "bars", "x": 0, "y": 0, "w": 100, "h": 100, "z": 0, "from": {"x": 0, "y": 0, "s": 1.05}, "to": {"x": -3, "y": 0, "s": 1.15}}],
                "bubbles": [{"kind": "say", "who": "Host", "text": "Good evening, crew.", "x": 8, "y": 8, "w": 40, "tail": "bl", "at": 800}],
                "sfx": [{"cue": "static", "at": 0}],
            },
            {
                "id": "p2", "ms": 4000, "enter": "slide", "alt": "The same picture, closer.",
                "layers": [{"art": "bars", "x": -10, "y": -10, "w": 120, "h": 120, "z": 0, "anim": "pulse"}],
                "bubbles": [{"kind": "caption", "text": "The bars hum a message nobody sent.", "x": 5, "y": 78, "w": 90}],
            },
        ],
        "art": {"bars": GOOD_SVG},
    }


def problems(obj):
    return [str(p) for p in fmt.validate_bundle(obj)]


# ---- the good file ---------------------------------------------------------------------------------------------------------

def test_a_good_bundle_has_no_problems():
    assert fmt.validate_bundle(bundle()) == []


def test_a_good_bundle_survives_a_round_trip_through_strict_json():
    obj, found = fmt.load_bundle(json.dumps(bundle()))
    assert found == [] and obj == bundle()
    obj, found = fmt.load_bundle(json.dumps(bundle()).encode("utf-8"))
    assert found == []


# ---- strict JSON -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw",
    ['{"v": 1, "v": 1}', "NaN", '{"v": NaN}', '{"v": Infinity}', "{", "", "[]", "null", '"text"'],
)
def test_json_that_is_not_strict_or_not_an_object_is_refused(raw):
    obj, found = fmt.load_bundle(raw)
    assert found, raw


def test_a_repeated_key_is_refused_even_in_an_otherwise_good_bundle():
    raw = json.dumps(bundle()).replace('"v": 1,', '"v": 1, "v": 1,', 1)
    assert raw.count('"v"') == 2
    obj, found = fmt.load_bundle(raw)
    assert obj is None and "appears twice" in str(found[0])


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_a_json_constant_is_refused_even_where_a_number_would_do(constant):
    raw = json.dumps(bundle()).replace('"ms": 5000', f'"ms": {constant}', 1)
    assert constant in raw
    obj, found = fmt.load_bundle(raw)
    assert obj is None and "not valid JSON" in str(found[0])


def test_a_file_over_the_size_limit_is_refused_before_it_is_read():
    obj, found = fmt.load_bundle("x" * (fmt.LIMITS["bundle_bytes"] + 1))
    assert obj is None and "limit" in str(found[0])


# ---- the script ------------------------------------------------------------------------------------------------------------

def mutate(fn):
    obj = copy.deepcopy(bundle())
    fn(obj)
    return obj


SCRIPT_REFUSALS = [
    ("an unknown key at the top", lambda b: b.update(story="x"), "story"),
    ("the wrong version", lambda b: b.update(v=2), "v"),
    ("a slug with capitals", lambda b: b.update(slug="Test Card"), "slug"),
    ("an empty title", lambda b: b.update(title=""), "title"),
    ("a title with markup", lambda b: b.update(title="<b>x</b>"), "title"),
    ("a kicker with a control character", lambda b: b.update(kicker="CH‮ 01"), "kicker"),
    ("a language that is not a code", lambda b: b.update(lang="english"), "lang"),
    ("no panels", lambda b: b.update(panels=[]), "panels"),
    ("too many panels", lambda b: b.update(panels=[dict(b["panels"][0], id=f"p{i}") for i in range(13)]), "panels"),
    ("a panel that is not an object", lambda b: b["panels"].append("x"), "panels[2]"),
    ("an unknown key in a panel", lambda b: b["panels"][0].update(speed=2), "panels[0].speed"),
    ("a repeated panel id", lambda b: b["panels"][1].update(id="p1"), "panels[1].id"),
    ("a bad panel id", lambda b: b["panels"][0].update(id="P 1"), "panels[0].id"),
    ("a panel that is too short", lambda b: b["panels"][0].update(ms=500), "panels[0].ms"),
    ("a panel that is too long", lambda b: b["panels"][0].update(ms=60000), "panels[0].ms"),
    ("a time that is not whole", lambda b: b["panels"][0].update(ms=5000.5), "panels[0].ms"),
    ("a time that is a boolean", lambda b: b["panels"][0].update(ms=True), "panels[0].ms"),
    ("an entrance nobody defined", lambda b: b["panels"][0].update(enter="explode"), "panels[0].enter"),
    ("no alt text", lambda b: b["panels"][0].pop("alt"), "panels[0].alt"),
    ("alt text that is too long", lambda b: b["panels"][0].update(alt="x" * 301), "panels[0].alt"),
    ("no layers", lambda b: b["panels"][0].update(layers=[]), "panels[0].layers"),
    ("too many layers", lambda b: b["panels"][0].update(layers=[b["panels"][0]["layers"][0]] * 13), "panels[0].layers"),
    ("a layer with an unknown key", lambda b: b["panels"][0]["layers"][0].update(href="x"), "panels[0].layers[0].href"),
    ("a layer that points at nothing", lambda b: b["panels"][0]["layers"][0].update(art="ghost"), "panels[0].layers[0].art"),
    ("a layer art id with a path in it", lambda b: b["panels"][0]["layers"][0].update(art="../x"), "panels[0].layers[0].art"),
    ("a missing size", lambda b: b["panels"][0]["layers"][0].pop("w"), "panels[0].layers[0].w"),
    ("a huge size", lambda b: b["panels"][0]["layers"][0].update(w=100000), "panels[0].layers[0].w"),
    ("a size that is not a number", lambda b: b["panels"][0]["layers"][0].update(h="100%"), "panels[0].layers[0].h"),
    ("a layer order beyond 9", lambda b: b["panels"][0]["layers"][0].update(z=10), "panels[0].layers[0].z"),
    ("an opacity above 1", lambda b: b["panels"][0]["layers"][0].update(opacity=2), "panels[0].layers[0].opacity"),
    ("a flip that is not true or false", lambda b: b["panels"][0]["layers"][0].update(flip="yes"), "panels[0].layers[0].flip"),
    ("an animation nobody defined", lambda b: b["panels"][0]["layers"][0].update(anim="spin"), "panels[0].layers[0].anim"),
    ("a pivot nobody defined", lambda b: b["panels"][0]["layers"][0].update(pivot="corner"), "panels[0].layers[0].pivot"),
    ("a move that is not an object", lambda b: b["panels"][0]["layers"][0].update(**{"from": 3}), "panels[0].layers[0].from"),
    ("a move with an unknown key", lambda b: b["panels"][0]["layers"][0]["to"].update(rotate=90), "panels[0].layers[0].to.rotate"),
    ("a scale beyond the limit", lambda b: b["panels"][0]["layers"][0]["to"].update(s=40), "panels[0].layers[0].to.s"),
    ("a scale of zero", lambda b: b["panels"][0]["layers"][0]["to"].update(s=0), "panels[0].layers[0].to.s"),
    ("a bubble kind nobody defined", lambda b: b["panels"][0]["bubbles"][0].update(kind="scream"), "panels[0].bubbles[0].kind"),
    ("a bubble without text", lambda b: b["panels"][0]["bubbles"][0].update(text=""), "panels[0].bubbles[0].text"),
    ("a bubble with markup", lambda b: b["panels"][0]["bubbles"][0].update(text="hello <img src=x>"), "panels[0].bubbles[0].text"),
    ("a bubble that is too long", lambda b: b["panels"][0]["bubbles"][0].update(text="x" * 161), "panels[0].bubbles[0].text"),
    ("a bubble with a direction mark", lambda b: b["panels"][0]["bubbles"][0].update(text="a‮b"), "panels[0].bubbles[0].text"),
    ("a bubble with a line break", lambda b: b["panels"][0]["bubbles"][0].update(text="a\nb"), "panels[0].bubbles[0].text"),
    ("a speaker name that is too long", lambda b: b["panels"][0]["bubbles"][0].update(who="x" * 25), "panels[0].bubbles[0].who"),
    ("a bubble outside the panel", lambda b: b["panels"][0]["bubbles"][0].update(x=140), "panels[0].bubbles[0].x"),
    ("a bubble that is too narrow", lambda b: b["panels"][0]["bubbles"][0].update(w=2), "panels[0].bubbles[0].w"),
    ("a tail nobody defined", lambda b: b["panels"][0]["bubbles"][0].update(tail="up"), "panels[0].bubbles[0].tail"),
    ("a size nobody defined", lambda b: b["panels"][0]["bubbles"][0].update(size="xl"), "panels[0].bubbles[0].size"),
    ("a bubble after the panel is over", lambda b: b["panels"][0]["bubbles"][0].update(at=9000), "panels[0].bubbles[0].at"),
    ("an unknown key in a bubble", lambda b: b["panels"][0]["bubbles"][0].update(html="<b>"), "panels[0].bubbles[0].html"),
    ("too many bubbles", lambda b: b["panels"][0].update(bubbles=[b["panels"][0]["bubbles"][0]] * 7), "panels[0].bubbles"),
    ("a bubble list that is not a list", lambda b: b["panels"][0].update(bubbles="hi"), "panels[0].bubbles"),
    ("a cue nobody defined", lambda b: b["panels"][0]["sfx"][0].update(cue="explosion"), "panels[0].sfx[0].cue"),
    ("a cue after the panel is over", lambda b: b["panels"][0]["sfx"][0].update(at=99999), "panels[0].sfx[0].at"),
    ("too many cues", lambda b: b["panels"][0].update(sfx=[b["panels"][0]["sfx"][0]] * 5), "panels[0].sfx"),
    ("art that is not an object", lambda b: b.update(art=[]), "art"),
    ("art with a bad id", lambda b: b["art"].update(**{"Bad Id": GOOD_SVG}), "art.Bad Id"),
    ("art that nothing uses", lambda b: b["art"].update(spare=GOOD_SVG), "art.spare"),
    ("too much art", lambda b: b.update(art={f"a{i}": GOOD_SVG for i in range(41)}), "art"),
]


@pytest.mark.parametrize("label,change,where", SCRIPT_REFUSALS, ids=[r[0] for r in SCRIPT_REFUSALS])
def test_every_bad_script_is_refused_with_the_place_named(label, change, where):
    found = problems(mutate(change))
    assert any(where in line for line in found), (label, found)


def test_a_bundle_over_the_size_limit_is_refused_even_when_every_piece_is_small():
    piece = HEAD + '<path d="M0 0L1 1"/>' * 2400 + "</svg>"
    assert len(piece.encode()) < fmt.LIMITS["art_bytes"] and fmt.check_svg(piece) == [], "each picture is within its own limit"
    big = bundle()
    big["art"] = {f"a{i}": piece for i in range(12)}
    big["panels"] = [dict(copy.deepcopy(big["panels"][0]), id=f"p{i}") for i in range(12)]
    for i, panel in enumerate(big["panels"]):
        panel["layers"][0]["art"] = f"a{i}"
    found = problems(big)
    assert any("the bundle is" in line for line in found), found
    assert not any(line.startswith("art.") for line in found), "and nothing is wrong with the pieces themselves"


# ---- the art ---------------------------------------------------------------------------------------------------------------

GOOD_ART = {
    "shapes": HEAD + '<rect x="1" y="1" width="10" height="10" rx="2"/><circle cx="5" cy="5" r="3"/><ellipse cx="5" cy="5" rx="3" ry="2"/><line x1="0" y1="0" x2="9" y2="9" stroke="#000" stroke-width="2" stroke-linecap="round"/><polyline points="0,0 5,5 9,0" fill="none" stroke="red"/><polygon points="0,0 5,5 9,0"/><path d="M0 0 C 10 10, 20 -10, 30 0 Z" fill="rgb(10,20,30)"/></svg>',
    "gradient": HEAD + '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2a1b5a"/><stop offset="1" stop-color="#ff9a4a" stop-opacity=".5"/></linearGradient></defs><rect width="160" height="90" fill="url(#g)"/></svg>',
    "radial": HEAD + '<defs><radialGradient id="r" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="white"/><stop offset="1" stop-color="black"/></radialGradient></defs><circle cx="80" cy="45" r="40" fill="url(#r)"/></svg>',
    "pattern": HEAD + '<defs><pattern id="dots" width="6" height="6" patternUnits="userSpaceOnUse"><circle cx="3" cy="3" r="1" fill="#000"/></pattern></defs><rect width="160" height="90" fill="url(#dots)" opacity=".3"/></svg>',
    "filter": HEAD + '<defs><filter id="glow"><feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs><circle cx="80" cy="45" r="10" fill="#ff0" filter="url(#glow)"/></svg>',
    "clip and mask": HEAD + '<defs><clipPath id="c"><rect width="80" height="90"/></clipPath><mask id="m"><rect width="160" height="90" fill="white"/></mask></defs><g clip-path="url(#c)" mask="url(#m)"><rect width="160" height="90"/></g></svg>',
    "text": HEAD + '<text x="10" y="20" font-family="Arial, sans-serif" font-size="12" font-weight="bold" text-anchor="middle">NOW SHOWING</text></svg>',
    "quoted font names": HEAD + "<text x='1' y='9' font-family=\"'Arial Black', Impact, sans-serif\">NOW</text></svg>",
    "title and desc": HEAD + '<title>A picture</title><desc>With a sun</desc><circle r="3"/></svg>',
    "one level of use": HEAD + '<defs><path id="flag" d="M0 0h16l-8 20z"/></defs><use href="#flag" x="10" y="10" fill="#fc0"/><use href="#flag" x="40" y="10" fill="#f0c"/></svg>',
    "transforms": HEAD + '<g transform="translate(10,20) rotate(-5) scale(1.2)"><rect width="10" height="10"/></g></svg>',
    "turbulence": HEAD + '<defs><filter id="n"><feTurbulence type="fractalNoise" baseFrequency=".8" numOctaves="2" seed="3"/><feColorMatrix type="saturate" values="0"/></filter></defs><rect width="160" height="90" filter="url(#n)" opacity=".1"/></svg>',
}


@pytest.mark.parametrize("name", sorted(GOOD_ART))
def test_the_art_that_the_stories_need_is_accepted(name):
    assert fmt.check_svg(GOOD_ART[name]) == [], name


BAD_ART = {
    "a script element": HEAD + "<script>alert(1)</script></svg>",
    "a script element with a namespace trick": '<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="http://www.w3.org/1999/xhtml" viewBox="0 0 1 1"><x:script>alert(1)</x:script></svg>',
    "an event handler": HEAD + '<rect width="1" height="1" onload="alert(1)"/></svg>',
    "an event handler on the root": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1" onload="alert(1)"></svg>',
    "a style element": HEAD + "<style>@import url(http://evil.example/x.css);</style></svg>",
    "a style attribute": HEAD + '<rect width="1" height="1" style="fill:red"/></svg>',
    "a class attribute": HEAD + '<rect width="1" height="1" class="x"/></svg>',
    "a foreignObject": HEAD + '<foreignObject width="10" height="10"><p xmlns="http://www.w3.org/1999/xhtml">hi</p></foreignObject></svg>',
    "an image element": HEAD + '<image href="http://evil.example/x.png" width="10" height="10"/></svg>',
    "an image with a data address": HEAD + '<image href="data:image/png;base64,AAAA" width="10" height="10"/></svg>',
    "a link": HEAD + '<a href="http://evil.example"><rect width="1" height="1"/></a></svg>',
    "an animation element": HEAD + '<rect width="1" height="1"><animate attributeName="x" to="5" dur="1s"/></rect></svg>',
    "a set element": HEAD + '<rect width="1" height="1"><set attributeName="href" to="javascript:alert(1)"/></rect></svg>',
    "a use pointing outside": HEAD + '<use href="http://evil.example/x.svg#a"/></svg>',
    "a use pointing at a data address": HEAD + '<use href="data:image/svg+xml;base64,AAAA#a"/></svg>',
    "an xlink href": '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1 1"><use xlink:href="#a"/></svg>',
    "a use pointing at nothing": HEAD + '<use href="#ghost"/></svg>',
    "an href on a rect": HEAD + '<rect href="#a" width="1" height="1"/></svg>',
    "a fill pointing outside": HEAD + '<rect width="1" height="1" fill="url(http://evil.example/x.svg#a)"/></svg>',
    "a fill that is a javascript address": HEAD + '<rect width="1" height="1" fill="javascript:alert(1)"/></svg>',
    "a filter pointing outside": HEAD + '<rect width="1" height="1" filter="url(http://evil.example/f.svg#f)"/></svg>',
    "a clip pointing at an undefined id": HEAD + '<rect width="1" height="1" clip-path="url(#ghost)"/></svg>',
    "a path with a letter that is not a command": HEAD + '<path d="M0 0 L 5 5 <script>"/></svg>',
    "a path that is too long": HEAD + '<path d="' + "M0 0 " * 5000 + '"/></svg>',
    "a transform with a call": HEAD + '<g transform="translate(1,1) url(#a)"><rect/></g></svg>',
    "text with markup": HEAD + "<text>&lt;img src=x&gt;</text></svg>",
    "text that is too long": HEAD + "<text>" + "x" * 121 + "</text></svg>",
    "text inside a rect": HEAD + "<rect>hello</rect></svg>",
    "text outside an element": HEAD + "<g/>stray</svg>",
    "an external DOCTYPE": '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">' + HEAD + "</svg>",
    "an entity": '<!DOCTYPE svg [<!ENTITY a "aaaa">]>' + HEAD + "<text>&a;</text></svg>",
    "a billion laughs": '<!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>' + HEAD + "<text>&b;</text></svg>",
    "an external entity": '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + HEAD + "<text>&x;</text></svg>",
    "a processing instruction": '<?xml-stylesheet href="http://evil.example/x.css"?>' + HEAD + "</svg>",
    "an xml declaration": '<?xml version="1.0"?>' + HEAD + "</svg>",
    "a comment": HEAD + "<!-- hello --></svg>",
    "a CDATA section": HEAD + "<text><![CDATA[x]]></text></svg>",
    "a root that is not svg": '<html xmlns="http://www.w3.org/1999/xhtml"></html>',
    "an svg without the namespace": '<svg viewBox="0 0 1 1"></svg>',
    "an svg without a viewBox": '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
    "a viewBox of zero": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 0 0"></svg>',
    "a viewBox that is huge": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100000 100000"></svg>',
    "malformed xml": HEAD + "<g></svg>",
    "an unknown attribute": HEAD + '<rect width="1" height="1" data-x="1"/></svg>',
    "a number that is a word": HEAD + '<rect width="wide" height="1"/></svg>',
    "a blur that is far too big": HEAD + '<defs><filter id="f"><feGaussianBlur stdDeviation="400"/></filter></defs></svg>',
    "too many octaves": HEAD + '<defs><filter id="f"><feTurbulence baseFrequency=".1" numOctaves="12"/></filter></defs></svg>',
    "a displacement beyond the limit": HEAD + '<defs><filter id="f"><feDisplacementMap in="SourceGraphic" scale="9000"/></filter></defs></svg>',
    "too many elements": HEAD + "<g/>" * 2600 + "</svg>",
    "nesting that is too deep": HEAD + "<g>" * 30 + "</g>" * 30 + "</svg>",
    "a use of a use": HEAD + '<defs><g id="a"><rect width="1" height="1"/></g><g id="b"><use href="#a"/></g></defs><use href="#b"/></svg>',
    "a use of itself": HEAD + '<g id="a"><use href="#a"/></g></svg>',
    "more filters than a picture needs": HEAD + "<defs>" + "".join(f'<filter id="f{i}"><feGaussianBlur stdDeviation="1"/></filter>' for i in range(13)) + "</defs></svg>",
    "more noise than a picture needs": HEAD + "<defs>" + "".join(f'<filter id="n{i}"><feTurbulence baseFrequency=".1"/></filter>' for i in range(3)) + "</defs></svg>",
    "a font name with a quote": HEAD + "<text font-family=\"x'; fill: red\">a</text></svg>",
    "an id with odd characters": HEAD + '<g id="a b"/></svg>',
    "a file over the size limit": HEAD + "<g/>" * 25000 + "</svg>",
}


@pytest.mark.parametrize("name", sorted(BAD_ART))
def test_every_dangerous_or_unreviewed_construction_in_the_art_is_refused(name):
    assert fmt.check_svg(BAD_ART[name]), name


PRECISE = [
    # an element that is not on the list, with nothing inside it for another rule to refuse
    *[(f"an empty <{tag}>", HEAD + f"<{tag}/></svg>", f"the element <{tag}> is not allowed")
      for tag in ("script", "style", "foreignObject", "image", "a", "animate", "set", "animateTransform", "iframe", "switch", "symbol", "marker", "feImage", "video")],
    ("a style attribute", HEAD + '<rect width="1" height="1" style="fill:red"/></svg>', "the attribute style is not allowed on <rect>"),
    ("a class attribute", HEAD + '<rect width="1" height="1" class="x"/></svg>', "the attribute class is not allowed on <rect>"),
    ("an event handler", HEAD + '<rect width="1" height="1" onload="alert(1)"/></svg>', "the attribute onload is not allowed on <rect>"),
    ("an event handler on the root", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1" onload="alert(1)"></svg>', "the attribute onload is not allowed on the root <svg>"),
    ("an href to another site", HEAD + '<use href="http://evil.example/x.svg#a"/></svg>', "href must point at an id inside the same picture"),
    ("an href that is a path on the same site", HEAD + '<g id="a"/><use href="/a"/></svg>', "href must point at an id inside the same picture"),
    ("an href that only ends like an id", HEAD + '<g id="a"/><use href="Xa"/></svg>', "href must point at an id inside the same picture"),
    ("an href on a rect", HEAD + '<g id="a"/><rect href="#a" width="1" height="1"/></svg>', "href is only allowed on <use>"),
    ("a path with a call", HEAD + '<path d="M0 0 url(#a)"/></svg>', "a path may only hold path commands and numbers"),
    ("a path with a script address", HEAD + '<path d="M0 0 javascript:alert(1)"/></svg>', "a path may only hold path commands and numbers"),
    ("a fill that is a javascript address", HEAD + '<rect width="1" height="1" fill="javascript:alert(1)"/></svg>', "fill must be a colour, none or url(#id)"),
    ("a fill pointing outside", HEAD + '<rect width="1" height="1" fill="url(http://evil.example/x.svg#a)"/></svg>', "fill must be a colour, none or url(#id)"),
    ("a filter pointing outside", HEAD + '<rect width="1" height="1" filter="url(http://evil.example/f.svg#f)"/></svg>', "filter must be none or url(#id)"),
    ("a transform with a call", HEAD + '<g transform="translate(1,1) url(#a)"><rect/></g></svg>', "a transform may only hold transform functions and numbers"),
    ("a number that is a word", HEAD + '<rect width="wide" height="1"/></svg>', "width must be a number"),
    ("a font name with a quote", HEAD + "<text font-family=\"x'; fill: red\">a</text></svg>", "font-family may only hold plain font names"),
    ("an id with odd characters", HEAD + '<g id="a b"/></svg>', "an id must be letters, digits, - or _"),
    ("text with markup", HEAD + "<text>&lt;img src=x&gt;</text></svg>", "text inside <text> must be plain"),
    ("text inside a rect", HEAD + "<rect>hello</rect></svg>", "<rect> may not hold text"),
    ("a use of a use", HEAD + '<defs><g id="a"><rect width="1" height="1"/></g><g id="b"><use href="#a"/></g></defs><use href="#b"/></svg>', "a <use> may not point at something that holds another <use>"),
]


@pytest.mark.parametrize("name,svg,reason", PRECISE, ids=[row[0] for row in PRECISE])
def test_each_guard_refuses_for_its_own_reason_so_no_other_rule_can_hide_a_broken_one(name, svg, reason):
    found = fmt.check_svg(svg)
    assert len(found) == 1 and reason in found[0], (name, found)


def test_every_allowed_attribute_has_its_own_rule_so_the_catch_all_never_decides():
    for name in sorted(fmt.ALLOWED_ATTRS):
        assert fmt._attribute_problem("rect", name, "!") != f"the attribute {name} is not allowed", name


@pytest.mark.parametrize(
    "template",
    ['<rect x="{}" width="1" height="1"/>', '<polygon points="{}"/>', '<rect width="1" height="1" stroke-dasharray="{}"/>', '<filter id="f"><feColorMatrix values="{}"/></filter>'],
)
def test_a_long_run_of_digits_is_refused_at_once_and_not_after_minutes(template):
    """The number checks once backtracked quadratically: 8,000 digits and a stray letter took two seconds, 80 KB about three minutes,
    inside a gevent worker that holds the store's lock (found by the independent review)."""
    svg = HEAD + template.format("1" * 8000 + "x") + "</svg>"
    started = time.perf_counter()
    found = fmt.check_svg(svg)
    assert found, "refused"
    assert time.perf_counter() - started < 0.5, "and at once"


def test_a_very_long_but_well_formed_number_is_refused_for_its_length_not_trusted():
    assert fmt.check_svg(HEAD + '<rect x="' + "1" * 41 + '" width="1" height="1"/></svg>'), "a single number is at most 40 characters"
    assert fmt.check_svg(HEAD + '<rect x="' + "1" * 40 + '" width="1" height="1"/></svg>') == []


def test_a_list_of_numbers_has_a_length_limit_too():
    limit = fmt.LIMITS["list_chars"]
    short, long = ("1 " * (limit // 2 - 1)).strip(), ("1 " * (limit // 2 + 1)).strip()
    assert len(short) <= limit < len(long)
    assert fmt.check_svg(HEAD + f'<polygon points="{short}"/></svg>') == []
    found = fmt.check_svg(HEAD + f'<polygon points="{long}"/></svg>')
    assert len(found) == 1 and "points must be a list of numbers" in found[0], found


def _many(count, body):
    return "".join(body.format(i=i) for i in range(count))


EXPENSIVE = [
    ("a pattern that paints with another pattern",
     HEAD + '<defs><pattern id="a" width="2" height="2" patternUnits="userSpaceOnUse"><rect width="1" height="1"/></pattern>'
            '<pattern id="b" width="2" height="2" patternUnits="userSpaceOnUse"><rect width="1" height="1" fill="url(#a)"/></pattern></defs><rect width="9" height="9" fill="url(#b)"/></svg>',
     "fill may not point at anything from inside a pattern, mask or clip path"),
    ("a pattern that holds a use",
     HEAD + '<defs><circle id="c" r="1"/><pattern id="p" width="2" height="2" patternUnits="userSpaceOnUse"><use href="#c"/></pattern></defs><rect width="9" height="9" fill="url(#p)"/></svg>',
     "<use> may not be used inside a pattern, mask or clip path"),
    ("a mask that holds a filtered shape",
     HEAD + '<defs><filter id="f"><feGaussianBlur stdDeviation="2"/></filter><mask id="m"><circle r="3" filter="url(#f)"/></mask></defs><rect width="9" height="9" mask="url(#m)"/></svg>',
     "filter may not point at anything from inside a pattern, mask or clip path"),
    ("three hundred copies of a thousand circles",
     HEAD + '<defs><g id="big">' + _many(1000, '<circle r="1"/>') + "</g></defs>" + _many(300, '<use href="#big"/>') + "</svg>",
     "is too expensive to draw"),
    ("a blur on three hundred copies",
     HEAD + '<defs><filter id="f"><feGaussianBlur stdDeviation="30"/></filter><g id="s" filter="url(#f)"><circle r="3"/></g></defs>' + _many(300, '<use href="#s"/>') + "</svg>",
     "is too expensive to draw"),
    ("a pattern used by two hundred big shapes",
     HEAD + '<defs><pattern id="p" width="2" height="2" patternUnits="userSpaceOnUse">' + _many(60, '<circle r="1"/>') + "</pattern></defs>" + _many(200, '<rect width="1" height="1" fill="url(#p)"/>') + "</svg>",
     "is too expensive to draw"),
    ("a filter that covers a continent", HEAD + '<defs><filter id="f" x="-1000%" y="-1000%" width="2100%" height="100%"><feGaussianBlur stdDeviation="3"/></filter></defs><rect width="1" height="1" filter="url(#f)"/></svg>',
     "a filter region is at most"),
    ("a mask as large as the sky", HEAD + '<defs><mask id="m" width="900000" height="900"><rect width="1" height="1"/></mask></defs><rect width="1" height="1" mask="url(#m)"/></svg>', "a mask region is at most"),
    ("a fill that points at a shape", HEAD + '<rect id="r" width="1" height="1"/><rect width="1" height="1" fill="url(#r)"/></svg>', "fill must point at a gradient or a pattern"),
    ("a clip path that points at a mask", HEAD + '<defs><mask id="m"><rect width="1" height="1"/></mask></defs><rect width="1" height="1" clip-path="url(#m)"/></svg>', "clip-path must point at a clipPath"),
    ("a mask that points at a filter", HEAD + '<defs><filter id="f"><feGaussianBlur stdDeviation="1"/></filter></defs><rect width="1" height="1" mask="url(#f)"/></svg>', "mask must point at a mask"),
    ("a filter that points at a gradient", HEAD + '<defs><linearGradient id="g"><stop offset="0"/></linearGradient></defs><rect width="1" height="1" filter="url(#g)"/></svg>', "filter must point at a filter"),
    ("a colour that is a reference", HEAD + '<defs><linearGradient id="g"><stop offset="0" stop-color="url(#g)"/></linearGradient></defs><rect width="1" height="1" fill="url(#g)"/></svg>', "stop-color must be a colour"),
    ("a self-referencing group", HEAD + '<g id="a"><rect width="1" height="1" fill="url(#a)"/></g></svg>', "fill must point at a gradient or a pattern"),
]


@pytest.mark.parametrize("name,svg,reason", EXPENSIVE, ids=[row[0] for row in EXPENSIVE])
def test_art_that_passes_the_shape_checks_but_would_hang_a_browser_is_refused_for_its_own_reason(name, svg, reason):
    assert len(svg.encode()) < fmt.LIMITS["art_bytes"], "small enough to reach the cost check at all"
    found = fmt.check_svg(svg)
    assert len(found) == 1 and reason in found[0], (name, found)


HEAVY = HEAD.replace("0 0 160 90", "0 0 3200 900") + '<defs><g id="b">' + _many(100, '<circle r="20"/>') + "</g></defs>" + _many(56, '<use href="#b"/>') + "</svg>"


def test_a_whole_script_of_heavy_pictures_is_refused_even_when_every_picture_passes_alone():
    """The strip shown for reduced motion paints every layer at once: one picture at the limit takes a quarter of a second to paint
    (measured in Chrome), a hundred and forty-four of them would take most of a minute."""
    assert fmt.check_svg(HEAVY) == [] and 5000 < fmt.art_cost(HEAVY) <= fmt.LIMITS["svg_cost"]
    two = bundle()
    two["art"] = {"bars": HEAVY}
    assert problems(two) == [], "two layers of the heaviest picture are fine"
    many = bundle()
    many["art"] = {"bars": HEAVY}
    many["panels"] = [dict(copy.deepcopy(many["panels"][0]), id=f"p{i}") for i in range(12)]
    for panel in many["panels"]:
        panel["layers"] = panel["layers"] * 12
    found = problems(many)
    assert len(found) == 1 and "together cost" in found[0], found


def test_the_cost_of_the_art_the_stories_need_is_far_below_the_limit():
    """So the limit is a fence for mistakes and attacks, not a squeeze on the artists: every good picture above costs a hundredth of it."""
    for name, svg in GOOD_ART.items():
        assert fmt.art_cost(svg) < fmt.LIMITS["svg_cost"] / 20, name


def test_art_that_is_not_text_is_refused():
    for value in (None, 5, b"<svg/>", [], {}):
        assert fmt.check_svg(value), value


def test_a_bad_picture_inside_a_bundle_is_reported_under_its_id():
    obj = bundle()
    obj["art"]["bars"] = HEAD + "<script>alert(1)</script></svg>"
    found = problems(obj)
    assert any(line.startswith("art.bars") and "script" in line for line in found), found


def test_a_picture_may_not_be_used_to_smuggle_a_script_through_a_hostile_namespace():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><g xmlns="http://evil.example/ns"><rect/></g></svg>'
    assert fmt.check_svg(svg)
