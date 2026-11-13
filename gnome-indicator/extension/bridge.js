// Controle do bridge: serviço systemd (estado por D-Bus, sem polling com o menu fechado) e /api/status.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export const SERVICE = 'whatsapp-bridge.service';
const PORT = GLib.getenv('WHATSAPP_BRIDGE_PORT') || '9090';
const STATUS_URL = `http://127.0.0.1:${PORT}/api/status`;

function exec(argv, {stdout = true, stderr = false, cancellable = null} = {}) {
    return new Promise(resolve => {
        try {
            let flags = Gio.SubprocessFlags.NONE;
            flags |= stdout ? Gio.SubprocessFlags.STDOUT_PIPE : Gio.SubprocessFlags.STDOUT_SILENCE;
            flags |= stderr ? Gio.SubprocessFlags.STDERR_PIPE : Gio.SubprocessFlags.STDERR_SILENCE;
            const proc = new Gio.Subprocess({argv, flags});
            proc.init(cancellable);
            proc.communicate_utf8_async(null, cancellable, (p, res) => {
                try {
                    const [, out, err] = p.communicate_utf8_finish(res);
                    resolve({ok: p.get_successful(), out: (out ?? '').trim(), err: (err ?? '').trim()});
                } catch (e) {
                    resolve({ok: false, out: '', err: String(e)});
                }
            });
        } catch (e) {
            resolve({ok: false, out: '', err: String(e)});
        }
    });
}

/** Diretório `store` do bridge: vem do WorkingDirectory do serviço; senão o caminho padrão do projeto. */
export function storeDir() {
    const fallback = GLib.build_filenamev([GLib.get_home_dir(), 'tools', 'automacao', 'wpp-mcp', 'whatsapp-bridge', 'store']);
    try {
        const unit = GLib.build_filenamev([GLib.get_user_config_dir(), 'systemd', 'user', SERVICE]);
        const [ok, bytes] = GLib.file_get_contents(unit);
        if (ok) {
            const m = new TextDecoder().decode(bytes).match(/^WorkingDirectory=(.+)$/m);
            if (m)
                return GLib.build_filenamev([m[1].trim(), 'store']);
        }
    } catch (_e) { /* cai no padrão */ }
    return fallback;
}

export function openLogsTerminal() {
    const cmd = ['journalctl', '--user', '-u', SERVICE, '-n', '200', '-f'];
    const terms = [
        {bin: 'ptyxis', args: c => ['ptyxis', '--', ...c]},
        {bin: 'gnome-terminal', args: c => ['gnome-terminal', '--', ...c]},
        {bin: 'konsole', args: c => ['konsole', '-e', ...c]},
        {bin: 'xterm', args: c => ['xterm', '-hold', '-e', ...c]},
    ];
    for (const t of terms) {
        if (!GLib.find_program_in_path(t.bin))
            continue;
        try {
            Gio.Subprocess.new(t.args(cmd), Gio.SubprocessFlags.NONE);
            return true;
        } catch (e) {
            logError(e, `whatsapp-bridge: falha ao abrir logs com ${t.bin}`);
        }
    }
    return false;
}

export class Bridge {
    constructor() {
        this._cancel = new Gio.Cancellable();
        this._subId = 0;
        this._monitor = null;
        this._monitorId = 0;
        this._debounceId = 0;
    }

    async service() {
        const r = await exec(['systemctl', '--user', 'is-active', SERVICE], {cancellable: this._cancel});
        return r.out || 'unknown';
    }

    async health() {
        const r = await exec(['curl', '-s', '-m', '2', '--fail', STATUS_URL], {cancellable: this._cancel});
        if (!r.ok)
            return null;
        try {
            return JSON.parse(r.out);
        } catch (_e) {
            return null;
        }
    }

    async action(verb) {
        const r = await exec(['systemctl', '--user', verb, SERVICE], {stdout: false, stderr: true, cancellable: this._cancel});
        return {ok: r.ok, stderr: r.err};
    }

    /** Avisa quando a unidade muda de estado (PropertiesChanged via D-Bus do systemd do usuário). */
    watchUnit(onChange) {
        const bus = Gio.DBus.session;
        bus.call('org.freedesktop.systemd1', '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager',
            'LoadUnit', new GLib.Variant('(s)', [SERVICE]), new GLib.VariantType('(o)'),
            Gio.DBusCallFlags.NONE, 3000, this._cancel, (conn, res) => {
                try {
                    const [path] = conn.call_finish(res).deepUnpack();
                    this._subId = bus.signal_subscribe('org.freedesktop.systemd1', 'org.freedesktop.DBus.Properties',
                        'PropertiesChanged', path, null, Gio.DBusSignalFlags.NONE, () => onChange());
                } catch (e) {
                    if (!e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        logError(e, 'whatsapp-bridge: não foi possível observar o serviço pelo D-Bus');
                }
            });
    }

    /** Avisa (com espera de 400 ms) quando contacts.json ou conversation_mode.json mudam. */
    watchFiles(onChange) {
        try {
            const dir = Gio.File.new_for_path(storeDir());
            this._monitor = dir.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, null);
            this._monitorId = this._monitor.connect('changed', (_m, file, other) => {
                const names = [file?.get_basename(), other?.get_basename()];
                if (!names.some(n => n === 'contacts.json' || n === 'conversation_mode.json'))
                    return;
                if (this._debounceId)
                    GLib.source_remove(this._debounceId);
                this._debounceId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 400, () => {
                    this._debounceId = 0;
                    onChange();
                    return GLib.SOURCE_REMOVE;
                });
            });
        } catch (e) {
            logError(e, 'whatsapp-bridge: não foi possível observar a pasta store');
        }
    }

    destroy() {
        this._cancel.cancel();
        if (this._subId) {
            Gio.DBus.session.signal_unsubscribe(this._subId);
            this._subId = 0;
        }
        if (this._debounceId) {
            GLib.source_remove(this._debounceId);
            this._debounceId = 0;
        }
        if (this._monitor) {
            this._monitor.disconnect(this._monitorId);
            this._monitor.cancel();
            this._monitor = null;
        }
    }
}
