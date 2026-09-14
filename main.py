#!/usr/bin/env python3
"""
main.py — Suivi Bourse (Kivy, client léger) — v3

Nouveautés vs v2 :
- Rafraîchissement progressif : chaque position affiche son résultat dès
  qu'il arrive (pool de threads + mise à jour ligne par ligne), au lieu
  d'attendre que TOUT le portefeuille soit chargé avant d'afficher quoi
  que ce soit.
- UI retravaillée : palette cohérente, cartes avec accent coloré selon
  PV/MV, hiérarchie visuelle plus claire.
- Écran Actualités par position (titres + sentiment basique + lien).

Toujours un client léger (kivy + requests) : la vraie logique tourne sur
server.py (voir README).
"""

import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from kivy.app import App
from kivy.clock import mainthread, Clock
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.properties import StringProperty, ListProperty, BooleanProperty
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.uix.label import Label
from kivy.factory import Factory

import storage
import api_client

BG = (0.07, 0.08, 0.10, 1)
CARD = (0.13, 0.145, 0.175, 1)
TXT_MUTED = (0.62, 0.65, 0.70, 1)
GREEN = (0.32, 0.78, 0.48, 1)
RED = (0.92, 0.38, 0.38, 1)
ORANGE = (0.95, 0.65, 0.25, 1)
WHITE = (0.95, 0.96, 0.97, 1)

