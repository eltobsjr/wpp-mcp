import test from 'node:test';
import assert from 'node:assert/strict';

import {
    deriveState, parseDoctor, doctorChips, doctorSummary, initial, formatPhone,
    servedContacts, visibleContacts, modeArgs, contextArgs, subtitle, validPhone, STATES,
} from '../extension/logic.js';

const ok = {connected: true, logged_in: true};

test('deriveState: cada combinação de fatos vira um estado', () => {
    assert.equal(deriveState({service: 'inactive', pending: false}), 'off');
    assert.equal(deriveState({service: 'failed', pending: false}), 'off');
    assert.equal(deriveState({service: 'active', health: null, pending: false}), 'starting');
    assert.equal(deriveState({service: 'activating', health: ok, pending: false}), 'starting');
    assert.equal(deriveState({service: 'active', health: {connected: true, logged_in: false}, pending: false}), 'disconnected');
    assert.equal(deriveState({service: 'active', health: ok, conversation: {enabled: false}, pending: false}), 'on');
    assert.equal(deriveState({service: 'active', health: ok, conversation: {enabled: true}, doctor: [], pending: false}), 'convOn');
    assert.equal(deriveState({service: 'active', health: ok, conversation: {enabled: true}, doctor: [{status: 'falha', label: 'x'}], pending: false}), 'problem');
    assert.equal(deriveState({service: 'weird', pending: false}), 'error');
    assert.equal(deriveState({service: 'active', health: ok, pending: true}), 'pending');
});

test('aviso do doctor NÃO vira estado de problema (só falha)', () => {
    const d = [{status: 'aviso', label: 'contato em draft/auto'}];
    assert.equal(deriveState({service: 'active', health: ok, conversation: {enabled: true}, doctor: d, pending: false}), 'convOn');
});

test('todo estado tem classe, pílula e rótulo', () => {
    for (const [k, v] of Object.entries(STATES)) {
        assert.ok(v.cls && v.pill && v.pillCls && v.label, k);
    }
});

const DOC = `OK    bridge do WhatsApp responde (porta 9090)
OK    vigia (wpp-conversation) rodando (ativo)
OK    modo conversacional ligado (ligado)
OK    rotina do OpenBot configurada no vigia (configurada)
FALHA OpenBot aberto e alcançável (abra o OpenBot (sem ele nada é respondido))
AVISO contato em draft/auto (nenhum ainda: wpp contact "<nome>" --mode draft)

1 problema(s). Corrija os marcados FALHA.`;

test('parseDoctor lê OK, AVISO e FALHA, com detalhe entre parênteses aninhados', () => {
    const r = parseDoctor(DOC);
    assert.equal(r.length, 6);
    assert.deepEqual(r.map(x => x.status), ['ok', 'ok', 'ok', 'ok', 'falha', 'aviso']);
    assert.equal(r[4].label, 'OpenBot aberto e alcançável');
    assert.match(r[4].detail, /abra o OpenBot/);
});

test('doctorChips e resumo', () => {
    const r = parseDoctor(DOC);
    assert.deepEqual(doctorChips(r), {vigia: 'ok', openbot: 'falha', rotina: 'ok'});
    assert.match(doctorSummary(r), /1 problema\(s\): OpenBot/);
    assert.equal(doctorSummary(parseDoctor('OK a (b)')), 'Tudo certo');
    assert.equal(doctorSummary([]), '');
    assert.deepEqual(doctorChips([]), {vigia: 'desconhecido', openbot: 'desconhecido', rotina: 'desconhecido'});
});

test('initial pula emoji e símbolos', () => {
    assert.equal(initial('Mãe ❤️'), 'M');
    assert.equal(initial('[]Yan[]'), 'Y');
    assert.equal(initial('❤️❤️'), '#');
    assert.equal(initial('ana'), 'A');
});

test('formatPhone', () => {
    assert.equal(formatPhone('558999990003'), '+55 89 9999-0003');
    assert.equal(formatPhone('5589999990004'), '+55 89 99999-0004');
    assert.equal(formatPhone('11948606787'), '+11948606787');
    assert.equal(formatPhone(''), '');
});

const L = [
    {name: 'Zé', phone: '5511000000001', reply_mode: 'off'},
    {name: 'Ana', phone: '5511000000002', reply_mode: 'auto'},
    {name: 'Mãe ❤️', phone: '558999990003', reply_mode: 'draft'},
    {name: 'Breno', phone: '5511000000003', reply_mode: 'off'},
    {name: 'Breno', phone: '5511000000004', reply_mode: 'off'},
];

test('servedContacts: só draft/auto, em ordem alfabética sem acento', () => {
    assert.deepEqual(servedContacts(L).map(c => c.name), ['Ana', 'Mãe ❤️']);
});

test('visibleContacts: sem busca mostra só atendidos; busca acha qualquer um, atendidos primeiro', () => {
    assert.deepEqual(visibleContacts(L, '', 8).map(c => c.name), ['Ana', 'Mãe ❤️']);
    assert.deepEqual(visibleContacts(L, 'a', 8).map(c => c.name), ['Ana', 'Mãe ❤️']); // 1 letra = sem busca
    assert.deepEqual(visibleContacts(L, 'bre', 8).map(c => c.phone), ['5511000000003', '5511000000004']);
    assert.deepEqual(visibleContacts(L, 'mae', 8).map(c => c.name), ['Mãe ❤️']);   // sem acento
    assert.deepEqual(visibleContacts(L, '5589999', 8).map(c => c.name), ['Mãe ❤️']);  // por telefone
    assert.equal(visibleContacts(L, 'zz', 8).length, 0);
    assert.equal(visibleContacts(L, 'br', 1).length, 1);
});

test('modeArgs: auto exige confirmação e leva --yes; identifica por telefone (nomes repetem)', () => {
    assert.deepEqual(modeArgs('5511000000003', 'draft', false), ['contact', '5511000000003', '--mode', 'draft']);
    assert.deepEqual(modeArgs('5511000000003', 'off', false), ['contact', '5511000000003', '--mode', 'off']);
    assert.throws(() => modeArgs('5511000000003', 'auto', false), /confirmação/);
    assert.deepEqual(modeArgs('5511000000003', 'auto', true), ['contact', '5511000000003', '--mode', 'auto', '--yes']);
});

test('contextArgs aceita texto vazio (apaga o contexto)', () => {
    assert.deepEqual(contextArgs('1', ''), ['contact', '1', '--context', '']);
    assert.deepEqual(contextArgs('1', 'churrasco'), ['contact', '1', '--context', 'churrasco']);
});

test('subtitle e validPhone', () => {
    assert.equal(subtitle('convOn', 1), 'Conectado · respondendo contatos · 1 contato atendido');
    assert.equal(subtitle('on', 2), 'Conectado · 2 contatos atendidos');
    assert.equal(subtitle('off', 0), 'Desligado');
    assert.ok(validPhone('(89) 99999-0003'));
    assert.ok(!validPhone('123'));
});
