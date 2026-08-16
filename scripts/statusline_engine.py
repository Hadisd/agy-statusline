#!/usr/bin/env python3
"""
Awesome Statusline Engine for Antigravity CLI
==============================================
Supports 4 size levels, 5 color themes, and accurate mode detection
(excluding model names), artifact status & detailed Git indicators:
- micro  (xs)  1 line:  Model + Ctx/5H/7D percentages only
- small  (s)   2 lines: Compact layout (Line 1: Model, State, Mode, Path, Git, Artifacts 📄N ⚙️M)
- medium (m)   3 lines: Balanced layout with Model, State, Mode, Artifacts, Path & Git
- large  (l)   4 lines: Full detailed statusline with Tokens, Artifacts & Quota Bars

Auto-shrinks to a narrower size if the real terminal is too narrow to fit the
configured one (see maybe_shrink()). Run with --selftest to render every
size x theme combination against representative payloads as a regression check.
"""

import os
import sys
import io
import json
import time
import shutil
import subprocess
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

RESET = "\033[0m"
BOLD = "\033[1m"
CLR = "\033[K"

# Named roles (teal/pink/peach/...) are a Catppuccin convention; the other
# palettes below are mapped onto the same roles by nearest-fit hue, so some
# roles reuse a color where a theme doesn't have a distinct one (e.g.
# Dracula has no separate blue, Gruvbox's "blue" is teal-leaning). "red" is
# the danger/high-usage endpoint for the Ctx/5H/7D gradient bars.
THEMES: Dict[str, Dict[str, Tuple[int, int, int]]] = {
    "mocha": {
        "teal": (148, 226, 213), "pink": (245, 194, 231), "peach": (250, 179, 135),
        "green": (166, 227, 161), "subtext": (166, 173, 200), "lavender": (180, 190, 254),
        "yellow": (249, 226, 175), "cyan": (137, 220, 235), "blue": (137, 180, 250),
        "red": (210, 15, 57),
    },
    "tokyo-night": {
        "teal": (125, 207, 255), "pink": (187, 154, 247), "peach": (255, 158, 100),
        "green": (158, 206, 106), "subtext": (86, 95, 137), "lavender": (122, 162, 247),
        "yellow": (224, 175, 104), "cyan": (125, 207, 255), "blue": (122, 162, 247),
        "red": (219, 75, 75),
    },
    "nord": {
        "teal": (143, 188, 187), "pink": (180, 142, 173), "peach": (208, 135, 112),
        "green": (163, 190, 140), "subtext": (76, 86, 106), "lavender": (129, 161, 193),
        "yellow": (235, 203, 139), "cyan": (136, 192, 208), "blue": (94, 129, 172),
        "red": (191, 97, 106),
    },
    "dracula": {
        "teal": (139, 233, 253), "pink": (255, 121, 198), "peach": (255, 184, 108),
        "green": (80, 250, 123), "subtext": (98, 114, 164), "lavender": (189, 147, 249),
        "yellow": (241, 250, 140), "cyan": (139, 233, 253), "blue": (189, 147, 249),
        "red": (255, 85, 85),
    },
    "gruvbox": {
        "teal": (142, 192, 124), "pink": (211, 134, 155), "peach": (254, 128, 25),
        "green": (184, 187, 38), "subtext": (146, 131, 116), "lavender": (131, 165, 152),
        "yellow": (250, 189, 47), "cyan": (142, 192, 124), "blue": (131, 165, 152),
        "red": (251, 73, 52),
    },
}
DEFAULT_THEME = "mocha"

# Mocha's gradient bars were hand-tuned with a punchier mid-stop than a
# plain midpoint blend would give (e.g. context ramps toward red faster
# than linear). Preserve that exact curve so the default theme's look
# doesn't regress; other themes derive their mid-stop as a 50% blend
# between their start color and their "red" danger color.
_MOCHA_GRAD_MID = {"ctx": (230, 69, 83), "5h": (30, 102, 245), "7d": (254, 100, 11)}

def _ansi(rgb: Tuple[int, int, int]) -> str:
    return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"

def _lerp(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))

def _ctx_gradient(start, mid, end):
    def fn(pct: float) -> Tuple[int, int, int]:
        pct = max(0.0, min(100.0, pct))
        if pct < 30:
            return _lerp(start, mid, pct / 30.0)
        if pct < 70:
            return _lerp(mid, end, (pct - 30.0) / 40.0)
        return end
    return fn