KV = """
#:import dp kivy.metrics.dp

<SectionLabel@Label>:
    bold: True
    font_size: "13sp"
    color: 0.62, 0.65, 0.70, 1
    size_hint_y: None
    height: dp(26)
    halign: "left"
    text_size: self.size

<PillButton@Button>:
    background_normal: ""
    background_color: 0.30, 0.62, 0.98, 1
    color: 1, 1, 1, 1
    bold: True
    font_size: "13sp"

<GhostButton@Button>:
    background_normal: ""
    background_color: 0.18, 0.20, 0.24, 1
    color: 0.85, 0.87, 0.90, 1
    font_size: "13sp"

<PositionRow@BoxLayout>:
    orientation: "vertical"
    size_hint_y: None
    height: dp(118)
    padding: dp(14), dp(10)
    spacing: dp(4)
    canvas.before:
        Color:
            rgba: 0.13, 0.145, 0.175, 1
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]
        Color:
            rgba: root.accent_color
        RoundedRectangle:
            pos: self.pos
            size: (dp(4), self.height)
            radius: [dp(2)]

    ticker: ""
    nom: ""
    prix_txt: ""
    quantite_txt: ""
    pv_mv_txt: "..."
    pv_mv_color: 0.62, 0.65, 0.70, 1
    accent_color: 0.30, 0.62, 0.98, 1
    sante_txt: "…"
    div_txt: "…"
    verdict: ""
    a_des_alertes: False

    BoxLayout:
        size_hint_y: 0.35
        Label:
            text: root.nom + ("   [color=9fa3ab]" + root.prix_txt + "[/color]" if root.prix_txt else "")
            markup: True
            bold: True
            font_size: "15sp"
            color: 0.95, 0.96, 0.97, 1
            halign: "left"
            valign: "middle"
            text_size: self.size
            shorten: True
        Label:
            text: root.pv_mv_txt
            color: root.pv_mv_color
            bold: True
            font_size: "15sp"
            halign: "right"
            valign: "middle"
            text_size: self.size
            size_hint_x: 0.5

    BoxLayout:
        size_hint_y: 0.30
        Label:
            text: (root.quantite_txt + " actions" if root.quantite_txt else "")
            font_size: "12sp"
            color: 0.62, 0.65, 0.70, 1
            halign: "left"
            text_size: self.size

    BoxLayout:
        size_hint_y: 0.35
        spacing: dp(10)
        Label:
            text: "[color=9fa3ab]Santé[/color]  [b]" + root.sante_txt + "[/b]/10"
            markup: True
            font_size: "12sp"
            color: 0.85, 0.87, 0.90, 1
            halign: "left"
            text_size: self.size
        Label:
            text: "[color=9fa3ab]Div[/color]  [b]" + root.div_txt + "[/b]/10"
            markup: True
            font_size: "12sp"
            color: 0.85, 0.87, 0.90, 1
            halign: "left"
            text_size: self.size
        Label:
            markup: True
            text: ("[color=4caf50]" if root.verdict.startswith("OK") else "[color=e05555]" if root.verdict.startswith("KO") else "[color=f2a63f]" if root.verdict.startswith("MOYEN") else "[color=9fa3ab]") + (root.verdict.split(" ", 1)[-1] if " " in root.verdict else root.verdict) + "[/color]" + ("  [color=f2a63f][b]![/b][/color]" if root.a_des_alertes else "")
            font_size: "12sp"
            halign: "right"
            text_size: self.size

<NewsRow@BoxLayout>:
    orientation: "vertical"
    size_hint_y: None
    height: self.minimum_height
    padding: dp(14), dp(10)
    spacing: dp(4)
    canvas.before:
        Color:
            rgba: 0.13, 0.145, 0.175, 1
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]

    titre: ""
    editeur: ""
    date_txt: ""
    sentiment: "neutre"
    lien: ""
    alerte: False
    source: ""

    BoxLayout:
        size_hint_y: None
        height: dp(20) if root.alerte else 0
        opacity: 1 if root.alerte else 0
        Label:
            text: "! Actu récente pouvant impacter le cours" if root.alerte else ""
            font_size: "11sp"
            bold: True
            color: 0.95, 0.65, 0.25, 1
            halign: "left"
            text_size: self.size

    Label:
        text: root.titre
        bold: True
        font_size: "14sp"
        color: 0.95, 0.96, 0.97, 1
        size_hint_y: None
        height: self.texture_size[1]
        text_size: self.width, None
        halign: "left"

    BoxLayout:
        size_hint_y: None
        height: dp(22)
        spacing: dp(8)
        Label:
            text: root.editeur + ("  •  " + root.date_txt if root.date_txt else "")
            font_size: "11sp"
            color: 0.62, 0.65, 0.70, 1
            halign: "left"
            text_size: self.size
        Label:
            markup: True
            text: ("[color=4caf50]Positif[/color]" if root.sentiment == "positif" else "[color=e05555]Négatif[/color]" if root.sentiment == "negatif" else "[color=9fa3ab]Neutre[/color]")
            font_size: "11sp"
            halign: "right"
            text_size: self.size
            size_hint_x: 0.4

    BoxLayout:
        size_hint_y: None
        height: dp(18) if root.source else 0
        Label:
            text: root.source
            font_size: "10sp"
            color: 0.45, 0.48, 0.52, 1
            halign: "left"
            text_size: self.size

<PortfolioScreen>:
    name: "portfolio"
    canvas.before:
        Color:
            rgba: 0.07, 0.08, 0.10, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"

        BoxLayout:
            size_hint_y: None
            height: dp(72)
            padding: dp(16), dp(10)
            canvas.before:
                Color:
                    rgba: 0.10, 0.11, 0.14, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            BoxLayout:
                orientation: "vertical"
                Label:
                    text: "Suivi Bourse"
                    bold: True
                    font_size: "20sp"
                    color: 0.95, 0.96, 0.97, 1
                    halign: "left"
                    text_size: self.size
                    size_hint_y: 0.55
                Label:
                    text: root.total_txt + ("   ·   MàJ " + root.derniere_maj if root.derniere_maj else "")
                    bold: True
                    font_size: "14sp"
                    color: root.total_color
                    halign: "left"
                    text_size: self.size
                    size_hint_y: 0.45

        BoxLayout:
            size_hint_y: None
            height: dp(48)
            padding: dp(10), dp(6)
            spacing: dp(8)
            PillButton:
                text: "+ Ajouter"
                on_release: root.manager.current = "add"
            GhostButton:
                text: "Rafraîchir"
                disabled: root.refreshing
                on_release: root.rafraichir()
            GhostButton:
                text: "Param."
                size_hint_x: 0.3
                on_release: root.manager.current = "settings"

        Label:
            text: root.erreur_globale
            color: 0.95, 0.65, 0.25, 1
            size_hint_y: None
            height: dp(28) if root.erreur_globale else 0
            font_size: "12sp"

        ScrollView:
            BoxLayout:
                id: liste_box
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height
                padding: dp(10), dp(4)
                spacing: dp(8)

<AddPositionScreen>:
    name: "add"
    canvas.before:
        Color:
            rgba: 0.07, 0.08, 0.10, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"
        padding: dp(24)
        spacing: dp(14)

        Label:
            text: "Nouvelle position"
            font_size: "20sp"
            bold: True
            color: 0.95, 0.96, 0.97, 1
            size_hint_y: None
            height: dp(40)
            halign: "left"
            text_size: self.size

        TextInput:
            id: ticker_input
            hint_text: "Ticker (ex: MC.PA, AAPL, TTE.PA)"
            multiline: False
            size_hint_y: None
            height: dp(50)
            background_color: 0.13, 0.145, 0.175, 1
            foreground_color: 1, 1, 1, 1
            hint_text_color: 0.55, 0.58, 0.62, 1
            padding: dp(12), dp(14)

        TextInput:
            id: quantite_input
            hint_text: "Quantité"
            multiline: False
            input_filter: "float"
            size_hint_y: None
            height: dp(50)
            background_color: 0.13, 0.145, 0.175, 1
            foreground_color: 1, 1, 1, 1
            hint_text_color: 0.55, 0.58, 0.62, 1
            padding: dp(12), dp(14)

        TextInput:
            id: pru_input
            hint_text: "Prix de revient unitaire (PRU)"
            multiline: False
            input_filter: "float"
            size_hint_y: None
            height: dp(50)
            background_color: 0.13, 0.145, 0.175, 1
            foreground_color: 1, 1, 1, 1
            hint_text_color: 0.55, 0.58, 0.62, 1
            padding: dp(12), dp(14)

        Label:
            id: erreur_label
            text: ""
            color: 0.92, 0.38, 0.38, 1
            size_hint_y: None
            height: dp(26)
            font_size: "12sp"

        BoxLayout:
            size_hint_y: None
            height: dp(50)
            spacing: dp(10)
            GhostButton:
                text: "Annuler"
                on_release: root.annuler()
            PillButton:
                text: "Ajouter"
                on_release: root.ajouter()

        SectionLabel:
            text: "OU"

        Label:
            id: import_statut_label
            text: root.import_statut_txt
            color: root.import_statut_color
            size_hint_y: None
            height: dp(26) if root.import_statut_txt else 0
            font_size: "12sp"

        GhostButton:
            text: "⇩ Importer depuis Trading212"
            size_hint_y: None
            height: dp(50)
            disabled: root.import_en_cours
            on_release: root.importer_t212()

        Widget:

<DetailScreen>:
    name: "detail"
    canvas.before:
        Color:
            rgba: 0.07, 0.08, 0.10, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"

        BoxLayout:
            size_hint_y: None
            height: dp(64)
            padding: dp(12), dp(10)
            spacing: dp(8)
            canvas.before:
                Color:
                    rgba: 0.10, 0.11, 0.14, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            GhostButton:
                text: "<"
                size_hint_x: 0.15
                on_release: root.manager.current = "portfolio"
            Label:
                text: root.nom
                bold: True
                font_size: "17sp"
                color: 0.95, 0.96, 0.97, 1
                halign: "left"
                text_size: self.size
            PillButton:
                text: "Actus"
                size_hint_x: 0.25
                on_release: root.ouvrir_actualites()
            GhostButton:
                text: "Analystes"
                size_hint_x: 0.3
                on_release: root.ouvrir_analystes()

        ScrollView:
            BoxLayout:
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height
                padding: dp(16)
                spacing: dp(10)

                Label:
                    text: root.resume_txt
                    markup: True
                    size_hint_y: None
                    height: self.texture_size[1]
                    text_size: self.width, None
                    halign: "left"
                    color: 0.90, 0.92, 0.94, 1

                Label:
                    text: root.alertes_txt
                    markup: True
                    size_hint_y: None
                    height: self.texture_size[1] if root.alertes_txt else 0
                    text_size: self.width, None
                    halign: "left"

                SectionLabel:
                    text: "ANALYSE TECHNIQUE"
                    height: dp(26) if root.technique_txt else 0

                Label:
                    text: root.technique_txt
                    markup: True
                    size_hint_y: None
                    height: self.texture_size[1]
                    text_size: self.width, None
                    halign: "left"
                    color: 0.80, 0.83, 0.86, 1

                SectionLabel:
                    text: "POINTS CLÉS — SANTÉ FINANCIÈRE"
                    height: dp(26) if root.notes_sante else 0

                Label:
                    text: root.notes_sante_txt
                    size_hint_y: None
                    height: self.texture_size[1]
                    text_size: self.width, None
                    halign: "left"
                    color: 0.80, 0.83, 0.86, 1

                SectionLabel:
                    text: "POINTS CLÉS — FIABILITÉ DIVIDENDE"
                    height: dp(26) if root.notes_div else 0

                Label:
                    text: root.notes_div_txt
                    size_hint_y: None
                    height: self.texture_size[1]
                    text_size: self.width, None
                    halign: "left"
                    color: 0.80, 0.83, 0.86, 1

                BoxLayout:
                    size_hint_y: None
                    height: dp(50)
                    padding: 0, dp(10), 0, 0
                    GhostButton:
                        text: "Supprimer la position"
                        color: 0.92, 0.38, 0.38, 1
                        on_release: root.supprimer()

<NewsScreen>:
    name: "news"
    canvas.before:
        Color:
            rgba: 0.07, 0.08, 0.10, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"

        BoxLayout:
            size_hint_y: None
            height: dp(64)
            padding: dp(12), dp(10)
            spacing: dp(8)
            canvas.before:
                Color:
                    rgba: 0.10, 0.11, 0.14, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            GhostButton:
                text: "<"
                size_hint_x: 0.15
                on_release: root.manager.current = "detail"
            Label:
                text: "Actualités — " + root.nom
                bold: True
                font_size: "16sp"
                color: 0.95, 0.96, 0.97, 1
                halign: "left"
                text_size: self.size

        Label:
            text: root.statut_txt
            color: 0.62, 0.65, 0.70, 1
            size_hint_y: None
            height: dp(30) if root.statut_txt else 0
            font_size: "12sp"

        ScrollView:
            BoxLayout:
                id: news_box
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height
                padding: dp(10), dp(4)
                spacing: dp(8)

<AnalystesScreen>:
    name: "analystes"
    canvas.before:
        Color:
            rgba: 0.07, 0.08, 0.10, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"

        BoxLayout:
            size_hint_y: None
            height: dp(64)
            padding: dp(12), dp(10)
            spacing: dp(8)
            canvas.before:
                Color:
                    rgba: 0.10, 0.11, 0.14, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            GhostButton:
                text: "<"
                size_hint_x: 0.15
                on_release: root.manager.current = "detail"
            Label:
                text: "Analystes — " + root.nom
                bold: True
                font_size: "16sp"
                color: 0.95, 0.96, 0.97, 1
                halign: "left"
                text_size: self.size

        Label:
            text: root.statut_txt
            color: 0.62, 0.65, 0.70, 1
            size_hint_y: None
            height: dp(30) if root.statut_txt else 0
            font_size: "12sp"

        ScrollView:
            BoxLayout:
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height
                padding: dp(16)
                spacing: dp(12)

                Label:
                    text: root.contenu_txt
                    markup: True
                    size_hint_y: None
                    height: self.texture_size[1]
                    text_size: self.width, None
                    halign: "left"
                    color: 0.90, 0.92, 0.94, 1

                Label:
                    text: root.source_txt
                    font_size: "11sp"
                    color: 0.45, 0.48, 0.52, 1
                    size_hint_y: None
                    height: self.texture_size[1] if root.source_txt else 0
                    text_size: self.width, None
                    halign: "left"

<SettingsScreen>:
    name: "settings"
    canvas.before:
        Color:
            rgba: 0.07, 0.08, 0.10, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"
        padding: dp(24)
        spacing: dp(14)

        Label:
            text: "Paramètres"
            font_size: "20sp"
            bold: True
            color: 0.95, 0.96, 0.97, 1
            size_hint_y: None
            height: dp(40)
            halign: "left"
            text_size: self.size

        SectionLabel:
            text: "ADRESSE DU SERVEUR (server.py)"

        TextInput:
            id: url_input
            hint_text: "http://192.168.1.X:8765"
            multiline: False
            size_hint_y: None
            height: dp(50)
            background_color: 0.13, 0.145, 0.175, 1
            foreground_color: 1, 1, 1, 1
            hint_text_color: 0.55, 0.58, 0.62, 1
            padding: dp(12), dp(14)

        Label:
            id: statut_label
            text: root.statut_txt
            color: root.statut_color
            size_hint_y: None
            height: dp(30)
            font_size: "13sp"

        BoxLayout:
            size_hint_y: None
            height: dp(50)
            spacing: dp(10)
            GhostButton:
                text: "Tester"
                on_release: root.tester()
            PillButton:
                text: "Enregistrer"
                on_release: root.enregistrer()

        Label:
            text:
                ("Cette app ne fait tourner aucun calcul localement : elle "
                "interroge un petit serveur (server.py) lancé sur ton PC, "
                "ton VPS, ou Termux. Renseigne ici son adresse IP et son "
                "port (8765 par défaut).")
            size_hint_y: None
            height: self.texture_size[1]
            text_size: self.width, None
            halign: "left"
            font_size: "12sp"
            color: 0.55, 0.58, 0.62, 1

        Widget:

        GhostButton:
            text: "< Retour au portefeuille"
            size_hint_y: None
            height: dp(50)
            on_release: root.manager.current = "portfolio"
"""


