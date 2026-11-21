"""Builds every logo file the POS needs from ONE source image (packaging/winePOSlogo.png, transparent background).

    python packaging/make_icons.py                 (needs Pillow and numpy; run it from the project folder)

Writes
  packaging/winespos.ico, wizard_large.bmp, wizard_small.bmp     the .exe / shortcut / installer images
  core/static/core/logo.png, logo-mark.png                       the logo on the sign-in screens and the header
  core/static/core/favicon.ico, apple-touch-icon.png             browser tab icons
  core/static/core/icons/icon-<size>.png                         the window / taskbar icons (loaded by desktop/launcher.py)
and copies the web files into staticfiles/core/ as well (that folder is what the server and the installer ship).

Every size sits on a white tile so the logo stays solid and visible on any taskbar or wallpaper (the maroon "WINE"
would vanish on a dark background otherwise). Small sizes (16-48 px) show the swirl-and-arrow MARK alone, because words
cannot be read at taskbar size; large sizes (64-256 px) show the FULL logo.
"""
import io
import shutil
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "packaging" / "winePOSlogo.png"
SIZES = [16, 24, 32, 48, 64, 128, 192, 256, 512]
SMALL = {16, 24, 32, 48}          # mark only
WEB = ROOT / "core" / "static" / "core"
SERVED = ROOT / "staticfiles" / "core"


def alpha_box(img, thr=30):
    ys, xs = np.where(np.array(img)[..., 3] > thr)
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def trim(img, pad=0):
    x0, y0, x1, y1 = alpha_box(img)
    box = (max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad))
    return img.crop(box)


def split_mark(full):
    """The swirl and arrow are the top block; the first fully empty row below them ends it."""
    alpha = np.array(full)[..., 3] > 40
    rows = alpha.any(axis=1)
    end = next(y for y in range(1, len(rows)) if not rows[y])
    return trim(full.crop((0, 0, full.width, end)))


def resize(img, size):
    """Resize without dark fringes: premultiply the colour by alpha first (transparent pixels are black)."""
    return img.convert("RGBa").resize(size, Image.LANCZOS).convert("RGBA")


def fit(img, max_w, max_h):
    scale = min(max_w / img.width, max_h / img.height)
    return resize(img, (max(1, round(img.width * scale)), max(1, round(img.height * scale))))


def paste_center(canvas, img, dy=0):
    canvas.alpha_composite(img, ((canvas.width - img.width) // 2, (canvas.height - img.height) // 2 + dy))


def tile(size, radius=0.2, inset=0.0):
    """A white square tile with rounded corners."""
    scale = 4                                           # draw big, shrink: smooth corners
    big = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    m = round(size * inset * scale)
    ImageDraw.Draw(big).rounded_rectangle((m, m, size * scale - 1 - m, size * scale - 1 - m),
                                          radius=round(size * radius * scale), fill=(255, 255, 255, 255))
    return resize(big, (size, size))


def icon_frame(size, full, mark):
    """Every size is a white tile, so the logo is a solid, visible block on any taskbar or wallpaper."""
    canvas = tile(size, radius=0.14 if size in SMALL else 0.18, inset=0.0 if size in SMALL else 0.01)
    if size in SMALL:
        paste_center(canvas, fit(mark, size * 0.90, size * 0.90))      # mark only: words cannot be read this small
    else:
        paste_center(canvas, fit(full, size * 0.74, size * 0.80))
    return canvas


# --- .ico writer: classic BMP entries for every size but 256, which is PNG (what Windows itself does). -------------
def _dib(img):
    w, h = img.size
    bgra = np.array(img.convert("RGBA"))[..., [2, 1, 0, 3]][::-1]            # bottom-up rows, BGRA order
    mask = b"\x00" * (((w + 31) // 32 * 4) * h)                              # alpha does the masking
    pixels = bgra.tobytes()
    return struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(pixels) + len(mask), 0, 0, 0, 0) + pixels + mask


def write_ico(path, frames):
    blobs = []
    for img in frames:
        if img.width >= 256:
            buf = io.BytesIO()
            img.save(buf, "PNG")
            blobs.append(buf.getvalue())
        else:
            blobs.append(_dib(img))
    head = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = b""
    for img, blob in zip(frames, blobs):
        entries += struct.pack("<BBBBHHII", img.width % 256, img.height % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    Path(path).write_bytes(head + entries + b"".join(blobs))


def main():
    src = Image.open(SOURCE).convert("RGBA")
    full = trim(src, pad=4)
    mark = split_mark(trim(src))
    frames = {s: icon_frame(s, full, mark) for s in SIZES}

    (WEB / "icons").mkdir(parents=True, exist_ok=True)
    full.save(WEB / "logo.png")
    pad = round(mark.width * 0.02)
    marked = Image.new("RGBA", (mark.width + 2 * pad, mark.height + 2 * pad), (0, 0, 0, 0))
    marked.alpha_composite(mark, (pad, pad))
    marked.save(WEB / "logo-mark.png")
    for s, img in frames.items():
        img.save(WEB / "icons" / f"icon-{s}.png")
    write_ico(WEB / "favicon.ico", [frames[16], frames[32], frames[48]])
    touch = tile(180, radius=0.0)
    paste_center(touch, fit(mark, 180 * 0.82, 180 * 0.82))
    touch.convert("RGB").save(WEB / "apple-touch-icon.png")

    write_ico(ROOT / "packaging" / "winespos.ico", [frames[s] for s in SIZES])

    # Installer wizard images (Inno Setup wants BMP): tall side panel and the small corner picture.
    big = Image.new("RGBA", (164, 314), (255, 255, 255, 255))
    paste_center(big, fit(full, 140, 220))
    big.convert("RGB").save(ROOT / "packaging" / "wizard_large.bmp")
    small = Image.new("RGBA", (55, 55), (255, 255, 255, 255))
    paste_center(small, fit(mark, 49, 49))
    small.convert("RGB").save(ROOT / "packaging" / "wizard_small.bmp")

    # The server and the installer ship staticfiles/, so keep the served copy in step.
    for name in ("logo.png", "logo-mark.png", "favicon.ico", "apple-touch-icon.png"):
        shutil.copy2(WEB / name, SERVED / name)
    shutil.copytree(WEB / "icons", SERVED / "icons", dirs_exist_ok=True)
    print("Logo files written.")


if __name__ == "__main__":
    main()
