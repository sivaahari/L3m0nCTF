"""A small QR Code Model 2 encoder (ISO/IEC 18004): byte mode (UTF-8), error correction level M, versions 1-10, the lowest-penalty mask of the eight.

Written for the verification portal so a certificate page carries a scannable QR code with no JavaScript and no third-party package. It is the
Python twin of the JavaScript encoder the design sample was proved with (an independently written decoder read every symbol back, Reed-Solomon
syndromes zero); tools/tests/test_verify_site.py decodes this one's output with an independent reader too (tools/tests/qr_reference_reader.py).
Standard library only.
"""
from __future__ import annotations

# level M: version -> (total codewords, EC codewords per block, ((blocks, data codewords per block), ...))
_M = {
    1: (26, 10, ((1, 16),)), 2: (44, 16, ((1, 28),)), 3: (70, 26, ((1, 44),)), 4: (100, 18, ((2, 32),)), 5: (134, 24, ((2, 43),)),
    6: (172, 16, ((4, 27),)), 7: (196, 18, ((4, 31),)), 8: (242, 22, ((2, 38), (2, 39))), 9: (292, 22, ((3, 36), (2, 37))),
    10: (346, 26, ((4, 43), (1, 44))),
}
_ALIGN = {1: (), 2: (6, 18), 3: (6, 22), 4: (6, 26), 5: (6, 30), 6: (6, 34), 7: (6, 22, 38), 8: (6, 24, 42), 9: (6, 26, 46), 10: (6, 28, 50)}
_REMAINDER = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return _EXP[_LOG[a] + _LOG[b]] if a and b else 0


def _data_capacity(version: int) -> int:
    return sum(n * k for n, k in _M[version][2])


def _rs_remainder(block: list[int], ec_len: int) -> list[int]:
    gen = [1]
    for i in range(ec_len):
        nxt = [0] * (len(gen) + 1)
        for j, c in enumerate(gen):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, _EXP[i])
        gen = nxt
    rem = [0] * ec_len
    for b in block:
        factor = b ^ rem.pop(0)
        rem.append(0)
        for j in range(ec_len):
            rem[j] ^= _mul(gen[j + 1], factor)
    return rem


