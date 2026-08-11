#!/usr/bin/env bash
set -euo pipefail

echo "╔══════════════════════════════════════════╗"
echo "║   OpenTor MCP installer                  ║"
echo "║   Supervised Tor browsing for AI agents  ║"
echo "╚══════════════════════════════════════════╝"
echo

if ! command -v curl &>/dev/null; then
    echo "  ✗ curl is required for the Tor readiness check. Install curl and retry."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. Check Tor ────────────────────────────────────────────────

echo "▸ Checking Tor..."
if command -v tor &>/dev/null; then
    echo "  ✓ Tor found: $(tor --version | head -1)"
else
    echo "  ✗ Tor not found."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if ! command -v brew &>/dev/null; then
            echo "  ✗ Homebrew is required to install Tor on macOS: https://brew.sh"
            exit 1
        fi
        echo "  Installing via Homebrew..."
        brew install tor
    elif command -v apt-get &>/dev/null; then
        echo "  Installing via apt..."
        sudo apt-get update && sudo apt-get install -y tor
    elif command -v dnf &>/dev/null; then
        echo "  Installing via dnf..."
        sudo dnf install -y tor
    else
        echo "  Please install Tor manually: https://www.torproject.org/"
        exit 1
    fi
    echo "  ✓ Tor installed."
fi

# ── 2. Check Python ────────────────────────────────────────────

echo "▸ Checking Python..."
PYTHON=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$("$cmd" -c "import sys; print(sys.version_info.major)")
        minor=$("$cmd" -c "import sys; print(sys.version_info.minor)")
        if [[ "$major" -gt 3 || ( "$major" -eq 3 && "$minor" -ge 11 ) ]]; then
            PYTHON="$cmd"
            echo "  ✓ Python $version found."
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "  ✗ Python 3.11+ required. Install from https://python.org"
    exit 1
fi

# ── 3. Create venv ──────────────────────────────────────────────

echo "▸ Creating virtual environment..."
if [[ ! -d ".venv" ]]; then
    "$PYTHON" -m venv .venv
    echo "  ✓ venv created."
else
    echo "  ✓ venv already exists."
fi

source .venv/bin/activate

# ── 4. Install package ─────────────────────────────────────────

echo "▸ Installing OpenTor MCP..."
if command -v uv &>/dev/null; then
    if [[ "${TOR_MCP_INSTALL_OCR:-false}" == "true" ]]; then
        uv sync --locked --extra ocr
    else
        uv sync --locked
    fi
else
    echo "  ⚠ uv not found; using pip without the repository lockfile."
    python -m pip install --upgrade pip -q
    if [[ "${TOR_MCP_INSTALL_OCR:-false}" == "true" ]]; then
        if ! python -m pip install -e ".[ocr]"; then
            echo "  ⚠ Optional ddddocr installation failed; installing the core package only."
            python -m pip install -e .
        fi
    else
        python -m pip install -e .
    fi
fi
echo "  ✓ OpenTor MCP installed."

# ── 5. Install Playwright Firefox ──────────────────────────────

echo "▸ Installing Playwright Firefox browser..."
if [[ "$OSTYPE" == "linux"* ]]; then
    python -m playwright install --with-deps firefox
else
    python -m playwright install firefox
fi
echo "  ✓ Firefox browser installed."

# ── 6. Start Tor if not running ────────────────────────────────

echo "▸ Checking Tor service..."
if curl -s --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip 2>/dev/null | grep -q '"IsTor":true'; then
    echo "  ✓ Tor is running and connected."
else
    echo "  Starting Tor..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew services start tor 2>/dev/null || tor &
    else
        sudo systemctl start tor 2>/dev/null || tor &
    fi
    sleep 5
    if curl -s --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip 2>/dev/null | grep -q '"IsTor":true'; then
        echo "  ✓ Tor started and connected."
    else
        echo "  ⚠ Tor may still be bootstrapping. Give it 30 seconds."
    fi
fi

echo "▸ Checking authenticated Tor control port..."
if python - <<'PY'
import os
from stem.control import Controller

port = int(os.environ.get("TOR_CONTROL_PORT", "9051"))
password = os.environ.get("TOR_CONTROL_PASSWORD") or None
with Controller.from_port(port=port) as controller:
    controller.authenticate(password=password)
PY
then
    echo "  ✓ Tor control port is reachable and authenticated."
else
    echo "  ⚠ Tor control is unavailable. Browsing works, but NEWNYM will not."
    echo "    Configure 'ControlPort 9051' and 'CookieAuthentication 1' in torrc."
fi

# ── 7. Print config ───────────────────────────────────────────

VENV_COMMAND="$SCRIPT_DIR/.venv/bin/tor-mcp"

echo
echo "════════════════════════════════════════════"
echo "  ✓ Installation complete!"
echo "════════════════════════════════════════════"
echo
echo "Add this stdio server to your MCP client's mcpServers config:"
echo
echo "  \"opentor-mcp\": {"
echo "    \"command\": \"$VENV_COMMAND\","
echo "    \"env\": {"
echo "      \"TOR_SOCKS_PORT\": \"9050\","
echo "      \"TOR_CONTROL_PORT\": \"9051\","
echo "      \"TOR_HEADLESS\": \"true\","
echo "      \"TOR_MCP_DIR\": \"$SCRIPT_DIR\","
echo "      \"TOR_ALLOW_JAVASCRIPT\": \"false\","
echo "      \"TOR_IGNORE_HTTPS_ERRORS\": \"false\""
echo "    }"
echo "  }"
echo
echo "Restart your MCP client. You should then see the tor_* tools."
echo
