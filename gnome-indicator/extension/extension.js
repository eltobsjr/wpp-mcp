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
        this._notifSource = null;
        this._pollTimeoutId = null;

        this._panelIcon = new St.Icon({style_class: 'system-status-icon'});
        this.add_child(this._panelIcon);

        this._rebuild();
        this._poll();
        this._schedulePoll();

        this._settings.connect('changed::poll-interval', () => this._schedulePoll());

        this.menu.connect('open-state-changed', (menu, open) => {
            if (open)
                this._poll();
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

    _rebuild() {
        if (this._destroyed)
            return;
        this.menu.removeAll();

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

        const logsItem = new PopupMenu.PopupImageMenuItem(_('Ver logs'), 'utilities-terminal-symbolic');
        logsItem.connect('activate', () => {
            if (!openLogsTerminal())
                this._notifyError(_('Não foi possível abrir os logs'), _('Nenhum terminal compatível foi encontrado (ptyxis, gnome-terminal, konsole ou xterm).'));
        });
        this.menu.addMenuItem(logsItem);
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
