# WhatsApp MCP Server

Servidor MCP (Model Context Protocol) para WhatsApp. Com ele, o Claude consegue buscar e ler suas mensagens pessoais do WhatsApp (incluindo imagens, vídeos, documentos e áudios), buscar contatos e enviar mensagens para pessoas ou grupos, além de enviar arquivos de mídia.

A conexão é feita diretamente com a **sua conta pessoal do WhatsApp**, via API multi-dispositivo do WhatsApp Web (usando a biblioteca [whatsmeow](https://github.com/tulir/whatsmeow)). Todas as mensagens ficam armazenadas localmente num banco SQLite e só são enviadas a um LLM (como o Claude) quando o agente acessa esses dados através das ferramentas — o que você controla.

Exemplo de uso com o Claude:

![WhatsApp MCP](./example-use.png)

> **Cuidado:** como em outros servidores MCP, o WhatsApp MCP está sujeito à ["lethal trifecta"](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) — a combinação de acesso a dados privados, exposição a conteúdo não confiável e capacidade de comunicação externa. Isso significa que uma prompt injection pode, em teoria, levar à exfiltração de dados privados. Revise o que o agente está fazendo antes de dar carta branca para enviar mensagens.

Este repositório é uma cópia independente, mantida por mim, baseada no projeto original [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp) (MIT).

## O que tem aqui

Três componentes, um repositório só:

| Componente | O que faz |
|---|---|
| `whatsapp-bridge/` (Go) | Conecta na API do WhatsApp Web, autentica por QR code, guarda o histórico em SQLite e expõe uma API REST local. |
| `whatsapp-mcp-server/` (Python) | Servidor MCP: as ferramentas que o Claude usa pra ler/enviar dados do WhatsApp. |
| `gnome-indicator/` (GJS, opcional) | Ícone na barra do GNOME pra ligar/desligar o bridge sem terminal. |

## O que é diferente da versão original

- **whatsmeow atualizado**, com as chamadas de API já ajustadas para exigir `context.Context`.
- **API REST do bridge só em `127.0.0.1`** (antes escutava em todas as interfaces de rede — qualquer coisa na sua LAN conseguia mandar mensagem pela sua conta).
- **Porta configurável** via `WHATSAPP_BRIDGE_PORT` (padrão `9090`), lida tanto pelo bridge quanto pelo servidor MCP a partir da mesma variável — evita as duas pontas ficarem dessincronizadas.
- **Endpoint `/api/status`**, reportando se o bridge está de fato conectado e logado no WhatsApp (não só "processo rodando").
- **Logs sem conteúdo de mensagem**: o bridge loga metadados (remetente, tipo, tamanho) mas não o texto das suas conversas — importante porque, rodando como serviço, esses logs vão parar no `journalctl`.
- **`print()` do servidor MCP redirecionado para stderr**: como o MCP fala JSON-RPC via stdout, `print()` solto no meio do código podia corromper o protocolo.
- **Timeout em todas as chamadas HTTP** entre o servidor MCP e o bridge.
- **Instalador único** (`install.sh`) que builda, ajuda na autenticação, registra o MCP e pergunta se você quer o serviço systemd e/ou o indicador GNOME.
- **Indicador GNOME** pra ligar/desligar o bridge sem deixá-lo sempre ativo em segundo plano.

## Arquitetura

```
Claude ──MCP (stdio)──▶ whatsapp-mcp-server (Python) ──HTTP :9090──▶ whatsapp-bridge (Go) ──▶ WhatsApp
                                    │                                        │      ▲
                                    └──────────── lê direto ────────────────▶ SQLite │
                                                                                      │
                                              indicador GNOME ── systemctl + /api/status
```

- O **indicador GNOME** não fala com o servidor MCP — ele controla o `systemd` (liga/desliga o processo) e consulta `/api/status` direto no bridge, pelas mesmas portas usadas pelo servidor MCP.
- O **servidor MCP** é iniciado automaticamente pelo Claude a cada sessão (processo curto, via stdio). O **bridge** é quem precisa ficar rodando continuamente pra manter a sessão do WhatsApp — é ele que o systemd/indicador controlam.

### Armazenamento

- O histórico de mensagens fica em SQLite, dentro de `whatsapp-bridge/store/`.
- Por padrão só os **metadados** da mídia são salvos — o arquivo em si só é baixado quando a ferramenta `download_media` é chamada.

## Pré-requisitos

- [Go](https://go.dev/dl/) 1.25+
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (gerenciador de pacotes Python): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Claude Desktop, Claude Code ou Cursor
- FFmpeg (*opcional* — só necessário para enviar áudio como mensagem de voz reproduzível)
- `curl` (*opcional* — usado pelo indicador GNOME pra checar `/api/status`)

## Instalação rápida

A forma mais rápida — nem precisa clonar antes, o script se vira sozinho:

```bash
curl -fsSL https://raw.githubusercontent.com/eltobsjr/wpp-mcp/main/bootstrap.sh | bash
```

Isso clona o repositório em `~/wpp-mcp` (ou em `$WPP_MCP_DIR`, se definido) e já
executa o instalador. Se preferir clonar você mesmo:

```bash
git clone https://github.com/eltobsjr/wpp-mcp.git
cd wpp-mcp
./install.sh          # ou: make install
```

O script:

1. Confere se `go` e `uv` estão instalados — se não estiverem, **oferece instalar sozinho** (via `dnf`/`apt`/`pacman`/`brew`, conforme o sistema, ou o instalador oficial do `uv`), pedindo confirmação antes de qualquer `sudo`.
2. Compila o bridge (`whatsapp-bridge/wa-bridge-bin`).
3. Se ainda não houver sessão salva, roda o bridge em primeiro plano pra você escanear o QR code (**Configurações → Aparelhos conectados → Conectar um aparelho** no celular); assim que aparecer "✓ Connected to WhatsApp!", `Ctrl+C` continua a instalação.
4. Registra o servidor MCP no Claude Code (`claude mcp add`), se o CLI `claude` estiver disponível — senão, imprime o JSON pra colar manualmente no Claude Desktop/Cursor.
5. Pergunta se quer configurar o bridge como **serviço systemd de usuário** (recomendado — evita depender de um terminal aberto) e se quer deixá-lo subir sozinho no login (por padrão, não).
6. Se detectar GNOME Shell e o serviço tiver sido configurado, pergunta se quer instalar o **indicador na barra superior**.

Reinicie o Claude Desktop/Cursor (ou comece uma nova sessão no Claude Code) ao final.

### Atalhos (`make`)

Depois de instalado uma vez, o dia a dia pode ser só:

```bash
make status     # systemd + /api/status num comando só
make logs       # journalctl --user -u whatsapp-bridge.service -f
make restart    # systemctl --user restart
make bridge     # só recompila o binário do Go
make indicator  # (re)instala só o indicador GNOME
make uninstall  # desliga e remove o serviço + indicador (mantém a sessão salva)
```

### Instalação manual, passo a passo

Se preferir não rodar o script, ou estiver em outro SO:

**1. Compilar e autenticar**

```bash
cd whatsapp-bridge
go build -o wa-bridge-bin .   # ou: go run main.go
./wa-bridge-bin
```

Escaneie o QR code que aparece no terminal. A sessão dura cerca de 20 dias antes de pedir novo escaneamento.

**2. Registrar o servidor MCP**

Claude Code (CLI):

```bash
claude mcp add whatsapp --scope user -- \
  "$(which uv)" --directory "$(pwd)/../whatsapp-mcp-server" run main.py
```

Claude Desktop / Cursor — copie ajustando os `{{...}}`:

```json
{
  "mcpServers": {
    "whatsapp": {
      "command": "{{CAMINHO_DO_UV}}",
      "args": ["--directory", "{{CAMINHO_DO_REPO}}/whatsapp-mcp-server", "run", "main.py"]
    }
  }
}
```

- `{{CAMINHO_DO_UV}}`: saída de `which uv`
- `{{CAMINHO_DO_REPO}}`: saída de `pwd` dentro da pasta do repositório clonado

Salve como `~/Library/Application Support/Claude/claude_desktop_config.json` (Claude Desktop, macOS) ou `~/.cursor/mcp.json` (Cursor).

**3. (Opcional) Serviço systemd**

Crie `~/.config/systemd/user/whatsapp-bridge.service`:

```ini
[Unit]
Description=WhatsApp MCP Bridge (whatsmeow)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/caminho/para/whatsapp-mcp/whatsapp-bridge
ExecStart=%h/caminho/para/whatsapp-mcp/whatsapp-bridge/wa-bridge-bin
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user start whatsapp-bridge.service   # inicia agora
systemctl --user enable whatsapp-bridge.service  # opcional: sobe sozinho no login
```

Sem o `enable`, ele só roda quando você iniciar manualmente ou pelo indicador GNOME. Comandos úteis:

```bash
systemctl --user status whatsapp-bridge.service
journalctl --user -u whatsapp-bridge.service -f
systemctl --user stop whatsapp-bridge.service
```

**4. (Opcional) Indicador GNOME**

```bash
cd gnome-indicator
./install.sh
```

Ative a extensão (no Wayland, é preciso logout/login uma vez antes):

```bash
gnome-extensions enable whatsapp-bridge@eltobsjr.gmail.com
```

### Compatibilidade com Windows

`go-sqlite3` precisa de **CGO habilitado**, desabilitado por padrão no Windows.

1. Instale um compilador C — recomenda-se o [MSYS2](https://www.msys2.org/); depois de instalar, adicione `ucrt64\bin` ao `PATH` ([guia](https://code.visualstudio.com/docs/cpp/config-mingw)).
2. `go env -w CGO_ENABLED=1` e então `go run main.go`.

Sem isso: `Binary was compiled with 'CGO_ENABLED=0', go-sqlite3 requires cgo to work.`

## O indicador GNOME

Ícone na barra superior com quatro estados:

| Ícone | Significado |
|---|---|
| 🟢 verde | Serviço rodando e conectado/logado no WhatsApp |
| ⚪ cinza | Serviço desligado |
| 🟡 amarelo (iniciando) | Serviço acabou de subir, API ainda não respondeu |
| 🟡 amarelo (sem sessão) | Serviço rodando mas desconectado — pode precisar reescanear o QR code |

Menu:

- **Ligar/Desligar bridge** — `systemctl --user start/stop whatsapp-bridge.service`.
- **Ver logs** — abre `journalctl --user -u whatsapp-bridge.service -f` num terminal (tenta `ptyxis`, `gnome-terminal`, `konsole` ou `xterm`, nessa ordem).

Se ligar/desligar falhar (ex.: unidade não encontrada), aparece uma notificação com o motivo.

## Uso

Depois de conectado, é só conversar com o Claude normalmente — ele decide quando usar as ferramentas do WhatsApp.

### Ferramentas disponíveis no MCP

- **search_contacts** — busca contatos por nome ou número
- **list_messages** — lista mensagens com filtros e contexto opcionais
- **list_chats** — lista os chats disponíveis com metadados
- **get_chat** — informações de um chat específico
- **get_direct_chat_by_contact** — encontra o chat direto com um contato
- **get_contact_chats** — lista todos os chats envolvendo um contato
- **get_last_interaction** — última mensagem trocada com um contato
- **get_message_context** — contexto ao redor de uma mensagem específica
- **send_message** — envia mensagem para um número ou grupo (JID)
- **send_file** — envia arquivo (imagem, vídeo, áudio bruto, documento)
- **send_audio_message** — envia áudio como mensagem de voz do WhatsApp (precisa ser `.ogg` opus, ou ter o ffmpeg instalado)
- **download_media** — baixa a mídia de uma mensagem e retorna o caminho local do arquivo

### Envio e recebimento de mídia

**Enviando:** imagens/vídeos/documentos via `send_file`; mensagens de voz via `send_audio_message` (com ffmpeg, qualquer formato é convertido pra `.ogg` opus automaticamente; sem ffmpeg, dá pra mandar o áudio bruto via `send_file`, mas sem aparecer como mensagem de voz).

**Recebendo:** só os metadados ficam salvos por padrão — o arquivo só é baixado quando você pede pro Claude usar `download_media`, passando `message_id` e `chat_jid`.

## Segurança e privacidade

- A API REST do bridge (porta `9090` por padrão) só escuta em `127.0.0.1` — não é alcançável pela rede.
- Mesmo em localhost, ela **não tem autenticação** — qualquer processo seu consegue chamá-la. Não exponha essa porta (ex.: não faça port-forward/tunnel dela).
- `send_file`/`send_audio_message` aceitam qualquer caminho de arquivo local que o processo consiga ler — tenha isso em mente ao dar ao Claude acesso a diretórios sensíveis junto com este MCP (ver aviso da "lethal trifecta" no topo).
- Os logs do bridge (via `journalctl`, se rodando como serviço) não incluem o texto das mensagens, só metadados.

## Como funciona por baixo dos panos

1. O Claude manda a requisição para o servidor MCP em Python.
2. O servidor MCP consulta o bridge em Go (via HTTP, porta configurável por `WHATSAPP_BRIDGE_PORT`) ou lê direto do SQLite.
3. O bridge em Go mantém a conexão com a API do WhatsApp e o banco atualizado.
4. Os dados voltam pela mesma cadeia até o Claude.
5. Para enviar mensagens, o fluxo é o inverso: Claude → servidor MCP → bridge Go → WhatsApp.

## Solução de problemas

- Se der erro de permissão ao rodar o `uv`, adicione-o ao `PATH` ou use o caminho completo do executável.
- O **bridge precisa estar rodando** (via `go run`/binário, serviço systemd, ou ligado pelo indicador) para haver dados — o servidor MCP sozinho não faz nada.

### Problemas de autenticação

- **QR code não aparece**: reinicie o script de autenticação; verifique se o terminal suporta exibir QR codes.
- **"WhatsApp já conectado"**: se a sessão já estiver ativa, o bridge reconecta sozinho sem pedir novo QR code.
- **Limite de aparelhos atingido**: remova algum aparelho existente em **Configurações → Aparelhos conectados** no celular.
- **Mensagens não carregam**: após a autenticação inicial, pode levar alguns minutos até o histórico carregar.
- **WhatsApp fora de sincronia**: apague `whatsapp-bridge/store/messages.db` e `whatsapp-bridge/store/whatsapp.db` e reinicie o bridge para reautenticar do zero.
- **Indicador mostra "sem sessão" (amarelo) mesmo com o serviço ativo**: normalmente é a sessão expirada (~20 dias) — use "Ver logs" pra confirmar, e rode o bridge em primeiro plano uma vez pra reescanear o QR code.

Para problemas de integração com o Claude Desktop, veja a [documentação oficial do MCP](https://modelcontextprotocol.io/quickstart/server#claude-for-desktop-integration-issues).

## Licença

MIT — veja [LICENSE](./LICENSE). Baseado no trabalho original de [Luke Harries](https://github.com/lharries/whatsapp-mcp).
