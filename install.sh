#!/usr/bin/env sh
set -e

REPO="https://github.com/agi-inc/claude-web.git"
DEST="${CLAUDE_WEB_DIR:-$HOME/Tools/claude-web}"

echo "Installing claude-web → $DEST"

if [ -d "$DEST/.git" ]; then
  echo "  → updating existing clone"
  git -C "$DEST" pull --ff-only
else
  git clone "$REPO" "$DEST"
fi

cd "$DEST"

uv tool install --editable . --quiet

mkdir -p "$HOME/.claude/skills"
SKILL_SRC="$DEST/.claude/skills/web"
SKILL_DST="$HOME/.claude/skills/web"
if [ -L "$SKILL_DST" ] || [ -e "$SKILL_DST" ]; then
  rm -rf "$SKILL_DST"
fi
ln -s "$SKILL_SRC" "$SKILL_DST"

echo ""
echo "Done. Verify:"
echo "  web-agent fetch https://example.com --markdown-only"
echo ""
echo "Then open Claude Code and try: /web browse hacker news"