def _half_gradient(start, mid, end):
    def fn(pct: float) -> Tuple[int, int, int]:
        pct = max(0.0, min(100.0, pct))
        if pct < 50:
            return _lerp(start, mid, pct / 50.0)
        return _lerp(mid, end, (pct - 50.0) / 50.0)
    return fn

C_TEAL = C_PINK = C_PEACH = C_GREEN = C_SUBTEXT = C_LAVENDER = C_YELLOW = C_CYAN = C_BLUE = C_RED = ""
get_context_color = get_5h_color = get_7d_color = None

def apply_theme(name: str) -> None:
    global C_TEAL, C_PINK, C_PEACH, C_GREEN, C_SUBTEXT, C_LAVENDER, C_YELLOW, C_CYAN, C_BLUE, C_RED
    global get_context_color, get_5h_color, get_7d_color
    theme_key = name if name in THEMES else DEFAULT_THEME
    theme = THEMES[theme_key]
    C_TEAL = _ansi(theme["teal"])
    C_PINK = _ansi(theme["pink"])
    C_PEACH = _ansi(theme["peach"])
    C_GREEN = _ansi(theme["green"])
    C_SUBTEXT = _ansi(theme["subtext"])
    C_LAVENDER = _ansi(theme["lavender"])
    C_YELLOW = _ansi(theme["yellow"])
    C_CYAN = _ansi(theme["cyan"])
    C_BLUE = _ansi(theme["blue"])
    C_RED = _ansi(theme["red"])

    red = theme["red"]
    if theme_key == "mocha":
        ctx_mid, h5_mid, d7_mid = _MOCHA_GRAD_MID["ctx"], _MOCHA_GRAD_MID["5h"], _MOCHA_GRAD_MID["7d"]
    else:
        ctx_mid = _lerp(theme["pink"], red, 0.5)
        h5_mid = _lerp(theme["lavender"], red, 0.5)
        d7_mid = _lerp(theme["yellow"], red, 0.5)
    get_context_color = _ctx_gradient(theme["pink"], ctx_mid, red)
    get_5h_color = _half_gradient(theme["lavender"], h5_mid, red)
    get_7d_color = _half_gradient(theme["yellow"], d7_mid, red)

apply_theme(DEFAULT_THEME)

def mode_color(mode: str) -> str:
    if mode == "plan":
        return BOLD + C_BLUE
    if mode == "accept-edits":
        return BOLD + C_GREEN
    return BOLD + C_PEACH

def _warn_icon(pct: float, threshold: float = 90.0) -> str:
    return " ⚠️" if pct >= threshold else ""

def generate_bar(pct: float, width: int = 10, bar_type: str = "ctx") -> str:
    fn = get_5h_color if bar_type == "5h" else (get_7d_color if bar_type in ["7d", "weekly"] else get_context_color)
    filled = max(0, min(width, int((pct * width + 50) / 100.0)))
    end_r, end_g, end_b = fn(pct)
    bar = "".join(f"\033[38;2;{fn((i*100.0)/width)[0]};{fn((i*100.0)/width)[1]};{fn((i*100.0)/width)[2]}m█" for i in range(filled))
    bar += "".join(f"\033[38;2;{end_r};{end_g};{end_b}m░" for _ in range(width - filled))
    return f"{bar}{RESET}"

def format_time_remaining(seconds: Optional[int]) -> str:
    if seconds is None or seconds <= 0:
        return ""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"

def format_reset_day(reset_iso_or_seconds: Any) -> str:
    if isinstance(reset_iso_or_seconds, (int, float)):
        reset_time = time.time() + reset_iso_or_seconds
        return time.strftime("%a", time.localtime(reset_time))
    elif isinstance(reset_iso_or_seconds, str):
        try:
            dt = datetime.fromisoformat(reset_iso_or_seconds.replace('Z', '+00:00'))
            return dt.strftime("%a")
        except Exception:
            pass
    return "Mon"

def _quota_pct(bucket: Dict[str, Any]) -> float:
    if "used_percentage" in bucket:
        return bucket["used_percentage"]
    if "remaining_fraction" in bucket:
        return (1.0 - bucket["remaining_fraction"]) * 100.0
    if "remaining_percentage" in bucket:
        return 100.0 - bucket["remaining_percentage"]
    return 0.0

