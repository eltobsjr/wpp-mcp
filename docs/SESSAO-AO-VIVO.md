# Sessão ao vivo (conversa conduzida no chat do OpenBot)

Diferente do [modo conversacional](MODO-CONVERSACIONAL.md) (vigia + rotina, em segundo plano), aqui o agente do chat
acompanha UMA conversa em tempo real, mostra cada mensagem e deixa o dono escolher, a qualquer momento:

| Modo | O que acontece |
|---|---|
| ✋ `approve` | cada resposta passa por um cartão: Enviar / Editar / Descartar |
| 🤖 `auto` | conversa sozinho, com travas (ver abaixo) |
| ⏸ `paused` | só acompanha, não responde |

O dono também define o **assunto** (`context`), que vira o fio da conversa e fica no perfil do contato.

## Estados mostrados no painel
⏳ aguardando resposta dele · 👀 lendo · ✍️ redigindo · 🟡 aguardando sua aprovação · 📤 enviando · 🔴 precisa de você · ⏸ pausado · ⏹ encerrado

## Comandos (`cd whatsapp-mcp-server && uv run python live.py ...`)
`start <contato> [--mode approve|auto|paused] [--context "assunto"]`, `panel [--json]`, `wait [--timeout 300] [--quiet 8]`,
`send "<texto>" --how approved|auto`, `mode <modo>`, `context "<assunto>"`, `state <nome> ["nota"]`, `resume`, `stop`.

## Travas do modo auto (valem juntas)
- máximo de respostas automáticas por hora (padrão 8) e de respostas seguidas sem o dono participar (padrão 12; depois disso pede `resume`);
- mensagem longa (> 400 caracteres) ou com assunto sensível (pix, senha, CPF, cartão, valores em reais, empréstimo...) exige o cartão de aprovação;
- `start` recusa se o modo conversacional em segundo plano estiver ligado para o mesmo contato (responderia em dobro).

## Por que a sessão guarda o que ela envia
O bridge não grava no banco as mensagens enviadas pela API (só as digitadas no celular). A sessão registra o que envia
(`sent` em `store/live_session.json`) para o painel e a visão ao vivo mostrarem os dois lados. As mensagens que o dono digita
no celular aparecem como "Você (celular)" e zeram a sequência automática.

## Limites conhecidos
- A sessão só anda enquanto o agente do chat estiver ativo e esperando (cada mensagem nova acorda uma rodada). Para
  funcionar sem o chat aberto, use o modo conversacional.
- "Tempo real" = a cada rajada de mensagens (espera 8 s de silêncio), não palavra por palavra.
- Melhoria futura: fazer o bridge gravar as mensagens enviadas pela API no banco (mudança em Go + reinício do bridge).
