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
   `set_contact_profile(contact="Ana", tone="informal e curto", address_as="Aninha", reply_mode="draft")`
2. Crie no OpenBot uma rotina **pausada** (ativa = falso) com a instrução abaixo, no agente que você escolher, e anote `agent_id` e `routine_id`.
3. `conversation_mode(agent_id="...", routine_id="...")` e depois `conversation_mode(enabled=true)`
   (ou o interruptor "Modo conversacional" no indicador GNOME).
4. `make conv-install && make conv-start` sobe o vigia.

Desligar: `conversation_mode(enabled=false)`, o interruptor do indicador ou `make conv-stop`.

## Instrução sugerida para a rotina

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
