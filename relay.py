#!/usr/bin/env python3
"""relay (relay-desktop) — one Claude Desktop sidebar across several Claude accounts.

Built on aswap (https://github.com/BlackHole1/aswap), which gives every account
its own Claude Desktop window. Claude Desktop keeps a separate Code-tab session
list per account; relay carries that list from the account you leave to the
account you open, so every account shows the same sessions, and switches
between accounts with one command.

usage:
  relay                 switch to the next account in aswap's order
  relay <num>           switch to that account
  relay status          per account: window open?, signed in as whom?, sidebar up to date?
  relay sync            copy the newest sidebar to every account whose window is closed
  relay help | version

options:
  -n, --dry-run         say what would happen, change nothing
  --from <num>          with sync: copy this account's sidebar (default: the open
                        window's, else the most recently used one)
  --force               switch even when run from inside a Claude session

environment:
  RELAY_ASWAP           aswap command (default: aswap)
  RELAY_BACKUP_DIR      aswap's data dir (default: ~/.claude-swap-backup)
  RELAY_SESSIONS_DIR    Claude Desktop's session dir
                        (default: ~/Library/Application Support/Claude/claude-code-sessions)
  RELAY_SIGNIN_WAIT     seconds to wait for the sign-in check after a switch (default 20)
  NO_COLOR              disable colors
"""

import filecmp
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

VERSION = "2.0.0"


def env_path(name, default):
    return Path(os.environ.get(name) or default).expanduser()


ASWAP = os.environ.get("RELAY_ASWAP", "aswap")
BACKUP = env_path("RELAY_BACKUP_DIR", "~/.claude-swap-backup")
SESSIONS = env_path(
    "RELAY_SESSIONS_DIR", "~/Library/Application Support/Claude/claude-code-sessions"
)
SIGNIN_WAIT_S = int(os.environ.get("RELAY_SIGNIN_WAIT", "20"))
SIDEBAR_GLOB = "local_*.json"  # one file per session; everything else stays per account

_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code):
    return f"\033[{code}m" if _color else ""


G, R, Y, D, B, X = _c(32), _c(31), _c(33), _c(2), _c(1), _c(0)


class RelayError(Exception):
    pass


# -- accounts -----------------------------------------------------------------

def load_accounts():
    """Accounts and their order from aswap's own table (sequence.json)."""
    seq = BACKUP / "sequence.json"
    try:
        data = json.loads(seq.read_text())
    except OSError:
        raise RelayError(f"can't read {seq}: is aswap installed and are accounts added?")
    except ValueError:
        raise RelayError(f"{seq} is not valid JSON")
    accts = {}
    for num, a in data.get("accounts", {}).items():
        if not a.get("uuid") or not a.get("organizationUuid"):
            continue
        accts[str(num)] = {
            "num": str(num),
            "email": a.get("email", "?"),
            "uuid": a["uuid"],
            "org": a["organizationUuid"],
        }
    if not accts:
        raise RelayError("aswap has no accounts yet (aswap add)")
    order = [str(n) for n in data.get("sequence", []) if str(n) in accts]
    order += [n for n in sorted(accts, key=int) if n not in order]
    return accts, order


def profile_dir(a):
    hits = sorted((BACKUP / "desktop").glob(f"{a['num']}-*"))
    return hits[0] if hits else None


def signed_in_uuid(a):
    """The account a window last signed in as (Claude Desktop's config.json)."""
    p = profile_dir(a)
    if not p:
        return None
    try:
        return json.loads((p / "config.json").read_text()).get("lastKnownAccountUuid")
    except (OSError, ValueError):
        return None


def find_account(accts, uuid):
    return next((a for a in accts.values() if a["uuid"] == uuid), None)


# -- aswap --------------------------------------------------------------------

def aswap(*args, quiet=False):
    try:
        return subprocess.run([ASWAP, *args], text=True, capture_output=quiet)
    except FileNotFoundError:
        raise RelayError(f"'{ASWAP}' not found: run ./install.sh (or uv tool install aswap)")


def open_windows():
    """Account numbers whose Desktop window is open, from `aswap desktop`."""
    out = aswap("desktop", quiet=True).stdout or ""
    nums = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in ("●", "○") and "open" in parts[2:4]:
            nums.append(parts[1].rstrip("."))
    return nums


