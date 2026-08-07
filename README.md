# supa — multi-profile Supabase CLI

`supa` is a thin wrapper around the [Supabase CLI](https://supabase.com/docs/guides/cli)
that manages **multiple accounts (profiles)** side by side, without the OS keychain.

It solves one problem: the official CLI keeps a single token per machine
(`~/.supabase/access-token` or the native keychain), so switching between
accounts means re-logging-in and clobbering the previous token. Agents, CI,
SSH sessions and headless machines often **cannot write to the keychain at
all**, which makes `supabase --profile` silently fall back to one token.

`supa` stores one token per named profile and injects it as
`SUPABASE_ACCESS_TOKEN` (the highest-precedence source the real CLI reads) for
each command — no keychain, no re-login, no clobbering.

## Install

Requires Python 3.9+ (stdlib only) and the Supabase CLI on `PATH`.

```bash
# pip / pipx / uv
pipx install supabase-profiles
# or straight from the repo
curl -fsSL https://raw.githubusercontent.com/themuuln/supabase-profiles/main/src/supa/cli.py -o ~/.local/bin/supa && chmod +x ~/.local/bin/supa
```

## Usage

```bash
supa login zerone                     # opens the token page, paste → stored + activated
supa login tolom --token sbp_...      # explicit token
supa ls                               # profiles, active flag, orgs (live lookup)
supa use zerone                       # default for plain `supabase` commands
supa current                          # show the active profile
supa run tolom db push                # one command under a specific account
supa token zerone                     # print a token (CI/export)
supa rm tolom                         # remove a profile
supa projects list                    # passthrough: runs with the active profile
```

### How `supa login` gets a token (in order)

1. `--token <T>`
2. `--from-file` — captures whatever the Supabase CLI currently uses (keychain `supabase` → `~/.supabase/access-token`)
3. `SUPABASE_ACCESS_TOKEN` env var
4. interactive paste prompt (TTY only — opens the dashboard token page)

Every token is validated against the Management API (`GET /organizations`)
before it is stored, and the profile's orgs are cached for `supa ls`.

### Shell aliases

`supa` regenerates a token file (default `~/.cache/zsh/supabase-tokens.sh`)
on every change, one var per profile:

```sh
__sb_zerone="sbp_..."
__sb_tolom="sbp_..."
```

Source it and define per-app aliases any way you like, e.g.:

```sh
alias s-zerone='SUPABASE_ACCESS_TOKEN="$__sb_zerone" supabase'
alias s-tolom='SUPABASE_ACCESS_TOKEN="$__sb_tolom" supabase'
```

### Environment overrides

| Variable | Default | Purpose |
|---|---|---|
| `SUPA_CONFIG_DIR` | `~/.config/supa` | token store directory (`profiles.json`) |
| `SUPA_ZSH_FILE` | `~/.cache/zsh/supabase-tokens.sh` | regenerated token file for aliases |
| `SUPA_ACCESS_TOKEN_FILE` | `~/.supabase/access-token` | file written by `supa use` |
| `SUPA_API_BASE` | `https://api.supabase.com/v1` | Management API base (staging/self-hosted) |

## Security

- Tokens are stored **plaintext in `profiles.json`, mode 600** — the same
  posture as the Supabase CLI's own file fallback. Protect the file with
  filesystem permissions; on multi-user machines consider full-disk encryption.
- `supa` never prints tokens (except explicit `supa token <profile>`),
  sends no telemetry, and only calls the Management API to validate
  tokens and list orgs.
- Rotate a token in the Supabase dashboard, then re-run
  `supa login <profile> --token <new>`.
- Sharing the store across machines is your choice: keep `profiles.json` out
  of git; use encrypted dotfile sync (e.g. chezmoi age) if you do.

## Why not `supabase --profile`?

The official CLI's profile support stores tokens in the native credentials
store (keychain). That works in interactive desktop sessions, but headless
contexts (agents, SSH, CI, launchd) often fail keychain writes with
"User interaction is not allowed", and the CLI silently falls back to a
single `~/.supabase/access-token` — making `--profile` a no-op. `supa`
sidesteps the keychain entirely by injecting the token via the environment.

## Development

```bash
pip install -e . pytest
pytest
```

## License

MIT