def couleur_pv(valeur):
    if valeur is None:
        return TXT_MUTED
    if valeur > 0:
        return GREEN
    if valeur < 0:
        return RED
    return WHITE


class PortfolioScreen(Screen):
    total_txt = StringProperty("")
    total_color = ListProperty(list(WHITE))
    refreshing = BooleanProperty(False)
    erreur_globale = StringProperty("")
    derniere_maj = StringProperty("")

    INTERVALLE_AUTO_REFRESH = 300  # secondes (5 minutes)
    _auto_refresh_event = None

    def on_pre_enter(self):
        self.rafraichir()

    def on_enter(self):
        # Auto-rafraîchissement tant que cet écran est affiché ; annulé
        # dans on_leave pour ne pas continuer à interroger le serveur en
        # arrière-plan une fois qu'on a quitté l'écran portefeuille.
        if self._auto_refresh_event is None:
            self._auto_refresh_event = Clock.schedule_interval(
                lambda dt: self.rafraichir(), self.INTERVALLE_AUTO_REFRESH
            )

    def on_leave(self):
        if self._auto_refresh_event is not None:
            self._auto_refresh_event.cancel()
            self._auto_refresh_event = None

    def rafraichir(self):
        if self.refreshing:
            return
        positions = storage.charger_positions()
        self.ids.liste_box.clear_widgets()
        self._rows = []
        self._total_pv = 0.0
        self._total_connu = False
        self._erreurs = 0

        if not positions:
            self.ids.liste_box.add_widget(
                Label(text="Aucune position. Appuie sur + Ajouter.",
                      size_hint_y=None, height=80, color=TXT_MUTED)
            )
            self.total_txt = ""
            return

        self.refreshing = True
        self.erreur_globale = ""

        for pos in positions:
            row = Factory.PositionRow()
            row.ticker = pos["ticker"]
            row.nom = pos["ticker"]
            row.pv_mv_txt = "…"
            quantite = pos.get("quantite")
            if quantite is not None:
                # Affichage sans décimales inutiles si quantité entière
                row.quantite_txt = f"{quantite:g}"
            self.ids.liste_box.add_widget(row)
            self._rows.append(row)

        settings = storage.charger_settings()
        server_url = settings.get("server_url", "")
        threading.Thread(target=self._lancer_pool, args=(positions, server_url), daemon=True).start()

    def _lancer_pool(self, positions, server_url):
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(api_client.analyser_position, server_url,
                                 pos["ticker"], pos["quantite"], pos["pru"]): i
                for i, pos in enumerate(positions)
            }
            for future in futures:
                i = futures[future]
                try:
                    resultat = future.result()
                except Exception as e:
                    resultat = {"ticker": positions[i]["ticker"], "erreur": str(e)}
                self._maj_ligne(i, resultat)
        self._finaliser()

    @mainthread
    def _maj_ligne(self, index, r):
        if index >= len(self._rows):
            return
        row = self._rows[index]
        row.ticker = r.get("ticker", row.ticker)
        row.nom = r.get("nom") or row.ticker

        prix = r.get("prix_actuel")
        devise = r.get("devise") or ""
        row.prix_txt = f"{prix:.2f} {devise}".strip() if prix is not None else ""

        if r.get("erreur"):
            err = r["erreur"] or ""
            if "injoignable" in err or "timeout" in err.lower():
                self._erreurs += 1
            row.pv_mv_txt = "Erreur"
            row.pv_mv_color = ORANGE
            row.accent_color = ORANGE
        elif r.get("pv_mv_eur") is not None:
            self._total_pv += r["pv_mv_eur"]
            self._total_connu = True
            signe = "+" if r["pv_mv_eur"] >= 0 else ""
            pct = r.get("pv_mv_pct")
            pct_txt = f" ({signe}{pct:.1f}%)" if pct is not None else ""
            row.pv_mv_txt = f"{signe}{r['pv_mv_eur']:.2f}{pct_txt}"
            row.pv_mv_color = couleur_pv(r["pv_mv_eur"])
            row.accent_color = couleur_pv(r["pv_mv_eur"])
        else:
            row.pv_mv_txt = "N/A"

        row.sante_txt = f"{r['note_sante']}" if r.get("note_sante") is not None else "N/A"
        row.div_txt = f"{r['note_div']}" if r.get("note_div") is not None else "N/A"
        row.verdict = r.get("verdict", "")
        row.a_des_alertes = bool(r.get("alertes"))

        row.bind(on_touch_up=lambda inst, touch, res=r:
                  self._ouvrir_detail(res) if inst.collide_point(*touch.pos) else None)

        self.total_txt = (f"PV/MV totale: {'+' if self._total_pv >= 0 else ''}{self._total_pv:.2f} €"
                           if self._total_connu else "")
        self.total_color = list(couleur_pv(self._total_pv if self._total_connu else None))

    @mainthread
    def _finaliser(self):
        self.refreshing = False
        if self._erreurs and self._erreurs == len(self._rows):
            settings = storage.charger_settings()
            self.erreur_globale = f"Serveur injoignable à {settings.get('server_url', '')} — vérifie Paramètres."
        else:
            self.erreur_globale = ""
        self.derniere_maj = datetime.now().strftime("%H:%M")

    def _ouvrir_detail(self, resultat):
        detail = self.manager.get_screen("detail")
        detail.charger(resultat)
        self.manager.transition = SlideTransition(direction="left")
        self.manager.current = "detail"