# -- sidebar folders ----------------------------------------------------------
#
# Claude Desktop lists an account's Code sessions from
# <SESSIONS>/<account uuid>/<org uuid>/local_<id>.json. It saves into that
# folder with a call that refuses symlinks, so each account needs a real
# folder; relay keeps them the same by copying.

def session_dir(a):
    return SESSIONS / a["uuid"] / a["org"]


def sidebar_files(d):
    return {p.name: p for p in d.glob(SIDEBAR_GLOB)} if d.is_dir() else {}


def last_used(d):
    """When the app last wrote this account's sidebar (0 if never)."""
    return max((p.stat().st_mtime for p in sidebar_files(d).values()), default=0)


def newest_account(accts, order, among=None):
    nums = [n for n in (among or order) if session_dir(accts[n]).is_dir()]
    return max(nums, key=lambda n: last_used(session_dir(accts[n])), default=None)


def differences(src, dst):
    s, d = sidebar_files(src), sidebar_files(dst)
    changed = [n for n in s if n not in d or not filecmp.cmp(s[n], d[n], shallow=False)]
    removed = [n for n in d if n not in s]
    return changed, removed


def make_real(a, dry=False):
    """Turn an old relay-1.x symlinked folder into a real one with the same
    sessions. Safe while that account's window is open: the sessions are
    identical at that moment, and the app can save again right after."""
    d = session_dir(a)
    if not d.is_symlink():
        return False
    if dry:
        print(f"  {a['num']} {a['email']}: would turn the old link into a real folder")
        return True
    src = d.resolve()
    tmp = d.with_name(d.name + ".relay-tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(mode=0o700)
    for name, p in sidebar_files(src).items():
        shutil.copy2(p, tmp / name)
    d.unlink()  # the link only
    tmp.rename(d)
    print(f"  {G}{a['num']} {a['email']}: old link replaced by a real folder{X}")
    return True


def mirror(src_acct, dst_acct, stash, dry=False):
    """Make dst's session list equal to src's. Files dst has that src doesn't
    are moved into `stash`, never deleted."""
    src, dst = session_dir(src_acct), session_dir(dst_acct)
    changed, removed = differences(src, dst)
    label = f"{src_acct['num']} → {dst_acct['num']}"
    if not changed and not removed:
        print(f"  {label}: already the same")
        return
    if dry:
        print(f"  {label}: would copy {len(changed)}, set aside {len(removed)}")
        return
    dst.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in changed:
        tmp = dst / (name + ".relay-tmp")
        shutil.copy2(src / name, tmp)
        os.replace(tmp, dst / name)
    if removed:
        where = stash / dst_acct["num"]
        where.mkdir(parents=True, exist_ok=True)
        for name in removed:
            shutil.move(str(dst / name), str(where / name))
    print(f"  {G}{label}: copied {len(changed)}"
          + (f", set aside {len(removed)} (in {stash})" if removed else "") + X)


def new_stash():
    return BACKUP / "relay-replaced" / time.strftime("%Y%m%dT%H%M%S")


# -- commands -----------------------------------------------------------------

def status(accts, order):
    opened = open_windows()
    newest = newest_account(accts, order)
    problems = 0
    print(f"{B}{'slot':<5} {'account':<36} {'window':<7} {'signed in as':<36} sidebar{X}")
    for n in order:
        a = accts[n]
        u = signed_in_uuid(a)
        if u is None:
            signed, sc = "not signed in yet", Y
        elif u == a["uuid"]:
            signed, sc = "✓ correct", G
        else:
            other = find_account(accts, u)
            signed, sc = f"✗ {other['email'] if other else u}", R
        problems += u != a["uuid"]

        d = session_dir(a)
        if d.is_symlink():
            side, lc = "old link: run relay sync", R
            problems += 1
        elif not d.is_dir():
            side, lc = "empty (filled on switch)", D
        elif n == newest:
            side, lc = f"newest ({len(sidebar_files(d))} sessions)", G
        else:
            ch, rm = differences(session_dir(accts[newest]), d)
            side, lc = ("up to date", G) if not ch and not rm else \
                (f"{len(ch) + len(rm)} behind (catches up on switch)", Y)
        win = "open" if n in opened else "closed"
        print(f"{n:<5} {a['email']:<36} {(G if win == 'open' else D)}{win:<7}{X} "
              f"{sc}{signed:<36}{X} {lc}{side}{X}")
    if problems:
        print(f"\n{Y}✗ or not signed in: `relay <slot>`, sign out in that window if needed, "
              f"sign in as the account in its row.\n"
              f"old link: run `relay sync` (relay 1.x shared the sidebar by linking, "
              f"which Claude Desktop can't save into).{X}")
        return 1
    print(f"\n{G}All accounts signed in correctly.{X}")
    return 0


def sync(accts, order, src=None, dry=False):
    opened = open_windows()
    for n in order:
        make_real(accts[n], dry=dry)
    if src is None:
        src = opened[0] if len(opened) == 1 else newest_account(accts, order)
    if src not in accts:
        raise RelayError(f"no account {src} (have {', '.join(order)})")
    print(f"copying account {src}'s sidebar ({len(sidebar_files(session_dir(accts[src])))} sessions)")
    stash = new_stash()
    for n in order:
        if n == src:
            continue
        if n in opened:
            print(f"  {Y}{src} → {n}: skipped, window open (it catches up when you switch to it){X}")
            continue
        mirror(accts[src], accts[n], stash, dry=dry)
    return 0


def switch(accts, order, target=None, dry=False, force=False):
    if os.environ.get("CLAUDECODE") and not dry and not force:
        raise RelayError(
            "you seem to be inside a Claude session: switching closes Claude Desktop's "
            "window, and this session with it. Run relay from a normal terminal, "
            "or pass --force.")
    opened = open_windows()
    if target is None:
        cur = next((n for n in opened if n in order), None)
        target = order[(order.index(cur) + 1) % len(order)] if cur else order[0]
    if target not in accts:
        raise RelayError(f"no account {target} (have {', '.join(order)})")
    a = accts[target]
    # The sidebar to carry over: the window being closed, else the last one used.
    src = next((n for n in opened if n != target), None) or newest_account(accts, order)
    print(f"open now: {', '.join(opened) or 'none'}  →  account {target} ({a['email']})"
          + (f", taking account {src}'s sidebar" if src and src != target else ""))
    if dry:
        return 0

    for n in opened:
        if n != target and aswap("desktop", "--quit", n).returncode != 0:
            raise RelayError(f"could not close account {n}'s window; stopping")

    if target not in opened:
        for n in {src, target} - {None}:
            make_real(accts[n])
        if src and src != target:
            mirror(accts[src], a, new_stash())

    # Terminal Claude Code follows, so both bill the same account.
    if aswap("switch", target, quiet=True).returncode == 0:
        print(f"terminal Claude Code → account {target}")

    if aswap("desktop", target).returncode != 0:
        raise RelayError(f"aswap could not open account {target}'s window")
    return check_signin(accts, a)


def check_signin(accts, a):
    """Warn when the window is signed in as another account: usage would go there."""
    before = u = signed_in_uuid(a)
    for _ in range(SIGNIN_WAIT_S):
        if u == a["uuid"] or (u and u != before):
            break
        time.sleep(1)
        u = signed_in_uuid(a)
    if u == a["uuid"]:
        print(f"{G}✓ signed in as {a['email']}{X}")
        return 0
    if u is None:
        print(f"{Y}! this window isn't signed in yet: sign in as {a['email']}{X}")
        return 0
    other = find_account(accts, u)
    print(f"{R}✗ this window is signed in as {other['email'] if other else u}, "
          f"not {a['email']}: usage goes to the wrong account.\n"
          f"  In the window: sign out, then sign in as {a['email']}.{X}")
    return 2


def main(argv):
    dry = force = False
    src = None
    args = []
    it = iter(argv)
    for arg in it:
        if arg in ("-n", "--dry-run"):
            dry = True
        elif arg == "--force":
            force = True
        elif arg == "--from":
            src = next(it, "")
        elif arg in ("-h", "--help", "help"):
            print(__doc__.strip())
            return 0
        elif arg in ("-V", "--version", "version"):
            print(f"relay {VERSION}")
            return 0
        else:
            args.append(arg)

    accts, order = load_accounts()
    cmd = args[0] if args else None
    if cmd == "status":
        return status(accts, order)
    if cmd in ("sync", "link"):  # `link` was relay 1.x's name for this
        return sync(accts, order, src=src, dry=dry)
    if cmd == "unlink":
        raise RelayError("relay 2 no longer links folders; nothing to undo (see `relay sync`)")
    return switch(accts, order, target=cmd, dry=dry, force=force)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except RelayError as e:
        print(f"{R}relay: {e}{X}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
