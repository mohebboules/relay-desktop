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

- **One shared sidebar.** Each switch carries the Code-tab session list from the
  account you leave to the account you open.
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

1. **Open your main account** (the one whose sessions you want everywhere) and
   close the others: `aswap desktop` lists the windows, `aswap desktop --quit <num>`
   closes one.
2. **Copy its sidebar to every account:**
   ```bash
   relay sync
   ```
   It copies from the open window. Sessions another account had on its own are
   moved to `~/.claude-swap-backup/relay-replaced/`, never deleted.
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
   1     me@example.com                       open    ✓ correct                            newest (12 sessions)
   2     work@example.com                     closed  ✓ correct                            up to date
   3     side@example.com                     closed  ✗ work@example.com                   2 behind (catches up on switch)
   ```
   Fix any ✗ by opening that slot (`relay 3`), signing out, and signing in as
   the account in its row. Usage goes to whoever a window is signed in as, not
   to the slot number.

## Use

| To… | Run |
|---|---|
| Move to the next account (1→2→…→1, in aswap's order) | `relay` |
| Go to a specific account | `relay 3` |
| Check sign-ins and sidebars | `relay status` |
| Copy the newest sidebar to every closed account now | `relay sync` |
| See what a command would do | `relay -n`, `relay -n 3`, `relay -n sync` |

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
| `--from <num>` | open window, else last used | With `sync`: the account to copy from |
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

When you switch, `relay` closes the open window, then makes the next account's
sub-folder match the one you just left: new and changed session files are
copied, and files the other account no longer has are moved aside to
`~/.claude-swap-backup/relay-replaced/`. Only `local_*.json` files are copied;
the rest of each folder (scheduled tasks and other per-account state) stays
with its account. With no window open, the account used most recently is the
source.

Why copy instead of a symlink? Claude Desktop saves session files with a call
that refuses to follow symlinks, so a linked folder can be read but not saved
into. relay 1.x linked the folders; `relay sync` (or any switch) converts those
links to real folders.

Account IDs and order come from aswap's `~/.claude-swap-backup/sequence.json`.
The sign-in check reads `lastKnownAccountUuid` from each window's
`~/.claude-swap-backup/desktop/<num>-<email>/config.json`.

## Limitations

- **One window at a time.** Changes are carried over when you switch with `relay`.
  If you open two accounts' windows by hand, each keeps its own changes until the
  next `relay` switch or `relay sync`, and the last-used one wins.
- **Switching stops running work.** Closing a window ends any session that is
  mid-reply.
- **Unofficial.** A Claude Desktop update can change the file layout; `relay status`
  is the first thing to run if sharing stops working.
- **macOS only**, like aswap's desktop support.

## Troubleshooting

| Symptom | Fix |
|---|---|
| A window shows an empty or old sidebar | Close it, `relay sync`, open it again |
| `status` shows `old link` | `relay sync` |
| `status` shows ✗ | Open that slot, sign out, sign in as the right account |
| `relay: command not found` | Run `./install.sh`; check `~/.local/bin` is on `PATH` |
| `'aswap' not found` | Install aswap, or set `RELAY_ASWAP` |

## Uninstall

```bash
rm ~/.local/bin/relay
```

Every account keeps a real copy of the sidebar it last had; nothing else to undo.

## Development

```bash
python3 -m unittest discover tests -v
```

The tests run `relay` against a fake aswap and temporary folders; they never
touch your real setup.

## License

[MIT](LICENSE)
