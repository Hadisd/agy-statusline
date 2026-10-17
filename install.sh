#!/usr/bin/env bash
# ============================================================================
# Awesome Antigravity CLI Statusline — Installer (macOS / Linux)
# ----------------------------------------------------------------------------
# Usage:
#   ./install.sh                        # prompts for size, theme, effort style
#   ./install.sh s                      # install small (2 lines), prompts for theme
#   ./install.sh m dracula              # install medium (3 lines) with the Dracula theme
#   ./install.sh l tokyo-night          # install large (4 lines) with the Tokyo Night theme
#   ./install.sh xs nord                # install micro (1 line) with the Nord theme
#   ./install.sh m dracula bars         # choose original / dots / bars / minimal
#
#   curl -fsSL https://raw.githubusercontent.com/Hadisd/agy-statusline/main/install.sh \
#     | bash -s -- medium dracula       # no clone needed — fetches its own files from GitHub
#
# 4 Size Levels:
#   micro  (xs)  1 line, Ctx/5H/7D only — for tmux/screen bars & cramped panes
#   small  (s)   2 lines, compact layout, key info
#   medium (m)   3 lines, balanced layout with brand, state, path & quota
#   large  (l)   4 lines, full detailed statusline with token numbers & quota bars
#
# 5 Themes:
#   mocha (default), tokyo-night, nord, dracula, gruvbox
#
# Target:
#   Config Dir: ~/.gemini/antigravity-cli
#   Script:     ~/.gemini/antigravity-cli/statusline.sh
#   Settings:   ~/.gemini/antigravity-cli/settings.json
# ============================================================================
set -euo pipefail

# --- paths ------------------------------------------------------------------
AGY_DIR="${AGY_CONFIG_DIR:-$HOME/.gemini/antigravity-cli}"
SETTINGS="$AGY_DIR/settings.json"
DEST="$AGY_DIR/statusline.sh"
ENGINE_DEST="$AGY_DIR/statusline_engine.py"

if [ -n "${BASH_SOURCE:-}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  SCRIPT_DIR=""
fi
SRC_DIR="$SCRIPT_DIR/scripts"

# --- output helpers ---------------------------------------------------------
err()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }
info() { printf '\033[36m%s\033[0m\n' "$*"; }

# --- remote fetch (for `curl | bash`, where there's no local checkout) ------
REPO_RAW_BASE="${AGY_STATUSLINE_RAW:-https://raw.githubusercontent.com/Hadisd/agy-statusline/main}"

fetch_remote() {
  # $1 = path relative to repo root, $2 = destination file
  local url="$REPO_RAW_BASE/$1"
  if command -v curl >/dev/null 2>&1; then
    if ! curl -fsSL --connect-timeout 10 --max-time 60 "$url" -o "$2" 2>/dev/null; then
      # Retry forcing IPv4 in case IPv6 routing hangs or blackholes
      curl -4 -fsSL --connect-timeout 10 --max-time 60 "$url" -o "$2"
    fi
  elif command -v wget >/dev/null 2>&1; then
    if ! wget -q --timeout=10 --tries=2 -O "$2" "$url" 2>/dev/null; then
      wget -4 -q --timeout=10 --tries=2 -O "$2" "$url"
    fi
  else
    err "Need curl or wget to fetch $1 — neither is installed."
    return 1
  fi
}

normalize_mode() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    xs|micro)  echo "micro"  ;;
    s|small)   echo "small"  ;;
    m|medium)  echo "medium" ;;
    l|large)   echo "large"  ;;
    *) return 1 ;;
  esac
}

normalize_theme() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr ' _' '--')" in
    mocha|catppuccin-mocha|catppuccin) echo "mocha" ;;
    tokyo-night|tokyonight|tn)         echo "tokyo-night" ;;
    nord)                              echo "nord" ;;
    dracula)                           echo "dracula" ;;
    gruvbox|gruvbox-dark)              echo "gruvbox" ;;
    *) return 1 ;;
  esac
}

normalize_effort_style() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    original|text) echo "original" ;;
    dots|bars|minimal) printf '%s\n' "$1" | tr '[:upper:]' '[:lower:]' ;;
    *) return 1 ;;
  esac
}

ensure_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    info "jq not found — attempting automatic install…"
    if   command -v brew    >/dev/null 2>&1; then brew install jq
    elif command -v apt-get >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y jq
    elif command -v dnf     >/dev/null 2>&1; then sudo dnf install -y jq
    elif command -v yum     >/dev/null 2>&1; then sudo yum install -y jq
    elif command -v pacman  >/dev/null 2>&1; then sudo pacman -S --noconfirm jq
    elif command -v apk     >/dev/null 2>&1; then sudo apk add jq
    else
      err "Please install jq manually."
      exit 1
    fi
  fi
}

