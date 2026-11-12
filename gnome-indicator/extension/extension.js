import GObject from 'gi://GObject';
import St from 'gi://St';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';

const SERVICE = 'whatsapp-bridge.service';
const BRIDGE_PORT = GLib.getenv('WHATSAPP_BRIDGE_PORT') || '9090';
const STATUS_URL = `http://127.0.0.1:${BRIDGE_PORT}/api/status`;
const HTTP_TIMEOUT_S = 2;

// Terminais tentados nessa ordem pra abrir os logs; o primeiro que existir no
// PATH vence. Cada um tem uma sintaxe própria pra "rodar e ficar aberto".
const TERMINAL_CANDIDATES = [
    {bin: 'ptyxis', args: cmd => ['ptyxis', '--', ...cmd]},
    {bin: 'gnome-terminal', args: cmd => ['gnome-terminal', '--', ...cmd]},
    {bin: 'konsole', args: cmd => ['konsole', '-e', ...cmd]},
    {bin: 'xterm', args: cmd => ['xterm', '-hold', '-e', ...cmd]},
];

// Os labels ficam sem tradução aqui de propósito: este objeto é avaliado no
// carregamento do módulo, antes de o Shell registrar a extensão — chamar
// gettext() (_()) nesse ponto lança "gettext can only be called from
// extensions". A tradução acontece no ponto de uso, em runtime (_rebuild).
const STATE = {
    on: {icon: 'network-transmit-receive-symbolic', cls: 'wab-icon-on', label: 'Conectado'},
    off: {icon: 'network-offline-symbolic', cls: 'wab-icon-off', label: 'Desligado'},
    starting: {icon: 'content-loading-symbolic', cls: 'wab-icon-pending', label: 'Iniciando…'},
    disconnected: {icon: 'network-wired-disconnected-symbolic', cls: 'wab-icon-pending', label: 'Rodando, mas sem sessão do WhatsApp — pode precisar reescanear o QR code'},
    pending: {icon: 'content-loading-symbolic', cls: 'wab-icon-pending', label: 'Aguarde…'},
    error: {icon: 'dialog-warning-symbolic', cls: 'wab-icon-error', label: 'Não foi possível checar o serviço'},
};


// ---- Agenda de contatos (mesmo arquivo que o servidor MCP lê) ----
const CONTACTS_RENDER_LIMIT = 60;

function contactsFilePath() {
    const fallback = GLib.build_filenamev([GLib.get_home_dir(), 'tools', 'wpp-mcp', 'whatsapp-bridge', 'store', 'contacts.json']);
    try {
        // Descobre o diretório do bridge pelo WorkingDirectory do serviço systemd.
        const unit = GLib.build_filenamev([GLib.get_user_config_dir(), 'systemd', 'user', SERVICE]);
        const [ok, bytes] = GLib.file_get_contents(unit);
        if (ok) {
            const m = new TextDecoder().decode(bytes).match(/^WorkingDirectory=(.+)$/m);
            if (m)
                return GLib.build_filenamev([m[1].trim(), 'store', 'contacts.json']);
        }
    } catch (e) {
        // cai no caminho padrão
    }
    return fallback;
}

function loadContacts() {
    try {
        const [ok, bytes] = GLib.file_get_contents(contactsFilePath());
        if (!ok)
            return [];
        return JSON.parse(new TextDecoder().decode(bytes)).contacts ?? [];
    } catch (e) {
        return [];
    }
}

function saveContacts(contacts) {
    contacts.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
    const data = JSON.stringify({contacts}, null, 2);
    const file = Gio.File.new_for_path(contactsFilePath());
    file.replace_contents(new TextEncoder().encode(data), null, false,
        Gio.FileCreateFlags.REPLACE_DESTINATION, null);
}


// ---- Modo conversacional (mesmo arquivo que o MCP e o vigia usam) ----
function conversationFilePath() {
    return GLib.build_filenamev([GLib.path_get_dirname(contactsFilePath()), 'conversation_mode.json']);
}

function loadConversation() {
    try {
        const [ok, bytes] = GLib.file_get_contents(conversationFilePath());
        return ok ? JSON.parse(new TextDecoder().decode(bytes)) : {};
    } catch (e) {
        return {};
    }
}

function saveConversation(state) {
    const file = Gio.File.new_for_path(conversationFilePath());
    file.replace_contents(new TextEncoder().encode(JSON.stringify(state, null, 2)), null, false,
        Gio.FileCreateFlags.REPLACE_DESTINATION, null);
}

