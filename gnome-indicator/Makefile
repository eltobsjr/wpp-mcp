UUID = whatsapp-bridge@eltobsjr.gmail.com
EXT  = extension
DEST = $(HOME)/.local/share/gnome-shell/extensions/$(UUID)

.PHONY: all install uninstall enable disable schema pack clean

all: install

install: schema
	@rm -rf "$(DEST)"
	@mkdir -p "$(DEST)"
	@cp -r $(EXT)/. "$(DEST)/"
	@glib-compile-schemas "$(DEST)/schemas"
	@echo "Instalado em $(DEST)"

schema:
	@glib-compile-schemas $(EXT)/schemas

enable:
	@gnome-extensions enable $(UUID)

disable:
	@gnome-extensions disable $(UUID)

uninstall:
	@rm -rf "$(DEST)"
	@echo "Removido."

pack: schema
	@cd $(EXT) && zip -r -FS ../$(UUID).shell-extension.zip . \
		-x 'schemas/gschemas.compiled'
	@echo "Pacote: $(UUID).shell-extension.zip"

clean:
	@rm -f $(UUID).shell-extension.zip
	@rm -f $(EXT)/schemas/gschemas.compiled
