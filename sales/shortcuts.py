"""POS keyboard shortcuts: defaults, and the checks that stop a manager's changes from breaking the till.

Pure Python. The manager may rebind any action to another function key (or Alt + letter), but:
  * every key must belong to exactly one action (no conflicts),
  * Escape, Enter, Tab and Shift+Tab are fixed and cannot be bound, so a shortcut can never replace
    them. Escape only ever closes a harmless dialog; it cannot skip an approval.
Whatever is configured, the server still demands the approval, so a shortcut grants nothing.
"""

ACTIONS = {
    "help": "Show the shortcut guide",
    "search": "Search product by name",
    "scan": "Go to the scan / code box",
    "quantity": "Change quantity of the selected line",
    "hold": "Hold the current sale",
    "retrieve": "Retrieve a held sale",
    "discount": "Discount the selected line",
    "payment": "Choose payment method",
    "complete": "Complete the sale",
    "print": "Print the last receipt",
    "shift": "Shift summary",
    "approval": "Supervisor authorization (remove, void, price)",
    "customer": "Customer name and KRA PIN",
    "refund": "Refunds (ask, approve, pay back)",
}

DEFAULTS = {
    "help": "F1", "search": "F2", "scan": "F3", "quantity": "F4", "hold": "F5", "retrieve": "F6",
    "discount": "F7", "payment": "F8", "complete": "F9", "print": "F10", "shift": "F11",
    "approval": "F12", "customer": "Alt+C", "refund": "Alt+R",
}

FIXED_KEYS = {"Escape", "Enter", "Tab", "Shift+Tab", "Delete", "ArrowUp", "ArrowDown"}
_FKEYS = {f"F{n}" for n in range(1, 13)}
# Ctrl+letter is not offered: Ctrl+P, Ctrl+W, Ctrl+T and friends belong to the browser or the printer.
_COMBOS = {f"Alt+{ch}" for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
ALLOWED_KEYS = _FKEYS | _COMBOS


class ShortcutError(ValueError):
    pass


def merged(overrides):
    """Defaults with the manager's overrides applied. Raises ShortcutError if the result is unusable."""
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise ShortcutError("Shortcuts must be a list of action: key pairs.")
    result = dict(DEFAULTS)
    for action, key in overrides.items():
        if action not in ACTIONS:
            raise ShortcutError(f"Unknown action '{action}'.")
        key = str(key).strip()
        if key in FIXED_KEYS:
            raise ShortcutError(f"{key} is reserved and cannot be a shortcut.")
        if key not in ALLOWED_KEYS:
            raise ShortcutError(f"'{key}' is not an allowed shortcut. Use F1-F12 or Alt+letter.")
        result[action] = key
    seen = {}
    for action, key in result.items():
        if key in seen:
            raise ShortcutError(f"{key} is used for both '{ACTIONS[seen[key]]}' and '{ACTIONS[action]}'.")
        seen[key] = action
    return result
