#!/usr/bin/env bash
# Instalador do wpp-mcp: bridge (Go) + servidor MCP (Python) e,
# opcionalmente, o serviço systemd e o indicador GNOME.
#
# Funciona tanto rodado direto (./install.sh) quanto via:
#   curl -fsSL https://raw.githubusercontent.com/eltobsjr/wpp-mcp/main/bootstrap.sh | bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_DIR="$ROOT/whatsapp-bridge"
MCP_SERVER_DIR="$ROOT/whatsapp-mcp-server"
INDICATOR_DIR="$ROOT/gnome-indicator"
SERVICE_NAME="whatsapp-bridge.service"
SERVICE_PATH="$HOME/.config/systemd/user/$SERVICE_NAME"

# Sob "curl | bash" o stdin do script É o próprio script sendo lido — não dá
# pra usar `read` nele. Toda entrada interativa vem do terminal de verdade.
TTY="/dev/tty"
[ -t 0 ] && TTY="/dev/stdin"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
step() { echo ""; bold "==> $1"; }
ask_yes_no() {
    # ask_yes_no "pergunta" default(y|n)
    local prompt="$1" default="${2:-n}" reply
    local hint="y/N"
    [ "$default" = "y" ] && hint="Y/n"
    if [ ! -e "$TTY" ]; then
        reply="$default" # sem terminal (ex.: CI): assume o default, não trava
    else
        read -r -p "$prompt [$hint] " reply < "$TTY" || reply="$default"
    fi
    reply="${reply:-$default}"
    [[ "$reply" =~ ^[Yy]$ ]]
}
pause() {
    [ -e "$TTY" ] && read -r -p "$1" _ < "$TTY" || true
}

# Detecta o gerenciador de pacotes do sistema pra instalar dependências que
# faltarem, em vez de só reclamar e abortar.
pkg_install() {
    local dnf_pkg="$1" apt_pkg="$2" pacman_pkg="$3" brew_pkg="$4"
    if command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y "$dnf_pkg"
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y "$apt_pkg"
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -Sy --noconfirm "$pacman_pkg"
    elif command -v brew >/dev/null 2>&1; then
        brew install "$brew_pkg"
    else
        return 1
    fi
}

ensure_dep() {
    local bin="$1" label="$2" dnf_pkg="$3" apt_pkg="$4" pacman_pkg="$5" brew_pkg="$6"
    command -v "$bin" >/dev/null 2>&1 && return 0
    echo "$label não encontrado."
    if ask_yes_no "Instalar $label agora (via sudo)?" y; then
        pkg_install "$dnf_pkg" "$apt_pkg" "$pacman_pkg" "$brew_pkg" \
            || { echo "Não achei um gerenciador de pacotes suportado (dnf/apt/pacman/brew). Instale $label manualmente." >&2; return 1; }
        command -v "$bin" >/dev/null 2>&1 || { echo "$label ainda não está no PATH depois de instalar — abra um terminal novo e rode de novo." >&2; return 1; }
    else
        return 1
    fi
}

step "Verificando pré-requisitos"
ensure_dep go "Go" golang golang-go go go || { echo "Go é obrigatório: https://go.dev/dl/" >&2; exit 1; }
if ! command -v uv >/dev/null 2>&1; then
    echo "uv não encontrado."
    if ask_yes_no "Instalar uv agora (curl -LsSf https://astral.sh/uv/install.sh | sh)?" y; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi
command -v uv >/dev/null 2>&1 || { echo "uv é obrigatório: https://docs.astral.sh/uv/" >&2; exit 1; }
echo "OK: go, uv encontrados."

step "Compilando o bridge (Go)"
(cd "$BRIDGE_DIR" && go build -o wa-bridge-bin .)
echo "Binário gerado em $BRIDGE_DIR/wa-bridge-bin"

if [ ! -f "$BRIDGE_DIR/store/whatsapp.db" ]; then
    step "Autenticação com o WhatsApp"
    echo "Vamos rodar o bridge em primeiro plano pra você escanear o QR code."
    echo "No celular: Configurações > Aparelhos conectados > Conectar um aparelho."
    echo "Depois de ver \"✓ Connected to WhatsApp!\", pressione Ctrl+C para continuar a instalação."
    echo ""
    pause "Pressione Enter para iniciar..."
    (cd "$BRIDGE_DIR" && ./wa-bridge-bin) || true
else
    echo ""
    echo "Sessão já autenticada encontrada em $BRIDGE_DIR/store/ — pulando o QR code."
fi

step "Registrando o servidor MCP"
if command -v claude >/dev/null 2>&1; then
    if claude mcp list 2>/dev/null | grep -q '^whatsapp'; then
        echo "MCP 'whatsapp' já registrado no Claude Code."
    elif ask_yes_no "Registrar automaticamente no Claude Code (claude mcp add)?" y; then
        claude mcp add whatsapp --scope user -- "$(command -v uv)" --directory "$MCP_SERVER_DIR" run main.py
        echo "Registrado. Confira com: claude mcp list"
    fi
else
    cat <<EOF
Claude Code (CLI) não encontrado no PATH. Para Claude Desktop ou Cursor,
adicione manualmente ao arquivo de configuração MCP:

{
  "mcpServers": {
    "whatsapp": {
      "command": "$(command -v uv || echo '{{CAMINHO_DO_UV}}')",
      "args": ["--directory", "$MCP_SERVER_DIR", "run", "main.py"]
    }
  }
}

Claude Desktop: ~/Library/Application Support/Claude/claude_desktop_config.json (macOS)
Cursor:         ~/.cursor/mcp.json
EOF
fi

INSTALLED_SERVICE=0
if command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
    step "Serviço systemd (ligar/desligar sob demanda)"
    if ask_yes_no "Configurar o bridge como serviço systemd de usuário (em vez de terminal aberto)?" y; then
        mkdir -p "$(dirname "$SERVICE_PATH")"
        cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=WhatsApp MCP Bridge (whatsmeow)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$BRIDGE_DIR
ExecStart=$BRIDGE_DIR/wa-bridge-bin
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user start "$SERVICE_NAME"
        echo "Serviço iniciado (systemctl --user status $SERVICE_NAME)."
        if ask_yes_no "Deixar ele subir sozinho a cada login (autostart)?" n; then
            systemctl --user enable "$SERVICE_NAME"
            echo "Autostart habilitado."
        else
            echo "Autostart NÃO habilitado — use 'systemctl --user start $SERVICE_NAME' ou o indicador GNOME pra ligar quando quiser."
        fi
        INSTALLED_SERVICE=1
    fi
fi

if [ "$INSTALLED_SERVICE" -eq 1 ] && [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]] && command -v gnome-extensions >/dev/null 2>&1; then
    step "Indicador na barra do GNOME"
    echo "Com o bridge como serviço, dá pra ligar/desligar direto por um ícone na"
    echo "barra superior — sem terminal. O ícone mostra o estado (verde = conectado,"
    echo "cinza = desligado, amarelo = rodando mas sem sessão ativa) e tem um item"
    echo "\"Ver logs\" pra abrir o journalctl num terminal."
    if ask_yes_no "Instalar o indicador GNOME agora?" y; then
        "$INDICATOR_DIR/install.sh"
    fi
fi

step "Pronto"
echo "Veja o README.md para detalhes, solução de problemas e as ferramentas MCP disponíveis."
