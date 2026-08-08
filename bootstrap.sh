#!/usr/bin/env bash
# Bootstrap de instalação com um comando só, sem precisar clonar antes:
#   curl -fsSL https://raw.githubusercontent.com/eltobsjr/wpp-mcp/main/bootstrap.sh | bash
#
# Clona (ou atualiza, se já existir) o repositório e passa a mão pro install.sh.
set -euo pipefail

REPO_URL="https://github.com/eltobsjr/wpp-mcp.git"
TARGET="${WPP_MCP_DIR:-$HOME/wpp-mcp}"

if ! command -v git >/dev/null 2>&1; then
    echo "git não encontrado — instale git e rode de novo." >&2
    exit 1
fi

if [ -d "$TARGET/.git" ]; then
    echo "Repositório já existe em $TARGET, atualizando..."
    git -C "$TARGET" pull --ff-only
else
    echo "Clonando em $TARGET..."
    git clone "$REPO_URL" "$TARGET"
fi

exec "$TARGET/install.sh"