class AddPositionScreen(Screen):
    import_statut_txt = StringProperty("")
    import_statut_color = ListProperty(list(WHITE))
    import_en_cours = BooleanProperty(False)

    def annuler(self):
        self.ids.ticker_input.text = ""
        self.ids.quantite_input.text = ""
        self.ids.pru_input.text = ""
        self.ids.erreur_label.text = ""
        self.manager.current = "portfolio"

    def ajouter(self):
        ticker = self.ids.ticker_input.text.strip()
        quantite = self.ids.quantite_input.text.strip()
        pru = self.ids.pru_input.text.strip()

        if not ticker or not quantite or not pru:
            self.ids.erreur_label.text = "Tous les champs sont obligatoires."
            return
        try:
            quantite_f = float(quantite)
            pru_f = float(pru)
            if quantite_f <= 0 or pru_f <= 0:
                raise ValueError
        except ValueError:
            self.ids.erreur_label.text = "Quantité et PRU doivent être des nombres positifs."
            return

        storage.ajouter_position(ticker, quantite_f, pru_f,
                                  date_achat=datetime.now().strftime("%Y-%m-%d"))
        self.ids.ticker_input.text = ""
        self.ids.quantite_input.text = ""
        self.ids.pru_input.text = ""
        self.ids.erreur_label.text = ""
        self.manager.current = "portfolio"

    def importer_t212(self):
        self.import_en_cours = True
        self.import_statut_txt = "Import en cours..."
        self.import_statut_color = list(WHITE)
        threading.Thread(target=self._importer_t212_en_fond, daemon=True).start()

    def _importer_t212_en_fond(self):
        settings = storage.charger_settings()
        server_url = settings.get("server_url", "")
        positions, erreur = api_client.obtenir_portefeuille_t212(server_url)
        self._traiter_import(positions, erreur)

    @mainthread
    def _traiter_import(self, positions, erreur):
        self.import_en_cours = False
        if erreur:
            self.import_statut_txt = f"Échec : {erreur}"
            self.import_statut_color = list(RED)
            return
        if not positions:
            self.import_statut_txt = "Aucune position trouvée sur Trading212."
            self.import_statut_color = list(ORANGE)
            return

        existantes = {p["ticker"].upper() for p in storage.charger_positions()}
        ajoutees = 0
        non_resolus = []
        for p in positions:
            ticker = p.get("ticker", "").strip().upper()
            if not ticker or ticker in existantes:
                continue
            try:
                storage.ajouter_position(ticker, p["quantite"], p["pru"],
                                          date_achat=datetime.now().strftime("%Y-%m-%d"))
                ajoutees += 1
                existantes.add(ticker)
                # Si le ticker importé est identique au ticker T212 brut,
                # c'est que la conversion (générique + ISIN) a échoué.
                if ticker == p.get("ticker_t212_origine", "").upper():
                    non_resolus.append(ticker)
            except Exception:
                continue

        message = f"{ajoutees} position(s) importée(s) sur {len(positions)} trouvée(s)."
        if non_resolus:
            message += (f" ! {len(non_resolus)} ticker(s) non résolu(s), à corriger "
                        f"manuellement : {', '.join(non_resolus)}")
            self.import_statut_color = list(ORANGE)
        else:
            self.import_statut_color = list(GREEN)
        self.import_statut_txt = message


