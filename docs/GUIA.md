# Guia do WhatsApp (bridge + wpp + skill)

Atualizado em 2026-11-13. Este é o guia único; os documentos `MODO-CONVERSACIONAL.md` e `SESSAO-AO-VIVO.md` detalham dois modos.

## 1. O que é cada peça
| Peça | O que é | Onde |
|---|---|---|
| **Bridge** (Go) | mantém a sessão do WhatsApp, grava o histórico num SQLite e expõe uma API só em `127.0.0.1:9090` | `whatsapp-bridge/`, serviço `whatsapp-bridge.service` |
| **Núcleo** (Python) | regras: contatos, perfis, modos, leitura, travas | `whatsapp-mcp-server/*.py` |
| **`wpp`** | o comando de terminal: a interface padrão de agentes e do dono | `bin/wpp` (link em `~/tools/scripts/wpp`) |
| **Skill** | texto de instrução ("Envio de mensagens WhatsApp"): diz ao agente quando pedir aprovação e quais comandos usar | `~/OpenBot/Shared/instrucoes/skills/whatsapp/SKILL.md` |
| **Vigia** | serviço que percebe mensagem nova e acorda a rotina do OpenBot; nunca envia | `wpp-conversation.service` (`conversation.py watch`) |
| **Rotina do OpenBot** | a tarefa acordada pelo vigia (o horário dela é só requisito do app) | agente WhatsApp Conversacional |
| **Indicador GNOME** | interruptor na barra | `gnome-indicator/` |
| **MCP** | **arquivado** em 2026-11-13 | `_arquivo/mcp/` (README ensina a religar) |

A skill é o manual; o `wpp` é a ferramenta. O MCP saiu porque o `wpp` tem trava de envio em código, ocupa ≈ 360 tokens contra ≈ 4.400 do MCP, roda em qualquer agente com terminal e tem testes próprios.

## 2. Instalar e conferir
```
ln -sfn "$PWD/bin/wpp" ~/.local/bin/wpp     # o install.sh faz isto
wpp doctor                                   # o que está de pé e o que falta
```
Saída: `OK`, `AVISO` (estado, não defeito, como "nenhum contato ligado") ou `FALHA`. Código de saída 1 só com falha.

## 3. Comandos
Leitura em texto curto; ação em uma linha JSON (`success`, `message` ou `blocked`). Saída: `0` ok, `1` recusado/falhou, `2` uso errado ou destinatário ambíguo.

| Para | Comando |
|---|---|
| conversa com os dois lados, em ordem, com segundos | `wpp thread "<chat>" -n 12` |
| listar / buscar | `wpp chats [busca]` · `wpp msgs [chat] -q termo` · `wpp new --cursor N --chat X --mine` |
| achar contato | `wpp find "<texto>"` |
| dados de um chat · última interação · mensagem com vizinhas | `wpp chat <jid ou tel>` · `wpp last <chat>` · `wpp context <id>` |
| log em texto (recupera mensagem apagada) | `wpp log -q termo` |
| baixar mídia | `wpp media <chat> <id>` |
| perfil do contato (aceita `@lid`) | `wpp profile <chat>` |
| enviar (só com OK do dono) | `wpp send <para> "texto"` · `wpp send-file <para> <caminho>` · `wpp audio <para> <caminho>` |
| responder sozinho, com travas | `wpp reply <chat> "texto"` (`--check` só simula) |
| agenda (começa vazia; o agente pergunta nome/número de quem não está registrado e registra com `save`) | `wpp agenda [q]` · `wpp save <nome> <tel>` · `wpp forget <nome> --yes` · `wpp import-vcf <arq>` · `wpp dedupe [--apply]` (junta contatos com o mesmo número; simula por padrão e guarda cópia `contacts.json.antes-do-dedupe`) |
| perfil de um contato | `wpp contact "<nome>" --mode off|draft|auto --context "assunto" --tone … --avoid … --language … --signature … [--yes]` |
| chave geral | `wpp auto status|on|off|config` (`config`: `--debounce`, `--max-per-hour`, `--agent-id`, `--routine-id`) |
| diagnóstico | `wpp doctor` · `wpp status` · `wpp --version` |

`send-file` e `audio` só aceitam arquivos da sua home (não ocultos) ou do `/tmp`, e recusam os arquivos do próprio bridge.

