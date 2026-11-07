"""Barcode and QR rendering to inline SVG (no Django).

Code 128 and EAN-13 are implemented here directly, so label printing needs no extra
library and works offline. QR uses the small pure-Python `segno` package (requirements.txt).
"""
from . import codes

# Code 128 bar/space widths for symbol values 0-105 (+ stop). Each is 6 alternating widths
# starting with a bar, totalling 11 modules; the stop pattern has 7 widths totalling 13.
_C128 = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232"
).split()
_C128_STOP = "2331112"
START_B = 104

_L = ("0001101 0011001 0010011 0111101 0100011 0110001 0101111 0111011 0110111 0001011").split()
_R = ["".join("1" if c == "0" else "0" for c in p) for p in _L]
_G = [p[::-1] for p in _R]
_PARITY = "LLLLLL LLGLGG LLGGLG LLGGGL LGLLGG LGGLLG LGGGLL LGLGLG LGLGGL LGGLGL".split()


def code128_modules(text):
    """Return a string of '1' (bar) / '0' (space) modules for Code 128 set B (printable ASCII)."""
    values = []
    for ch in text:
        o = ord(ch)
        if not 32 <= o <= 126:
            raise codes.CodeError("Code 128 labels support printable ASCII only.")
        values.append(o - 32)
    checksum = (START_B + sum(v * i for i, v in enumerate(values, start=1))) % 103
    symbols = [START_B] + values + [checksum]
    modules = ""
    for sym in symbols:
        modules += _expand(_C128[sym])
    return modules + _expand(_C128_STOP)


def _expand(widths):
    out, bar = "", True
    for w in widths:
        out += ("1" if bar else "0") * int(w)
        bar = not bar
    return out


def ean13_modules(code):
    if not codes.is_valid_ean13(code):
        raise codes.CodeError("Not a valid EAN-13 number.")
    parity = _PARITY[int(code[0])]
    left = "".join((_L if p == "L" else _G)[int(d)] for p, d in zip(parity, code[1:7]))
    right = "".join(_R[int(d)] for d in code[7:])
    return "101" + left + "01010" + right + "101"


def modules_for(code):
    """EAN-13 where the text is a valid EAN-13, otherwise Code 128."""
    return ean13_modules(code) if codes.is_valid_ean13(code) else code128_modules(code)


def _bars_svg(modules, quiet=10, height=40):
    rects, i = [], 0
    while i < len(modules):
        if modules[i] == "1":
            j = i
            while j < len(modules) and modules[j] == "1":
                j += 1
            rects.append(f'<rect x="{quiet + i}" y="0" width="{j - i}" height="{height}"/>')
            i = j
        else:
            i += 1
    width = len(modules) + 2 * quiet
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" shape-rendering="crispEdges" role="img" aria-label="barcode">'
            f'<g fill="#000">{"".join(rects)}</g></svg>')


def barcode_svg(code):
    return _bars_svg(modules_for(code))


def qr_svg(text):
    """QR code as inline SVG. Needs the `segno` package; raises a clear error if it is missing."""
    try:
        import segno
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("QR labels need the 'segno' package: pip install -r requirements.txt") from exc
    matrix = segno.make(text, error="m", micro=False).matrix
    border = 2
    size = len(matrix) + 2 * border
    rects = []
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):
            if row[x]:
                start = x
                while x < len(row) and row[x]:
                    x += 1
                rects.append(f'<rect x="{start + border}" y="{y + border}" width="{x - start}" height="1"/>')
            else:
                x += 1
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
            f'shape-rendering="crispEdges" role="img" aria-label="QR code"><rect width="{size}" height="{size}" fill="#fff"/>'
            f'<g fill="#000">{"".join(rects)}</g></svg>')
