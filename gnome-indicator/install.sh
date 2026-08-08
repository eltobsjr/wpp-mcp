#!/usr/bin/env bash
# Instalador do WhatsApp Bridge indicator (extensão GNOME Shell)
set -euo pipefail

UUID="whatsapp-bridge@eltobsjr.gmail.com"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/extension"
DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"

echo "==> Instalando WhatsApp Bridge indicator"
echo "==> Copiando para $DEST"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -r "$SRC/." "$DEST/"

echo ""
echo "✅ Instalado!"
echo ""
echo "Agora ative a extensão:"
echo "  • Wayland: faça logout/login e rode  gnome-extensions enable $UUID"
echo "  • X11:     pressione Alt+F2, digite 'r', Enter, depois ative"
echo ""
echo "Ou use:  gnome-extensions enable $UUID"
