import Adw from 'gi://Adw';
import Gtk from 'gi://Gtk';
import Gio from 'gi://Gio';

import {ExtensionPreferences, gettext as _} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class WhatsAppBridgePrefs extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        window.set_default_size(560, 520);

        const page = new Adw.PreferencesPage({
            title: _('Geral'),
            icon_name: 'preferences-desktop-display-symbolic',
        });
        window.add(page);

        // =================== Posição na barra (seletor visual) ===================
        const posGroup = new Adw.PreferencesGroup({
            title: _('Posição na barra'),
        });
        page.add(posGroup);

        const currentPosicao = settings.get_string('panel-position');

        const posCSS = new Gtk.CssProvider();
        posCSS.load_from_string(
            '.wab-badge{background-color:alpha(@accent_bg_color,.9);color:@accent_fg_color;' +
            'border-radius:4px;padding:1px 7px;font-size:.78em;font-weight:bold;}'
        );
        Gtk.StyleContext.add_provider_for_display(
            window.get_display(), posCSS, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        );

        const posRow = new Adw.PreferencesRow({ activatable: false, focusable: false });
        const posContainer = new Gtk.Box({
            orientation: Gtk.Orientation.VERTICAL,
            spacing: 10,
            margin_top: 12,
            margin_bottom: 14,
            margin_start: 12,
            margin_end: 12,
        });
        posRow.set_child(posContainer);
        posGroup.add(posRow);

        const miniBar = new Gtk.Box({
            orientation: Gtk.Orientation.HORIZONTAL,
            css_classes: ['card'],
            height_request: 34,
            overflow: Gtk.Overflow.HIDDEN,
        });

        const barLeft = new Gtk.Box({
            orientation: Gtk.Orientation.HORIZONTAL,
            spacing: 4,
            margin_start: 10,
            valign: Gtk.Align.CENTER,
        });
        const leftEdgeBadge = new Gtk.Label({
            label: 'WPP',
            css_classes: ['wab-badge'],
            visible: currentPosicao === 'left-edge',
        });
        barLeft.append(leftEdgeBadge);
        barLeft.append(new Gtk.Label({ label: 'Ativid.', css_classes: ['dim-label', 'caption'] }));
        const leftBadge = new Gtk.Label({
            label: 'WPP',
            css_classes: ['wab-badge'],
            visible: currentPosicao === 'left',
        });
        barLeft.append(leftBadge);
        miniBar.append(barLeft);

        const barCenter = new Gtk.Box({ hexpand: true, halign: Gtk.Align.CENTER, valign: Gtk.Align.CENTER });
        barCenter.append(new Gtk.Label({ label: '12:00', css_classes: ['caption'] }));
        miniBar.append(barCenter);

        const barRight = new Gtk.Box({
            orientation: Gtk.Orientation.HORIZONTAL,
            spacing: 4,
            margin_end: 10,
            valign: Gtk.Align.CENTER,
        });
        const rightBadge = new Gtk.Label({
            label: 'WPP',
            css_classes: ['wab-badge'],
            visible: currentPosicao === 'right',
        });
        barRight.append(rightBadge);
        barRight.append(new Gtk.Label({ label: '🔊 ◉ ◉ ☰', css_classes: ['dim-label', 'caption'] }));
        miniBar.append(barRight);

        posContainer.append(miniBar);

        const posBadges = { 'left-edge': leftEdgeBadge, 'left': leftBadge, 'right': rightBadge };
        const updatePosPreview = (pos) => {
            Object.entries(posBadges).forEach(([k, b]) => { b.visible = k === pos; });
        };

        const posicoes = [
            { id: 'left-edge', label: _('Borda esquerda') },
            { id: 'left',      label: _('Esquerda') },
            { id: 'right',     label: _('Direita') },
        ];

        const btnBox = new Gtk.Box({
            orientation: Gtk.Orientation.HORIZONTAL,
            homogeneous: true,
            css_classes: ['linked'],
        });

        const toggleBtns = [];
        posicoes.forEach(p => {
            const btn = new Gtk.ToggleButton({ label: p.label, active: currentPosicao === p.id });
            if (currentPosicao === p.id) btn.add_css_class('suggested-action');
            btn.connect('toggled', () => {
                if (btn.active) {
                    toggleBtns.forEach(b => {
                        if (b !== btn) { b.active = false; b.remove_css_class('suggested-action'); }
                    });
                    btn.add_css_class('suggested-action');
                    settings.set_string('panel-position', p.id);
                    updatePosPreview(p.id);
                } else if (!toggleBtns.some(b => b.active)) {
                    btn.active = true;
                    btn.add_css_class('suggested-action');
                }
            });
            btnBox.append(btn);
            toggleBtns.push(btn);
        });

        posContainer.append(btnBox);

        // =================== Comportamento ===================
        const behaviorGroup = new Adw.PreferencesGroup({
            title: _('Comportamento'),
        });
        page.add(behaviorGroup);

        const intervalRow = new Adw.SpinRow({
            title: _('Intervalo de checagem'),
            subtitle: _('Frequência com que o indicador consulta o status do bridge (segundos)'),
            adjustment: new Gtk.Adjustment({ lower: 2, upper: 60, step_increment: 1, page_increment: 5 }),
        });
        settings.bind('poll-interval', intervalRow, 'value', Gio.SettingsBindFlags.DEFAULT);
        behaviorGroup.add(intervalRow);

        const notifRow = new Adw.SwitchRow({
            title: _('Mostrar notificações de erro'),
            subtitle: _('Avisa quando ligar/desligar a bridge falha ou os logs não abrem'),
        });
        settings.bind('show-notifications', notifRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        behaviorGroup.add(notifRow);

        // =================== Sobre ===================
        const aboutPage = new Adw.PreferencesPage({
            title: _('Sobre'),
            icon_name: 'help-about-symbolic',
        });
        window.add(aboutPage);
        const aboutGroup = new Adw.PreferencesGroup();
        aboutPage.add(aboutGroup);
        aboutGroup.add(new Adw.ActionRow({
            title: _('WhatsApp Bridge'),
            subtitle: _('Liga e desliga a bridge do WhatsApp (whatsmeow) usada pelo MCP direto da barra superior.'),
        }));
    }
}