def find_quota_buckets(data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Finds the weekly and five-hour quota buckets for the active model's group.

    Antigravity CLI splits quota per model family rather than one global
    bucket (confirmed via `/usage`, which shows separate "GEMINI MODELS" and
    "CLAUDE AND GPT MODELS" groups, each with its own Weekly and Five Hour
    limit). Exact JSON key spelling for these buckets isn't documented, so
    they're matched by keyword instead of a fixed key name, and scoped to
    whichever group the active model belongs to when more than one matches.
    """
    quota = data.get("quota") or data.get("rate_limits") or {}
    if not isinstance(quota, dict):
        return {}, {}

    model_obj = data.get("model")
    model_name = ""
    if isinstance(model_obj, dict):
        model_name = (model_obj.get("display_name") or model_obj.get("id") or "").lower()
    elif isinstance(model_obj, str):
        model_name = model_obj.lower()

    group_hint = ""
    if "claude" in model_name or "gpt" in model_name:
        group_hint = "claude"
    elif "gemini" in model_name:
        group_hint = "gemini"

    def pick(keywords: List[str]) -> Dict[str, Any]:
        matches = [
            (k, v) for k, v in quota.items()
            if isinstance(v, dict) and any(kw in k.lower().replace("_", "-") for kw in keywords)
        ]
        if not matches:
            return {}
        if group_hint:
            for k, v in matches:
                if group_hint in k.lower():
                    return v
        return matches[0][1]

    weekly = pick(["week", "7d"])
    five_hour = pick(["five-hour", "fivehour", "5h", "5-hour"])
    return weekly, five_hour

def read_stdin_json() -> Dict[str, Any]:
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw and raw.strip():
                return json.loads(raw)
        except Exception:
            pass
    return {}

def _safe_dict(v: Any) -> Dict[str, Any]:
    """Antigravity's payload schema is undocumented and can shift between
    versions — guard against a field being missing, null, or a bare string
    where a dict is expected, so the statusline degrades instead of crashing.
    """
    return v if isinstance(v, dict) else {}

def get_model_name(data: Dict[str, Any]) -> str:
    model_obj = data.get("model")
    if isinstance(model_obj, dict):
        return model_obj.get("display_name") or model_obj.get("id") or os.environ.get("AGY_MODEL", "Gemini 3.6 Flash (High)")
    if isinstance(model_obj, str) and model_obj.strip():
        return model_obj
    return os.environ.get("AGY_MODEL", "Gemini 3.6 Flash (High)")

def format_dir_path(cwd: str, max_len: Optional[int] = None) -> str:
    home = os.path.expanduser("~")
    dir_path = "~" if cwd == home else (f"~{cwd[len(home):]}" if cwd.startswith(home + "/") else cwd)
    if max_len and len(dir_path) > max_len:
        parts = [p for p in dir_path.split("/") if p]
        tail = "/".join(parts[-2:]) if len(parts) >= 2 else dir_path
        dir_path = f"…/{tail}"
    return dir_path

def resolve_execution_mode(data: Dict[str, Any]) -> str:
    """Resolves the active permission/cycle mode (plan, accept-edits, bypass, default).

    Antigravity CLI exposes this as top-level "cycle_mode" (confirmed via
    manager.go SetCycleMode / statusline_debug capture: values seen are
    "plan", "accept-edits", and "" for default). "execution_mode" is a
    separate, unrelated field (reasoning speed: planning/fast) and is only
    used as a last-resort fallback below for older CLI versions.
    """
    cycle_mode = data.get("cycle_mode")
    if isinstance(cycle_mode, str) and cycle_mode.strip():
        return cycle_mode.strip().lower()

    model_obj = data.get("model")
    model_name = ""
    if isinstance(model_obj, dict):
        model_name = (model_obj.get("display_name") or model_obj.get("id") or "").lower()
    elif isinstance(model_obj, str):
        model_name = model_obj.lower()

    keys_to_check = [
        "prompt_mode", "mode", "current_mode",
        "agent_mode", "planner_mode", "output_style", "execution_mode"
    ]
    # "planning"/"fast" are confirmed execution_mode (reasoning-speed) values,
    # not permission-mode values — never let them surface as if they were
    # the cycle/permission mode, even via this last-resort fallback.
    reasoning_speed_values = {"planning", "fast"}
    for k in keys_to_check:
        val = data.get(k)
        if isinstance(val, dict):
            val = val.get("name") or val.get("id") or val.get("mode")
        if val and isinstance(val, str) and val.strip():
            res = val.strip().lower()
            # Ignore defaults, model names, and model keywords
            if res not in ["default", "normal", "standard", "none"]:
                if k == "execution_mode" and res in reasoning_speed_values:
                    continue
                if model_name and res in model_name:
                    continue
                if any(kw in res for kw in ["gemini", "claude", "gpt", "flash", "pro"]):
                    continue
                return res

    # Fallback: check active conversation brain directory for recent plan artifacts
    conv_id = data.get("conversation_id") or data.get("session_id")
    transcript_path = data.get("transcript_path", "")
    
    brain_dir = None
    if transcript_path:
        try:
            brain_dir = Path(transcript_path).parent.parent
        except Exception:
            pass
    elif conv_id:
        brain_dir = Path.home() / ".gemini" / "antigravity-cli" / "brain" / str(conv_id)

    if brain_dir and brain_dir.exists():
        try:
            plan_files = list(brain_dir.glob("*plan*.md"))
            now = time.time()
            for p in plan_files:
                if (now - p.stat().st_mtime) < 1800:
                    return "planning"
        except Exception:
            pass

    return ""

def get_git_status(cwd: str, vcs_payload: Optional[Dict[str, Any]] = None) -> Tuple[str, str, Dict[str, int], str]:
    """Returns (branch, git_disp, counts, mod_disp).

    mod_disp is the dirty/clean indicator (📝 symbols or ✅) split out on its
    own so callers can place it at the end of the line instead of stuck
    right after the branch name.
    """
    branch = (vcs_payload.get("branch") if vcs_payload else None)
    try:
        # Timeouts matter here: this runs on every prompt render, so a slow
        # git (network mount, huge repo, hooks) must never hang the CLI.
        is_git = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, check=False, timeout=2
        ).stdout.strip()

        if is_git != "true":
            return "", "", {"staged": 0, "unstaged": 0, "untracked": 0, "conflicted": 0}, ""

        if not branch:
            branch = subprocess.run(
                ["git", "-C", cwd, "symbolic-ref", "--short", "HEAD"],
                capture_output=True, text=True, check=False, timeout=2
            ).stdout.strip() or "HEAD"

        status_raw = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=2
        ).stdout

        # NOTE: don't .strip() status_raw as a whole — porcelain status codes
        # are fixed two-char columns where a leading space is meaningful
        # (e.g. " M" = unstaged-only); stripping the full string eats that
        # space off the first line and misclassifies it as staged.
        status_lines = [l for l in status_raw.splitlines() if l.strip()]

        # Unmerged (conflicted) entries use XY codes like "UU", "AA", "DD",
        # "AU", "UA", "UD", "DU" — neither X nor Y alone reliably means
        # "conflicted" (e.g. plain "A" is just staged-add), so they're
        # matched as a fixed set of codes rather than folded into the
        # staged/unstaged checks below, which would otherwise silently drop
        # them (X="U"/Y="U" isn't in "MADRC" or "MD") and render mid-conflict
        # repos as "Clean".
        conflict_codes = {"UU", "AA", "DD", "AU", "UA", "UD", "DU"}
        conflicted = sum(1 for l in status_lines if l[:2] in conflict_codes)
        staged = sum(1 for l in status_lines if l[:2] not in conflict_codes and l[0] in "MADRC")
        unstaged = sum(1 for l in status_lines if l[:2] not in conflict_codes and l[1] in "MD")
        untracked = sum(1 for l in status_lines if l.startswith("??"))

        counts = {"staged": staged, "unstaged": unstaged, "untracked": untracked, "conflicted": conflicted}

        if not status_lines:
            mod_disp = f"{C_GREEN}✅{RESET}"
        else:
            symbols = ""
            if conflicted > 0: symbols += f"{C_RED}✗{RESET}"
            if staged > 0: symbols += f"{C_GREEN}+{RESET}"
            if unstaged > 0: symbols += f"{C_YELLOW}!{RESET}"
            if untracked > 0: symbols += f"{C_SUBTEXT}?{RESET}"
            mod_disp = f"📝{symbols}"

        git_disp = f"{C_GREEN}🌿({branch}){RESET}{mod_disp}"

        return branch, git_disp, counts, mod_disp
    except Exception:
        if branch:
            dirty = vcs_payload.get("dirty", False) if vcs_payload else False
            mod_disp = f"{C_YELLOW}📝{RESET}" if dirty else f"{C_GREEN}✅{RESET}"
            return branch, f"{C_GREEN}🌿({branch}){RESET}{mod_disp}", {"staged": 0, "unstaged": 0, "untracked": 0, "conflicted": 0}, mod_disp
        return "", "", {"staged": 0, "unstaged": 0, "untracked": 0, "conflicted": 0}, ""

def render_small(data: Dict[str, Any]):
    model_name = get_model_name(data)
    state = data.get("agent_state", "").lower()
    state_str = f" {C_PINK}🤔 thinking{RESET}" if state == "thinking" else (f" {C_CYAN}⚡ working{RESET}" if state in ["working", "tool_use"] else "")
    model_disp = f"🤖 {BOLD}{C_TEAL}{model_name}{RESET}{state_str}"

    exec_mode = resolve_execution_mode(data)
    mode_disp = f"{mode_color(exec_mode)}🎯 {exec_mode.upper()}{RESET}" if exec_mode else ""

    cwd = _safe_dict(data.get("workspace")).get("current_dir") or data.get("cwd") or os.getcwd()
    dir_path = format_dir_path(cwd, max_len=40)
    dir_disp = f"📂 {C_SUBTEXT}{dir_path}{RESET}"

    _, git_disp, _, dirty_disp = get_git_status(cwd, data.get("vcs"))
    branch_disp = git_disp[:-len(dirty_disp)] if git_disp and dirty_disp and git_disp.endswith(dirty_disp) else git_disp

    artifacts = data.get("artifact_count", 0)
    tasks = data.get("task_count", 0)
    art_parts = []
    if artifacts > 0:
        art_parts.append(f"{C_LAVENDER}📄{artifacts}{RESET}")
    if tasks > 0:
        art_parts.append(f"{C_PEACH}⚙️{tasks}{RESET}")

    art_disp = (" " + " ".join(art_parts)) if art_parts else ""
    git_part = f" {branch_disp}" if branch_disp else ""
    dirty_part = f" {dirty_disp}" if dirty_disp else ""
    mode_part = f" {mode_disp}" if mode_disp else ""

    line1 = f"{model_disp} │ {dir_disp}{git_part}{art_disp}{dirty_part}{mode_part}"

    ctx_pct = _safe_dict(data.get("context_window")).get("used_percentage", 0.0)
    cr, cg, cb = get_context_color(ctx_pct)
    ctx_disp = f"🧠 {C_PINK}Ctx{RESET} {generate_bar(ctx_pct, 10, 'ctx')} {BOLD}\033[38;2;{cr};{cg};{cb}m{ctx_pct:.0f}%{RESET}{_warn_icon(ctx_pct)}"

    w_data, f_data = find_quota_buckets(data)

    f_pct = _quota_pct(f_data) if f_data else 0.0
    f_reset = format_time_remaining(f_data.get("reset_in_seconds")) if f_data.get("reset_in_seconds") else ""
    fr, fg, fb = get_5h_color(f_pct)
    f_disp = f"{C_LAVENDER}5H{RESET} {generate_bar(f_pct, 10, '5h')} {BOLD}\033[38;2;{fr};{fg};{fb}m{f_pct:.0f}%{RESET}{_warn_icon(f_pct)}" + (f" ({f_reset})" if f_reset else "")

    w_pct = _quota_pct(w_data) if w_data else 0.0
    w_reset = format_reset_day(w_data.get("reset_time") or w_data.get("reset_in_seconds")) if (w_data.get("reset_time") or w_data.get("reset_in_seconds")) else ""
    wr, wg, wb = get_7d_color(w_pct)
    w_disp = f"{C_YELLOW}7D{RESET} {generate_bar(w_pct, 10, '7d')} {BOLD}\033[38;2;{wr};{wg};{wb}m{w_pct:.0f}%{RESET}{_warn_icon(w_pct)}" + (f" ({w_reset})" if w_reset else "")

    line2 = f"{ctx_disp} │ {f_disp} │ {w_disp} │ {C_SUBTEXT}🕒 {time.strftime('%H:%M:%S')}{RESET}"
    print(f"{line1}{CLR}\n{line2}{CLR}")

def render_medium(data: Dict[str, Any]):
    model_name = get_model_name(data)
    state = data.get("agent_state", "").lower()
    state_str = f" {C_PINK}🤔 thinking{RESET}" if state == "thinking" else (f" {C_CYAN}⚡ working{RESET}" if state in ["working", "tool_use"] else "")
    model_disp = f"🤖 {BOLD}{C_TEAL}{model_name}{RESET}{state_str}"

    exec_mode = resolve_execution_mode(data)
    mode_disp = f" │ 🎯 {mode_color(exec_mode)}{exec_mode.upper()}{RESET}" if exec_mode else ""

    tasks = data.get("task_count", 0)
    artifacts = data.get("artifact_count", 0)
    task_parts = []
    if artifacts > 0:
        task_parts.append(f"{C_LAVENDER}📄 Artifacts {artifacts}{RESET}")
    if tasks > 0:
        task_parts.append(f"{C_PEACH}⚙️ Tasks {tasks}{RESET}")
    task_disp = (" │ " + " │ ".join(task_parts)) if task_parts else f" │ {C_SUBTEXT}📄 Artifacts 0{RESET}"

    line1 = f"{model_disp}{task_disp}{mode_disp}"

    cwd = _safe_dict(data.get("workspace")).get("current_dir") or data.get("cwd") or os.getcwd()
    dir_path = format_dir_path(cwd, max_len=48)
    dir_disp = f"📂 {C_SUBTEXT}{dir_path}{RESET}"

    _, git_disp, _, _ = get_git_status(cwd, data.get("vcs"))

    line2 = f"{dir_disp} {git_disp}"

    ctx_pct = _safe_dict(data.get("context_window")).get("used_percentage", 0.0)
    cr, cg, cb = get_context_color(ctx_pct)
    ctx_disp = f"🧠 {C_PINK}Context{RESET} {generate_bar(ctx_pct, 10, 'ctx')} {BOLD}\033[38;2;{cr};{cg};{cb}m{ctx_pct:.0f}%{RESET}{_warn_icon(ctx_pct)}"

    w_data, f_data = find_quota_buckets(data)

    f_pct = _quota_pct(f_data) if f_data else 0.0
    f_reset = format_time_remaining(f_data.get("reset_in_seconds")) if f_data.get("reset_in_seconds") else ""
    fr, fg, fb = get_5h_color(f_pct)
    f_disp = f"{C_LAVENDER}5H{RESET} {generate_bar(f_pct, 10, '5h')} {BOLD}\033[38;2;{fr};{fg};{fb}m{f_pct:.0f}%{RESET}{_warn_icon(f_pct)}" + (f" ({f_reset})" if f_reset else "")

    w_pct = _quota_pct(w_data) if w_data else 0.0
    w_reset = format_reset_day(w_data.get("reset_time") or w_data.get("reset_in_seconds")) if (w_data.get("reset_time") or w_data.get("reset_in_seconds")) else ""
    wr, wg, wb = get_7d_color(w_pct)
    w_disp = f"{C_YELLOW}7D{RESET} {generate_bar(w_pct, 10, '7d')} {BOLD}\033[38;2;{wr};{wg};{wb}m{w_pct:.0f}%{RESET}{_warn_icon(w_pct)}" + (f" ({w_reset})" if w_reset else "")

    line3 = f"{ctx_disp} │ {f_disp} │ {w_disp} │ {C_SUBTEXT}🕒 {time.strftime('%H:%M:%S')}{RESET}"
    print(f"{line1}{CLR}\n{line2}{CLR}\n{line3}{CLR}")

def render_large(data: Dict[str, Any]):
    model_name = get_model_name(data)
    model_disp = f"🤖 Model: {BOLD}{C_TEAL}{model_name}{RESET}"

    state = data.get("agent_state", "").lower()
    if state == "thinking":
        state_icon, state_label = "🤔", "THINKING"
    elif state in ["working", "tool_use"]:
        state_icon, state_label = "⚡", "WORKING"
    else:
        state_icon, state_label = "💤", "IDLE"
    state_disp = f"{state_icon} State: {C_PINK}{state_label}{RESET}"

    exec_mode = resolve_execution_mode(data)
    mode_str = exec_mode.upper() if exec_mode else "DEFAULT"
    mode_disp = f"🎯 Mode: {mode_color(exec_mode)}{mode_str}{RESET}"

    artifacts = data.get("artifact_count", 0)
    art_disp = f" │ 📄 Artifacts: {C_LAVENDER}{artifacts}{RESET}"

    line1 = f"{model_disp} │ {state_disp}{art_disp} │ {mode_disp}"

    cwd = _safe_dict(data.get("workspace")).get("current_dir") or data.get("cwd") or os.getcwd()
    dir_path = format_dir_path(cwd)
    dir_disp = f"📂 Workspace: {C_SUBTEXT}{dir_path}{RESET}"

    branch, _, counts, _ = get_git_status(cwd, data.get("vcs"))
    if branch:
        staged, unstaged, untracked = counts["staged"], counts["unstaged"], counts["untracked"]
        conflicted = counts.get("conflicted", 0)
        if staged == 0 and unstaged == 0 and untracked == 0 and conflicted == 0:
            git_disp = f"🌿 Git: {C_GREEN}{branch}{RESET} {C_GREEN}✅ Clean{RESET}"
        else:
            conflict_part = f", conflicted: {C_RED}{conflicted}{RESET}" if conflicted else ""
            status_label = f"{C_RED}⚠️ Conflict{RESET}" if conflicted else f"{C_YELLOW}📝 Modified{RESET}"
            git_disp = (
                f"🌿 Git: {C_GREEN}{branch}{RESET} "
                f"(staged: {C_GREEN}{staged}{RESET}, unstaged: {C_YELLOW}{unstaged}{RESET}, untracked: {C_SUBTEXT}{untracked}{RESET}{conflict_part}) "
                f"{status_label}"
            )
    else:
        git_disp = ""

    line2 = f"{dir_disp} │ {git_disp}" if git_disp else dir_disp

    ctx = _safe_dict(data.get("context_window"))
    ctx_pct = ctx.get("used_percentage", 0.0)
    cr, cg, cb = get_context_color(ctx_pct)

    total_in = ctx.get("total_input_tokens", 0)
    total_out = ctx.get("total_output_tokens", 0)
    used_tok = total_in + total_out
    ctx_size = ctx.get("context_window_size", 1048576)
    used_k = f"{used_tok/1000.0:.1f}k" if used_tok > 0 else "0k"
    size_m = f"{ctx_size/1048576.0:.1f}M" if ctx_size >= 1048576 else f"{ctx_size/1000.0:.0f}k"

    ctx_bar = generate_bar(ctx_pct, 20, "ctx")
    line3 = f"🧠 Context Window: {BOLD}\033[38;2;{cr};{cg};{cb}m{ctx_pct:.1f}%{RESET}{_warn_icon(ctx_pct)} [{ctx_bar}] ({used_k} / {size_m} tokens)"

    w_data, f_data = find_quota_buckets(data)

    f_pct = _quota_pct(f_data) if f_data else 0.0
    f_reset = format_time_remaining(f_data.get("reset_in_seconds")) if f_data.get("reset_in_seconds") else ""
    fr, fg, fb = get_5h_color(f_pct)
    f_disp = f"⏱️ 5H Quota: {BOLD}\033[38;2;{fr};{fg};{fb}m{f_pct:.0f}%{RESET}{_warn_icon(f_pct)} [{generate_bar(f_pct, 12, '5h')}]" + (f" ({f_reset})" if f_reset else "")

    w_pct = _quota_pct(w_data) if w_data else 0.0
    w_reset = format_reset_day(w_data.get("reset_time") or w_data.get("reset_in_seconds")) if (w_data.get("reset_time") or w_data.get("reset_in_seconds")) else ""
    wr, wg, wb = get_7d_color(w_pct)
    w_disp = f"📅 7D Quota: {BOLD}\033[38;2;{wr};{wg};{wb}m{w_pct:.0f}%{RESET}{_warn_icon(w_pct)} [{generate_bar(w_pct, 12, '7d')}]" + (f" ({w_reset})" if w_reset else "")

    line4 = f"{f_disp} │ {w_disp} │ {C_SUBTEXT}🕒 {time.strftime('%H:%M:%S')}{RESET}"
    print(f"{line1}{CLR}\n{line2}{CLR}\n{line3}{CLR}\n{line4}{CLR}")

def render_micro(data: Dict[str, Any]):
    """1-line, no-bars layout for very cramped panes (tmux/screen status
    bars, split panes): model name plus Ctx/5H/7D percentages only."""
    model_name = get_model_name(data)
    model_disp = f"🤖 {BOLD}{C_TEAL}{model_name}{RESET}"

    ctx_pct = _safe_dict(data.get("context_window")).get("used_percentage", 0.0)
    cr, cg, cb = get_context_color(ctx_pct)
    ctx_disp = f"{C_PINK}Ctx{RESET} {BOLD}\033[38;2;{cr};{cg};{cb}m{ctx_pct:.0f}%{RESET}{_warn_icon(ctx_pct)}"

    w_data, f_data = find_quota_buckets(data)

    f_pct = _quota_pct(f_data) if f_data else 0.0
    fr, fg, fb = get_5h_color(f_pct)
    f_disp = f"{C_LAVENDER}5H{RESET} {BOLD}\033[38;2;{fr};{fg};{fb}m{f_pct:.0f}%{RESET}{_warn_icon(f_pct)}"

    w_pct = _quota_pct(w_data) if w_data else 0.0
    wr, wg, wb = get_7d_color(w_pct)
    w_disp = f"{C_YELLOW}7D{RESET} {BOLD}\033[38;2;{wr};{wg};{wb}m{w_pct:.0f}%{RESET}{_warn_icon(w_pct)}"

    line1 = f"{model_disp} │ {ctx_disp} │ {f_disp} │ {w_disp}"
    print(f"{line1}{CLR}")

RENDERERS = {
    "small": render_small,
    "medium": render_medium,
    "large": render_large,
    "micro": render_micro,
}
_SIZE_ALIASES = {"s": "small", "m": "medium", "l": "large", "xs": "micro"}

def canonical_size(size: str) -> str:
    size = _SIZE_ALIASES.get(size, size)
    return size if size in RENDERERS else "large"

_SHRINK_ORDER = ["large", "medium", "small", "micro"]
_SIZE_MIN_WIDTH = {"large": 100, "medium": 72, "small": 56, "micro": 0}

def get_terminal_width(default: int = 200) -> int:
    """Best-effort real terminal width, even though stdout is piped/captured
    here (Antigravity runs this as a subprocess and reads its stdout) —
    /dev/tty refers to the controlling terminal regardless of stdio
    redirection. Falls back to a generous default when it can't be
    determined at all, so non-interactive/test usage is unaffected.
    """
    try:
        with open("/dev/tty") as tty:
            return os.get_terminal_size(tty.fileno()).columns
    except Exception:
        pass
    try:
        return shutil.get_terminal_size(fallback=(default, 24)).columns
    except Exception:
        return default

def maybe_shrink(size: str) -> str:
    """Auto-downgrade to a narrower layout if the terminal can't fit the
    configured one, so a wide layout doesn't wrap/break in a narrow pane.
    Thresholds are a rough heuristic (not an exact rendered-width
    measurement), tuned to each layout's roughly-fixed-width content.
    """
    width = get_terminal_width()
    idx = _SHRINK_ORDER.index(size)
    for candidate in _SHRINK_ORDER[idx:]:
        if width >= _SIZE_MIN_WIDTH[candidate]:
            return candidate
    return "micro"

def selftest() -> bool:
    sample_payloads = [
        {},
        {
            "model": {"display_name": "Test Model"}, "cycle_mode": "plan", "agent_state": "working",
            "artifact_count": 2, "task_count": 1,
            "context_window": {"used_percentage": 92},
            "quota": {"five_hour_gemini": {"used_percentage": 95}, "week_gemini": {"used_percentage": 40}},
        },
        {"model": "bare-string-model", "agent_state": "thinking"},
    ]
    ok = True
    for size in RENDERERS:
        for theme in THEMES:
            apply_theme(theme)
            for payload in sample_payloads:
                try:
                    with redirect_stdout(io.StringIO()):
                        RENDERERS[size](payload)
                except Exception as e:
                    ok = False
                    print(f"FAIL size={size} theme={theme} payload_keys={list(payload.keys())}: {e!r}", file=sys.stderr)
    apply_theme(DEFAULT_THEME)
    combos = len(RENDERERS) * len(THEMES) * len(sample_payloads)
    if ok:
        print(f"selftest OK — {combos} combinations rendered without error")
    else:
        print("selftest FAILED — see stderr for details", file=sys.stderr)
    return ok

def main():
    size = "large"
    if "--size" in sys.argv:
        idx = sys.argv.index("--size")
        if idx + 1 < len(sys.argv):
            size = sys.argv[idx + 1].lower()

    theme = os.environ.get("AGY_STATUSLINE_THEME", DEFAULT_THEME)
    if "--theme" in sys.argv:
        idx = sys.argv.index("--theme")
        if idx + 1 < len(sys.argv):
            theme = sys.argv[idx + 1].lower()
    apply_theme(theme.replace(" ", "-").replace("_", "-"))

    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)

    size = canonical_size(size)
    if "--no-autoshrink" not in sys.argv and os.environ.get("AGY_STATUSLINE_NO_AUTOSHRINK") != "1":
        size = maybe_shrink(size)

    data = read_stdin_json()

    try:
        RENDERERS[size](data)
    except Exception:
        # Antigravity's payload schema is undocumented and can change without
        # notice — never let a render bug take down the whole prompt line.
        print(f"🤖 agy{CLR}")

if __name__ == "__main__":
    main()
