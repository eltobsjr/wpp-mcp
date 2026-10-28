# WhatsApp Bridge — indicador do GNOME Shell

Ícone na barra superior para ligar e desligar o bridge do WhatsApp (whatsmeow) do
[wpp-mcp](../README.md) sem deixar o serviço sempre ativo. Mostra o estado (ligado,
desligado, conectando, erro) e controla o serviço `systemd --user`.

## Instalação

```bash
cd gnome-indicator
./install.sh
gnome-extensions enable whatsapp-bridge@eltobsjr.gmail.com
```

No Wayland, faça logout/login depois de instalar ou atualizar.

### Tema e opções (escolhidos na instalação)

O `./install.sh` pergunta no terminal, antes de copiar qualquer coisa:

- **Paleta**: Catppuccin Macchiato (padrão), Frappé ou Mocha, com uma prévia em cores.
- **Cor de destaque**: 12 opções, ou **seguir o destaque do GNOME** (47+).
- **Opções da extensão**: posição, avisos e intervalo de verificação.

Nenhuma cor fica escrita no código: o `stylesheet.css` é gerado na instalação a partir da paleta que você escolheu.
Para refazer as escolhas, rode `./install.sh --reconfigure`. Sem terminal (CI, `curl | bash`) ou com `--yes`,
vale o que já estava salvo ou o padrão. Também aceita `--palette macchiato|frappe|mocha`, `--accent blue|mauve|…` e `--system-accent`.
Detalhes do padrão visual em [`docs/DESIGN.md`](docs/DESIGN.md).

## Desenvolvimento

`make check` (sintaxe, schema e tema), `make pack` (gera o .zip) e `make clean`.

## Licença

MIT, como o restante do wpp-mcp.