// Mesma regra do contacts.py: só dígitos; 10-11 dígitos sem DDI ganham 55.
function normalizePhone(raw) {
    let d = (raw ?? '').replace(/\D/g, '');
    if (d.startsWith('00'))
        d = d.slice(2);
    if (d.length === 10 || d.length === 11)
        d = `55${d}`;
    return d;
}

function checkStatus(callback) {
    try {
        const proc = Gio.Subprocess.new(
            ['systemctl', '--user', 'is-active', SERVICE],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
        proc.communicate_utf8_async(null, null, (p, res) => {
            try {
                const [, stdout] = p.communicate_utf8_finish(res);
                callback(stdout.trim());
            } catch (e) {
                callback('error');
            }
        });
    } catch (e) {
        callback('error');
    }
}

// Consulta /api/status via curl: mais simples e previsível entre versões do
// Shell do que lidar com libsoup. Se o curl não existir ou o bridge ainda não
// tiver subido a API REST, trata como "sem resposta" (bridge ainda iniciando).
function checkBridgeHealth(callback) {
    try {
        const proc = Gio.Subprocess.new(
            ['curl', '-s', '-m', String(HTTP_TIMEOUT_S), '--fail', STATUS_URL],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
        proc.communicate_utf8_async(null, null, (p, res) => {
            try {
                const [, stdout] = p.communicate_utf8_finish(res);
                if (!proc.get_successful()) {
                    callback(null);
                    return;
                }
                callback(JSON.parse(stdout));
            } catch (e) {
                callback(null);
            }
        });
    } catch (e) {
        callback(null);
    }
}

function runSystemctl(action, callback) {
    try {
        const proc = Gio.Subprocess.new(
            ['systemctl', '--user', action, SERVICE],
            Gio.SubprocessFlags.STDERR_PIPE);
        proc.communicate_utf8_async(null, null, (p, res) => {
            let ok = false;
            let stderr = '';
            try {
                const [, , errOut] = p.communicate_utf8_finish(res);
                stderr = errOut?.trim() ?? '';
                ok = p.get_successful();
            } catch (e) {
                ok = false;
            }
            callback?.(ok, stderr);
        });
    } catch (e) {
        logError(e, `whatsapp-bridge: falha ao rodar systemctl ${action}`);
        callback?.(false, String(e));
    }
}

function openLogsTerminal() {
    const cmd = ['journalctl', '--user', '-u', SERVICE, '-n', '200', '-f'];
    for (const candidate of TERMINAL_CANDIDATES) {
        if (!GLib.find_program_in_path(candidate.bin))
            continue;
        try {
            Gio.Subprocess.new(candidate.args(cmd), Gio.SubprocessFlags.NONE);
            return true;
        } catch (e) {
            logError(e, `whatsapp-bridge: falha ao abrir logs com ${candidate.bin}`);
        }
    }
    return false;
}

const Indicator = GObject.registerClass(
class Indicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.0, _('WhatsApp Bridge'), false);
        this._extension = extension;
        this._settings = extension.getSettings();
        this._destroyed = false;
        this._state = 'off';
        this._pending = false;
        this._tab = 'status';
        this._editingPhone = null;
        this._notifSource = null;
        this._pollTimeoutId = null;

        this._panelIcon = new St.Icon({style_class: 'system-status-icon'});
        this.add_child(this._panelIcon);

        this._rebuild();
        this._poll();
        this._schedulePoll();

        this._settings.connect('changed::poll-interval', () => this._schedulePoll());

        this.menu.connect('open-state-changed', (menu, open) => {
            if (open) {
                this._poll();
                if (this._tab === 'contacts')
                    this._rebuild(); // recarrega do disco
            }
        });
    }

    _schedulePoll() {
        if (this._pollTimeoutId) {
            GLib.source_remove(this._pollTimeoutId);
            this._pollTimeoutId = null;
        }
        const interval = Math.max(2, this._settings.get_int('poll-interval'));
        this._pollTimeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, interval, () => {
            if (this._destroyed)
                return GLib.SOURCE_REMOVE;
            this._poll();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _applyState(state) {
        if (this._destroyed)
            return;
        this._state = state;
        const s = STATE[state] ?? STATE.error;
        this._panelIcon.icon_name = s.icon;
        this._panelIcon.style_class = `system-status-icon ${s.cls}`;
        if (this._tab === 'status') // não destrói a aba de contatos (e o foco da busca) a cada poll
            this._rebuild();
    }

    _poll() {
        if (this._destroyed || this._pending)
            return; // não deixa o poll pisar num toggle em andamento
        checkStatus(status => {
            if (this._destroyed || this._pending)
                return;
            if (status === 'inactive' || status === 'failed') {
                this._applyState('off');
                return;
            }
            if (status !== 'active') {
                this._applyState('error');
                return;
            }
            // Serviço ativo não é o mesmo que "conectado ao WhatsApp": o
            // processo pode estar de pé sem sessão válida (ex.: logout, ou
            // ainda subindo). Confirma via /api/status do próprio bridge.
            checkBridgeHealth(health => {
                if (this._destroyed || this._pending)
                    return;
                if (!health)
                    this._applyState('starting');
                else if (health.connected && health.logged_in)
                    this._applyState('on');
                else
                    this._applyState('disconnected');
            });
        });
    }

    _toggle() {
        if (this._pending || this._destroyed)
            return;
        const isRunning = this._state !== 'off';
        const action = isRunning ? 'stop' : 'start';
        this._pending = true;
        this._applyState('pending');
        runSystemctl(action, (ok, stderr) => {
            if (this._destroyed)
                return;
            this._pending = false;
            if (!ok) {
                const verb = action === 'start' ? _('ligar') : _('desligar');
                this._notifyError(_('Falha ao %s a bridge').format(verb), stderr);
            }
            this._poll();
        });
    }

    _notifyError(title, detail) {
        if (!this._settings.get_boolean('show-notifications'))
            return;
        try {
            if (!this._notifSource) {
                this._notifSource = new MessageTray.Source({
                    title: _('WhatsApp Bridge'),
                    iconName: 'dialog-warning-symbolic',
                });
                this._notifSource.connect('destroy', () => {
                    this._notifSource = null;
                });
                Main.messageTray.add(this._notifSource);
            }
            const notification = new MessageTray.Notification({
                source: this._notifSource,
                title,
                body: detail || _('Veja os logs para mais detalhes.'),
                iconName: 'dialog-warning-symbolic',
            });
            this._notifSource.addNotification(notification);
        } catch (e) {
            logError(e, 'whatsapp-bridge: falha ao mostrar notificação de erro');
        }
    }


    _addTabBar() {
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const bar = new St.BoxLayout({style_class: 'wab-tabs', x_expand: true});
        for (const [id, text] of [['status', _('Status')], ['contacts', _('Contatos')]]) {
            const btn = new St.Button({
                label: text, can_focus: true, x_expand: true,
                style_class: `wab-tab${this._tab === id ? ' wab-tab-active' : ''}`,
            });
            btn.connect('clicked', () => {
                if (this._tab === id)
                    return;
                this._tab = id;
                this._rebuild();
            });
            bar.add_child(btn);
        }
        item.add_child(bar);
        this.menu.addMenuItem(item);
    }

    _buildContactsTab() {
        const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const box = new St.BoxLayout({vertical: true, x_expand: true, style_class: 'wab-contacts'});

        this._search = new St.Entry({hint_text: _('Buscar contato…'), can_focus: true, style_class: 'wab-entry'});
        this._search.clutter_text.connect('text-changed', () => this._renderContacts());
        box.add_child(this._search);

        this._scroll = new St.ScrollView({style_class: 'wab-contacts-scroll', overlay_scrollbars: true});
        this._list = new St.BoxLayout({vertical: true});
        this._scroll.set_child(this._list);
        box.add_child(this._scroll);

        this._countLabel = new St.Label({style_class: 'wab-contacts-count'});
        box.add_child(this._countLabel);

        const form = new St.BoxLayout({style_class: 'wab-form'});
        this._nameEntry = new St.Entry({hint_text: _('Nome'), can_focus: true, x_expand: true, style_class: 'wab-entry'});
        this._phoneEntry = new St.Entry({hint_text: _('Telefone'), can_focus: true, x_expand: true, style_class: 'wab-entry'});
        this._saveBtn = new St.Button({label: _('Salvar'), can_focus: true, style_class: 'wab-btn'});
        this._saveBtn.connect('clicked', () => this._saveFromForm());
        this._phoneEntry.clutter_text.connect('activate', () => this._saveFromForm());
        this._nameEntry.clutter_text.connect('activate', () => this._phoneEntry.grab_key_focus());
        form.add_child(this._nameEntry);
        form.add_child(this._phoneEntry);
        form.add_child(this._saveBtn);
        box.add_child(form);

        item.add_child(box);
        this.menu.addMenuItem(item);
        this._contacts = loadContacts();
        this._renderContacts();
        this._search.grab_key_focus();
    }

    _renderContacts() {
        if (!this._list)
            return;
        this._list.destroy_all_children();
        const q = this._search.get_text().trim().toLowerCase();
        const qd = q.replace(/\D/g, '');
        const matches = this._contacts.filter(c =>
            !q || c.name.toLowerCase().includes(q) || (qd && c.phone.includes(qd)));
        this._countLabel.text = matches.length > CONTACTS_RENDER_LIMIT
            ? _('Mostrando %d de %d — refine a busca').format(CONTACTS_RENDER_LIMIT, matches.length)
            : _('%d contatos').format(matches.length);

        for (const c of matches.slice(0, CONTACTS_RENDER_LIMIT)) {
            const row = new St.BoxLayout({style_class: 'wab-row'});
            const info = new St.Button({can_focus: true, x_expand: true, style_class: 'wab-row-info'});
            const col = new St.BoxLayout({vertical: true});
            col.add_child(new St.Label({text: c.name, style_class: 'wab-row-name'}));
            col.add_child(new St.Label({text: c.phone, style_class: 'wab-row-phone'}));
            info.set_child(col);
            // clicar edita: preenche o formulário com o contato
            info.connect('clicked', () => {
                this._editingPhone = c.phone;
                this._nameEntry.set_text(c.name);
                this._phoneEntry.set_text(c.phone);
                this._saveBtn.label = _('Atualizar');
                this._nameEntry.grab_key_focus();
            });
            row.add_child(info);

            // Escolhe em quais contatos o modo conversacional atua: off -> rascunho -> auto -> off
            const mode = c.profile?.reply_mode ?? 'off';
            const modeBtn = new St.Button({
                label: {off: _('Conversa: off'), draft: _('Conversa: rascunho'), auto: _('Conversa: AUTO')}[mode] ?? _('Conversa: off'),
                can_focus: true, style_class: 'wab-btn',
            });
            modeBtn.connect('clicked', () => this._cycleReplyMode(c.phone));
            row.add_child(modeBtn);

            const del = new St.Button({
                child: new St.Icon({icon_name: 'user-trash-symbolic', icon_size: 14}),
                can_focus: true, style_class: 'wab-icon-btn',
            });
            del.connect('clicked', () => {
                this._contacts = this._contacts.filter(x => x.phone !== c.phone);
                this._persist();
                this._renderContacts();
            });
            row.add_child(del);
            this._list.add_child(row);
        }
    }

    _cycleReplyMode(phone) {
        // recarrega do disco: o MCP ou o usuário podem ter editado o arquivo
        this._contacts = loadContacts();
        const c = this._contacts.find(x => x.phone === phone);
        if (!c)
            return;
        const order = ['off', 'draft', 'auto'];
        const current = c.profile?.reply_mode ?? 'off';
        const next = order[(order.indexOf(current) + 1) % order.length];
        c.profile = {...(c.profile ?? {}), reply_mode: next};
        this._persist();
        this._renderContacts();
    }

    _persist() {
        try {
            saveContacts(this._contacts);
        } catch (e) {
            logError(e, 'whatsapp-bridge: falha ao salvar contacts.json');
            this._notifyError(_('Não foi possível salvar os contatos'), String(e.message ?? e));
        }
    }

    _saveFromForm() {
        const name = this._nameEntry.get_text().trim();
        const phone = normalizePhone(this._phoneEntry.get_text());
        if (!name || !phone)
            return;
        // recarrega do disco: o MCP ou o usuário podem ter editado o arquivo
        this._contacts = loadContacts();
        const oldPhone = this._editingPhone ?? phone;
        const existing = this._contacts.find(c => c.phone === oldPhone);
        if (existing) {
            existing.name = name;
            existing.phone = phone;
        } else {
            this._contacts.push({name, phone, notes: ''});
        }
        // se o telefone mudou e colide com outro, mantém um só
        this._contacts = this._contacts.filter((c, i, a) => a.findIndex(x => x.phone === c.phone) === i);
        this._persist();
        this._editingPhone = null;
        this._nameEntry.set_text('');
        this._phoneEntry.set_text('');
        this._saveBtn.label = _('Salvar');
        this._renderContacts();
    }

    _toggleConversation(enable) {
        try {
            const state = loadConversation();
            if (enable && !(state.agent_id && state.routine_id)) {
                this._notifyError(_('Modo conversacional'),
                    _('Configure a rotina do OpenBot antes de ligar (ferramenta conversation_mode).'));
                this._rebuild();
                return;
            }
            state.enabled = enable;
            if (enable) {
                // cursor vazio: o vigia começa do ponto atual e não responde o histórico
                state.cursor = null;
                state.enabled_at = new Date().toISOString().slice(0, 19);
            }
            saveConversation(state);
        } catch (e) {
            logError(e, 'whatsapp-bridge: falha ao salvar conversation_mode.json');
            this._notifyError(_('Não foi possível alterar o modo conversacional'), String(e.message ?? e));
            this._rebuild();
        }
    }

    _rebuild() {
        if (this._destroyed)
            return;
        this.menu.removeAll();

        this._addTabBar();
        if (this._tab === 'contacts') {
            this._buildContactsTab();
            return;
        }

        const s = STATE[this._state] ?? STATE.error;

        const header = new PopupMenu.PopupBaseMenuItem({
            reactive: false, can_focus: false, style_class: 'wab-header',
        });
        const box = new St.BoxLayout({vertical: true, style_class: 'wab-header-box'});
        box.add_child(new St.Label({text: _('WhatsApp Bridge'), style_class: 'wab-header-title'}));
        const subtitle = new St.Label({text: _(s.label), style_class: `wab-header-subtitle ${s.cls}`});
        subtitle.clutter_text.set_line_wrap(true);
        box.add_child(subtitle);
        header.add_child(box);
        this.menu.addMenuItem(header);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const isRunning = this._state !== 'off';
        const label = this._pending ? _('Aguarde…') : isRunning ? _('Desligar bridge') : _('Ligar bridge');
        const icon = isRunning ? 'media-playback-stop-symbolic' : 'media-playback-start-symbolic';
        const toggleItem = new PopupMenu.PopupImageMenuItem(label, icon);
        toggleItem.setSensitive(!this._pending);
        toggleItem.connect('activate', () => this._toggle());
        this.menu.addMenuItem(toggleItem);

        const conv = loadConversation();
        const served = loadContacts().filter(c => ['draft', 'auto'].includes(c.profile?.reply_mode)).length;
        const convItem = new PopupMenu.PopupSwitchMenuItem(
            _('Modo conversacional (%d contatos)').format(served), !!conv.enabled);
        convItem.connect('toggled', (_item, state) => this._toggleConversation(state));
        this.menu.addMenuItem(convItem);

        const logsItem = new PopupMenu.PopupImageMenuItem(_('Ver logs'), 'utilities-terminal-symbolic');
        logsItem.connect('activate', () => {
            if (!openLogsTerminal())
                this._notifyError(_('Não foi possível abrir os logs'), _('Nenhum terminal compatível foi encontrado (ptyxis, gnome-terminal, konsole ou xterm).'));
        });
        this.menu.addMenuItem(logsItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const prefsItem = new PopupMenu.PopupImageMenuItem(_('Configurações'), 'preferences-system-symbolic');
        prefsItem.connect('activate', () => this._extension.openPreferences());
        this.menu.addMenuItem(prefsItem);
    }

    _onDestroy() {
        this._destroyed = true;
        if (this._pollTimeoutId) {
            GLib.source_remove(this._pollTimeoutId);
            this._pollTimeoutId = null;
        }
        this._notifSource?.destroy(MessageTray.NotificationDestroyedReason.SOURCE_CLOSED);
        this._notifSource = null;
        this._panelIcon = null;
        this._settings = null;
        this._list = null;
        super._onDestroy();
    }
});

export default class WhatsAppBridgeExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._posHandler = this._settings.connect('changed::panel-position',
            () => this._reposition());
        this._create();
    }

    _create() {
        this._indicator = new Indicator(this);
        const pos = this._settings.get_string('panel-position');
        switch (pos) {
            case 'left':
                Main.panel.addToStatusArea(this.uuid, this._indicator, -1, 'left');
                break;
            case 'left-edge':
                Main.panel.addToStatusArea(this.uuid, this._indicator, 0, 'left');
                break;
            default: // 'right'
                Main.panel.addToStatusArea(this.uuid, this._indicator);
                break;
        }
    }

    _reposition() {
        delete Main.panel.statusArea[this.uuid];
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
