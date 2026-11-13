// Cliente do comando `wpp` (a interface padrão do WhatsApp): sempre assíncrono, com tempo limite e cancelamento.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export class Wpp {
    constructor(settings) {
        this._settings = settings;
        this._cancel = new Gio.Cancellable();
        this._procs = new Set();
    }

    path() {
        const home = GLib.get_home_dir();
        const custom = (this._settings?.get_string('wpp-path') ?? '').trim();
        for (const c of [custom, `${home}/.local/bin/wpp`, `${home}/tools/scripts/wpp`]) {
            if (c && GLib.file_test(c, GLib.FileTest.IS_EXECUTABLE))
                return c;
        }
        return GLib.find_program_in_path('wpp');
    }

    /** @returns {Promise<{ok:boolean, code:number, stdout:string, stderr:string, timedOut:boolean}>} */
    run(args, timeoutS = 8) {
        return new Promise(resolve => {
            const bin = this.path();
            if (!bin) {
                resolve({ok: false, code: -1, stdout: '', stderr: 'comando wpp não encontrado', timedOut: false});
                return;
            }
            let proc;
            try {
                proc = new Gio.Subprocess({
                    argv: [bin, ...args],
                    flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
                });
                proc.init(this._cancel);
            } catch (e) {
                resolve({ok: false, code: -1, stdout: '', stderr: String(e), timedOut: false});
                return;
            }
            this._procs.add(proc);
            let timedOut = false;
            let timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, timeoutS, () => {
                timer = 0;
                timedOut = true;
                try {
                    proc.force_exit();
                } catch (_e) { /* já terminou */ }
                return GLib.SOURCE_REMOVE;
            });
            proc.communicate_utf8_async(null, this._cancel, (p, res) => {
                if (timer)
                    GLib.source_remove(timer);
                this._procs.delete(proc);
                try {
                    const [, out, err] = p.communicate_utf8_finish(res);
                    resolve({ok: p.get_successful(), code: p.get_exit_status(), stdout: out ?? '', stderr: err ?? '', timedOut});
                } catch (e) {
                    resolve({ok: false, code: -1, stdout: '', stderr: String(e), timedOut});
                }
            });
        });
    }

    /** Roda e interpreta a última linha JSON da saída. */
    async json(args, timeoutS = 8) {
        const r = await this.run(args, timeoutS);
        let data = null;
        for (const line of r.stdout.split('\n').reverse()) {
            const t = line.trim();
            if (t.startsWith('{') || t.startsWith('[')) {
                try {
                    data = JSON.parse(t);
                } catch (_e) { /* tenta a linha anterior */ }
                break;
            }
        }
        return {...r, data};
    }

    destroy() {
        this._cancel.cancel();
        for (const p of this._procs) {
            try {
                p.force_exit();
            } catch (_e) { /* ignore */ }
        }
        this._procs.clear();
    }
}
