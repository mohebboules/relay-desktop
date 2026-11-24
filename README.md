# relay-desktop

**One Claude Desktop sidebar across several Claude accounts — switch accounts
with one command and keep working in the same sessions.**

The command is `relay`: like runners in a relay race, your accounts take turns
carrying the same conversation.

Claude Desktop keeps a separate list of Code sessions for every account. Sign in
as another account and your sidebar is empty, even though the conversations are
still on disk. `relay` makes every account show the same sidebar, so a session
you started on one account can be continued on another.

```text
$ relay
open now: 1  →  account 2 (work@example.com)
terminal Claude Code → account 2
✓ signed in as work@example.com
```

`relay` builds on [aswap](https://github.com/BlackHole1/aswap) (a fork of
[claude-swap](https://github.com/realiti4/claude-swap)), which gives every
account its own Claude Desktop window, signed in once. `relay` adds:

- **One shared sidebar.** Every account's Code-tab session list points at one
  "home" account's.
- **One-command switching.** Closes the open account's window, opens the next,
  and moves terminal Claude Code to the same account.
- **Sign-in checks.** Warns when a window is signed in as a different account
  than its slot, so usage doesn't land on the wrong account.

> [!IMPORTANT]
> Not affiliated with Anthropic. `relay` relies on how Claude Desktop stores its
> files, which isn't a public interface and can change in any update. Check that
> your use of several accounts fits [Anthropic's terms](https://www.anthropic.com/legal/consumer-terms).

## Requirements

- macOS (Claude Desktop's paths and aswap's per-account windows are macOS-only here)
- Python 3.8+ (the one macOS ships is fine)
- Claude Desktop, and Claude Code in the terminal

Everything else, including aswap, is installed by `install.sh`.

## Install

```bash
git clone https://github.com/<you>/relay-desktop.git
```
```bash
cd relay-desktop && ./install.sh
```

`install.sh`:

1. installs [aswap](https://github.com/BlackHole1/aswap) if it's missing, with
   [uv](https://docs.astral.sh/uv/) (and installs uv first if needed: with Homebrew
   when you have it, otherwise with uv's official installer);
2. links the `relay` command (`~/.local/bin/relay`) to `relay.py` in the clone,
   so `git pull` updates it;
3. tells you what's left: adding your accounts, and `~/.local/bin` on your `PATH`
   if it isn't.

It's safe to rerun at any time. Already manage aswap yourself? `./install.sh --no-aswap`.

**Add your accounts to aswap** (once per account): sign in to Claude Code as that
account (`claude`, then `/login`), then run:

```bash
aswap add
```

## Set up (once)

1. **Close every account's window except the home one** (account 1 by default):
   ```bash
   aswap desktop
   ```
   lists them; `aswap desktop --quit <num>` closes one.
2. **Share the sidebar:**
   ```bash
   relay link
   ```
   An account that already had sessions of its own has them moved to
   `~/.claude-swap-backup/relay-replaced/` — never deleted.
3. **Sign each account's window in to the right account.** For every slot:
   ```bash
   relay <num>
   ```
   and sign in, in the window that opens, as the account aswap has in that slot.
4. **Check:**
   ```bash
   relay status
   ```
   ```text
   slot  account                              window  signed in as                         sidebar
   1     me@example.com                       open    ✓ correct                            home
   2     work@example.com                     closed  ✓ correct                            linked
   3     side@example.com                     closed  ✗ work@example.com                   linked
   ```
   Fix any ✗ by opening that slot (`relay 3`), signing out, and signing in as
   the account in its row. Usage goes to whoever a window is signed in as, not
   to the slot number.

## Use

| To… | Run |
|---|---|
| Move to the next account (1→2→…→1, in aswap's order) | `relay` |
| Go to a specific account | `relay 3` |
| Check sign-ins and sharing | `relay status` |
| See what a command would do | `relay -n`, `relay -n 3`, `relay -n link` |
| Re-share the sidebar | `relay link` |
| Go back to separate sidebars | `relay unlink` |

When an account hits its limit:

1. Let the current reply finish.
2. Run `relay` **in a normal terminal**, not inside Claude Desktop: it closes
   the open window, along with any session running in it. (`relay` refuses to
   switch from inside a Claude session unless you pass `--force`.)
3. In the new window, open the same session from the sidebar and carry on.

### On a keyboard shortcut

Shortcuts → New Shortcut → **Run Shell Script** with `~/.local/bin/relay` →
in the shortcut's details, **Add Keyboard Shortcut**.

### Options

| Option / variable | Default | Meaning |
|---|---|---|
| `--home <num>` / `RELAY_HOME` | `1` | Account whose sidebar everyone shares |
| `-n`, `--dry-run` | | Print what would happen, change nothing |
| `--force` | | Switch even from inside a Claude session |
| `RELAY_ASWAP` | `aswap` | aswap command to call |
| `RELAY_BACKUP_DIR` | `~/.claude-swap-backup` | aswap's data folder |
| `RELAY_SESSIONS_DIR` | `~/Library/Application Support/Claude/claude-code-sessions` | Claude Desktop's session index |
| `RELAY_SIGNIN_WAIT` | `20` | Seconds to wait for the sign-in check after switching |
| `NO_COLOR` | | Plain output |

## How it works

Claude Desktop lists Code sessions from one small JSON file per session,
filed by account and organization:

```text
~/Library/Application Support/Claude/claude-code-sessions/
  <account-uuid>/<org-uuid>/local_<id>.json
```

Each file points at a Claude Code transcript in `~/.claude/projects/`, which
every account reads. aswap already points every account's window at that one
`claude-code-sessions` folder, but each window still reads only its own
`<account>/<org>` sub-folder.

`relay link` turns every other account's sub-folder into a symlink to the home
account's:

```text
<account-2>/<org-2>  →  <account-1>/<org-1>
<account-3>/<org-3>  →  <account-1>/<org-1>
```

Account IDs and order come from aswap's `~/.claude-swap-backup/sequence.json`.
The sign-in check reads `lastKnownAccountUuid` from each window's
`~/.claude-swap-backup/desktop/<num>-<email>/config.json`.

## Limitations

- **One window at a time.** Two windows open on the shared sidebar don't see each
  other's changes and can overwrite each other. `relay` closes the old window
  before opening the new one; if you open two by hand, press ⌘R in one to reload.
- **Switching stops running work.** Closing a window ends any session that is
  mid-reply.
- **Unofficial.** A Claude Desktop update can change the file layout; `relay status`
  is the first thing to run if sharing stops working.
- **macOS only**, like aswap's desktop support.

## Troubleshooting

| Symptom | Fix |
|---|---|
| A window shows an empty sidebar | Close it, `relay link`, open it again |
| `status` shows `private` or `missing` | Close that window, `relay link` |
| `status` shows ✗ | Open that slot, sign out, sign in as the right account |
| `relay: command not found` | Run `./install.sh`; check `~/.local/bin` is on `PATH` |
| `'aswap' not found` | Install aswap, or set `RELAY_ASWAP` |

## Uninstall

```bash
relay unlink
```
```bash
rm ~/.local/bin/relay
```

`unlink` removes only the symlinks; the home account keeps every session, and the
other accounts start with empty sidebars again.

## Development

```bash
python3 -m unittest discover tests -v
```

The tests run `relay` against a fake aswap and temporary folders; they never
touch your real setup.

## License

[MIT](LICENSE)