## 4. Modos de um contato (`reply_mode`)
- `off` (padrão): ignorado.
- `draft`: o agente lê, escreve a resposta e **mostra a você**, sem enviar.
- `auto`: o agente responde sozinho, só depois do checklist da skill **e** das travas do `wpp reply`.

`context` é o assunto combinado com você para aquela conversa; o agente o segue e, se a mensagem sair do assunto, relata a você.

## 5. Travas do envio automático (`wpp reply`)
Só envia se tudo valer: modo conversacional ligado; chat 1:1 (nunca grupo, canal ou status); contato da agenda com `reply_mode=auto`; até 400 caracteres; sem assunto sensível (pix, senha, CPF, cartão, valores…); sem link; sem número longo; no máximo 8 por hora por conversa. Qualquer falha devolve `blocked` e o agente relata como rascunho. O vigia tem a própria trava: 6 acionamentos por conversa por hora e 20 s de silêncio antes de acordar.

## 6. Fluxo do modo conversacional
mensagem chega → vigia espera 20 s → acorda a rotina → agente lê a skill → `wpp profile` (modo real) → `wpp thread` (conversa) → redige → em `draft` relata; em `auto` checklist + `wpp reply`.
Uso: `wpp contact "Ana" --mode draft --context "…"`, depois `wpp auto on`. Para parar: `wpp auto off`.
O bridge **não grava no banco** o que é enviado pela API; por isso `wpp send/reply` registram o envio em `store/sent_log.json` e o `wpp thread` os mostra.

## 7. Quem usa o quê
Claude Code: skill (link em `~/.claude/skills/whatsapp`) + `wpp`. OpenBot: skill nos agentes WhatsApp Orchestrator, WhatsApp Conversacional e DSpace Contatos + `wpp`. O servidor MCP do WhatsApp está desligado nos dois. O OpenBot também tem um WhatsApp nativo à parte (agente Orchestrator), independente deste bridge.

## 8. Paridade com o MCP arquivado
Cada ferramenta do MCP tem equivalente no `wpp` (teste `test_toda_ferramenta_do_mcp_tem_equivalente_no_wpp`): `search_contacts→find`, `list_saved_contacts→agenda`, `save_contact→save`, `remove_saved_contact→forget`, `import_contacts_vcf→import-vcf`, `set_contact_profile→contact`, `get_contact_profile→profile`, `prepare_message→profile + thread`, `conversation_mode→auto`, `list_messages→msgs`, `read_new_messages→new`, `list_chats/get_contact_chats→chats`, `get_chat/get_direct_chat_by_contact→chat`, `get_last_interaction→last`, `get_message_context→context`, `read_message_log→log`, `send_message→send`, `send_file→send-file`, `send_audio_message→audio`, `download_media→media`.

## 9. Problemas conhecidos e como resolver
- **Nada é respondido:** o OpenBot precisa estar aberto (`wpp doctor` mostra). Ele já fechou sozinho uma vez.
- **Mídia não baixa (403):** corrigido em 2026-11-13 (o bridge apagava a query do endereço). Se voltar, rode `go test` em `whatsapp-bridge/`.
- **Contato `@lid` "não achado":** use `wpp profile <jid>`; o `profile_of` resolve por telefone.
- **Aprovação pendente no OpenBot:** ações sensíveis feitas por agente (ex.: desligar um MCP) esperam o seu aceite no app. Não é travamento.
- **Religar o MCP:** `_arquivo/mcp/README.md`. Backup da configuração antiga: `~/backups/wpp-padronizacao-2026-11-12/`.

## 10. Desenvolvimento
`make test` (pytest, 106 testes), `make lint` (ruff), `go test ./...` no bridge. Antes de mexer no `wpp`: ler `wpp_cli.py` e `tests/test_wpp_cli.py`. Mudança na skill: editar a fonte, reaplicar pelo Engenheiro do OpenBot e **comparar o texto inteiro** (agentes e pacote instalado). Nunca commitar sem permissão; commits do dono sem `Co-Authored-By`.

## Requisito do agente no OpenBot

O agente que executa o modo conversacional (WhatsApp Conversacional) precisa de **Permissões = Acesso completo** (Editar agente → Permissões). No modo "Espaço de trabalho" o sandbox bloqueia a porta 9090 do bridge, o `systemctl` e a escrita no `store`, e o `wpp doctor` do agente falha. A segurança de envio não depende do sandbox: o `wpp reply` recusa em código tudo que não seja contato 1:1 em `reply_mode=auto`.