class DetailScreen(Screen):
    nom = StringProperty("")
    ticker = StringProperty("")
    resume_txt = StringProperty("")
    notes_sante = ListProperty([])
    notes_div = ListProperty([])
    notes_sante_txt = StringProperty("")
    notes_div_txt = StringProperty("")
    technique_txt = StringProperty("")
    analystes_txt = StringProperty("")
    alertes_txt = StringProperty("")
    _resultat = None

    def charger(self, resultat):
        self._resultat = resultat
        self.nom = resultat.get("nom") or resultat.get("ticker", "")
        self.ticker = resultat.get("ticker", "")
        r = resultat

        lignes = [f"[b]{r.get('nom')}[/b] ({r.get('ticker')})", ""]
        if r.get("erreur"):
            lignes.append(f"[color=e04c4c]Erreur: {r['erreur']}[/color]")
        else:
            positions_locales = storage.charger_positions()
            quantite_detenue = None
            for p in positions_locales:
                if p["ticker"].upper() == r.get("ticker", "").upper():
                    quantite_detenue = p.get("quantite")
                    break
            if quantite_detenue is not None:
                lignes.append(f"Quantité détenue : {quantite_detenue:g} actions")

            prix = r.get("prix_actuel")
            devise = r.get("devise") or ""
            lignes.append(f"Prix actuel : {prix:.2f} {devise}" if prix is not None else "Prix actuel : N/A")
            if r.get("valeur_position") is not None:
                lignes.append(f"Valeur position : {r['valeur_position']:.2f} {devise}")
            if r.get("pv_mv_eur") is not None:
                signe = "+" if r["pv_mv_eur"] >= 0 else ""
                pct = r.get("pv_mv_pct")
                pct_txt = f" ({signe}{pct:.1f}%)" if pct is not None else ""
                couleur = "5ecc66" if r["pv_mv_eur"] >= 0 else "e04c4c"
                lignes.append(f"[color={couleur}]PV/MV : {signe}{r['pv_mv_eur']:.2f} {devise}{pct_txt}[/color]")
            lignes.append("")
            if r.get("prochain_dividende_date"):
                lignes.append(f"Prochain détachement (ex-div) : {r['prochain_dividende_date']}")
            if r.get("prochain_dividende_montant"):
                lignes.append(f"Dividende annuel estimé : {r['prochain_dividende_montant']:.2f} / action")
            if r.get("rendement_pct") is not None:
                lignes.append(f"Rendement actuel : {r['rendement_pct']:.2f}%")
            lignes.append("")
            note_s = r.get("note_sante")
            note_d = r.get("note_div")
            lignes.append(f"Note santé financière : {note_s}/10" if note_s is not None else "Note santé financière : N/A")
            lignes.append(f"Note fiabilité dividende : {note_d}/10" if note_d is not None else "Note fiabilité dividende : N/A")
            lignes.append(f"Verdict : {r.get('verdict', '')}")

        self.resume_txt = "\n".join(lignes)
        self.notes_sante = r.get("notes_sante", [])
        self.notes_div = r.get("notes_div", [])
        self.notes_sante_txt = "\n".join(f"• {n}" for n in self.notes_sante) or "Données insuffisantes."
        self.notes_div_txt = "\n".join(f"• {n}" for n in self.notes_div) or "Données insuffisantes."

        # --- Analyse technique (SMA50/SMA200, volume) ---
        lignes_tech = []
        sma50 = r.get("sma50")
        sma200 = r.get("sma200")
        if sma50 is not None:
            lignes_tech.append(f"Moyenne mobile 50j : {sma50:.2f}")
        if sma200 is not None:
            lignes_tech.append(f"Moyenne mobile 200j : {sma200:.2f}")
        au_dessus = r.get("au_dessus_sma200")
        if au_dessus is not None:
            couleur = "5ecc66" if au_dessus else "e04c4c"
            position = "au-dessus" if au_dessus else "en-dessous"
            lignes_tech.append(f"[color={couleur}]Cours actuellement {position} de sa SMA200[/color]")
        croisement = r.get("sma50_au_dessus_sma200")
        if croisement is not None:
            couleur = "5ecc66" if croisement else "e04c4c"
            etat = "SMA50 au-dessus de la SMA200 (config. haussière)" if croisement else "SMA50 en-dessous de la SMA200 (config. baissière)"
            lignes_tech.append(f"[color={couleur}]{etat}[/color]")
        ratio_vol = r.get("ratio_volume")
        if ratio_vol is not None:
            lignes_tech.append(f"Volume vs moyenne : x{ratio_vol:.1f}")
        self.technique_txt = "\n".join(lignes_tech)

        # --- Alertes actives ---
        alertes = r.get("alertes", [])
        if alertes:
            lignes_alertes = ["[b][color=f2a63f]! ALERTES ACTIVES[/color][/b]"]
            for a in alertes:
                lignes_alertes.append(f"[color=f2a63f]• {a.get('message', '')}[/color]")
            self.alertes_txt = "\n".join(lignes_alertes)
        else:
            self.alertes_txt = ""

    def ouvrir_actualites(self):
        news_screen = self.manager.get_screen("news")
        news_screen.charger(self.ticker, self.nom)
        self.manager.transition = SlideTransition(direction="left")
        self.manager.current = "news"

    def ouvrir_analystes(self):
        analystes_screen = self.manager.get_screen("analystes")
        analystes_screen.charger(self.ticker, self.nom, self._resultat.get("devise") if self._resultat else "")
        self.manager.transition = SlideTransition(direction="left")
        self.manager.current = "analystes"

    def supprimer(self):
        if self._resultat:
            positions = storage.charger_positions()
            ticker = self._resultat.get("ticker")
            for i, p in enumerate(positions):
                if p["ticker"] == ticker:
                    storage.supprimer_position(i)
                    break
        self.manager.current = "portfolio"


