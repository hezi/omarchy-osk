#!/bin/bash
# Report whether the on-screen keyboard can actually type.
#
#   check-deps.sh          print "ok", or "missing: <what>"
#   check-deps.sh --nag    same, but if something is missing, fire a desktop
#                          notification pointing at setup.sh - once (a state
#                          flag stops it repeating on every shell start)
here=$(cd "$(dirname "$0")" && pwd)
miss=()
command -v ydotool >/dev/null 2>&1 || miss+=(ydotool)
command -v wtype   >/dev/null 2>&1 || miss+=(wtype)
python3 -c "import gi" >/dev/null 2>&1 || miss+=(python-gobject)
systemctl --user is-active ydotool.service >/dev/null 2>&1 || miss+=(ydotool.service)

if (( ${#miss[@]} == 0 )); then
  echo ok
  exit 0
fi
status="missing: ${miss[*]}"
echo "$status"

if [[ ${1:-} == --nag ]]; then
  flag="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/piccolo-osk-setup-nagged"
  [[ -f $flag ]] && exit 0
  mkdir -p "$(dirname "$flag")"; : >"$flag"
  body="Run:  bash $here/setup.sh   ($status)"
  omarchy-notification-send -u critical "On-screen keyboard needs setup" "$body" 2>/dev/null \
    || notify-send -u critical "On-screen keyboard needs setup" "$body" 2>/dev/null || true
fi
