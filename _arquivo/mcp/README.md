# MCP do WhatsApp — arquivado (desligado em 2026-11-13)

**Por que saiu:** a interface padrão passou a ser a skill "Envio de mensagens WhatsApp" + o comando `wpp`
(`~/tools/scripts/wpp`). Fatos que decidiram: `wpp` tem trava de envio em código (o `send_message` do MCP não tem),
usa ≈ 360 tokens contra ≈ 4.400 dos esquemas do MCP, funciona em qualquer agente com terminal, e tem testes próprios.
Paridade completa: cada uma das 22 ferramentas tem comando equivalente (tabela em `docs/GUIA.md`; o teste
`test_toda_ferramenta_do_mcp_tem_equivalente_no_wpp` falha se o MCP ganhar ferramenta sem equivalente).

**O que continua funcionando sem ele:** o bridge (`whatsapp-bridge.service`), o vigia (`wpp-conversation.service`,
que roda `conversation.py watch`), o indicador do GNOME, a sessão ao vivo e o `wpp`.

## Religar (se precisar)
Claude Code:
```
claude mcp add whatsapp --scope user -- "$(command -v uv)" --directory "$HOME/tools/automacao/wpp-mcp/whatsapp-mcp-server" run python "$HOME/tools/automacao/wpp-mcp/_arquivo/mcp/main.py"
```
OpenBot: religar o servidor `whatsapp` em Configurações > MCP (ou pedir ao Engenheiro `set_mcp_server_enabled`).
Reiniciar a sessão depois. Backup da configuração antiga do Claude Code: `~/backups/wpp-padronizacao-2026-11-12/claude.json`.

## Notas
- Este `main.py` é o do commit original mais a correção do `get_contact_profile` para JID `@lid`.
- Os testes `test_wpp_cli.py` carregam este arquivo para garantir a paridade e a correção acima.