class NewsScreen(Screen):
    nom = StringProperty("")
    statut_txt = StringProperty("")
    _ticker = None

    def charger(self, ticker, nom):
        self._ticker = ticker
        self.nom = nom
        self.ids.news_box.clear_widgets()
        self.statut_txt = "Chargement des actualités..."
        threading.Thread(target=self._charger_en_fond, args=(ticker,), daemon=True).start()

    def _charger_en_fond(self, ticker):
        settings = storage.charger_settings()
        server_url = settings.get("server_url", "")
        actus = api_client.obtenir_actualites(server_url, ticker)
        self._afficher(actus)

    @mainthread
    def _afficher(self, actus):
        self.ids.news_box.clear_widgets()
        if not actus:
            self.statut_txt = "Aucune actualité trouvée (ou serveur injoignable)."
            return
        self.statut_txt = ""
        for item in actus:
            row = Factory.NewsRow()
            row.titre = item.get("titre", "")
            row.editeur = item.get("editeur", "")
            row.date_txt = item.get("date", "")
            row.sentiment = item.get("sentiment", "neutre")
            row.lien = item.get("lien", "")
            row.alerte = bool(item.get("alerte", False))
            row.source = item.get("source", "")
            if row.lien:
                row.bind(on_touch_up=lambda inst, touch, url=row.lien:
                          webbrowser.open(url) if inst.collide_point(*touch.pos) else None)
            self.ids.news_box.add_widget(row)


