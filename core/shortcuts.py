"""Pure rules for the POS keyboard shortcuts (no Django).

The manager may change any shortcut (Shop settings, "POS shortcuts"). Changes are checked here so two
actions can never share a key, and keys that are needed for scanning or that the browser or Windows
keep for themselves cannot be chosen. Anything invalid falls back to the defaults, so a bad setting can
never lock a cashier out of the till.

A key is written like "F9", "Delete", "Alt+C" or "Ctrl+Shift+Delete".
"""
import re

DEFAULTS = {
    "help": "F1",
    "search": "F2",
    "scan": "F3",
    "quantity": "F4",
    "hold": "F5",
    "retrieve": "F6",
    "discount": "F7",
    "payment": "F8",
    "complete": "F9",
    "print": "F10",
    "shift": "F11",
    "supervisor": "F12",
    "remove_line": "Delete",
    "customer": "Alt+C",
}

LABELS = {
    "help": "Shortcut guide",
    "search": "Search product by name",
    "scan": "Scan or type a code",
    "quantity": "Change quantity of selected line",
    "hold": "Hold this sale",
    "retrieve": "Retrieve a held sale",
    "discount": "Discount on selected line",
    "payment": "Choose payment method",
    "complete": "Complete the sale",
    "print": "Print receipt",
    "shift": "Shift summary",
    "supervisor": "Supervisor authorization",
    "remove_line": "Remove selected line (needs approval)",
    "customer": "Customer name",
}

# Keys that must keep their normal job: Esc closes a dialog, Enter confirms, Tab and the arrows move around.
# They do not match KEY_RE at all, so they can never be assigned.
KEY_RE = re.compile(r"^(?:(Ctrl|Alt|Shift)\+)*(F(?:[1-9]|1[0-2])|Delete|Insert|Home|End|PageUp|PageDown|[A-Z0-9])$")
MODIFIER_ORDER = ("Ctrl", "Alt", "Shift")

# Windows or the browser take these before the page sees them, so they would silently not work.
RESERVED = {"Ctrl+W", "Ctrl+T", "Ctrl+N", "Ctrl+R", "Ctrl+Q", "Ctrl+L", "Ctrl+P", "Ctrl+F4", "Alt+F4",
            "Ctrl+Shift+Delete", "Ctrl+Alt+Delete", "Alt+Tab"}


class ShortcutError(ValueError):
    pass


def normalize(key):
    """'alt+c' -> 'Alt+C', ' f9 ' -> 'F9'. Raises ShortcutError if the key is not allowed."""
    raw = (key or "").strip()
    if not raw:
        raise ShortcutError("A shortcut cannot be empty.")
    parts = [p.strip() for p in raw.split("+")]
    base = parts[-1]
    mods = parts[:-1]
    canon_mods = []
    for m in mods:
        m_title = m.capitalize()
        if m_title not in MODIFIER_ORDER or m_title in canon_mods:
            raise ShortcutError(f"'{raw}' is not a valid shortcut.")
        canon_mods.append(m_title)
    canon_mods.sort(key=MODIFIER_ORDER.index)
    base_u = base.upper() if len(base) == 1 else base
    if len(base_u) > 1 and not base_u.startswith("F"):
        base_u = base_u[:1].upper() + base_u[1:]
    if len(base_u) > 1 and base_u.startswith("F") and base_u[1:].isdigit():
        base_u = base_u.upper()
    canonical = "+".join(canon_mods + [base_u])
    if not KEY_RE.match(canonical):
        raise ShortcutError(f"'{raw}' is not a valid shortcut.")
    if len(base_u) == 1 and not (set(canon_mods) & {"Ctrl", "Alt"}):
        raise ShortcutError(f"'{raw}' needs Ctrl or Alt: plain letters and digits are used when scanning.")
    if canonical in RESERVED:
        raise ShortcutError(f"'{canonical}' is reserved by Windows or the browser.")
    return canonical


def merge(overrides):
    """Defaults plus the manager's overrides. Returns (shortcuts, errors).

    errors is a list of messages; when it is not empty the returned shortcuts are the plain defaults.
    """
    errors = []
    merged = dict(DEFAULTS)
    if overrides in (None, ""):
        overrides = {}
    if not isinstance(overrides, dict):
        return dict(DEFAULTS), ["Shortcuts must be a list of action: key pairs."]
    for action, key in overrides.items():
        if action not in DEFAULTS:
            errors.append(f"Unknown action '{action}'.")
            continue
        try:
            merged[action] = normalize(key)
        except ShortcutError as exc:
            errors.append(f"{LABELS[action]}: {exc}")
    seen = {}
    for action, key in merged.items():
        if key in seen:
            errors.append(f"'{key}' is used by both '{LABELS[seen[key]]}' and '{LABELS[action]}'.")
        seen[key] = action
    if errors:
        return dict(DEFAULTS), errors
    return merged, []


def effective(overrides):
    """What the POS screen should use: valid overrides, or the defaults if anything is wrong."""
    return merge(overrides)[0]
