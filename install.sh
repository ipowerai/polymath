#!/usr/bin/env bash
# Installs Polymath on macOS or Linux, keeps it running, and opens it in your browser.
#
#   curl -fsSL https://raw.githubusercontent.com/ipowerai/polymath/main/install.sh | bash
#   ... | bash -s -- --port 9000     use another port (run it again any time to change it)
#   ... | bash -s -- --llm claude    choose the default model without being asked
#   ... | bash -s -- --uninstall     remove the app; your study data stays in ~/.polymath
#
# It needs Python 3.9 or later and a working AI model. It checks first: Claude Code signed in with an active
# subscription, a running local model, or an accepted API key. If none passes, nothing is installed.
# It writes only to your own account: no administrator password is needed.
set -euo pipefail

ZIP_URL="https://github.com/ipowerai/polymath/releases/latest/download/polymath.zip"
LABEL="${POLYMATH_SERVICE_LABEL:-com.polymath.dashboard}"
UNIT="polymath.service"
COMMAND_MARK="# Polymath terminal command"
PARTS="blueprints references scripts skills web .claude-plugin LICENSE NOTICE README.md install.sh install.ps1"

say() { printf '%s\n' "$*"; }
die() { printf 'Polymath could not be installed: %s\n' "$*" >&2; exit 1; }

set_paths() {
  OS=$(uname -s)
  case "$OS" in
    Darwin)
      ROOT="$HOME/Library/Application Support/Polymath"
      LOG_DIR="$HOME/Library/Logs/Polymath"
      PLIST="$HOME/Library/LaunchAgents/$LABEL.plist" ;;
    Linux)
      ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/polymath"
      LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/polymath"
      UNIT_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$UNIT" ;;
    *) die "this script is for macOS and Linux. On Windows, use install.ps1." ;;
  esac
  APP="$ROOT/app"
}

usable() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; }

find_python() {
  local name found
  for name in python3.14 python3.13 python3.12 python3.11 python3.10 python3 \
              /opt/homebrew/bin/python3 /usr/local/bin/python3 python; do
    found=$(command -v "$name" 2>/dev/null) || continue
    # Without the developer tools, Apple's python3 is a stub that opens an install dialog.
    if [ "$OS" = Darwin ] && [ "$found" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then
      continue
    fi
    if usable "$found"; then printf '%s' "$found"; return 0; fi
  done
  return 1
}

find_claude() {
  local place
  if [ -n "${POLYMATH_CLAUDE_BIN:-}" ]; then  # tests point this at a stand-in
    if [ -x "$POLYMATH_CLAUDE_BIN" ]; then printf '%s' "$POLYMATH_CLAUDE_BIN"; return 0; fi
    return 1
  fi
  for place in "$(command -v claude 2>/dev/null || true)" "$HOME/.local/bin/claude" "$HOME/.claude/local/claude" \
               /opt/homebrew/bin/claude /usr/local/bin/claude; do
    if [ -n "$place" ] && [ -x "$place" ]; then printf '%s' "$place"; return 0; fi
  done
  return 1
}

has_systemd() { command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; }

port_busy() {
  "$PY" -c 'import socket, sys
s = socket.socket(); s.settimeout(0.3)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)' "$1"
}

stop_running() {
  if [ "$OS" = Darwin ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  elif has_systemd; then
    systemctl --user disable --now "$UNIT" >/dev/null 2>&1 || true
  fi
  if [ -f "$ROOT/pid" ]; then
    local pid
    pid=$(cat "$ROOT/pid" 2>/dev/null || true)
    case "$pid" in
      ''|*[!0-9]*) ;;
      *) if ps -o command= -p "$pid" 2>/dev/null | grep -F "$APP/scripts/polymath.py" >/dev/null; then
           kill "$pid" 2>/dev/null || true
           for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 0.3; done
         fi ;;
    esac
    rm -f "$ROOT/pid"
  fi
}

