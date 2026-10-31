# Hermes Pattybot configuration

This directory contains the non-secret Pattybot persona and DoorDash skill installed into `~/.hermes/`.

Runtime secrets remain outside Git:

- `~/.hermes/auth.json` — OpenAI Codex OAuth
- `~/.hermes/.env` — Telegram bot token and allowlists
- `~/.hermes/config.yaml` — generated Hermes configuration
- `~/.hermes/state.db`, `sessions/`, and `logs/` — live state

Intended runtime settings:

- provider/model: `openai-codex` / `gpt-6.1-terra` (the global `model.default`, so cron jobs without their own model use it too)
- reasoning effort: `medium`
- gateway streaming: enabled
- local terminal cwd: `/Users/pattybot/.hermes/workspace`
- DM allowlist: George (`7953915703`)
- group allowlist: George and Michelle's shared chat (`-4998071511`)
- group behavior: observe ordinary chatter; respond on mention/reply
- DoorDash: search/cart/preview for collaborators; checkout URL only after owner confirmation; never direct-submit

## macOS Keychain requirement

`dd-cli` stores its DoorDash credentials in the macOS login Keychain. The Hermes
gateway must therefore run in the logged-in `Aqua` launchd session, not the
background user session. The active LaunchAgent should have:

```xml
<key>LimitLoadToSessionType</key>
<array>
  <string>Aqua</string>
</array>
```

It must also set `HOME=/Users/pattybot`. If a Hermes install or update rewrites
`~/Library/LaunchAgents/ai.hermes.gateway.plist`, run:

```bash
./hermes/bin/repair-gateway-keychain
```

The script validates the plist, removes any copy of the gateway loaded in the
background user domain, and reloads it in `gui/<uid>`.
