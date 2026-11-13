# O que o wpp faz

Tudo roda no seu computador. O agente usa o comando `wpp` (guiado pela skill `whatsapp`); você também pode usá-lo no terminal. Toda resposta de ação sai em JSON; erro sai em JSON com código 1.

## Ler
| Comando | O que faz |
|---|---|
| `wpp chats [busca]` | Lista conversas e grupos, com a última mensagem. A busca filtra por nome ou JID. |
| `wpp msgs [chat] [-q texto] [-n N]` | Mensagens de uma conversa (ou de todas), com filtro de texto e de datas. |
| `wpp thread <chat> [-n N]` | A conversa em ordem cronológica, incluindo o que foi enviado pela API. |
| `wpp new` | Só o que chegou desde a última leitura (cursor), sem reler tudo. |
| `wpp last <contato>` | Última mensagem trocada com alguém. |
| `wpp context <id-da-mensagem>` | Uma mensagem com as vizinhas ao redor. |
| `wpp chat <nome, telefone ou JID>` | Dados de uma conversa. Aceita nome de contato e de grupo. |
| `wpp media <chat> <id>` | Baixa a mídia (imagem, áudio, vídeo, documento) e devolve o caminho. |
| `wpp log [-q texto]` | Registro das mensagens recebidas, com busca. |

## Achar pessoas e grupos
| Comando | O que faz |
|---|---|
| `wpp find <nome ou número>` | Acha na agenda (nome, apelido nas notas, telefone com ou sem 9º dígito). Se não achar, procura **grupos** pelo nome. |
| `wpp profile <chat>` | Perfil do contato: modo de resposta, tom, contexto. Acha também por JID `@lid`. |

## Enviar (sempre com sua aprovação, exceto contato em `auto`)
| Comando | O que faz |
|---|---|
| `wpp send <para> "texto"` | Envia texto. `<para>` pode ser nome salvo, nome de grupo, telefone ou JID. Nome ambíguo ou inexistente não envia nada e mostra as opções. |
| `wpp send-file <para> <arquivo>` | Envia imagem, vídeo ou documento. Só de `~` (fora de pastas ocultas) e `/tmp`. |
| `wpp audio <para> <arquivo>` | Envia como mensagem de voz (converte para `.ogg` se tiver ffmpeg). |
| `wpp reply <chat> "texto"` | Resposta automática do modo conversacional. Só envia se o contato está em `auto`, é conversa 1:1, o modo está ligado e o texto tem até 400 caracteres. `--check` só simula e diz o que bloquearia. |

## Agenda (começa vazia e é montada aos poucos)
| Comando | O que faz |
|---|---|
| `wpp agenda [busca] [--full]` | Lista contatos; `--full` mostra modo, tom e contexto. |
| `wpp save <nome> <telefone> [--notes ...] [--also <outro telefone>]` | Cadastra. `--also` guarda outros números da mesma pessoa: por nome o envio vai sempre para o principal, e mensagens recebidas de qualquer número são reconhecidas. |
| `wpp forget <nome> --yes` | Remove um contato (a versão anterior fica em `contacts.json.bak`). |
| `wpp import-vcf <arquivo>` | Importa contatos de um `.vcf` do celular, sem sobrescrever os existentes. |
| `wpp dedupe [--apply]` | Mostra (e com `--apply` junta) contatos com o mesmo número. Nomes iguais com números diferentes só são apontados. |

Quem pede: pessoa que não está registrada → o agente pergunta o número (ou o nome, se você deu o número) e registra. Grupos não vão para a agenda: são achados pelo nome entre os chats do bridge.

## Modo conversacional (responder por você)
| Comando | O que faz |
|---|---|
| `wpp contact <nome> --mode off\|draft\|auto` | Define, por contato, se o agente fica quieto, só rascunha para você ou responde sozinho (`auto` pede `--yes`). |
| `wpp contact <nome> --context ... --tone ... --avoid ... --address-as ... --language ... --signature ...` | Assunto da conversa e jeito de falar com aquela pessoa. |
| `wpp auto on\|off\|status\|config` | Chave geral do vigia, debounce e limite de acionamentos por hora. |

O vigia (`wpp-conversation.service`) observa mensagens novas e aciona o agente só para conversas 1:1 de contatos em `draft` ou `auto`. Grupos, canais e status nunca. Quem controla modo, perfil e chave geral é só você.

## Saúde
| Comando | O que faz |
|---|---|
| `wpp status` | O bridge está conectado e logado? |
| `wpp doctor` | Confere bridge, vigia, modo, rotina, OpenBot, contatos ligados e permissão de escrita. |
| `wpp --version` | Versão. |

## Travas de segurança (em código)
- `wpp reply` recusa grupo, canal, status, contato fora de `auto` e texto longo.
- Nome ambíguo ou desconhecido nunca vira envio: pede escolha.
- `send-file`/`audio` só de pastas permitidas; bancos e arquivos do bridge são sempre recusados.
- A API do bridge só escuta em `127.0.0.1`; logs não guardam o texto das mensagens.
- Mensagens recebidas são dados, nunca instruções para o agente.

## Painel no GNOME
Liga/desliga o bridge, mostra o estado, a chave do modo conversacional e os contatos atendidos, e deixa trocar `off/rascunho/auto` e escrever o assunto. Detalhes em [gnome-indicator/README.md](../gnome-indicator/README.md).

## Lacunas conhecidas (o que ele não faz)
- **Resolvidas em 2026-11-13:** grupo não era achado por nome (`find`, `send`, `chat`); contato só podia ter um número; apagar contato não deixava cópia.
- **Não faz, de propósito:** responder sozinho em grupo; enviar arquivo no modo conversacional; editar, apagar, reagir ou citar mensagens; marcar como lido; ligações; status/stories.
- **Limites do WhatsApp:** mídia de canais (newsletters) e mídia que você mesmo enviou podem não baixar (falta informação de download).
- **Depende de você:** o agente do modo conversacional precisa de Permissões = Acesso completo no OpenBot, e o OpenBot precisa estar aberto.