uninstall() {
  stop_running
  if [ "$OS" = Darwin ]; then
    launchctl enable "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
    rm -f "$PLIST"
  else
    rm -f "$UNIT_FILE"
    if has_systemd; then systemctl --user daemon-reload >/dev/null 2>&1 || true; fi
  fi
  rm -rf "$APP" "$APP.new"
  local file
  file=$(cat "$ROOT/command-path" 2>/dev/null || true)
  if [ -n "$file" ] && grep -qF "$COMMAND_MARK" "$file" 2>/dev/null; then rm -f "$file"; fi
  rm -f "$ROOT/port" "$ROOT/polymath-open" "$ROOT/install.json" "$ROOT/command-path"
  rmdir "$ROOT" 2>/dev/null || true
  say "Polymath is removed. Your study data is still in ${POLYMATH_HOME:-$HOME/.polymath}."
}

locate_source() {
  local here=""
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  fi
  if [ -n "$here" ] && [ -f "$here/scripts/polymath.py" ]; then
    SRC="$here"
    return
  fi
  command -v curl >/dev/null 2>&1 || die "curl is needed to download Polymath."
  say "Downloading Polymath..."
  curl -fsSL "$ZIP_URL" -o "$WORK/polymath.zip" || die "the download failed. Check your connection and try again."
  "$PY" -m zipfile -e "$WORK/polymath.zip" "$WORK/unpacked" >/dev/null
  SRC="$WORK/unpacked/polymath"
  [ -f "$SRC/scripts/polymath.py" ] || die "the download was incomplete."
}

choose_model() {
  local input=/dev/null
  if (exec </dev/tty) 2>/dev/null; then input=/dev/tty; fi
  local args=(check --out "$WORK/choice.json")
  if [ -n "$LLM" ]; then args+=(--llm "$LLM"); fi
  PYTHONDONTWRITEBYTECODE=1 "$PY" "$SRC/scripts/polymath_setup.py" "${args[@]}" <"$input" || exit 1
}

copy_app() {
  mkdir -p "$ROOT" "$LOG_DIR"
  chmod 700 "$ROOT"
  rm -rf "$APP.new"
  mkdir -p "$APP.new"
  local part
  for part in $PARTS; do
    [ -e "$SRC/$part" ] || die "$part is missing from the download."
    cp -R "$SRC/$part" "$APP.new/"
  done
  find "$APP.new" -name __pycache__ -type d -prune -exec rm -rf {} +
  rm -rf "$APP"
  mv "$APP.new" "$APP"
}

choose_port() {
  if [ -n "$PORT" ]; then
    case "$PORT" in ''|*[!0-9]*) die "the port must be a number." ;; esac
    if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then die "choose a port from 1024 to 65535."; fi
    if port_busy "$PORT"; then die "port $PORT is already in use. Choose another with --port."; fi
    return
  fi
  local saved candidate
  saved=$(cat "$ROOT/port" 2>/dev/null || true)
  for candidate in $saved $(seq 8765 8785); do
    if ! port_busy "$candidate"; then PORT=$candidate; return; fi
  done
  die "ports 8765 to 8785 are all in use. Choose one with --port."
}

service_path() {
  # Claude Code's usual folders, so it is found even if it is installed after Polymath.
  local path="$HOME/.local/bin:$HOME/.claude/local:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" tool
  [ "$OS" = Darwin ] && path="/opt/homebrew/bin:$path"
  for tool in "${CLAUDE:-}" "$(command -v node 2>/dev/null || true)"; do
    if [ -n "$tool" ]; then path="$(dirname "$tool"):$path"; fi
  done
  printf '%s' "$path"
}

start_background() {
  (
    cd "$APP"
    PYTHONUTF8=1 nohup "$PY" "$APP/scripts/polymath.py" serve --port "$PORT" \
      >/dev/null 2>>"$LOG_DIR/server.log" </dev/null &
    printf '%s' "$!" > "$ROOT/pid"
  )
}

