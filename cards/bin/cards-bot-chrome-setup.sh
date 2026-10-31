#!/bin/bash
# cards-bot-chrome-setup.sh — one-time bootstrap for the persistent cards Chrome.
#
# What this does:
#   1. Verify port 19223 isn't already taken (would prevent daemon binding).
#   2. Create the persistent user-data-dir at the canonical path.
#   3. Verify the LaunchAgent plist symlink resolves into the dotfiles repo.
#   4. Print the launchctl bootstrap + 1Password setup instructions.
#
# What this deliberately doesn't do:
#   - Copy cookies from your real Chrome profile. We learned via the twitter
#     setup that route brings WAL corruption hazards and produces cookies
#     with weird provenance. Instead, the daemon launches with an empty
#     profile. The daily job signs in from 1Password and explicitly signs out;
#     the stable profile preserves the device/browser fingerprint.
#   - Quit any running Chrome. Doesn't touch the user's daily Chrome at all.
#     Multiple Chrome.app instances with different --user-data-dirs coexist
#     fine (Chromium's process singleton is keyed to the data dir). Cards bot
#     Chrome will run alongside twitter bot Chrome AND the user's daily Chrome.

set -euo pipefail

PERSISTENT_DIR="$HOME/Library/Application Support/cards-bot-chrome"
PLIST="$HOME/Library/LaunchAgents/com.pattybot.cards-bot-chrome.plist"
DEBUG_PORT=19223

echo "===> cards-bot-chrome bootstrap"
echo

# 1. Port collision guard.
if lsof -nP -iTCP:${DEBUG_PORT} -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: port ${DEBUG_PORT} is already in use by:" >&2
  lsof -nP -iTCP:${DEBUG_PORT} -sTCP:LISTEN >&2
  echo "Kill that process or pick a different port (edit the plist + this script)." >&2
  exit 1
fi
echo "  ✓ port ${DEBUG_PORT} is free"

# 2. Create the persistent dir. Chrome will populate Default/ on first launch.
mkdir -p "$PERSISTENT_DIR"
echo "  ✓ persistent dir ready: $PERSISTENT_DIR"

# 3. Verify the plist symlink resolves into the dotfiles repo.
if [ ! -L "$PLIST" ]; then
  echo "ERROR: expected a symlink at $PLIST, found ${PLIST:+something else}" >&2
  echo "Run \`stow -t ~ cards\` from ~/dotfiles to symlink it." >&2
  exit 1
fi
RESOLVED=$(readlink -f "$PLIST" 2>/dev/null || python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$PLIST")
EXPECTED_PREFIX="$HOME/dotfiles/cards/"
if [ ! -f "$RESOLVED" ]; then
  echo "ERROR: plist symlink is dangling — resolves to $RESOLVED which doesn't exist" >&2
  exit 1
fi
case "$RESOLVED" in
  "$EXPECTED_PREFIX"*) ;;
  *)
    echo "ERROR: plist symlink resolves outside the cards package: $RESOLVED" >&2
    echo "Expected it to resolve under $EXPECTED_PREFIX" >&2
    exit 1
    ;;
esac
echo "  ✓ LaunchAgent plist symlink ok: $PLIST -> $RESOLVED"

cat <<EOF

===> Next steps:

1. Load the daemon:
     launchctl bootstrap gui/\$(id -u) "$PLIST"

2. Wait ~3 seconds, then verify the daemon is listening:
     curl -fsS http://127.0.0.1:${DEBUG_PORT}/json/version

   Expected: a JSON blob with "Browser":"Chrome/<version>".

3. A Chrome window will appear (the cards bot's). Its stable device profile is:
     $PERSISTENT_DIR

4. Offers run through the Hermes credit-card-offers skill and cron job,
   which attach to this Chrome on port 19223. The old 1Password/Keychain
   provisioners, prefire, login and offers scripts were
   removed from this repo on 2026-10-31 (recoverable from git history before that commit).

NEVER do these — they were considered and rejected:
  - Don't import cookies from your daily Chrome. Bank cookies frequently
    pin to a device fingerprint that won't transfer cleanly, causing
    immediate re-auth challenges.
  - Don't put bank credentials or the 1Password service token in plist files,
    shell arguments, environment files, or the repository.
  - Don't run this on a different machine. The "trusted device" cookies
    are bound to this Mac mini's network/hardware fingerprint.
EOF