class AnalystesScreen(Screen):
    nom = StringProperty("")
    statut_txt = StringProperty("")
    contenu_txt = StringProperty("")
    source_txt = StringProperty("")
    _ticker = None
    _devise = ""

    def charger(self, ticker, nom, devise=""):
        self._ticker = ticker
        self._devise = devise or ""
        self.nom = nom
        self.contenu_txt = ""
        self.statut_txt = "Chargement des avis analystes..."
        threading.Thread(target=self._charger_en_fond, args=(ticker,), daemon=True).start()

    def _charger_en_fond(self, ticker):
        settings = storage.charger_settings()
        server_url = settings.get("server_url", "")
        avis = api_client.obtenir_avis_analystes(server_url, ticker)
        self._afficher(avis)

    @mainthread
    def _afficher(self, avis):
        if not avis:
            self.statut_txt = "Aucun avis analyste disponible pour ce titre."
            self.contenu_txt = ""
            self.source_txt = ""
            return

        self.statut_txt = ""
        lignes = []

        # Cas Finnhub : répartition détaillée des recommandations
        if avis.get("strong_buy") is not None:
            total = avis.get("nb_analystes") or 0
            lignes.append(f"[b]{total} analyste(s)[/b]" + (f" — période {avis['periode']}" if avis.get("periode") else ""))
            lignes.append("")
            lignes.append(f"[color=4caf50]Achat fort[/color] : {avis.get('strong_buy', 0)}")
            lignes.append(f"[color=4caf50]Achat[/color] : {avis.get('buy', 0)}")
            lignes.append(f"[color=9fa3ab]Conserver[/color] : {avis.get('hold', 0)}")
            lignes.append(f"[color=e05555]Vente[/color] : {avis.get('sell', 0)}")
            lignes.append(f"[color=e05555]Vente forte[/color] : {avis.get('strong_sell', 0)}")
        # Cas repli yfinance : juste un consensus global
        elif avis.get("consensus"):
            lignes.append(f"Consensus : [b]{avis['consensus']}[/b]"
                           + (f" ({avis['nb_analystes']} analystes)" if avis.get("nb_analystes") else ""))

        prix_cible = avis.get("prix_cible_moyen")
        if prix_cible:
            lignes.append("")
            ligne_cible = f"Objectif de cours moyen : [b]{prix_cible:.2f} {self._devise}[/b]"
            if avis.get("prix_cible_bas") and avis.get("prix_cible_haut"):
                ligne_cible += f"\n(entre {avis['prix_cible_bas']:.2f} et {avis['prix_cible_haut']:.2f})"
            lignes.append(ligne_cible)

        self.contenu_txt = "\n".join(lignes) if lignes else "Données incomplètes."
        self.source_txt = f"Source : {avis.get('source', 'inconnue')}"


