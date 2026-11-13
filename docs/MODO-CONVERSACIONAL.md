# Modo conversacional

Responde conversas de forma contínua, com liga/desliga. **Vem desligado e não responde ninguém até você configurar.**

## Como funciona

```
mensagem chega -> bridge grava em messages.db
   -> vigia (wpp-conversation.service) detecta, espera a conversa assentar (debounce)
   -> acorda uma rotina do OpenBot
   -> o agente lê (read_new_messages), redige no tom do contato (prepare_message) e
        reply_mode "draft": só relata o rascunho a você
        reply_mode "auto":  envia com send_message
```

O vigia **não envia nada** e **não escreve** no banco do bridge: só acorda o agente.

## Travas

- Desligado por padrão. Ligar guarda o ponto atual: o histórico antigo nunca é respondido.
- Só conversas 1:1 de contatos da agenda com `reply_mode` `draft` ou `auto`. Grupos, canais e status são ignorados.
- Mensagens suas não disparam nada; se você respondeu antes do debounce, a espera é cancelada.
- Limite de acionamentos por conversa por hora (`max_triggers_per_hour`, padrão 6).
- O token do OpenBot é lido dos arquivos de automação e nunca vai para log.

## Configurar (uma vez)

1. Defina como escrever para cada contato e quem pode ser respondido (ferramentas do MCP):
   `wpp contact "Ana" --tone "informal e curto" --address-as "Aninha" --mode draft --context "assunto da conversa"`
2. Crie no OpenBot uma rotina **pausada** (ativa = falso) com a instrução abaixo, no agente que você escolher, e anote `agent_id` e `routine_id`.
3. `wpp auto config --agent-id ... --routine-id ...` e depois `wpp auto on`
   (ou o interruptor "Modo conversacional" no indicador GNOME).
4. `make conv-install && make conv-start` sobe o vigia.

Desligar: `wpp auto off`, o interruptor do indicador ou `make conv-stop`.

## Instrução da rotina (atualizado 2026-11-12)

As regras deixaram de ficar coladas na rotina: moram na skill **"Envio de mensagens WhatsApp"**, seção "Modo conversacional" (fonte: `~/OpenBot/Shared/instrucoes/skills/whatsapp/SKILL.md`), habilitada no agente WhatsApp Conversacional. A rotina só manda seguir a skill e, se ela não carregar, rascunhar sem enviar. Novidades: o `reply_mode` é confirmado com `get_contact_profile` (não se confia no payload) e o modo auto passa por uma checagem de 7 itens antes de enviar.

O texto antigo abaixo fica como referência histórica.

## Instrução sugerida (versão antiga) para a rotina

> Você foi acionado pelo modo conversacional do WhatsApp do dono. O payload traz o contato, o chat_jid, o `reply_mode` e o cursor.
> 1. Leia as mensagens novas com `read_new_messages(cursor=<cursor do payload>, chat_jid=<chat_jid>, include_from_me=true)`.
> 2. Chame `prepare_message(<chat_jid>, <o que responder>)` e redija seguindo o perfil do contato (tom, tratamento, idioma, assinatura) e respeitando "avoid".
> 3. Se `reply_mode` for `draft`: NÃO envie. Relate ao dono quem escreveu, o que disse e o seu rascunho.
> 4. Se for `auto`: envie UMA mensagem curta com `send_message`, mas só se a resposta for simples e você tiver certeza. Para dinheiro, dados pessoais, compromissos, decisões ou qualquer pergunta que você não saiba responder, NÃO envie: relate ao dono como rascunho.
> 5. Nunca invente fatos. Nunca escreva em grupos. Nunca responda a mais de uma conversa por acionamento.
> 6. Esta instrução autoriza o envio apenas para contatos com `reply_mode=auto`.

## Limites conhecidos

- O vigia precisa estar de pé; se parar, nada é acordado (mensagens ficam no histórico e são lidas depois pelo cursor).
- Limite por hora e debounce vivem na memória do vigia: reiniciar zera o contador de limite.
- O interruptor do indicador GNOME foi validado só com `node --check`, não dentro do Shell.

## Envio automático com trava em código (2026-11-12)
No modo `auto` o agente NÃO usa `send_message`: usa `~/tools/scripts/wpp reply <chat_jid> "<texto>"` (`whatsapp-mcp-server/wpp_cli.py`, funções `check_auto_reply` e `auto_reply`). O código só envia se TUDO valer: modo conversacional ligado, chat 1:1, contato da agenda com `reply_mode=auto`, texto até 400 caracteres, sem assunto sensível (mesma lista da sessão ao vivo), sem link nem número longo e no máximo 8 por hora por conversa. Qualquer falha devolve `blocked` e o agente relata como rascunho. Limite conhecido: o `send_message` do MCP continua existindo e não passa por essa trava; a proteção depende de a skill mandar usar o `wpp reply`.
Contexto da conversa: `set_contact_profile(contact, context="assunto combinado")`.
