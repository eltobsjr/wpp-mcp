// WhatsApp Bridge — indicador da família Indicator (DESIGN.md). Opção A: painel com contatos expansíveis.
// Dados pelo comando `wpp` (agenda, contact, auto, doctor). Sem polling com o menu fechado:
// o estado do serviço vem do D-Bus do systemd e a agenda de um monitor de arquivos.
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import St from 'gi://St';

import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';

import {Wpp} from './wpp.js';
import {Bridge, openLogsTerminal} from './bridge.js';
import {
    MODES, MODE_LABEL, MODE_NAME, STATES, deriveState, parseDoctor, doctorChips, doctorSummary,
    initial, formatPhone, servedContacts, visibleContacts, modeArgs, contextArgs, subtitle, validPhone,
} from './logic.js';

const VISIBLE_LIMIT = 8;

function label(text, cls, props = {}) {
    const l = new St.Label({text, style_class: cls, y_align: Clutter.ActorAlign.CENTER, ...props});
    if (cls.includes('wab-ellipsis'))
        l.clutter_text.ellipsize = Pango.EllipsizeMode.END;
    return l;
}

function button(text, cls, onClick) {
    const b = new St.Button({label: text, style_class: `wab-btn ${cls ?? ''}`.trim(), can_focus: true});
    b.connect('clicked', onClick);
    return b;
}

function section(title) {
    const box = new St.BoxLayout({vertical: true, style_class: 'wab-section'});
    const head = new St.BoxLayout({style_class: 'wab-section-head'});
    const t = label(title, 'wab-section-title', {x_expand: true});
    head.add_child(t);
    box.add_child(head);
    return {box, head};
}