start_macos() {
  mkdir -p "$(dirname "$PLIST")"
  "$PY" - "$PLIST" "$LABEL" "$PY" "$APP" "$PORT" "$(service_path)" "$LOG_DIR/server.log" "${POLYMATH_HOME:-}" <<'PLIST_WRITER'
# Written by hand: some Python builds can't load the XML library that plistlib needs.
import html, sys
path, label, python, app, port, env_path, log, data = sys.argv[1:]
x = lambda value: "<string>%s</string>" % html.escape(value, quote=False)
env = [("PATH", env_path), ("LANG", "en_US.UTF-8"), ("PYTHONUTF8", "1")] + ([("POLYMATH_HOME", data)] if data else [])
program = "".join(x(a) for a in (python, app + "/scripts/polymath.py", "serve", "--port", port))
variables = "".join("<key>%s</key>%s" % (k, x(v)) for k, v in env)
text = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict><key>Label</key>' + x(label) + '<key>ProgramArguments</key><array>' + program +
        '</array><key>WorkingDirectory</key>' + x(app) + '<key>EnvironmentVariables</key><dict>' + variables +
        '</dict><key>RunAtLoad</key><true/><key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>'
        '<key>ThrottleInterval</key><integer>10</integer><key>StandardOutPath</key><string>/dev/null</string>'
        '<key>StandardErrorPath</key>' + x(log) + '</dict></plist>\n')
with open(path, "w", encoding="utf-8") as f:
    f.write(text)
PLIST_WRITER
  launchctl enable "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
}

start_linux() {
  mkdir -p "$(dirname "$UNIT_FILE")"
  local py="${PY//%/%%}" app="${APP//%/%%}" env_path
  env_path=$(service_path)
  {
    printf '[Unit]\nDescription=Polymath study dashboard\n\n[Service]\n'
    printf 'ExecStart="%s" "%s/scripts/polymath.py" serve --port %s\n' "$py" "$app" "$PORT"
    printf 'WorkingDirectory=%s\n' "$app"
    printf 'Environment="PATH=%s"\nEnvironment=PYTHONUTF8=1\n' "${env_path//%/%%}"
    if [ -n "${POLYMATH_HOME:-}" ]; then printf 'Environment="POLYMATH_HOME=%s"\n' "${POLYMATH_HOME//%/%%}"; fi
    printf 'Restart=on-failure\nRestartSec=10\nStandardOutput=null\n\n[Install]\nWantedBy=default.target\n'
  } > "$UNIT_FILE"
  systemctl --user daemon-reload
  systemctl --user enable --now "$UNIT" >/dev/null
}

start_app() {
  AUTOSTART=0
  if [ "$SERVICE" = 1 ] && [ "$OS" = Darwin ]; then start_macos; AUTOSTART=1
  elif [ "$SERVICE" = 1 ] && has_systemd; then start_linux; AUTOSTART=1
  else start_background; fi
  local _
  for _ in $(seq 1 60); do
    if port_busy "$PORT"; then return 0; fi
    sleep 0.5
  done
  die "it did not start. Details are in $LOG_DIR/server.log."
}

command_dir() {
  # A folder already on your PATH, so the command works right away without editing your shell profile.
  local dir
  for dir in "$HOME/.local/bin" "$HOME/bin" /opt/homebrew/bin /usr/local/bin; do
    case ":$USER_PATH:" in
      *":$dir:"*) if [ -d "$dir" ] && [ -w "$dir" ]; then printf '%s' "$dir"; return 0; fi ;;
    esac
  done
  printf '%s' "$HOME/.local/bin"
  return 1
}

