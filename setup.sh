#!/bin/bash
#
# One-time setup for the Piccolo on-screen keyboard (plugin id: piccolo.osk).
#
# `omarchy plugin add` only clones and loads the QML - it runs no scripts - so
# the pieces the keyboard needs to actually type have to be installed once:
#
#   * ydotool + its user service   inject keystrokes as a real uinput keyboard,
#                                  so punctuation reaches Chromium and Super+…
#                                  binds reach Hyprland (fcitx5's own forwarding
#                                  drops modifiers)
#   * wtype                        fallback for characters off the US layout
#   * python-gobject               the fcitx5 D-Bus bridge (focus detection)
#   * a keyd exclusion             so keyd does not re-grab ydotool's device
#
# Focus-driven pop-up (the keyboard appearing when you tap a text field) also
# needs fcitx5 running as your input method with its virtual-keyboard addon;
# that is left to you (see README) because it changes your system-wide IM. The
# keyboard is fully usable without it via the edge swipe or Super+Shift+K.
#
#   ./setup.sh            install and enable everything above
#   ./setup.sh --remove   undo the ydotool service and keyd exclusion
#
set -euo pipefail
say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m==> %s\033[0m\n' "$*"; }

KEYD=/etc/keyd/default.conf
YDOTOOL_ID="-2333:6666"

if [[ ${1:-} == --remove ]]; then
  say "Disabling the ydotool user service"
  systemctl --user disable --now ydotool.service 2>/dev/null || true
  if [[ -f $KEYD ]] && grep -q -- "$YDOTOOL_ID" "$KEYD"; then
    say "Removing the keyd exclusion (sudo)"
    sudo sed -i "\|$YDOTOOL_ID|d" "$KEYD"
    sudo keyd reload 2>/dev/null || true
  fi
  note "Packages (ydotool, wtype, python-gobject) were left installed; remove them by hand if you want."
  note "Remove the plugin itself with: omarchy plugin remove piccolo.osk"
  exit 0
fi

command -v pacman >/dev/null || { warn "This setup script is written for Arch/Omarchy (pacman)."; exit 1; }

# --- packages ----------------------------------------------------------------
say "Checking packages (ydotool, wtype, python-gobject)"
missing=()
for p in ydotool wtype python-gobject; do pacman -Q "$p" >/dev/null 2>&1 || missing+=("$p"); done
if (( ${#missing[@]} )); then
  note "installing: ${missing[*]}"
  sudo pacman -S --needed --noconfirm "${missing[@]}"
else
  note "already installed"
fi

# --- ydotool user service ----------------------------------------------------
if ! systemctl --user is-active ydotool.service >/dev/null 2>&1; then
  say "Enabling the ydotool user service"
  systemctl --user enable --now ydotool.service
else
  note "ydotool.service already running"
fi

# --- keyd exclusion ----------------------------------------------------------
# keyd grabs every keyboard and re-emits it through its own virtual device; it
# must ignore ydotool's device or the keys the on-screen keyboard injects get
# swallowed (and, in a tablet setup, silenced with the folded-back keyboard).
if [[ -f $KEYD ]]; then
  if grep -q -- "$YDOTOOL_ID" "$KEYD"; then
    note "keyd already excludes the ydotool device"
  else
    say "Excluding the ydotool device from keyd (sudo)"
    sudo cp "$KEYD" "$KEYD.bak.$(date +%s)"
    sudo python3 - "$KEYD" <<'PY'
import io, sys
p = sys.argv[1]; s = io.open(p).read()
add = ("[ids]\n*\n# ydotoold virtual device: keys injected by the on-screen keyboard must not\n"
       "# be re-routed through keyd (whose virtual keyboard a tablet mode disables).\n-2333:6666\n")
s = s.replace("[ids]\n*\n", add, 1) if "[ids]\n*\n" in s else add + "\n" + s
io.open(p, "w").write(s)
PY
    sudo keyd reload 2>/dev/null || true
  fi
else
  note "keyd is not configured on this system; nothing to exclude"
fi

# --- fcitx5 note -------------------------------------------------------------
if ! pgrep -x fcitx5 >/dev/null 2>&1; then
  warn "fcitx5 is not running."
  note "The keyboard still works via the bottom-edge swipe and Super+Shift+K,"
  note "but pop-up-on-text-field-focus needs fcitx5 as your input method with"
  note "its virtual-keyboard addon. See the README for the one-time IM setup."
fi

say "Done. Reload the plugin: omarchy-shell shell rescanPlugins  (or: omarchy restart shell)"