MODE="large"
THEME="mocha"
EFFORT_STYLE="dots"

is_korean_locale() {
  case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in
    ko|ko_*|ko.*|ko-*) return 0 ;;
    *) return 1 ;;
  esac
}

prompt_size() {
  echo
  if is_korean_locale; then
    echo "Antigravity statusline을 어떤 크기로 설치할까요? (micro - small - medium - large)"
    echo "  1. micro  (xs) — 1줄, Ctx/5H/7D 수치만 (tmux/screen 상태바 등 좁은 공간용)"
    echo "  2. small  (s)  — 공간 절약 2줄 레이아웃 (핵심 정보)"
    echo "  3. medium (m)  — 균형 잡힌 3줄 레이아웃 (브랜드, 작업 상태, 작업 디렉토리)"
    echo "  4. large  (l)  — (추천) 4줄 상세 레이아웃 (전체 토큰 사용량, Quota Bar)"
    echo
    printf '선택 [기본값: 4 (large)]: '
  else
    echo "Which size would you like to install for Antigravity CLI? (micro - small - medium - large)"
    echo "  1. micro  (xs) — 1 line, Ctx/5H/7D numbers only (tmux/screen bars, cramped panes)"
    echo "  2. small  (s)  — Space-saving 2-line layout"
    echo "  3. medium (m)  — Balanced 3-line layout"
    echo "  4. large  (l)  — (Recommended) Full detailed 4-line layout with token & quota bars"
    echo
    printf 'Choice [default: 4 (large)]: '
  fi
  local answer=""
  read -r answer || answer=""
  case "$answer" in
    1) MODE="micro"  ;;
    2) MODE="small"  ;;
    3) MODE="medium" ;;
    4|"") MODE="large"  ;;
    *)
      if ! MODE="$(normalize_mode "$answer")"; then
        echo "Unknown input '$answer', using 'large'."
        MODE="large"
      fi
      ;;
  esac
}

prompt_theme() {
  echo
  if is_korean_locale; then
    echo "테마를 선택하세요 (기본값: mocha)"
    echo "  1. mocha       — Catppuccin Mocha (기본값)"
    echo "  2. tokyo-night — Tokyo Night"
    echo "  3. nord        — Nord"
    echo "  4. dracula     — Dracula"
    echo "  5. gruvbox     — Gruvbox Dark"
    echo
    printf '선택 [기본값: 1 (mocha)]: '
  else
    echo "Which theme would you like? (default: mocha)"
    echo "  1. mocha       — Catppuccin Mocha (default)"
    echo "  2. tokyo-night — Tokyo Night"
    echo "  3. nord        — Nord"
    echo "  4. dracula     — Dracula"
    echo "  5. gruvbox     — Gruvbox Dark"
    echo
    printf 'Choice [default: 1 (mocha)]: '
  fi
  local answer=""
  read -r answer || answer=""
  case "$answer" in
    1|"") THEME="mocha" ;;
    2) THEME="tokyo-night" ;;
    3) THEME="nord" ;;
    4) THEME="dracula" ;;
    5) THEME="gruvbox" ;;
    *)
      if ! THEME="$(normalize_theme "$answer")"; then
        echo "Unknown input '$answer', using 'mocha'."
        THEME="mocha"
      fi
      ;;
  esac
}

prompt_effort_style() {
  echo
  if is_korean_locale; then
    echo "추론 강도 표시를 선택하세요 (기본값: dots)"
  else
    echo "How should model effort be displayed? (default: dots)"
  fi
  echo "  1. original — (Low) / (Medium) / (High)"
  echo "  2. dots     — ●○○ / ●●○ / ●●●"
  echo "  3. bars     — ▰▱▱ / ▰▰▱ / ▰▰▰"
  echo "  4. minimal  — L / M / H"
  if is_korean_locale; then
    printf '선택 [기본값: 2 (dots)]: '
  else
    printf 'Choice [default: 2 (dots)]: '
  fi
  local answer=""
  read -r answer || answer=""
  case "$answer" in
    1) EFFORT_STYLE="original" ;;
    2|"") EFFORT_STYLE="dots" ;;
    3) EFFORT_STYLE="bars" ;;
    4) EFFORT_STYLE="minimal" ;;
    *)
      if ! EFFORT_STYLE="$(normalize_effort_style "$answer")"; then
        echo "Unknown input '$answer', using 'dots'."
        EFFORT_STYLE="dots"
      fi
      ;;
  esac
}

