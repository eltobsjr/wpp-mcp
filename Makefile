SERVICE = whatsapp-bridge.service
CONV_SERVICE = wpp-conversation.service

.PHONY: install bridge service indicator status logs restart uninstall conv-install conv-start conv-stop conv-logs test lint

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

# Modo conversacional: vigia que acorda a rotina do OpenBot (ver conversation.py).
# O modo em si liga/desliga pela ferramenta MCP `conversation_mode`; o serviço só precisa estar de pé.
conv-install:
	@mkdir -p "$(HOME)/.config/systemd/user"
	@sed "s#__SERVER_DIR__#$(CURDIR)/whatsapp-mcp-server#g" systemd/wpp-conversation.service > "$(HOME)/.config/systemd/user/$(CONV_SERVICE)"
	@systemctl --user daemon-reload
	@echo "Instalado. Inicie com: make conv-start"

conv-start:
	@systemctl --user enable --now $(CONV_SERVICE)

conv-stop:
	@systemctl --user disable --now $(CONV_SERVICE)

conv-logs:
	@journalctl --user -u $(CONV_SERVICE) -n 100 -f

# Testes e lint do servidor MCP
test:
	@cd whatsapp-mcp-server && uv run --group dev pytest -q

lint:
	@cd whatsapp-mcp-server && uv run --group dev ruff check .
