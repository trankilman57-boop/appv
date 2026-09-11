import json
import os
import ssl
import time
import threading
import base64
import urllib.request
import urllib.error
from datetime import date

import certifi

from kivy.app import App
from kivy.lang import Builder
from kivy.clock import mainthread
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.popup import Popup
from kivy.metrics import dp
from kivy.core.window import Window

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(APP_DIR, "cave.json")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
TEMP_PHOTO = os.path.join(APP_DIR, "temp_label.jpg")
PHOTOS_DIR = os.path.join(APP_DIR, "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_bottles():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_bottles(bottles):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(bottles, f, ensure_ascii=False, indent=2)


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Drinking window logic
# ---------------------------------------------------------------------------

def compute_status(bottle):
    try:
        millesime = int(bottle.get("millesime", ""))
    except (ValueError, TypeError):
        return "unknown", "Millésime inconnu"

    garde_min = bottle.get("garde_min", "")
    garde_max = bottle.get("garde_max", "")
    try:
        garde_min = int(garde_min) if garde_min not in (None, "") else None
    except (ValueError, TypeError):
        garde_min = None
    try:
        garde_max = int(garde_max) if garde_max not in (None, "") else None
    except (ValueError, TypeError):
        garde_max = None

    if garde_min is None and garde_max is None:
        return "unknown", "Fenêtre inconnue"

    age = date.today().year - millesime
    debut = garde_min if garde_min is not None else 0
    fin = garde_max if garde_max is not None else debut + 5

    if age < debut:
        restant = debut - age
        return "wait", ("Presque prêt" if restant <= 1 else f"À garder encore {restant} ans")
    if age > fin:
        depuis = age - fin
        return "late", f"Passé son pic depuis {depuis} an{'s' if depuis > 1 else ''}"
    return "now", "À boire maintenant"


STATUS_COLORS = {
    "now": (0.31, 0.35, 0.25, 1),
    "wait": (0.69, 0.54, 0.31, 1),
    "late": (0.66, 0.36, 0.23, 1),
    "unknown": (0.6, 0.6, 0.6, 1),
}


# ---------------------------------------------------------------------------
# Android camera capture (falls back gracefully off-device)
# ---------------------------------------------------------------------------

def open_camera(on_captured, on_error):
    """Opens the native camera app and saves the photo to TEMP_PHOTO.
    Uses MediaStore insertion so no FileProvider/manifest changes are needed
    (works on Android 10+; on very old versions it may fail and we surface
    a clear error instead of crashing)."""
    try:
        from jnius import autoclass
        from android import activity, mActivity  # noqa

        Intent = autoclass('android.content.Intent')
        MediaStore = autoclass('android.provider.MediaStore')
        ContentValues = autoclass('android.content.ContentValues')
        REQUEST_CODE = 4321

        resolver = mActivity.getContentResolver()
        values = ContentValues()
        values.put(MediaStore.Images.Media.DISPLAY_NAME, f"macave_{int(time.time())}.jpg")
        values.put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
        uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
        if uri is None:
            on_error("Impossible de préparer le stockage pour la photo.")
            return

        def on_activity_result(request_code, result_code, intent):
            if request_code != REQUEST_CODE:
                return
            try:
                activity.unbind(on_activity_result=on_activity_result)
                _save_uri_to_file(uri)
                on_picked_mainthread(TEMP_PHOTO)
            except Exception as e:
                on_error_mainthread(f"Erreur lecture photo : {e}")

        @mainthread
        def on_picked_mainthread(path):
            on_captured(path)

        @mainthread
        def on_error_mainthread(msg):
            on_error(msg)

        activity.bind(on_activity_result=on_activity_result)
        intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
        intent.putExtra(MediaStore.EXTRA_OUTPUT, uri)
        mActivity.startActivityForResult(intent, REQUEST_CODE)
    except Exception as e:
        on_error(f"Appareil photo indisponible sur cet appareil ({e}).")


def pick_image_from_gallery(on_picked, on_error):
    try:
        from jnius import autoclass
        from android import activity, mActivity  # noqa

        Intent = autoclass('android.content.Intent')
        REQUEST_CODE = 1234

        def on_activity_result(request_code, result_code, intent):
            if request_code != REQUEST_CODE:
                return
            try:
                if intent is None:
                    on_error("Aucune image sélectionnée.")
                    return
                uri = intent.getData()
                if uri is None:
                    on_error("Aucune image sélectionnée.")
                    return
                _save_uri_to_file(uri)
                activity.unbind(on_activity_result=on_activity_result)
                on_picked(TEMP_PHOTO)
            except Exception as e:
                on_error(f"Erreur lecture image : {e}")

        activity.bind(on_activity_result=on_activity_result)
        intent = Intent(Intent.ACTION_GET_CONTENT)
        intent.setType("image/*")
        mActivity.startActivityForResult(intent, REQUEST_CODE)
    except Exception as e:
        on_error(f"Sélection d'image indisponible sur cet appareil ({e}).")


def _save_uri_to_file(uri):
    from jnius import autoclass
    from android import mActivity

    BitmapFactory = autoclass('android.graphics.BitmapFactory')
    CompressFormat = autoclass('android.graphics.Bitmap$CompressFormat')
    FileOutputStream = autoclass('java.io.FileOutputStream')

    resolver = mActivity.getContentResolver()
    input_stream = resolver.openInputStream(uri)
    bitmap = BitmapFactory.decodeStream(input_stream)
    input_stream.close()

    out = FileOutputStream(TEMP_PHOTO)
    bitmap.compress(CompressFormat.JPEG, 85, out)
    out.close()


# ---------------------------------------------------------------------------
# Gemini API (free tier) - stdlib only, with proper SSL cert bundle
# ---------------------------------------------------------------------------

PROMPT = """Tu es un sommelier expert. Analyse cette photo d'étiquette de vin et réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ni après, sans balises markdown, avec exactement ces clés :
{
  "nom": "nom du domaine/château/producteur",
  "appellation": "appellation ou dénomination",
  "millesime": "année en 4 chiffres ou vide si illisible",
  "cepage": "cépage(s) principal(aux), ou meilleure estimation selon l'appellation",
  "region": "région viticole",
  "garde_min": "nombre d'années après le millésime avant que ce vin soit à son meilleur (entier)",
  "garde_max": "nombre d'années après le millésime jusqu'à la fin de la fenêtre optimale (entier)",
  "prix_estime": "estimation du prix de vente moyen en euros (nombre seul)",
  "note_ia": "une ou deux phrases sur le style du vin et la raison de cette fenêtre de garde"
}
Fais ta meilleure estimation d'expert plutôt que de laisser un champ vide, sauf pour le nom et le millésime où l'exactitude prime."""


def test_api_key(api_key, on_success, on_error):
    def worker():
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
                json.loads(resp.read().decode("utf-8"))
            on_success_mainthread()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            on_error_mainthread(f"Clé refusée ({e.code}) : {body[:150]}")
        except Exception as e:
            on_error_mainthread(f"Échec de connexion : {e}")

    @mainthread
    def on_success_mainthread():
        on_success()

    @mainthread
    def on_error_mainthread(msg):
        on_error(msg)

    threading.Thread(target=worker, daemon=True).start()


def analyze_label(image_path, api_key, on_success, on_error):
    def worker():
        try:
            with open(image_path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("ascii")

            payload = {
                "contents": [{
                    "parts": [
                        {"text": PROMPT},
                        {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                    ]
                }],
                "generationConfig": {"temperature": 0.2},
            }
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.0-flash:generateContent?key={api_key}"
            )
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            candidates = data.get("candidates", [])
            if not candidates:
                on_error_mainthread("Réponse vide de l'API (photo peu lisible ?).")
                return
            text_block = candidates[0]["content"]["parts"][0]["text"]
            clean = text_block.strip().replace("```json", "").replace("```", "").strip()
            parsed = json.loads(clean)
            on_success_mainthread(parsed)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            on_error_mainthread(f"Erreur API ({e.code}) : {body[:200]}")
        except Exception as e:
            on_error_mainthread(f"Échec de l'analyse : {e}")

    @mainthread
    def on_success_mainthread(parsed):
        on_success(parsed)

    @mainthread
    def on_error_mainthread(msg):
        on_error(msg)

    threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

KV = """
#:import dp kivy.metrics.dp

<RoundCard@BoxLayout>:
    canvas.before:
        Color:
            rgba: 0.945, 0.914, 0.859, 1
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]

<AccentBar@Widget>:
    bar_color: 0.6, 0.6, 0.6, 1
    size_hint_x: None
    width: dp(5)
    canvas:
        Color:
            rgba: self.bar_color
        Rectangle:
            pos: self.pos
            size: self.size

<StyledInput@TextInput>:
    background_color: 1, 1, 1, 1
    foreground_color: 0.17, 0.13, 0.11, 1
    cursor_color: 0.29, 0.07, 0.13, 1
    padding: [dp(10), dp(10), dp(10), dp(10)]
    size_hint_y: None
    height: dp(44)
    multiline: False

<PrimaryButton@Button>:
    background_normal: ''
    background_color: 0.29, 0.07, 0.13, 1
    color: 0.945, 0.914, 0.859, 1
    bold: True
    size_hint_y: None
    height: dp(46)

<GhostButton@Button>:
    background_normal: ''
    background_color: 0, 0, 0, 0
    color: 0.29, 0.07, 0.13, 1
    size_hint_y: None
    height: dp(36)

<RootScreen>:
    canvas.before:
        Color:
            rgba: 0.945, 0.914, 0.859, 1
        Rectangle:
            pos: self.pos
            size: self.size

    BoxLayout:
        orientation: 'vertical'
        padding: dp(16)
        spacing: dp(12)

        BoxLayout:
            size_hint_y: None
            height: dp(56)
            Label:
                text: 'Ma Cave'
                font_size: dp(28)
                bold: True
                color: 0.29, 0.07, 0.13, 1
                halign: 'left'
                valign: 'middle'
                text_size: self.size
            GhostButton:
                text: 'Cle API'
                size_hint_x: None
                width: dp(90)
                on_release: root.open_settings()

        ScrollView:
            do_scroll_x: False
            BoxLayout:
                id: content
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                spacing: dp(14)
                padding: [0, 0, 0, dp(80)]
"""


class RootScreen(Screen):
    def open_settings(self):
        App.get_running_app().show_settings_popup()


class WineApp(App):
    def build(self):
        self.title = "Ma Cave"
        self.bottles = load_bottles()
        self.settings = load_settings()
        self.pending_photo = None
        self._pending_note_ia = ""
        Builder.load_string(KV)
        self.root_screen = RootScreen()
        sm = ScreenManager()
        sm.add_widget(self.root_screen)
        self.build_content()
        return sm

    # -- top-level content builder -----------------------------------
    def build_content(self):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.factory import Factory

        content = self.root_screen.ids.content
        content.clear_widgets()

        # Add-bottle card
        add_card = Factory.RoundCard(orientation="vertical", size_hint_y=None,
                                      padding=dp(14), spacing=dp(8))
        add_card.bind(minimum_height=add_card.setter('height'))

        title = Label(text="Ajouter une bouteille", bold=True, font_size=dp(17),
                       color=(0.29, 0.07, 0.13, 1), size_hint_y=None, height=dp(28),
                       halign="left", valign="middle")
        title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        add_card.add_widget(title)

        photo_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        camera_btn = Factory.PrimaryButton(text="Prendre une photo")
        camera_btn.bind(on_release=lambda *_: self.take_photo())
        photo_row.add_widget(camera_btn)
        gallery_btn = Factory.PrimaryButton(text="Galerie")
        gallery_btn.size_hint_x = 0.45
        gallery_btn.bind(on_release=lambda *_: self.pick_photo())
        photo_row.add_widget(gallery_btn)
        add_card.add_widget(photo_row)

        self.photo_status_label = Label(text="", size_hint_y=None, height=dp(20),
                                         font_size=dp(12), color=(0.29, 0.07, 0.13, 1))
        add_card.add_widget(self.photo_status_label)

        analyze_btn = Factory.PrimaryButton(text="Analyser l'etiquette (IA)")
        analyze_btn.bind(on_release=lambda *_: self.analyze_photo())
        add_card.add_widget(analyze_btn)

        self.inputs = {}
        for key, hint in [
            ("nom", "Nom du vin / domaine"),
            ("appellation", "Appellation"),
            ("millesime", "Millesime"),
            ("cepage", "Cepage"),
            ("region", "Region"),
            ("garde_min", "Garde min (ans)"),
            ("garde_max", "Garde max (ans)"),
            ("prix_paye", "Prix paye (EUR)"),
            ("prix_estime", "Prix estime (EUR)"),
        ]:
            ti = Factory.StyledInput(hint_text=hint)
            self.inputs[key] = ti
            add_card.add_widget(ti)

        add_btn = Factory.PrimaryButton(text="Ajouter a la cave")
        add_btn.bind(on_release=lambda *_: self.add_bottle())
        add_card.add_widget(add_btn)

        content.add_widget(add_card)

        section = Label(text=f"La cave ({len(self.bottles)})", bold=True, font_size=dp(20),
                         color=(0.29, 0.07, 0.13, 1), size_hint_y=None, height=dp(30),
                         halign="left", valign="middle")
        section.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(section)

        if not self.bottles:
            empty = Label(text="Aucune bouteille pour l'instant.", size_hint_y=None,
                           height=dp(60), color=(0.29, 0.07, 0.13, 0.6))
            content.add_widget(empty)

        sorted_bottles = sorted(
            self.bottles,
            key=lambda b: {"now": 0, "wait": 1, "late": 2, "unknown": 3}[compute_status(b)[0]],
        )
        for bottle in sorted_bottles:
            content.add_widget(self.build_bottle_card(bottle))

    def build_bottle_card(self, bottle):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.image import Image as KivyImage
        from kivy.factory import Factory

        status, label = compute_status(bottle)
        has_photo = bottle.get("photo_path") and os.path.exists(bottle["photo_path"])
        card_height = dp(150) if not has_photo else dp(190)

        outer = BoxLayout(orientation="horizontal", size_hint_y=None, height=card_height)
        bar = Factory.AccentBar()
        bar.bar_color = STATUS_COLORS[status]
        outer.add_widget(bar)

        card = Factory.RoundCard(orientation="vertical", padding=dp(12), spacing=dp(4))

        if has_photo:
            img = KivyImage(source=bottle["photo_path"], size_hint_y=None, height=dp(90),
                             allow_stretch=True, keep_ratio=True)
            card.add_widget(img)

        top = BoxLayout(size_hint_y=None, height=dp(26))
        name_lbl = Label(text=f'{bottle.get("nom","?")}', bold=True, font_size=dp(16),
                          color=(0.17, 0.13, 0.11, 1), halign="left", valign="middle")
        name_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        top.add_widget(name_lbl)
        millesime_lbl = Label(text=bottle.get("millesime", "") or "", bold=True,
                               font_size=dp(18), color=(0.29, 0.07, 0.13, 1),
                               size_hint_x=None, width=dp(60))
        top.add_widget(millesime_lbl)
        del_btn = Factory.GhostButton(text="X", size_hint_x=None, width=dp(36))
        del_btn.bind(on_release=lambda *_: self.remove_bottle(bottle))
        top.add_widget(del_btn)
        card.add_widget(top)

        meta = f'{bottle.get("appellation","")}  ·  {bottle.get("cepage","")}'
        meta_lbl = Label(text=meta, font_size=dp(12), color=(0.17, 0.13, 0.11, 0.7),
                          size_hint_y=None, height=dp(20), halign="left", valign="middle")
        meta_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        card.add_widget(meta_lbl)

        prix = []
        if bottle.get("prix_paye"):
            prix.append(f'Paye: {bottle["prix_paye"]} EUR')
        if bottle.get("prix_estime"):
            prix.append(f'Estime: {bottle["prix_estime"]} EUR')
        prix_lbl = Label(text="  ·  ".join(prix), font_size=dp(12),
                          color=(0.17, 0.13, 0.11, 0.85), size_hint_y=None, height=dp(20),
                          halign="left", valign="middle")
        prix_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        card.add_widget(prix_lbl)

        status_lbl = Label(text=label, font_size=dp(13), bold=True,
                            color=STATUS_COLORS[status], size_hint_y=None, height=dp(22),
                            halign="left", valign="middle")
        status_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        card.add_widget(status_lbl)

        if bottle.get("note_ia"):
            note_lbl = Label(text=bottle["note_ia"], font_size=dp(11),
                              color=(0.17, 0.13, 0.11, 0.65), size_hint_y=None, height=dp(30),
                              halign="left", valign="top")
            note_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            card.add_widget(note_lbl)

        outer.add_widget(card)
        return outer

    # -- photo picking / analysis -------------------------------------
    def take_photo(self):
        self.photo_status_label.text = "Ouverture de l'appareil photo..."

        def on_captured(path):
            self.pending_photo = path
            self.photo_status_label.text = "Photo prete. Appuyez sur Analyser."

        def on_error(msg):
            self.photo_status_label.text = msg

        open_camera(on_captured, on_error)

    def pick_photo(self):
        self.photo_status_label.text = "Ouverture de la galerie..."

        def on_picked(path):
            self.pending_photo = path
            self.photo_status_label.text = "Photo prete. Appuyez sur Analyser."

        def on_error(msg):
            self.photo_status_label.text = msg

        pick_image_from_gallery(on_picked, on_error)

    def analyze_photo(self):
        if not self.pending_photo:
            self.photo_status_label.text = "Choisissez d'abord une photo."
            return
        api_key = self.settings.get("api_key", "").strip()
        if not api_key:
            self.photo_status_label.text = "Ajoutez votre cle API (bouton en haut a droite)."
            return

        self.photo_status_label.text = "Analyse en cours..."

        def on_success(parsed):
            self.photo_status_label.text = "Analyse terminee."
            mapping = ["nom", "appellation", "millesime", "cepage", "region",
                       "garde_min", "garde_max", "prix_estime"]
            for key in mapping:
                val = parsed.get(key)
                if val not in (None, ""):
                    self.inputs[key].text = str(val)
            self._pending_note_ia = parsed.get("note_ia", "")

        def on_error(msg):
            self.photo_status_label.text = msg

        analyze_label(self.pending_photo, api_key, on_success, on_error)

    # -- CRUD -----------------------------------------------------------
    def add_bottle(self):
        nom = self.inputs["nom"].text.strip()
        if not nom:
            self.photo_status_label.text = "Indiquez au moins un nom de vin."
            return
        bottle = {k: ti.text.strip() for k, ti in self.inputs.items()}
        bottle["note_ia"] = self._pending_note_ia

        # Keep the label photo permanently attached to this bottle's card
        if self.pending_photo and os.path.exists(self.pending_photo):
            permanent_path = os.path.join(PHOTOS_DIR, f"bottle_{int(time.time()*1000)}.jpg")
            try:
                with open(self.pending_photo, "rb") as src, open(permanent_path, "wb") as dst:
                    dst.write(src.read())
                bottle["photo_path"] = permanent_path
            except Exception:
                pass

        self.bottles.insert(0, bottle)
        save_bottles(self.bottles)
        for ti in self.inputs.values():
            ti.text = ""
        self.pending_photo = None
        self._pending_note_ia = ""
        self.photo_status_label.text = ""
        self.build_content()

    def remove_bottle(self, bottle):
        photo_path = bottle.get("photo_path")
        self.bottles.remove(bottle)
        save_bottles(self.bottles)
        if photo_path and os.path.exists(photo_path):
            try:
                os.remove(photo_path)
            except Exception:
                pass
        self.build_content()

    # -- settings popup ---------------------------------------------
    def show_settings_popup(self):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.factory import Factory

        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(14))
        box.add_widget(Label(text="Cle API Gemini (gratuite)", size_hint_y=None, height=dp(24),
                              color=(0.17, 0.13, 0.11, 1)))
        key_input = Factory.StyledInput(text=self.settings.get("api_key", ""), password=True)
        box.add_widget(key_input)
        info = Label(text="Obtenue gratuitement sur aistudio.google.com/apikey. Stockee uniquement sur ce telephone.",
                     font_size=dp(11), color=(0.17, 0.13, 0.11, 0.6), size_hint_y=None, height=dp(50))
        box.add_widget(info)

        test_status = Label(text="", font_size=dp(12), color=(0.17, 0.13, 0.11, 0.8),
                             size_hint_y=None, height=dp(30))
        box.add_widget(test_status)

        btn_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        test_btn = Factory.PrimaryButton(text="Tester la cle")
        save_btn = Factory.PrimaryButton(text="Enregistrer")
        btn_row.add_widget(test_btn)
        btn_row.add_widget(save_btn)
        box.add_widget(btn_row)

        popup = Popup(title="Parametres", content=box, size_hint=(0.9, 0.55))

        def do_test(*_):
            key = key_input.text.strip()
            if not key:
                test_status.text = "Entrez d'abord une cle."
                return
            test_status.text = "Test en cours..."

            def on_success():
                test_status.text = "Cle valide, connexion OK."
                test_status.color = (0.31, 0.35, 0.25, 1)

            def on_error(msg):
                test_status.text = msg
                test_status.color = (0.66, 0.36, 0.23, 1)

            test_api_key(key, on_success, on_error)

        def do_save(*_):
            self.settings["api_key"] = key_input.text.strip()
            save_settings(self.settings)
            popup.dismiss()

        test_btn.bind(on_release=do_test)
        save_btn.bind(on_release=do_save)
        popup.open()


if __name__ == "__main__":
    Window.clearcolor = (0.945, 0.914, 0.859, 1)
    WineApp().run()
