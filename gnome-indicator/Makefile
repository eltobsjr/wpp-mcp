UUID = whatsapp-bridge@eltobsjr.gmail.com
EXT  = extension
DEST = $(HOME)/.local/share/gnome-shell/extensions/$(UUID)

.PHONY: all install uninstall enable disable pack clean

all: install

install:
	@rm -rf "$(DEST)"
	@mkdir -p "$(DEST)"
	@cp -r $(EXT)/. "$(DEST)/"
	@echo "Instalado em $(DEST)"

enable:
	@gnome-extensions enable $(UUID)

disable:
	@gnome-extensions disable $(UUID)

uninstall:
	@rm -rf "$(DEST)"
	@echo "Removido."

pack:
	@cd $(EXT) && zip -r -FS ../$(UUID).shell-extension.zip .
	@echo "Pacote: $(UUID).shell-extension.zip"

clean:
	@rm -f $(UUID).shell-extension.zip