const Indicator = GObject.registerClass(
class WhatsAppBridgeIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.0, _('WhatsApp Bridge'), false);
        this._ext = extension;
        this._settings = extension.getSettings();
        this._wpp = new Wpp(this._settings);
        this._bridge = new Bridge();

        this._destroyed = false;
        this._pending = false;
        this._service = 'unknown';
        this._health = null;
        this._conv = null;
        this._doctor = [];
        this._doctorMsg = '';
        this._contacts = [];
        this._query = '';
        this._expanded = null;
        this._confirm = null;
        this._ctxDraft = '';
        this._addOpen = false;
        this._wppMissing = false;
        this._pollId = 0;
        this._burstId = 0;
        this._burstLeft = 0;
        this._notifSource = null;

        const icon = new St.Icon({
            gicon: Gio.FileIcon.new(Gio.File.new_for_path(`${extension.path}/icons/whatsapp-bridge-symbolic.svg`)),
            style_class: 'system-status-icon wab-bar-icon',
        });
        this._bar = new St.Bin({style_class: 'wab-bar wab-st-off', child: icon, y_align: Clutter.ActorAlign.CENTER});
        this.add_child(this._bar);

        this._buildMenu();
        this.menu.connect('open-state-changed', (_m, open) => this._onOpen(open));
        this.connect('destroy', () => this._cleanup());

        this._bridge.watchUnit(() => this._onUnitChanged());
        this._bridge.watchFiles(() => this._refreshData());
        this._refreshService();
    }

    // ------------------------------------------------------------------ construção
    _buildMenu() {
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false, style_class: 'wab-item'});
        this._root = new St.BoxLayout({vertical: true, x_expand: true, style_class: 'wab-root'});
        item.add_child(this._root);
        this.menu.addMenuItem(item);

        // cabeçalho
        const head = new St.BoxLayout({style_class: 'wab-head'});
        this._avatar = new St.Bin({
            style_class: 'wab-avatar wab-st-off', y_align: Clutter.ActorAlign.CENTER,
            child: new St.Icon({
                gicon: Gio.FileIcon.new(Gio.File.new_for_path(`${this._ext.path}/icons/whatsapp-bridge-symbolic.svg`)),
                style_class: 'wab-avatar-icon',
            }),
        });
        const titles = new St.BoxLayout({vertical: true, x_expand: true, style_class: 'wab-titles'});
        titles.add_child(label(_('WhatsApp Bridge'), 'wab-title'));
        this._sub = label('', 'wab-sub');
        this._sub.clutter_text.set_line_wrap(true);
        titles.add_child(this._sub);
        this._pill = label('', 'wab-pill wab-pill-off');
        head.add_child(this._avatar);
        head.add_child(titles);
        head.add_child(this._pill);
        this._root.add_child(head);

        // bridge
        const sb = section(_('Bridge'));
        const rowB = new St.BoxLayout({style_class: 'wab-row'});
        this._dot = new St.Widget({style_class: 'wab-dot wab-dot-off', y_align: Clutter.ActorAlign.CENTER});
        const textB = new St.BoxLayout({vertical: true, x_expand: true, style_class: 'wab-grow'});
        this._bridgeText = label('', 'wab-text');
        this._bridgeHint = label('', 'wab-hint');
        textB.add_child(this._bridgeText);
        textB.add_child(this._bridgeHint);
        this._bridgeBtn = button(_('Ligar'), 'wab-btn-main', () => this._toggleBridge());
        rowB.add_child(this._dot);
        rowB.add_child(textB);
        rowB.add_child(this._bridgeBtn);
        sb.box.add_child(rowB);
        this._root.add_child(sb.box);

        // modo conversacional
        const sc = section(_('Modo conversacional'));
        const rowC = new St.BoxLayout({style_class: 'wab-row'});
        const textC = new St.BoxLayout({vertical: true, x_expand: true, style_class: 'wab-grow'});
        textC.add_child(label(_('Responder contatos'), 'wab-text'));
        textC.add_child(label(_('chave geral'), 'wab-hint'));
        this._switch = new PopupMenu.Switch(false);
        this._switchBtn = new St.Button({
            child: this._switch, can_focus: true, style_class: 'wab-switch-btn', accessible_name: _('Modo conversacional'),
        });
        this._switchBtn.connect('clicked', () => this._toggleConversation());
        rowC.add_child(textC);
        rowC.add_child(this._switchBtn);
        sc.box.add_child(rowC);
        const chips = new St.BoxLayout({style_class: 'wab-chips'});
        this._chips = {};
        for (const [key, text] of [['vigia', _('vigia')], ['openbot', _('OpenBot')], ['rotina', _('rotina')]]) {
            const chip = new St.BoxLayout({style_class: 'wab-chip'});
            const dot = new St.Widget({style_class: 'wab-dot wab-dot-off', y_align: Clutter.ActorAlign.CENTER});
            chip.add_child(dot);
            chip.add_child(label(text, 'wab-chip-text'));
            chips.add_child(chip);
            this._chips[key] = dot;
        }
        sc.box.add_child(chips);
        this._doctorLabel = label('', 'wab-hint wab-doctor');
        this._doctorLabel.clutter_text.set_line_wrap(true);
        this._doctorLabel.visible = false;
        sc.box.add_child(this._doctorLabel);
        this._root.add_child(sc.box);

        // contatos
        const sk = section(_('Contatos'));
        this._addBtn = button(_('+ adicionar'), 'wab-btn-sm', () => this._toggleAdd());
        sk.head.add_child(this._addBtn);
        this._search = new St.Entry({hint_text: _('Buscar contato…'), can_focus: true, style_class: 'wab-entry', x_expand: true});
        this._search.clutter_text.connect('text-changed', () => {
            this._query = this._search.get_text();
            this._renderContacts();
        });
        sk.box.add_child(this._search);

        this._addForm = new St.BoxLayout({style_class: 'wab-form'});
        this._addName = new St.Entry({hint_text: _('Nome'), can_focus: true, style_class: 'wab-entry', x_expand: true});
        this._addPhone = new St.Entry({hint_text: _('Telefone com DDD'), can_focus: true, style_class: 'wab-entry', x_expand: true});
        this._addName.clutter_text.connect('activate', () => this._addPhone.grab_key_focus());
        this._addPhone.clutter_text.connect('activate', () => this._saveNew());
        this._addForm.add_child(this._addName);
        this._addForm.add_child(this._addPhone);
        this._addForm.add_child(button(_('Salvar'), 'wab-btn-main wab-btn-sm', () => this._saveNew()));
        this._addForm.visible = false;
        sk.box.add_child(this._addForm);

        this._list = new St.BoxLayout({vertical: true, style_class: 'wab-list'});
        sk.box.add_child(this._list);
        this._count = label('', 'wab-hint');
        sk.box.add_child(this._count);
        this._root.add_child(sk.box);

        // rodapé
        const foot = new St.BoxLayout({style_class: 'wab-foot'});
        const mk = (text, fn) => {
            const b = button(text, '', fn);
            b.x_expand = true;
            return b;
        };
        foot.add_child(mk(_('Verificar'), () => this._refreshDoctor()));
        foot.add_child(mk(_('Logs'), () => {
            if (!openLogsTerminal())
                this._notifyError(_('Não foi possível abrir os logs'), _('Nenhum terminal compatível foi encontrado.'));
        }));
        foot.add_child(mk(_('Ajustes'), () => {
            this.menu.close();
            this._ext.openPreferences();
        }));
        this._root.add_child(foot);

        this._renderContacts();
        this._applyState();
    }

    // ------------------------------------------------------------------ estado
    _state() {
        return deriveState({
            service: this._service, health: this._health, conversation: this._conv, doctor: this._doctor, pending: this._pending,
        });
    }

    _served() {
        return servedContacts(this._contacts).length;
    }

    _applyState() {
        if (this._destroyed)
            return;
        const st = this._state();
        const s = STATES[st];
        this._bar.style_class = `wab-bar ${s.cls}`;
        this._avatar.style_class = `wab-avatar ${s.cls}`;
        this._pill.text = _(s.pill);
        this._pill.style_class = `wab-pill ${s.pillCls}`;
        this._sub.text = subtitle(st, this._served(), _);

        const running = this._service === 'active' || this._service === 'activating';
        const dotCls = {off: 'off', pending: 'warn', starting: 'warn', disconnected: 'warn', on: 'ok', convOn: 'ok', problem: 'err', error: 'err'}[st];
        this._dot.style_class = `wab-dot wab-dot-${dotCls}`;
        this._bridgeText.text = _(s.label.split(' · ')[0]);
        this._bridgeHint.text = running ? _('serviço ativo') : _('serviço parado');
        this._bridgeBtn.label = this._pending ? _('Aguarde…') : running ? _('Desligar') : _('Ligar');
        this._bridgeBtn.reactive = !this._pending;
        this._switch.state = !!this._conv?.enabled;
        this._switchBtn.reactive = !this._pending;

        for (const [key, dot] of Object.entries(this._chips)) {
            const v = doctorChips(this._doctor)[key];
            dot.style_class = `wab-dot wab-dot-${{ok: 'ok', aviso: 'warn', falha: 'err'}[v] ?? 'off'}`;
        }
        this._doctorLabel.text = this._doctorMsg;
        this._doctorLabel.visible = !!this._doctorMsg;
    }

    // ------------------------------------------------------------------ atualização
    async _refreshService() {
        const svc = await this._bridge.service();
        const health = svc === 'active' ? await this._bridge.health() : null;
        if (this._destroyed)
            return;
        this._service = svc;
        this._health = health;
        this._applyState();
    }

    async _refreshData() {
        const [ag, au] = await Promise.all([this._wpp.json(['agenda', '--full']), this._wpp.json(['auto', 'status'])]);
        if (this._destroyed)
            return;
        this._wppMissing = /não encontrado/.test(ag.stderr);
        if (Array.isArray(ag.data))
            this._contacts = ag.data;
        if (au.data && typeof au.data === 'object')
            this._conv = au.data;
        this._renderContacts();
        this._applyState();
    }

    async _refreshDoctor() {
        this._doctorMsg = _('Verificando…');
        this._applyState();
        const r = await this._wpp.run(['doctor'], 15);
        if (this._destroyed)
            return;
        this._doctor = parseDoctor(r.stdout);
        this._doctorMsg = this._doctor.length ? doctorSummary(this._doctor) : _('Não foi possível rodar o wpp doctor');
        this._applyState();
    }

    _onUnitChanged() {
        this._refreshService();
        this._burst(15); // enquanto o bridge sobe, confirma a sessão a cada 2 s (limitado)
    }

    _burst(times) {
        this._burstLeft = times;
        if (this._burstId)
            return;
        this._burstId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => {
            if (this._destroyed || --this._burstLeft <= 0) {
                this._burstId = 0;
                return GLib.SOURCE_REMOVE;
            }
            this._refreshService();
            const st = this._state();
            if (st === 'on' || st === 'convOn' || st === 'off' || st === 'problem') {
                this._burstId = 0;
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    _onOpen(open) {
        if (this._pollId) {
            GLib.source_remove(this._pollId);
            this._pollId = 0;
        }
        if (!open)
            return;
        this._refreshService();
        this._refreshData();
        this._refreshDoctor();
        // só enquanto o menu está aberto
        const every = Math.max(2, this._settings.get_int('poll-interval'));
        this._pollId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, every, () => {
            if (this._destroyed)
                return GLib.SOURCE_REMOVE;
            this._refreshService();
            return GLib.SOURCE_CONTINUE;
        });
    }

    // ------------------------------------------------------------------ ações
    async _toggleBridge() {
        if (this._pending || this._destroyed)
            return;
        const running = this._service === 'active' || this._service === 'activating';
        this._pending = true;
        this._applyState();
        const r = await this._bridge.action(running ? 'stop' : 'start');
        if (this._destroyed)
            return;
        this._pending = false;
        if (!r.ok) {
            this._notifyError(running ? _('Falha ao desligar o bridge') : _('Falha ao ligar o bridge'), r.stderr);
        }
        await this._refreshService();
        this._burst(15);
    }

    async _toggleConversation() {
        if (this._pending || this._destroyed)
            return;
        const turnOn = !this._conv?.enabled;
        this._pending = true;
        this._applyState();
        const r = await this._wpp.json(['auto', turnOn ? 'on' : 'off']);
        if (this._destroyed)
            return;
        this._pending = false;
        if (!r.ok)
            this._notifyError(_('Não foi possível mudar o modo conversacional'), r.data?.message ?? r.stderr);
        await this._refreshData();
        this._refreshDoctor();
    }

    async _setMode(c, mode, confirmed = false) {
        if (this._pending || this._destroyed || c.reply_mode === mode)
            return;
        if (mode === 'auto' && !confirmed) {
            this._confirm = c.phone; // a interface pede confirmação (o wpp exige --yes)
            this._renderContacts();
            return;
        }
        this._confirm = null;
        this._pending = true;
        const r = await this._wpp.json(modeArgs(c.phone, mode, confirmed));
        if (this._destroyed)
            return;
        this._pending = false;
        if (!r.ok)
            this._notifyError(_('Não foi possível mudar o modo de %s').format(c.name), r.data?.message ?? r.stderr);
        await this._refreshData();
    }

    async _saveContext(c, text) {
        if (this._pending || this._destroyed)
            return;
        this._pending = true;
        const r = await this._wpp.json(contextArgs(c.phone, text));
        if (this._destroyed)
            return;
        this._pending = false;
        if (!r.ok)
            this._notifyError(_('Não foi possível salvar o contexto'), r.data?.message ?? r.stderr);
        this._expanded = null;
        this._ctxDraft = '';
        await this._refreshData();
    }

    _toggleAdd() {
        this._addOpen = !this._addOpen;
        this._addForm.visible = this._addOpen;
        if (this._addOpen)
            this._addName.grab_key_focus();
    }

    async _saveNew() {
        const name = this._addName.get_text().trim();
        const phone = this._addPhone.get_text().trim();
        if (!name || !validPhone(phone)) {
            this._notifyError(_('Dados inválidos'), _('Informe o nome e um telefone com DDD.'));
            return;
        }
        const r = await this._wpp.json(['save', name, phone.replace(/\D/g, '')]);
        if (this._destroyed)
            return;
        if (!r.ok) {
            this._notifyError(_('Não foi possível salvar o contato'), r.data?.message ?? r.stderr);
            return;
        }
        this._addName.set_text('');
        this._addPhone.set_text('');
        this._toggleAdd();
        this._query = name;
        this._search.set_text(name);
        await this._refreshData();
    }

    // ------------------------------------------------------------------ contatos
    _renderContacts() {
        if (this._destroyed)
            return;
        this._list.destroy_all_children();
        if (this._wppMissing) {
            this._list.add_child(label(_('Comando wpp não encontrado. Instale com o install.sh do projeto.'), 'wab-hint'));
            this._count.text = '';
            return;
        }
        const rows = visibleContacts(this._contacts, this._query, VISIBLE_LIMIT);
        for (const c of rows)
            this._list.add_child(this._contactRow(c));
        const searching = this._query.trim().length >= 2;
        if (!rows.length) {
            this._list.add_child(label(searching ? _('Nenhum contato encontrado.') : _('Nenhum contato atendido. Busque um contato para ligar.'), 'wab-hint'));
        }
        this._count.text = searching
            ? _('%d resultado(s) de %d contatos').format(rows.length, this._contacts.length)
            : _('%d atendido(s) de %d contatos · busque para ver os outros').format(rows.length, this._contacts.length);
    }

    _contactRow(c) {
        const box = new St.BoxLayout({vertical: true, style_class: 'wab-crow'});
        const top = new St.BoxLayout({style_class: 'wab-row'});

        const info = new St.Button({can_focus: true, x_expand: true, style_class: 'wab-crow-info', x_align: Clutter.ActorAlign.FILL});
        const infoBox = new St.BoxLayout({style_class: 'wab-row', x_expand: true, x_align: Clutter.ActorAlign.START});
        infoBox.add_child(label(initial(c.name), 'wab-av2'));
        const names = new St.BoxLayout({vertical: true, x_expand: true, x_align: Clutter.ActorAlign.START});
        names.add_child(label(c.name, 'wab-text wab-ellipsis'));
        names.add_child(label(c.context || formatPhone(c.phone), 'wab-hint wab-ellipsis'));
        infoBox.add_child(names);
        info.set_child(infoBox);
        info.connect('clicked', () => {
            this._expanded = this._expanded === c.phone ? null : c.phone;
            this._ctxDraft = c.context ?? '';
            this._renderContacts();
        });
        top.add_child(info);

        const seg = new St.BoxLayout({style_class: 'wab-seg'});
        for (const m of MODES) {
            const on = c.reply_mode === m;
            const b = new St.Button({
                label: MODE_LABEL[m], can_focus: true, accessible_name: `${c.name}: ${MODE_NAME[m]}`,
                style_class: `wab-seg-btn wab-seg-${m}${on ? ' wab-seg-on' : ''}`,
            });
            b.connect('clicked', () => this._setMode(c, m));
            seg.add_child(b);
        }
        top.add_child(seg);
        box.add_child(top);

        if (this._confirm === c.phone) {
            const bar = new St.BoxLayout({vertical: true, style_class: 'wab-confirm'});
            const msg = label(_('Responder sozinho a %s? O agente enviará mensagens de verdade.').format(c.name), 'wab-hint');
            msg.clutter_text.set_line_wrap(true);
            bar.add_child(msg);
            const act = new St.BoxLayout({style_class: 'wab-row'});
            act.add_child(button(_('Confirmar automático'), 'wab-btn-main wab-btn-sm', () => this._setMode(c, 'auto', true)));
            act.add_child(button(_('Cancelar'), 'wab-btn-sm', () => {
                this._confirm = null;
                this._renderContacts();
            }));
            bar.add_child(act);
            box.add_child(bar);
        }

        if (this._expanded === c.phone) {
            const ctx = new St.BoxLayout({vertical: true, style_class: 'wab-ctx'});
            const entry = new St.Entry({hint_text: _('Assunto da conversa'), can_focus: true, style_class: 'wab-entry', text: this._ctxDraft});
            entry.clutter_text.connect('text-changed', () => {
                this._ctxDraft = entry.get_text();
            });
            entry.clutter_text.connect('activate', () => this._saveContext(c, entry.get_text()));
            ctx.add_child(entry);
            const hint = label(_('O agente segue este assunto; fora dele, relata a você.'), 'wab-hint');
            hint.clutter_text.set_line_wrap(true);
            ctx.add_child(hint);
            const act = new St.BoxLayout({style_class: 'wab-row'});
            act.add_child(button(_('Salvar contexto'), 'wab-btn-main wab-btn-sm', () => this._saveContext(c, entry.get_text())));
            ctx.add_child(act);
            box.add_child(ctx);
        }
        return box;
    }

    // ------------------------------------------------------------------ avisos e limpeza
    _notifyError(title, detail) {
        if (!this._settings.get_boolean('show-notifications'))
            return;
        try {
            if (!this._notifSource) {
                this._notifSource = new MessageTray.Source({title: _('WhatsApp Bridge'), iconName: 'dialog-warning-symbolic'});
                this._notifSource.connect('destroy', () => {
                    this._notifSource = null;
                });
                Main.messageTray.add(this._notifSource);
            }
            this._notifSource.addNotification(new MessageTray.Notification({
                source: this._notifSource, title, body: detail || _('Veja os logs para mais detalhes.'),
                iconName: 'dialog-warning-symbolic',
            }));
        } catch (e) {
            logError(e, 'whatsapp-bridge: falha ao mostrar notificação de erro');
        }
    }

    _cleanup() {
        this._destroyed = true;
        for (const id of [this._pollId, this._burstId]) {
            if (id)
                GLib.source_remove(id);
        }
        this._pollId = 0;
        this._burstId = 0;
        this._bridge.destroy();
        this._wpp.destroy();
        this._notifSource?.destroy();
        this._notifSource = null;
    }
});

export default class WhatsAppBridgeExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._posHandler = this._settings.connect('changed::panel-position', () => this._reposition());
        this._create();
    }

    _create() {
        this._indicator = new Indicator(this);
        switch (this._settings.get_string('panel-position')) {
        case 'left':
            Main.panel.addToStatusArea(this.uuid, this._indicator, -1, 'left');
            break;
        case 'left-edge':
            Main.panel.addToStatusArea(this.uuid, this._indicator, 0, 'left');
            break;
        default:
            Main.panel.addToStatusArea(this.uuid, this._indicator);
        }
    }

    _reposition() {
        this._indicator?.destroy();
        this._indicator = null;
        this._create();
    }

    disable() {
        if (this._posHandler) {
            this._settings.disconnect(this._posHandler);
            this._posHandler = null;
        }
        this._indicator?.destroy();
        this._indicator = null;
        this._settings = null;
    }
}
