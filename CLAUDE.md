# CLAUDE.md — Wpp Mcp

## 1. Sempre ler a memória antes de começar

Ao iniciar qualquer conversa neste projeto:

1. Leia o índice de memória: `~/.claude/projects/-home-eltobsjr-tools-wpp-mcp/memory/MEMORY.md`
   É um índice — siga os links para os arquivos relevantes à tarefa atual.

2. Leia o devtrack mais recente:
   `ls /home/eltobsjr/tools/wpp-mcp/WppMcpSecondBrain/devtrack/ | sort | tail -1`
   e leia o arquivo retornado. Isso garante continuidade: o que foi feito,
   decisões tomadas e pendências abertas na última sessão.

## 2. Stack e tecnologias

- **Go 1.25** (`whatsapp-bridge/`) — client WhatsApp via [whatsmeow](https://github.com/tulir/whatsmeow), SQLite via `mattn/go-sqlite3` (sem ORM, SQL cru), API REST própria (`net/http`) só em `127.0.0.1`.
- **Python 3.11+ / uv** (`whatsapp-mcp-server/`) — servidor MCP via `mcp[cli]` (FastMCP), fala com o bridge por HTTP (`requests`) e lê o SQLite direto.
- **GJS / GNOME Shell 45–50** (`gnome-indicator/`) — extensão de indicador na barra superior (`PanelMenu.Button`), sem gschema/prefs (é só o toggle).
- **systemd** (serviço de usuário) — mantém o bridge rodando; autostart habilitado por padrão (sobe sozinho a cada login).
- **Bash** — `install.sh` (instalador guiado), `bootstrap.sh` (one-liner `curl | bash`), `Makefile` (atalhos).

## 3. Estrutura do projeto

- `whatsapp-bridge/` — bridge Go: conecta no WhatsApp, guarda histórico em SQLite (`store/`), expõe `/api/send`, `/api/download`, `/api/status`.
- `whatsapp-mcp-server/` — servidor MCP Python: as ferramentas que o Claude chama (`whatsapp.py` tem a lógica, `main.py` registra as tools do FastMCP).
- `gnome-indicator/` — extensão GNOME Shell: liga/desliga o bridge via `systemctl --user`, consulta `/api/status` pra saber se está de fato conectado (não só "processo rodando"), abre logs, notifica erro.
- `install.sh` / `bootstrap.sh` / `Makefile` — instalação e atalhos do dia a dia (`make status`, `make logs`, `make restart`, `make uninstall`).

### Como os três componentes se relacionam

```
Claude ──MCP(stdio)──▶ whatsapp-mcp-server ──HTTP :9090──▶ whatsapp-bridge ──▶ WhatsApp
                                                                    ▲
                                        gnome-indicator ── systemctl + /api/status
```

- O **servidor MCP** só fala com o **bridge** (HTTP + leitura direta do SQLite) — nunca com o indicador.
- O **indicador** só fala com o **systemd** (liga/desliga o processo) e com o **bridge** (`/api/status`, pra saber o estado real de conexão) — nunca com o servidor MCP.
- O **bridge** é o único componente com estado persistente (sessão do WhatsApp + histórico); os outros dois são "burros" em relação a isso.
- Porta do bridge é configurável via `WHATSAPP_BRIDGE_PORT` (padrão `9090`) — lida tanto pelo Go quanto pelo Python, pra não dessincronizar de novo (já aconteceu uma vez).

## 4. Documentação

Toda documentação fica no vault: `WppMcpSecondBrain/`
Logs de sessão: `WppMcpSecondBrain/devtrack/`
Formato dos logs: `YYYY-MM-DD - Título.md`

## 5. Regras de desenvolvimento

- **Nunca commitar ou dar push sem permissão explícita.**
- **Nunca reescrever histórico do git (rebase/amend/force-push) sem confirmar antes** — já fizemos squash uma vez a pedido explícito, mas isso não deve virar hábito automático.
- **Nunca expor a API REST do bridge além de `127.0.0.1`** — ela não tem autenticação, então isso é uma trava de segurança, não só de convenção.
- **Nunca commitar com "Co-Authored-By: Claude"** — os commits deste repo são só do usuário.

## 6. Fluxo de trabalho

1. Ler memória e devtrack mais recente
2. Entender o contexto antes de implementar
3. Implementar
4. Rodar `go build` (bridge) / `python3 -m py_compile` (mcp-server) / `node --check` (indicador) no que for tocado
5. Atualizar devtrack ao final da sessão
