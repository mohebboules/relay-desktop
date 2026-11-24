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
        for n, (e, u, _) in ACCOUNTS.items():
            self.sign_in(n, u)
        home = self.sessions / "aaaa1111" / "org-a"
        home.mkdir(parents=True)
        (home / "local_s1.json").write_text("{}")
        aswap = self.bin / "aswap"
        aswap.write_text(FAKE_ASWAP)
        aswap.chmod(0o755)
        self.set_open([])

    def sign_in(self, num, uuid):
        email = ACCOUNTS[num][0].replace("@", "_")
        p = self.backup / "desktop" / f"{num}-{email}"
        p.mkdir(parents=True, exist_ok=True)
        (p / "config.json").write_text(json.dumps({"lastKnownAccountUuid": uuid}))

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

    def sdir(self, num):
        _, u, o = ACCOUNTS[num]
        return self.sessions / u / o

    # -- link -------------------------------------------------------------

    def test_link_shares_home_sidebar(self):
        r = self.run_relay("link")
        self.assertEqual(r.returncode, 0, r.stderr)
        for n in ("2", "3"):
            self.assertTrue(self.sdir(n).is_symlink())
            self.assertTrue((self.sdir(n) / "local_s1.json").exists())

    def test_link_moves_own_sessions_aside_never_deletes(self):
        own = self.sdir("2")
        own.mkdir(parents=True)
        (own / "local_mine.json").write_text('{"x": 1}')
        r = self.run_relay("link")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.sdir("2").is_symlink())
        kept = list((self.backup / "relay-replaced").rglob("local_mine.json"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(), '{"x": 1}')

    def test_link_skips_open_window(self):
        self.set_open(["2"])
        self.run_relay("link")
        self.assertFalse(self.sdir("2").exists())
        self.assertTrue(self.sdir("3").is_symlink())

    def test_dry_run_link_changes_nothing(self):
        self.run_relay("-n", "link")
        self.assertFalse(self.sdir("2").exists())

    def test_unlink_removes_only_links(self):
        self.run_relay("link")
        r = self.run_relay("unlink")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.sdir("2").exists())
        self.assertTrue((self.sdir("1") / "local_s1.json").exists())

    # -- status -----------------------------------------------------------

    def test_status_ok(self):
        self.run_relay("link")
        r = self.run_relay("status")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("All accounts signed in correctly", r.stdout)

    def test_status_flags_wrong_sign_in(self):
        self.run_relay("link")
        self.sign_in("3", "bbbb2222")  # window 3 signed in as account 2
        r = self.run_relay("status")
        self.assertEqual(r.returncode, 1)
        self.assertIn("✗ two@example.com", r.stdout)

    # -- switch -----------------------------------------------------------

    def test_switch_rotates_and_wraps(self):
        self.run_relay("link")
        for start, expect in (("1", "2"), ("2", "3"), ("3", "1")):
            self.set_open([start])
            r = self.run_relay()
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(self.open_now(), [expect])

    def test_switch_to_named_account_and_links_it(self):
        self.set_open(["1"])
        r = self.run_relay("3")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.open_now(), ["3"])
        self.assertTrue(self.sdir("3").is_symlink())
        calls = (self.bin / "calls.log").read_text()
        self.assertIn("switch 3", calls)

    def test_switch_warns_on_wrong_sign_in(self):
        self.run_relay("link")
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
        r = self.run_relay("2", "--force", env_extra={"CLAUDECODE": "1"})
        self.assertEqual(self.open_now(), ["2"])

    def test_unknown_account(self):
        r = self.run_relay("9")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no account 9", r.stderr)

    def test_dry_run_switch_changes_nothing(self):
        self.set_open(["1"])
        r = self.run_relay("-n")
        self.assertIn("→  account 2", r.stdout)
        self.assertEqual(self.open_now(), ["1"])


if __name__ == "__main__":
    unittest.main()
