#!/bin/zsh
# Install relay: makes sure aswap is installed (via uv), then puts the `relay`
# command on PATH by linking ~/.local/bin/relay to relay.py in this folder.
# Safe to rerun (e.g. after moving this folder or to repair a broken link).
#
#   ./install.sh               install / repair
#   ./install.sh --no-aswap    skip the aswap check (you manage it yourself)
set -e

here="${0:A:h}"
bin="$HOME/.local/bin"
skip_aswap=0
[[ "${1:-}" == "--no-aswap" ]] && skip_aswap=1

say()  { print -P "%F{cyan}==>%f $*"; }
warn() { print -P "%F{yellow}!%f $*"; }
fail() { print -P "%F{red}✗%f $*" >&2; exit 1; }

[[ "$(uname)" == "Darwin" ]] || fail "relay supports macOS only."
command -v python3 >/dev/null || fail "python3 is missing: install Xcode Command Line Tools (xcode-select --install)."

# uv installs its tools into ~/.local/bin; make sure this script can see them.
path=("$bin" $path)

# -- aswap ---------------------------------------------------------------------
if (( ! skip_aswap )); then
  if command -v aswap >/dev/null; then
    say "aswap is installed ($(aswap --version 2>/dev/null | head -1))"
  else
    if ! command -v uv >/dev/null; then
      if command -v brew >/dev/null; then
        say "Installing uv with Homebrew…"
        brew install uv
      else
        say "Installing uv with its official installer (https://astral.sh/uv)…"
        curl -LsSf https://astral.sh/uv/install.sh | sh
        path=("$bin" "$HOME/.cargo/bin" $path)
      fi
      command -v uv >/dev/null || fail "uv didn't install; see https://docs.astral.sh/uv/ and rerun."
    fi
    say "Installing aswap with uv…"
    uv tool install aswap
    command -v aswap >/dev/null || fail "aswap didn't install; try: uv tool install aswap"
    say "aswap installed ($(aswap --version 2>/dev/null | head -1))"
  fi
fi

# -- relay ---------------------------------------------------------------------
chmod +x "$here/relay.py"
mkdir -p "$bin"
ln -sfn "$here/relay.py" "$bin/relay"
say "relay → $here/relay.py"

# -- what's left for the user --------------------------------------------------
case ":$PATH:" in
  *":$bin:"*) ;;
  *) warn "Add ~/.local/bin to your PATH, e.g.: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" ;;
esac

seq="${RELAY_BACKUP_DIR:-$HOME/.claude-swap-backup}/sequence.json"
n=$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1])).get('accounts',{})))" "$seq" 2>/dev/null || echo 0)
if (( n == 0 )); then
  warn "No accounts yet. For each account: sign in to Claude Code (claude, then /login) and run: aswap add"
  warn "Then follow \"Set up (once)\" in the README, starting with: relay link"
elif (( n == 1 )); then
  warn "aswap has 1 account; add the others (sign in to Claude Code as each, then: aswap add)."
else
  say "aswap has $n accounts. Next: relay status"
fi
