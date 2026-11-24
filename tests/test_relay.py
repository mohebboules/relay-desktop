"""Tests for relay. They run the real script against a fake aswap and temp dirs,
so nothing on the machine is touched.

    python3 -m unittest discover tests
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

RELAY = Path(__file__).resolve().parent.parent / "relay.py"

ACCOUNTS = {
    "1": ("one@example.com", "aaaa1111", "org-a"),
    "2": ("two@example.com", "bbbb2222", "org-b"),
    "3": ("three@example.com", "cccc3333", "org-c"),
}

FAKE_ASWAP = textwrap.dedent(r'''
    #!/usr/bin/env python3
    """Stands in for aswap: keeps open windows in state.json, logs every call."""
    import json, sys
    from pathlib import Path
    root = Path(__file__).parent
    state = json.loads((root / "state.json").read_text())
    with open(root / "calls.log", "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\n")
    args = sys.argv[1:]
    if args[:1] == ["desktop"]:
        if len(args) == 1:
            print("Claude Desktop profiles:")
            for n in state["accounts"]:
                mark, word = ("●", "open") if n in state["open"] else ("○", "closed")
                print(f"  {mark} {n}. user{n}@example.com  {word}  PID 1")
        elif args[1] == "--quit":
            state["open"] = [n for n in state["open"] if n != args[2]]
        else:
            state["open"].append(args[1])
    (root / "state.json").write_text(json.dumps(state))
''').lstrip()


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.backup = self.tmp / "backup"
        self.sessions = self.tmp / "sessions"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        seq = {
            "sequence": [1, 2, 3],
            "accounts": {
                n: {"email": e, "uuid": u, "organizationUuid": o}
                for n, (e, u, o) in ACCOUNTS.items()
            },
        }
        self.backup.mkdir()
        (self.backup / "sequence.json").write_text(json.dumps(seq))
        for n, (_, u, _) in ACCOUNTS.items():
            self.sign_in(n, u)
        self.write_session("1", "s1")
        aswap = self.bin / "aswap"
        aswap.write_text(FAKE_ASWAP)
        aswap.chmod(0o755)
        self.set_open([])

    # -- helpers ------------------------------------------------------------

    def sign_in(self, num, uuid):
        email = ACCOUNTS[num][0].replace("@", "_")
        p = self.backup / "desktop" / f"{num}-{email}"
        p.mkdir(parents=True, exist_ok=True)
        (p / "config.json").write_text(json.dumps({"lastKnownAccountUuid": uuid}))

    def sdir(self, num):
        _, u, o = ACCOUNTS[num]
        return self.sessions / u / o

    def write_session(self, num, sid, body="{}", age=0):
        d = self.sdir(num)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"local_{sid}.json"
        p.write_text(body)
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def sessions_of(self, num):
        return sorted(p.name for p in self.sdir(num).glob("local_*.json"))

    def set_open(self, nums):
        (self.bin / "state.json").write_text(json.dumps({"accounts": list(ACCOUNTS), "open": nums}))

    def open_now(self):
        return json.loads((self.bin / "state.json").read_text())["open"]

    def run_relay(self, *args, env_extra=None):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env.update(
            RELAY_ASWAP=str(self.bin / "aswap"),
            RELAY_BACKUP_DIR=str(self.backup),
            RELAY_SESSIONS_DIR=str(self.sessions),
            RELAY_SIGNIN_WAIT="0",
            NO_COLOR="1",
        )
        env.update(env_extra or {})
        return subprocess.run([sys.executable, str(RELAY), *args],
                              capture_output=True, text=True, env=env)

    # -- switching carries the sidebar --------------------------------------

    def test_switch_copies_sidebar_from_closed_window(self):
        self.set_open(["1"])
        r = self.run_relay("2")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.open_now(), ["2"])
        self.assertEqual(self.sessions_of("2"), ["local_s1.json"])
        self.assertFalse(self.sdir("2").is_symlink())

    def test_changes_on_one_account_reach_the_next(self):
        self.set_open(["1"])
        self.run_relay("2")
        self.write_session("2", "new-on-2")                 # made while on account 2
        (self.sdir("2") / "local_s1.json").write_text('{"title": "renamed"}')
        r = self.run_relay("3")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.sessions_of("3"), ["local_new-on-2.json", "local_s1.json"])
        self.assertEqual((self.sdir("3") / "local_s1.json").read_text(), '{"title": "renamed"}')

    def test_deleted_session_is_set_aside_not_deleted(self):
        self.write_session("2", "s1")
        self.write_session("2", "old", body='{"keep": true}')
        self.set_open(["1"])
        self.run_relay("2")
        self.assertEqual(self.sessions_of("2"), ["local_s1.json"])
        kept = list((self.backup / "relay-replaced").rglob("local_old.json"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(), '{"keep": true}')

    def test_no_window_open_uses_most_recently_used_account(self):
        self.write_session("1", "s1", age=3600)
        self.write_session("3", "fresh")                     # account 3 used last
        r = self.run_relay("2")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.sessions_of("2"), ["local_fresh.json"])

    def test_rotation_wraps(self):
        for start, expect in (("1", "2"), ("2", "3"), ("3", "1")):
            self.set_open([start])
            r = self.run_relay()
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(self.open_now(), [expect])

    # -- sync -----------------------------------------------------------------

    def test_sync_fills_closed_accounts_and_skips_open_ones(self):
        self.set_open(["2"])
        self.write_session("2", "s1")
        self.write_session("2", "two-only")
        r = self.run_relay("sync")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.sessions_of("1"), ["local_s1.json", "local_two-only.json"])
        self.assertEqual(self.sessions_of("3"), ["local_s1.json", "local_two-only.json"])

    def test_sync_never_writes_into_an_open_window(self):
        self.set_open(["1", "3"])
        self.write_session("3", "only-3")
        self.run_relay("sync", "--from", "1")
        self.assertEqual(self.sessions_of("3"), ["local_only-3.json"])
        self.assertEqual(self.sessions_of("2"), ["local_s1.json"])

    def test_sync_converts_old_links_to_real_folders(self):
        for n in ("2", "3"):
            self.sdir(n).parent.mkdir(parents=True, exist_ok=True)
            self.sdir(n).symlink_to(self.sdir("1"), target_is_directory=True)
        self.set_open(["2"])                                 # even with a window open
        r = self.run_relay("sync")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        for n in ("2", "3"):
            self.assertFalse(self.sdir(n).is_symlink())
            self.assertEqual(self.sessions_of(n), ["local_s1.json"])
        self.assertTrue((self.sdir("1") / "local_s1.json").exists())

    def test_dry_run_changes_nothing(self):
        self.run_relay("-n", "sync")
        self.assertFalse(self.sdir("2").exists())
        self.set_open(["1"])
        r = self.run_relay("-n")
        self.assertIn("→  account 2", r.stdout)
        self.assertEqual(self.open_now(), ["1"])

    # -- status ---------------------------------------------------------------

    def test_status_ok_and_shows_behind(self):
        self.run_relay("sync")
        r = self.run_relay("status")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("up to date", r.stdout)
        self.write_session("1", "newer")
        r = self.run_relay("status")
        self.assertIn("1 behind", r.stdout)

    def test_status_flags_wrong_sign_in_and_old_links(self):
        self.sign_in("3", "bbbb2222")
        self.sdir("2").parent.mkdir(parents=True, exist_ok=True)
        self.sdir("2").symlink_to(self.sdir("1"), target_is_directory=True)
        r = self.run_relay("status")
        self.assertEqual(r.returncode, 1)
        self.assertIn("✗ two@example.com", r.stdout)
        self.assertIn("old link", r.stdout)

    # -- safety ---------------------------------------------------------------

    def test_switch_warns_on_wrong_sign_in(self):
        self.sign_in("2", "cccc3333")
        self.set_open(["1"])
        r = self.run_relay("2")
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage goes to the wrong account", r.stdout)

    def test_switch_refuses_inside_claude_session(self):
        self.set_open(["1"])
        r = self.run_relay("2", env_extra={"CLAUDECODE": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.open_now(), ["1"])
        self.run_relay("2", "--force", env_extra={"CLAUDECODE": "1"})
        self.assertEqual(self.open_now(), ["2"])

    def test_unknown_account(self):
        r = self.run_relay("9")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no account 9", r.stderr)


if __name__ == "__main__":
    unittest.main()
