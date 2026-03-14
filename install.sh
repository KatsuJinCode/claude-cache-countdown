#!/usr/bin/env bash
# Install Claude Cache Countdown
# Adds the Stop hook to your Claude Code settings and creates an alias.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SETTINGS_FILE="$HOME/.claude/settings.json"
HOOK_SCRIPT="$SCRIPT_DIR/hooks/cache-timer-write.sh"

echo "Claude Cache Countdown Installer"
echo "================================"
echo ""

# Check prerequisites
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is required but not found."
    exit 1
fi

if [ ! -f "$HOOK_SCRIPT" ]; then
    echo "Error: Hook script not found at $HOOK_SCRIPT"
    exit 1
fi

chmod +x "$HOOK_SCRIPT"

# Create state directory
mkdir -p "$HOME/.claude/state"

echo "Hook script: $HOOK_SCRIPT"
echo "Ticker:      $SCRIPT_DIR/cache_countdown.py"
echo ""

# Check if settings.json exists
if [ ! -f "$SETTINGS_FILE" ]; then
    echo "Creating $SETTINGS_FILE..."
    mkdir -p "$(dirname "$SETTINGS_FILE")"
    echo '{}' > "$SETTINGS_FILE"
fi

# Add hook to settings.json
echo "Adding Stop hook to $SETTINGS_FILE..."
python3 -c "
import json, sys

settings_path = '$SETTINGS_FILE'
hook_cmd = 'bash $HOOK_SCRIPT'

with open(settings_path, 'r') as f:
    settings = json.load(f)

hooks = settings.setdefault('hooks', {})
stop_hooks = hooks.setdefault('Stop', [])

# Check if already installed
for entry in stop_hooks:
    for h in entry.get('hooks', []):
        if 'cache-timer-write' in h.get('command', ''):
            print('Stop hook already installed. Skipping.')
            sys.exit(0)

stop_hooks.append({
    'matcher': '',
    'hooks': [{
        'type': 'command',
        'command': hook_cmd,
        'timeout': 5
    }]
})

with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=2)

print('Stop hook added successfully.')
"

echo ""
echo "Installation complete!"
echo ""
echo "To start the countdown ticker, run:"
echo "  python3 $SCRIPT_DIR/cache_countdown.py"
echo ""
echo "Or add an alias to your shell profile:"
echo "  alias cache-ticker='python3 $SCRIPT_DIR/cache_countdown.py'"
echo ""
echo "The countdown will appear in your terminal title whenever a Claude Code"
echo "session stops. Restart Claude Code to load the new hook."