install_command() {
  local dir file existing
  NEEDS_PATH=0
  dir=$(command_dir) || NEEDS_PATH=1
  file="$dir/polymath"
  existing=$(PATH="$USER_PATH" command -v polymath 2>/dev/null || true)
  if { [ -e "$file" ] && ! grep -qF "$COMMAND_MARK" "$file" 2>/dev/null; } || \
     { [ -n "$existing" ] && [ "$existing" != "$file" ] && ! grep -qF "$COMMAND_MARK" "$existing" 2>/dev/null; }; then
    say "Note: another program is already called polymath, so it was left alone."
    COMMAND="\"$PY\" \"$APP/scripts/polymath_control.py\""
    NEEDS_PATH=0
    return
  fi
  mkdir -p "$dir"
  {
    printf '#!/bin/sh\n%s\n' "$COMMAND_MARK"
    printf 'exec %q %q "$@"\n' "$PY" "$APP/scripts/polymath_control.py"
  } > "$file"
  chmod 755 "$file"
  printf '%s' "$file" > "$ROOT/command-path"
  if [ "$NEEDS_PATH" = 1 ]; then COMMAND="$file"; else COMMAND=polymath; fi
}

main() {
  USER_PATH="$PATH"
  SERVICE=1 OPEN=1 REMOVE=0 PORT="${POLYMATH_PORT:-}" LLM="${POLYMATH_LLM:-}" NEEDS_PATH=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --port) [ $# -ge 2 ] || die "--port needs a number."; PORT="$2"; shift 2 ;;
      --port=*) PORT="${1#--port=}"; shift ;;
      --llm) [ $# -ge 2 ] || die "--llm needs a model, like claude or ollama:qwen3:8b."; LLM="$2"; shift 2 ;;
      --llm=*) LLM="${1#--llm=}"; shift ;;
      --uninstall) REMOVE=1; shift ;;
      --no-service) SERVICE=0; shift ;;
      --no-open) OPEN=0; shift ;;
      *) die "unknown option $1. Options: --port N, --llm MODEL, --uninstall, --no-service, --no-open." ;;
    esac
  done
  set_paths
  if [ "$REMOVE" = 1 ]; then
    PY=""
    uninstall
    return
  fi
  PY=$(find_python) || die "Python 3.9 or later is needed. Get it from https://www.python.org/downloads/ and run this again."
  CLAUDE=$(find_claude || true)
  if [ -n "$CLAUDE" ]; then PATH="$(dirname "$CLAUDE"):$PATH"; export PATH; fi
  WORK=$(mktemp -d)
  trap 'rm -rf "$WORK"' EXIT
  locate_source
  choose_model  # stops here, with nothing installed or changed, unless a model passes and you confirm it
  stop_running
  copy_app
  PYTHONDONTWRITEBYTECODE=1 "$PY" "$APP/scripts/polymath_setup.py" save --from "$WORK/choice.json"
  choose_port
  printf '%s' "$PORT" > "$ROOT/port"
  "$PY" -c 'import json, sys; json.dump({"label": sys.argv[1]}, open(sys.argv[2], "w"))' "$LABEL" "$ROOT/install.json"
  install_command
  start_app

  say ""
  say "Polymath is running at http://127.0.0.1:$PORT"
  if [ "$AUTOSTART" = 1 ]; then say "It starts by itself when you log in."; fi
  say "Your study data stays on this computer, in ${POLYMATH_HOME:-$HOME/.polymath}."
  say ""
  say "Control it from the terminal:"
  say "  $COMMAND status | start | stop | restart | open"
  if [ "$NEEDS_PATH" = 1 ]; then
    say ""
    say "To use the short command, add this line to your shell profile:"
    say "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    say "Until then, use: $COMMAND status"
  fi
  say ""
  say "Change port:    bash \"$APP/install.sh\" --port 9000"
  say "Remove it:      bash \"$APP/install.sh\" --uninstall"
  if [ "$OPEN" = 1 ]; then "$PY" "$APP/scripts/polymath_control.py" open >/dev/null 2>&1 || true; fi
}

# Everything runs from here, so a download cut off halfway does nothing.
main "$@" </dev/null