if [ "$#" -ge 1 ]; then
  if ! MODE="$(normalize_mode "$1")"; then
    err "Unknown size: '$1'"
    err "Valid options: micro (xs), small (s), medium (m), large (l)"
    exit 1
  fi
  if [ "$#" -ge 2 ]; then
    if ! THEME="$(normalize_theme "$2")"; then
      err "Unknown theme: '$2'"
      err "Valid options: mocha, tokyo-night, nord, dracula, gruvbox"
      exit 1
    fi
  elif [ -t 0 ]; then
    prompt_theme
  fi
  if [ "$#" -ge 3 ]; then
    if ! EFFORT_STYLE="$(normalize_effort_style "$3")"; then
      err "Unknown effort style: '$3'"
      err "Valid options: original, dots, bars, minimal"
      exit 1
    fi
  elif [ -t 0 ]; then
    prompt_effort_style
  fi
elif [ -t 0 ]; then
  prompt_size
  prompt_theme
  prompt_effort_style
elif { exec 3</dev/tty; } 2>/dev/null; then
  prompt_size <&3
  prompt_theme <&3
  prompt_effort_style <&3
  exec 3<&-
fi

# --- run --------------------------------------------------------------------
ensure_jq
mkdir -p "$AGY_DIR"

SRC="$SRC_DIR/awesome-statusline-$MODE.sh"
ENGINE_SRC="$SRC_DIR/statusline_engine.py"

if [ -f "$SRC" ] && [ -f "$ENGINE_SRC" ]; then
  WRAPPER_CONTENT="$(cat "$SRC")"
  cp "$ENGINE_SRC" "$ENGINE_DEST"
else
  # No local checkout next to this script (e.g. running via `curl | bash`,
  # where nothing but install.sh itself was ever downloaded) — fetch the
  # two files it needs straight from GitHub instead of requiring a clone.
  info "No local checkout found — fetching from GitHub…"
  TMP_WRAPPER="$(mktemp)"
  if ! fetch_remote "scripts/awesome-statusline-$MODE.sh" "$TMP_WRAPPER"; then
    rm -f "$TMP_WRAPPER"
    err "Failed to fetch scripts/awesome-statusline-$MODE.sh from GitHub."
    err "Tip: If raw.githubusercontent.com is blocked, slow, or times out on your network,"
    err "     clone the repository and run ./install.sh directly:"
    err "     git clone https://github.com/Hadisd/agy-statusline.git && cd agy-statusline && ./install.sh"
    exit 1
  fi
  WRAPPER_CONTENT="$(cat "$TMP_WRAPPER")"
  rm -f "$TMP_WRAPPER"
  if ! fetch_remote "scripts/statusline_engine.py" "$ENGINE_DEST"; then
    err "Failed to fetch scripts/statusline_engine.py from GitHub."
    err "Tip: If raw.githubusercontent.com is blocked, slow, or times out on your network,"
    err "     clone the repository and run ./install.sh directly:"
    err "     git clone https://github.com/Hadisd/agy-statusline.git && cd agy-statusline && ./install.sh"
    exit 1
  fi
fi

# Bake the theme and default effort style into the wrapper via portable bash
# substring substitution (no sed -i, whose -i flag differs between
# GNU and BSD/macOS).
WRAPPER_CONTENT="${WRAPPER_CONTENT/--size $MODE \"\$@\"/--size $MODE --theme $THEME \"\$@\"}"
EFFORT_EXPORT="export AGY_STATUSLINE_EFFORT_STYLE=\"\${AGY_STATUSLINE_EFFORT_STYLE:-$EFFORT_STYLE}\""
WRAPPER_CONTENT="${WRAPPER_CONTENT/exec python3/$EFFORT_EXPORT$'\n'exec python3}"
printf '%s\n' "$WRAPPER_CONTENT" > "$DEST"
chmod +x "$DEST" "$ENGINE_DEST"

STATUSLINE_JSON="{\"type\":\"command\",\"command\":\"bash $DEST\"}"
if [ -f "$SETTINGS" ]; then
  BACKUP="$SETTINGS.backup-$(date +%Y%m%d-%H%M%S)"
  cp "$SETTINGS" "$BACKUP"
  jq --argjson sl "$STATUSLINE_JSON" '.statusLine = $sl' "$SETTINGS" > "$SETTINGS.tmp" \
    && mv "$SETTINGS.tmp" "$SETTINGS"
  info "Existing settings backed up to: $BACKUP"
else
  jq -n --argjson sl "$STATUSLINE_JSON" '{statusLine: $sl}' > "$SETTINGS"
fi

ok "Installed Antigravity CLI Statusline (size: $MODE, theme: $THEME, effort: $EFFORT_STYLE)"
echo "  script:   $DEST"
echo "  settings: $SETTINGS (statusLine configured)"
echo
echo "Restart Antigravity CLI (agy) to see the new statusline."
