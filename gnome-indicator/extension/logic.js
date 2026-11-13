// Lógica pura do indicador (sem gi://): testável com `node --test` (tests/logic.test.mjs).
// Tudo que decide "o que mostrar" a partir de fatos fica aqui; a interface só desenha.

export const MODES = ['off', 'draft', 'auto'];
export const MODE_LABEL = {off: 'off', draft: 'rasc.', auto: 'auto'};
export const MODE_NAME = {off: 'desligado', draft: 'rascunho', auto: 'automático'};

// Estados do ícone/cabeçalho. Cor de estado nunca usa o acento (DESIGN.md).
export const STATES = {
    off: {cls: 'wab-st-off', pill: 'DESLIGADO', pillCls: 'wab-pill-off', label: 'Desligado'},
    pending: {cls: 'wab-st-warn', pill: 'AGUARDE', pillCls: 'wab-pill-warn', label: 'Aguarde…'},
    starting: {cls: 'wab-st-warn', pill: 'INICIANDO', pillCls: 'wab-pill-warn', label: 'Iniciando…'},
    disconnected: {cls: 'wab-st-warn', pill: 'SEM SESSÃO', pillCls: 'wab-pill-warn', label: 'Rodando, mas sem sessão do WhatsApp'},
    on: {cls: 'wab-st-on', pill: 'LIGADO', pillCls: 'wab-pill-ok', label: 'Conectado'},
    convOn: {cls: 'wab-st-conv', pill: 'ATIVO', pillCls: 'wab-pill-ok', label: 'Conectado · respondendo contatos'},
    problem: {cls: 'wab-st-err', pill: 'ATENÇÃO', pillCls: 'wab-pill-err', label: 'Conectado, mas o modo conversacional tem falha'},
    error: {cls: 'wab-st-err', pill: 'ERRO', pillCls: 'wab-pill-err', label: 'Não foi possível checar o serviço'},
};

/**
 * @param {{service:string, health:?{connected:boolean,logged_in:boolean}, conversation:?{enabled:boolean},
 *          doctor:?Array<{status:string,label:string}>, pending:boolean}} f
 */
export function deriveState(f) {
    if (f.pending)
        return 'pending';
    if (f.service === 'inactive' || f.service === 'failed')
        return 'off';
    if (f.service !== 'active' && f.service !== 'activating')
        return 'error';
    if (f.service === 'activating' || !f.health)
        return 'starting';
    if (!(f.health.connected && f.health.logged_in))
        return 'disconnected';
    if (f.conversation?.enabled) {
        const falha = (f.doctor ?? []).some(d => d.status === 'falha');
        return falha ? 'problem' : 'convOn';
    }
    return 'on';
}

/** Interpreta a saída de `wpp doctor`: linhas "OK|AVISO|FALHA <rótulo> (<detalhe>)". */
export function parseDoctor(text) {
    const out = [];
    for (const line of String(text ?? '').split('\n')) {
        const m = line.match(/^(OK|AVISO|FALHA)\s+(.*?)\s*(?:\((.*)\))?\s*$/);
        if (m)
            out.push({status: m[1].toLowerCase(), label: m[2], detail: m[3] ?? ''});
    }
    return out;
}

/** Os três pontos do cabeçalho: vigia, OpenBot, rotina (cada um 'ok' | 'aviso' | 'falha' | 'desconhecido'). */
export function doctorChips(items) {
    // casamento preciso: "rotina do OpenBot configurada…" também contém "OpenBot", mas é a rotina
    const find = re => items.find(i => re.test(i.label.toLowerCase()))?.status ?? 'desconhecido';
    return {vigia: find(/^vigia/), openbot: find(/openbot aberto/), rotina: find(/^rotina/)};
}

export function doctorSummary(items) {
    if (!items.length)
        return '';
    const falhas = items.filter(i => i.status === 'falha').length;
    const avisos = items.filter(i => i.status === 'aviso').length;
    if (falhas)
        return `${falhas} problema(s): ${items.filter(i => i.status === 'falha').map(i => i.label).join('; ')}`;
    return avisos ? `Tudo certo (${avisos} aviso(s))` : 'Tudo certo';
}

// ---- contatos ----

export function fold(s) {
    return String(s ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

export function initial(name) {
    const m = String(name ?? '').match(/[\p{L}\p{N}]/u);
    return m ? m[0].toUpperCase() : '#';
}

export function formatPhone(phone) {
    const d = String(phone ?? '').replace(/\D/g, '');
    if (d.startsWith('55') && d.length === 13)
        return `+55 ${d.slice(2, 4)} ${d.slice(4, 9)}-${d.slice(9)}`;
    if (d.startsWith('55') && d.length === 12)
        return `+55 ${d.slice(2, 4)} ${d.slice(4, 8)}-${d.slice(8)}`;
    return d ? `+${d}` : '';
}

export function servedContacts(list) {
    return list.filter(c => c.reply_mode === 'draft' || c.reply_mode === 'auto')
        .sort((a, b) => fold(a.name).localeCompare(fold(b.name)));
}

/** Sem busca: só os atendidos. Com busca (≥ 2 letras): qualquer contato, atendidos primeiro. */
export function visibleContacts(list, query, limit = 8) {
    const q = fold(query).trim();
    if (q.length < 2)
        return servedContacts(list).slice(0, limit);
    const hit = list.filter(c => fold(c.name).includes(q) || String(c.phone).includes(q.replace(/\D/g, '') || '\u0000'));
    hit.sort((a, b) => (MODES.indexOf(b.reply_mode) - MODES.indexOf(a.reply_mode)) || fold(a.name).localeCompare(fold(b.name)));
    return hit.slice(0, limit);
}

/** Argumentos do `wpp` para trocar o modo. Automático só vai com --yes (confirmado na interface). */
export function modeArgs(phone, mode, confirmed) {
    const args = ['contact', String(phone), '--mode', mode];
    if (mode === 'auto') {
        if (!confirmed)
            throw new Error('modo auto exige confirmação');
        args.push('--yes');
    }
    return args;
}

export function contextArgs(phone, text) {
    return ['contact', String(phone), '--context', String(text ?? '')];
}

export function subtitle(state, served, tr = x => x) {
    const base = tr(STATES[state]?.label ?? '');
    if (state === 'on' || state === 'convOn' || state === 'problem')
        return `${base} · ${served} contato${served === 1 ? '' : 's'} atendido${served === 1 ? '' : 's'}`;
    return base;
}

export function validPhone(raw) {
    const d = String(raw ?? '').replace(/\D/g, '');
    return d.length >= 10 && d.length <= 15;
}
