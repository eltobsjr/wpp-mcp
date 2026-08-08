SERVICE = whatsapp-bridge.service

.PHONY: install bridge service indicator status logs restart uninstall

# Fluxo completo guiado (build, auth, MCP, systemd, indicador)
install:
	@./install.sh

# Só compila o binário do bridge
bridge:
	@cd whatsapp-bridge && go build -o wa-bridge-bin .
	@echo "Binário em whatsapp-bridge/wa-bridge-bin"

# Instala/atualiza só o indicador GNOME
indicator:
	@gnome-indicator/install.sh

# Status combinado: systemd + /api/status do bridge
status:
	@systemctl --user status $(SERVICE) --no-pager || true
	@echo ""
	@curl -s -m 2 http://127.0.0.1:$${WHATSAPP_BRIDGE_PORT:-9090}/api/status || echo "(bridge não respondeu em /api/status)"
	@echo ""

logs:
	@journalctl --user -u $(SERVICE) -n 200 -f

restart:
	@systemctl --user restart $(SERVICE)

# Desliga e desabilita o serviço e remove a extensão GNOME instalada
# (não apaga a sessão do WhatsApp em whatsapp-bridge/store/)
uninstall:
	@systemctl --user disable --now $(SERVICE) 2>/dev/null || true
	@rm -f "$(HOME)/.config/systemd/user/$(SERVICE)"
	@systemctl --user daemon-reload
	@gnome-extensions uninstall whatsapp-bridge@eltobsjr.gmail.com 2>/dev/null || true
	@echo "Serviço e indicador removidos. A sessão do WhatsApp e o histórico continuam em whatsapp-bridge/store/."
