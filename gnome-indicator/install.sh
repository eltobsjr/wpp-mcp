#!/usr/bin/env bash
# Instalador do WhatsApp Bridge indicator (extensão GNOME Shell)
set -euo pipefail

UUID="whatsapp-bridge@eltobsjr.gmail.com"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/extension"
DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"

echo "==> Instalando WhatsApp Bridge indicator"

if ! command -v glib-compile-schemas >/dev/null 2>&1; then
    echo "ERRO: glib-compile-schemas não encontrado. Instale glib2-devel / libglib2.0-bin." >&2
    exit 1
fi

echo "==> Copiando para $DEST"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -r "$SRC/." "$DEST/"

echo "==> Compilando GSettings schema"
glib-compile-schemas "$DEST/schemas"

echo ""
echo "✅ Instalado!"
echo ""
echo "Agora ative a extensão:"
echo "  • Wayland: faça logout/login e rode  gnome-extensions enable $UUID"
echo "  • X11:     pressione Alt+F2, digite 'r', Enter, depois ative"
echo ""
echo "Ou use:  gnome-extensions enable $UUID"