class SettingsScreen(Screen):
    statut_txt = StringProperty("")
    statut_color = ListProperty(list(WHITE))

    def on_pre_enter(self):
        settings = storage.charger_settings()
        self.ids.url_input.text = settings.get("server_url", "")
        self.statut_txt = ""

    def tester(self):
        url = self.ids.url_input.text.strip()
        self.statut_txt = "Test en cours..."
        self.statut_color = list(WHITE)
        threading.Thread(target=self._tester_en_fond, args=(url,), daemon=True).start()

    def _tester_en_fond(self, url):
        ok, message = api_client.tester_connexion(url)
        self._afficher_statut(ok, message)

    @mainthread
    def _afficher_statut(self, ok, message):
        self.statut_txt = message
        self.statut_color = list(GREEN) if ok else list(RED)

    def enregistrer(self):
        url = self.ids.url_input.text.strip()
        if url:
            storage.set_server_url(url)
            self.statut_txt = "Enregistré."
            self.statut_color = list(GREEN)


class SuiviBourseApp(App):
    # Écran parent de chaque écran, pour que le bouton retour Android
    # remonte dans la hiérarchie de navigation au lieu de fermer l'app.
    # Un écran absent de cette table (ex: "portfolio", l'écran racine)
    # laisse le comportement par défaut d'Android s'exécuter (fermer l'app).
    _ECRAN_PARENT = {
        "add": "portfolio",
        "settings": "portfolio",
        "detail": "portfolio",
        "news": "detail",
        "analystes": "detail",
    }

    def build(self):
        Builder.load_string(KV)
        sm = ScreenManager()
        sm.add_widget(PortfolioScreen())
        sm.add_widget(AddPositionScreen())
        sm.add_widget(DetailScreen())
        sm.add_widget(NewsScreen())
        sm.add_widget(AnalystesScreen())
        sm.add_widget(SettingsScreen())
        Window.bind(on_keyboard=self._on_keyboard)
        return sm

    def _on_keyboard(self, window, key, *args):
        # keycode 27 = touche "retour" Android (mappée sur Escape par Kivy)
        if key == 27:
            ecran_actuel = self.root.current
            ecran_parent = self._ECRAN_PARENT.get(ecran_actuel)
            if ecran_parent:
                self.root.transition = SlideTransition(direction="right")
                self.root.current = ecran_parent
                return True  # événement consommé : on ne ferme pas l'app
            return False  # sur l'écran racine : comportement Android normal (quitter)
        return False


if __name__ == "__main__":
    SuiviBourseApp().run()
