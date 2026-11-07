"""Label sizes and what goes on a label (pure data, no Django)."""

# key: (description, width_mm, height_mm, columns, page). Rolls print one label per page;
# the A4 sheet prints a grid of labels on ordinary paper.
SIZES = {
    "roll-50x30": ("Roll 50 x 30 mm", 50, 30, 1, None),
    "roll-40x25": ("Roll 40 x 25 mm", 40, 25, 1, None),
    "roll-58x40": ("Roll 58 x 40 mm (58 mm printer)", 58, 40, 1, None),
    "roll-80x50": ("Roll 80 x 50 mm (80 mm printer)", 80, 50, 1, None),
    "a4-3x8": ("A4 sheet, 3 x 8 labels (70 x 37 mm)", 70, 37, 3, "A4"),
    "a4-4x10": ("A4 sheet, 4 x 10 labels (48 x 25 mm)", 48, 25, 4, "A4"),
}
DEFAULT_SIZE = "roll-50x30"
MAX_COPIES = 100
MAX_LABELS = 600


def size_choices():
    return [(k, v[0]) for k, v in SIZES.items()]


def size_info(key):
    desc, w, h, cols, page = SIZES.get(key, SIZES[DEFAULT_SIZE])
    return {"key": key if key in SIZES else DEFAULT_SIZE, "description": desc, "w": w, "h": h, "cols": cols, "page": page}
