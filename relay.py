#!/usr/bin/env python3
"""relay (relay-desktop) — one Claude Desktop sidebar across several Claude accounts.

Built on aswap (https://github.com/BlackHole1/aswap), which gives every account
its own Claude Desktop window. relay links each account's Code-tab session list
to one "home" account's, so every window shows the same sessions, and switches
between accounts with one command.

usage:
  relay                 switch to the next account in aswap's order
  relay <num>           switch to that account
  relay status          per account: window open?, signed in as whom?, sidebar shared?
  relay link            share the home account's sidebar with every account
  relay unlink          undo `link`: every account gets its own sidebar again
  relay help | version

options:
  -n, --dry-run         say what would happen, change nothing
  --home <num>          the account whose sidebar is shared (default 1, or $RELAY_HOME)
  --force               switch even when run from inside a Claude session

environment:
  RELAY_HOME            home account number (default 1)
  RELAY_ASWAP           aswap command (default: aswap)
  RELAY_BACKUP_DIR      aswap's data dir (default: ~/.claude-swap-backup)
  RELAY_SESSIONS_DIR    Claude Desktop's session dir
                        (default: ~/Library/Application Support/Claude/claude-code-sessions)
  RELAY_SIGNIN_WAIT     seconds to wait for the sign-in check after a switch (default 20)
  NO_COLOR              disable colors
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

VERSION = "1.0.0"


def env_path(name, default):
    return Path(os.environ.get(name) or default).expanduser()


ASWAP = os.environ.get("RELAY_ASWAP", "aswap")
BACKUP = env_path("RELAY_BACKUP_DIR", "~/.claude-swap-backup")
SESSIONS = env_path(
    "RELAY_SESSIONS_DIR", "~/Library/Application Support/Claude/claude-code-sessions"
)
SIGNIN_WAIT_S = int(os.environ.get("RELAY_SIGNIN_WAIT", "20"))

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
        raise RelayError(f"'{ASWAP}' not found: install aswap (uv tool install aswap)")


def open_windows():
    """Account numbers whose Desktop window is open, from `aswap desktop`."""
    out = aswap("desktop", quiet=True).stdout or ""
    nums = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in ("●", "○") and "open" in parts[2:4]:
            nums.append(parts[1].rstrip("."))
    return nums


# -- sidebar links ------------------------------------------------------------

def session_dir(a):
    return SESSIONS / a["uuid"] / a["org"]


def link_state(a, home):
    if a["num"] == home["num"]:
        return "home"
    d = session_dir(a)
    if d.is_symlink():
        return "linked" if d.resolve() == session_dir(home).resolve() else "linked-elsewhere"
    return "private" if d.exists() else "missing"


def link(accts, order, home, dry=False, only=None):
    target = session_dir(home)
    if target.is_symlink():
        raise RelayError(f"the home account's session folder is itself a link: {target}")
    if not dry:
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
    opened = open_windows()
    stash = None
    for n in only or order:
        a = accts[n]
        st = link_state(a, home)
        label = f"{n} {a['email']}"
        if st in ("home", "linked"):
            print(f"  {label}: {st}")
            continue
        if n in opened:
            print(f"  {Y}{label}: window is open; close it ({ASWAP} desktop --quit {n}) and rerun{X}")
            continue
        if dry:
            print(f"  {label}: would link ({st})")
            continue
        d = session_dir(a)
        if st == "linked-elsewhere":
            d.unlink()
        elif st == "private":
            # Never delete what an account had on its own: move it aside.
            stash = stash or BACKUP / "relay-replaced" / time.strftime("%Y%m%dT%H%M%S")
            stash.mkdir(parents=True, exist_ok=True)
            own = list(d.rglob("local_*.json"))
            shutil.move(str(d), str(stash / f"{n}-{a['uuid']}"))
            if own:
                print(f"  {Y}{label}: its {len(own)} own session(s) were moved to {stash}{X}")
        d.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        d.symlink_to(target, target_is_directory=True)
        print(f"  {G}{label}: linked{X}")


def unlink(accts, order, home, dry=False):
    opened = [n for n in open_windows() if n != home["num"]]
    if opened and not dry:
        raise RelayError(f"close these accounts' windows first: {', '.join(opened)}")
    for n in order:
        a = accts[n]
        st = link_state(a, home)
        if st in ("linked", "linked-elsewhere"):
            if dry:
                print(f"  {n} {a['email']}: would unlink")
            else:
                session_dir(a).unlink()  # a symlink: removes the link only
                print(f"  {n} {a['email']}: unlinked (own empty sidebar)")
        else:
            print(f"  {n} {a['email']}: {st}")


# -- commands -----------------------------------------------------------------

def status(accts, order, home):
    opened = open_windows()
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
        ls = link_state(a, home)
        ok_link = ls in ("home", "linked")
        problems += (u != a["uuid"]) + (not ok_link)
        win = "open" if n in opened else "closed"
        print(f"{n:<5} {a['email']:<36} {(G if win == 'open' else D)}{win:<7}{X} "
              f"{sc}{signed:<36}{X} {G if ok_link else R}{ls}{X}")
    if problems:
        print(f"\n{Y}✗ or not signed in: `relay <slot>`, sign out in that window if needed, "
              f"sign in as the account in its row.\n"
              f"sidebar not shared: close that account's window, then `relay link`.{X}")
        return 1
    print(f"\n{G}All accounts signed in correctly and sharing one sidebar.{X}")
    return 0


def switch(accts, order, home, target=None, dry=False, force=False):
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
    print(f"open now: {', '.join(opened) or 'none'}  →  account {target} ({a['email']})")
    if dry:
        return 0

    for n in opened:
        if n != target and aswap("desktop", "--quit", n).returncode != 0:
            raise RelayError(f"could not close account {n}'s window; stopping")

    if link_state(a, home) not in ("home", "linked"):
        link(accts, order, home, only=[target])

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
    home_num = os.environ.get("RELAY_HOME", "1")
    args = []
    it = iter(argv)
    for arg in it:
        if arg in ("-n", "--dry-run"):
            dry = True
        elif arg == "--force":
            force = True
        elif arg == "--home":
            home_num = next(it, "")
        elif arg in ("-h", "--help", "help"):
            print(__doc__.strip())
            return 0
        elif arg in ("-V", "--version", "version"):
            print(f"relay {VERSION}")
            return 0
        else:
            args.append(arg)

    accts, order = load_accounts()
    if home_num not in accts:
        raise RelayError(f"home account {home_num!r} isn't one of aswap's accounts ({', '.join(order)})")
    home = accts[home_num]

    cmd = args[0] if args else None
    if cmd == "status":
        return status(accts, order, home)
    if cmd == "link":
        link(accts, order, home, dry=dry)
        return 0
    if cmd == "unlink":
        unlink(accts, order, home, dry=dry)
        return 0
    return switch(accts, order, home, target=cmd, dry=dry, force=force)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except RelayError as e:
        print(f"{R}relay: {e}{X}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