_MASKS = (
    lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (y // 2 + x // 3) % 2 == 0, lambda x, y: (x * y) % 2 + (x * y) % 3 == 0,
    lambda x, y: ((x * y) % 2 + (x * y) % 3) % 2 == 0, lambda x, y: ((x + y) % 2 + (x * y) % 3) % 2 == 0,
)


def _format_bits(mask: int) -> int:
    data = (0b00 << 3) | mask  # level M = 00
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def matrix(text: str) -> list[list[bool]]:
    """The QR symbol for `text` as rows of modules (True = dark), without the quiet zone."""
    data = list(text.encode("utf-8"))
    version = next((v for v in range(1, 11) if 4 + (8 if v <= 9 else 16) + 8 * len(data) <= 8 * _data_capacity(v)), 0)
    if not version:
        raise ValueError("text too long for a version 1-10 QR code at level M (213 bytes at most)")
    size = 17 + 4 * version

    bits: list[int] = []

    def put(val: int, length: int) -> None:
        bits.extend((val >> i) & 1 for i in range(length - 1, -1, -1))

    put(0b0100, 4)
    put(len(data), 8 if version <= 9 else 16)
    for b in data:
        put(b, 8)
    cap = 8 * _data_capacity(version)
    put(0, min(4, cap - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    pad = 0xEC
    while len(bits) < cap:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    total, ec_len, groups = _M[version]
    blocks = []
    at = 0
    for n, k in groups:
        for _ in range(n):
            d = words[at:at + k]
            at += k
            blocks.append((d, _rs_remainder(d, ec_len)))
    out: list[int] = []
    for i in range(max(len(d) for d, _ in blocks)):
        out += [d[i] for d, _ in blocks if i < len(d)]
    for i in range(ec_len):
        out += [e[i] for _, e in blocks]
    assert len(out) == total
    stream = [(c >> i) & 1 for c in out for i in range(7, -1, -1)] + [0] * _REMAINDER[version]

    mods = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x: int, y: int, dark: bool) -> None:
        mods[y][x] = dark
        fn[y][x] = True

    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    d = max(abs(dx), abs(dy))
                    setf(x, y, d not in (2, 4))
    al = _ALIGN[version]
    for ay in al:
        for ax in al:
            if (ax, ay) in ((6, 6), (6, al[-1]), (al[-1], 6)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
    for i in range(9):
        if not fn[8][i]:
            setf(i, 8, False)
        if not fn[i][8]:
            setf(8, i, False)
    for i in range(8):
        setf(size - 1 - i, 8, False)
        setf(8, size - 1 - i, False)
    setf(8, size - 8, True)
    if version >= 7:
        rem = version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        vbits = (version << 12) | rem
        for i in range(18):
            bit = (vbits >> i) & 1 == 1
            a, b = size - 11 + i % 3, i // 3
            setf(a, b, bit)
            setf(b, a, bit)

    k = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vert if upward else vert
                if not fn[y][x]:
                    mods[y][x] = stream[k] == 1 if k < len(stream) else False
                    k += 1
        right -= 2
    assert k == len(stream)

    def build(mask: int) -> list[list[bool]]:
        g = [row[:] for row in mods]
        for y in range(size):
            for x in range(size):
                if not fn[y][x] and _MASKS[mask](x, y):
                    g[y][x] = not g[y][x]
        f = _format_bits(mask)
        b = lambda i: (f >> i) & 1 == 1  # noqa: E731
        for i in range(6):
            g[i][8] = b(i)
        g[7][8], g[8][8], g[8][7] = b(6), b(7), b(8)
        for i in range(9, 15):
            g[8][14 - i] = b(i)
        for i in range(8):
            g[8][size - 1 - i] = b(i)
        for i in range(8, 15):
            g[size - 15 + i][8] = b(i)
        g[size - 8][8] = True
        return g

    def penalty(g: list[list[bool]]) -> int:
        p = 0
        lines = [row for row in g] + [[g[y][x] for y in range(size)] for x in range(size)]
        for line in lines:
            run = 1
            for i in range(1, size + 1):
                if i < size and line[i] == line[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        p += 3 + run - 5
                    run = 1
            s = "0000" + "".join("1" if v else "0" for v in line) + "0000"  # the quiet zone counts as light
            for pat in ("10111010000", "00001011101"):
                i = s.find(pat)
                while i != -1:
                    p += 40
                    i = s.find(pat, i + 1)
        for y in range(size - 1):
            for x in range(size - 1):
                c = g[y][x]
                if c == g[y][x + 1] == g[y + 1][x] == g[y + 1][x + 1]:
                    p += 3
        dark = sum(sum(row) for row in g)
        p += 10 * (abs(dark * 20 - size * size * 10) // (size * size))
        return p

    best = min((build(m) for m in range(8)), key=penalty)
    return best


def svg(text: str, dark: str = "#070b1e", light: str = "#f4ead4", label: str = "") -> str:
    """The symbol as a small SVG (4-module quiet zone, crisp edges, one path). No style attribute, so a strict CSP is fine."""
    m = matrix(text)
    n, q = len(m), 4
    w = n + 2 * q
    # one rectangle per run of dark modules in a row (not one per module): the same picture in about half the bytes, which matters because the
    # studio page carries it inline (independent review of 27 Nov 2026: the per-module path put the page over its size budget)
    runs = []
    for y in range(n):
        x = 0
        while x < n:
            if not m[y][x]:
                x += 1
                continue
            start = x
            while x < n and m[y][x]:
                x += 1
            runs.append(f"M{start + q},{y + q}h{x - start}v1h-{x - start}z")
    path = "".join(runs)
    title = f"<title>{label}</title>" if label else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {w}" shape-rendering="crispEdges" role="img">{title}'
            f'<rect width="{w}" height="{w}" fill="{light}"/><path d="{path}" fill="{dark}"/></svg>')
