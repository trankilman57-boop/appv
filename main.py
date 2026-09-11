import json
import os
import ssl
import time
import threading
import base64
from collections import Counter
import urllib.request
import urllib.error
from datetime import date

import certifi

from kivy.app import App
from kivy.lang import Builder
from kivy.clock import mainthread
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.popup import Popup
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.spinner import Spinner
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.core.window import Window

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(APP_DIR, "cave.json")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
TEMP_PHOTO_FRONT = os.path.join(APP_DIR, "temp_label_front.jpg")
TEMP_PHOTO_BACK = os.path.join(APP_DIR, "temp_label_back.jpg")
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
STATUS_ORDER = {"now": 0, "wait": 1, "late": 2, "unknown": 3}

TYPE_OPTIONS = ["Rouge", "Blanc", "Rose", "Petillant", "Autre"]
TYPE_COLORS = {
    "Rouge": (0.55, 0.12, 0.16, 1),
    "Blanc": (0.82, 0.72, 0.35, 1),
    "Rose": (0.86, 0.55, 0.58, 1),
    "Petillant": (0.75, 0.68, 0.4, 1),
    "Autre": (0.5, 0.5, 0.5, 1),
}


def compute_type_breakdown(bottles):
    counts = {}
    for b in bottles:
        t = b.get("type") or "Autre"
        counts[t] = counts.get(t, 0) + 1
    total = sum(counts.values()) or 1
    return [(t, counts[t], round(100 * counts[t] / total)) for t in
            sorted(counts, key=lambda k: -counts[k])]


def compute_top_terms(bottles, field, top_n=3):
    counter = Counter()
    for b in bottles:
        raw = (b.get(field) or "").strip()
        if not raw:
            continue
        if field == "cepage":
            parts = [p.strip() for p in raw.replace("/", ",").split(",") if p.strip()]
        else:
            parts = [raw]
        for p in parts:
            counter[p] += 1
    return counter.most_common(top_n)


def matches_search(bottle, query):
    if not query:
        return True
    q = query.lower().strip()
    haystack = " ".join([
        bottle.get("nom", ""), bottle.get("appellation", ""),
        bottle.get("cepage", ""), bottle.get("region", ""),
    ]).lower()
    return q in haystack


def sort_bottles(bottles, mode):
    if mode == "Nom":
        return sorted(bottles, key=lambda b: (b.get("nom") or "").lower())
    if mode == "Prix":
        def price_key(b):
            try:
                return -float(b.get("prix_paye") or b.get("prix_estime") or 0)
            except ValueError:
                return 0
        return sorted(bottles, key=price_key)
    if mode == "Millesime":
        def year_key(b):
            try:
                return -int(b.get("millesime"))
            except (ValueError, TypeError):
                return 0
        return sorted(bottles, key=year_key)
    return sorted(bottles, key=lambda b: STATUS_ORDER[compute_status(b)[0]])


# ---------------------------------------------------------------------------
# Android camera / gallery capture (falls back gracefully off-device)
# ---------------------------------------------------------------------------

def open_camera(temp_path, on_captured, on_error):
    """Opens the native camera app and saves the photo to temp_path.
    Uses MediaStore insertion so no FileProvider/manifest changes are needed
    (works on Android 10+)."""
    try:
        from jnius import autoclass
        from android import activity, mActivity  # noqa

        Intent = autoclass('android.content.Intent')
        MediaStore = autoclass('android.provider.MediaStore')
        MediaStoreImagesMedia = autoclass('android.provider.MediaStore$Images$Media')
        ContentValues = autoclass('android.content.ContentValues')
        REQUEST_CODE = 4321

        resolver = mActivity.getContentResolver()
        values = ContentValues()
        values.put("_display_name", f"macave_{int(time.time())}.jpg")
        values.put("mime_type", "image/jpeg")
        uri = resolver.insert(MediaStoreImagesMedia.EXTERNAL_CONTENT_URI, values)
        if uri is None:
            on_error("Impossible de préparer le stockage pour la photo.")
            return

        def on_activity_result(request_code, result_code, intent):
            if request_code != REQUEST_CODE:
                return
            try:
                activity.unbind(on_activity_result=on_activity_result)
                _save_uri_to_file(uri, temp_path)
                on_picked_mainthread(temp_path)
            except Exception as e:
                on_error_mainthread(f"Erreur lecture photo : {e}")

        @mainthread
        def on_picked_mainthread(path):
            on_captured(path)

        @mainthread
        def on_error_mainthread(msg):
            on_error(msg)

        activity.bind(on_activity_result=on_activity_result)
        from jnius import cast
        intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
        intent.putExtra(MediaStore.EXTRA_OUTPUT, cast('android.os.Parcelable', uri))
        mActivity.startActivityForResult(intent, REQUEST_CODE)
    except Exception as e:
        on_error(f"Appareil photo indisponible sur cet appareil ({e}).")


def pick_image_from_gallery(temp_path, on_picked, on_error):
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
                _save_uri_to_file(uri, temp_path)
                activity.unbind(on_activity_result=on_activity_result)
                on_picked(temp_path)
            except Exception as e:
                on_error(f"Erreur lecture image : {e}")

        activity.bind(on_activity_result=on_activity_result)
        intent = Intent(Intent.ACTION_GET_CONTENT)
        intent.setType("image/*")
        mActivity.startActivityForResult(intent, REQUEST_CODE)
    except Exception as e:
        on_error(f"Sélection d'image indisponible sur cet appareil ({e}).")


def _save_uri_to_file(uri, dest_path):
    from jnius import autoclass
    from android import mActivity

    BitmapFactory = autoclass('android.graphics.BitmapFactory')
    CompressFormat = autoclass('android.graphics.Bitmap$CompressFormat')
    FileOutputStream = autoclass('java.io.FileOutputStream')

    resolver = mActivity.getContentResolver()
    input_stream = resolver.openInputStream(uri)
    bitmap = BitmapFactory.decodeStream(input_stream)
    input_stream.close()

    out = FileOutputStream(dest_path)
    bitmap.compress(CompressFormat.JPEG, 85, out)
    out.close()


def share_bottle(bottle):
    try:
        from jnius import autoclass
        from android import mActivity

        Intent = autoclass('android.content.Intent')
        status, status_label = compute_status(bottle)
        lines = [
            f'{bottle.get("nom","?")} {bottle.get("millesime","")}'.strip(),
            bottle.get("appellation", ""),
            bottle.get("cepage", ""),
            status_label,
        ]
        if bottle.get("note_ia"):
            lines.append("")
            lines.append(bottle["note_ia"])
        text = "\n".join(l for l in lines if l)

        intent = Intent(Intent.ACTION_SEND)
        intent.setType("text/plain")
        intent.putExtra(Intent.EXTRA_TEXT, text)
        chooser = Intent.createChooser(intent, "Partager cette bouteille")
        mActivity.startActivity(chooser)
    except Exception as e:
        print(f"Partage indisponible: {e}")


# ---------------------------------------------------------------------------
# Gemini API (free tier) - stdlib only, with proper SSL cert bundle
# ---------------------------------------------------------------------------

PROMPT = """Tu es un sommelier expert. Analyse cette ou ces photos d'étiquette de vin (recto, et verso si fourni) et réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ni après, sans balises markdown, avec exactement ces clés :
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
Utilise le verso s'il est fourni pour affiner le cépage exact et toute info complémentaire. Fais ta meilleure estimation d'expert plutôt que de laisser un champ vide, sauf pour le nom et le millésime où l'exactitude prime."""


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


def analyze_label(image_paths, api_key, on_success, on_error):
    def worker():
        try:
            parts = [{"text": PROMPT}]
            for path in image_paths:
                if not path or not os.path.exists(path):
                    continue
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})

            if len(parts) == 1:
                on_error_mainthread("Aucune photo valide à analyser.")
                return

            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": {"temperature": 0.2},
            }
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-3.6-flash:generateContent?key={api_key}"
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

<CardButton@ButtonBehavior+BoxLayout>:

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
            PrimaryButton:
                text: '+ Ajouter'
                size_hint_x: None
                width: dp(110)
                height: dp(40)
                on_release: root.open_add()

        ScrollView:
            do_scroll_x: False
            BoxLayout:
                id: content
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                spacing: dp(14)
                padding: [0, 0, 0, dp(80)]

<FormScreen>:
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
            height: dp(46)
            GhostButton:
                text: '< Retour'
                size_hint_x: None
                width: dp(100)
                on_release: root.go_back()

        ScrollView:
            do_scroll_x: False
            BoxLayout:
                id: form_content
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                spacing: dp(8)
                padding: [0, 0, 0, dp(80)]
"""


class RootScreen(Screen):
    def open_settings(self):
        App.get_running_app().show_settings_popup()

    def open_add(self):
        App.get_running_app().open_add_form()


class FormScreen(Screen):
    def go_back(self):
        App.get_running_app().cancel_form()


class DetailScreen(Screen):
    def show_bottle(self, bottle):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.scrollview import ScrollView
        from kivy.uix.label import Label
        from kivy.uix.image import Image as KivyImage
        from kivy.factory import Factory

        self.current_bottle = bottle
        self.clear_widgets()
        status, status_label = compute_status(bottle)

        root = BoxLayout(orientation="vertical")
        with root.canvas.before:
            Color(0.945, 0.914, 0.859, 1)
            bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, v: setattr(bg, "pos", v), size=lambda w, v: setattr(bg, "size", v))

        top_bar = BoxLayout(size_hint_y=None, height=dp(50), padding=[dp(8), 0], spacing=dp(6))
        back_btn = Factory.GhostButton(text="< Retour", size_hint_x=None, width=dp(90))
        back_btn.bind(on_release=lambda *_: setattr(App.get_running_app().sm, "current", "root"))
        top_bar.add_widget(back_btn)
        edit_btn = Factory.GhostButton(text="Modifier", size_hint_x=None, width=dp(90))
        edit_btn.bind(on_release=lambda *_: App.get_running_app().start_edit(bottle))
        top_bar.add_widget(edit_btn)
        share_btn = Factory.GhostButton(text="Partager")
        share_btn.bind(on_release=lambda *_: share_bottle(bottle))
        top_bar.add_widget(share_btn)
        root.add_widget(top_bar)

        scroll = ScrollView(do_scroll_x=False)
        content = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10),
                             padding=[dp(16), 0, dp(16), dp(30)])
        content.bind(minimum_height=content.setter("height"))

        photo_row = BoxLayout(size_hint_y=None, height=dp(200), spacing=dp(8))
        has_any_photo = False
        for key in ("photo_path", "photo_path_back"):
            p = bottle.get(key)
            if p and os.path.exists(p):
                has_any_photo = True
                img = KivyImage(source=p, allow_stretch=True, keep_ratio=True)
                photo_row.add_widget(img)
        if has_any_photo:
            content.add_widget(photo_row)

        name_lbl = Label(text=bottle.get("nom", "?"), bold=True, font_size=dp(24),
                          color=(0.29, 0.07, 0.13, 1), size_hint_y=None, height=dp(36),
                          halign="left", valign="middle")
        name_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(name_lbl)

        sub_lbl = Label(text=f'{bottle.get("appellation","")}  ·  {bottle.get("region","")}',
                         font_size=dp(14), color=(0.17, 0.13, 0.11, 0.7), size_hint_y=None,
                         height=dp(22), halign="left", valign="middle")
        sub_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(sub_lbl)

        chip_row = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
        wtype = bottle.get("type") or "Autre"
        type_chip = Label(text=wtype, size_hint_x=None, width=dp(80), font_size=dp(12),
                           bold=True, color=(1, 1, 1, 1))
        with type_chip.canvas.before:
            Color(*TYPE_COLORS.get(wtype, (0.5, 0.5, 0.5, 1)))
            chip_rect = Rectangle(pos=type_chip.pos, size=type_chip.size)
        type_chip.bind(pos=lambda w, v: setattr(chip_rect, "pos", v),
                        size=lambda w, v: setattr(chip_rect, "size", v))
        chip_row.add_widget(type_chip)
        chip_row.add_widget(Label(text=bottle.get("cepage", ""), font_size=dp(13),
                                   color=(0.17, 0.13, 0.11, 0.85)))
        content.add_widget(chip_row)

        millesime_lbl = Label(text=f'Millesime {bottle.get("millesime","?")}', font_size=dp(16),
                               color=(0.29, 0.07, 0.13, 1), bold=True, size_hint_y=None,
                               height=dp(26), halign="left", valign="middle")
        millesime_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(millesime_lbl)

        status_lbl = Label(text=status_label, font_size=dp(15), bold=True,
                            color=STATUS_COLORS[status], size_hint_y=None, height=dp(26),
                            halign="left", valign="middle")
        status_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(status_lbl)

        price_row = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(20))
        if bottle.get("prix_paye"):
            box = BoxLayout(orientation="vertical")
            box.add_widget(Label(text=f'{bottle["prix_paye"]} EUR', bold=True, font_size=dp(20),
                                  color=(0.29, 0.07, 0.13, 1)))
            box.add_widget(Label(text="Paye", font_size=dp(11), color=(0.17, 0.13, 0.11, 0.6)))
            price_row.add_widget(box)
        if bottle.get("prix_estime"):
            box = BoxLayout(orientation="vertical")
            box.add_widget(Label(text=f'{bottle["prix_estime"]} EUR', bold=True, font_size=dp(20),
                                  color=(0.69, 0.54, 0.31, 1)))
            box.add_widget(Label(text="Estime (marche)", font_size=dp(11), color=(0.17, 0.13, 0.11, 0.6)))
            price_row.add_widget(box)
        content.add_widget(price_row)

        if bottle.get("note_ia"):
            note_title = Label(text="Note du sommelier", bold=True, font_size=dp(14),
                                color=(0.29, 0.07, 0.13, 1), size_hint_y=None, height=dp(24),
                                halign="left", valign="middle")
            note_title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            content.add_widget(note_title)
            note_lbl = Label(text=bottle["note_ia"], font_size=dp(13),
                              color=(0.17, 0.13, 0.11, 0.85), size_hint_y=None,
                              halign="left", valign="top")
            note_lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
            note_lbl.bind(texture_size=lambda w, v: setattr(w, "height", v[1]))
            content.add_widget(note_lbl)

        scroll.add_widget(content)
        root.add_widget(scroll)
        self.add_widget(root)


class WineApp(App):
    def build(self):
        self.title = "Ma Cave"
        self.bottles = load_bottles()
        self.settings = load_settings()
        self.pending_photo = None
        self.pending_photo_back = None
        self._pending_note_ia = ""
        self.editing_bottle = None
        self.search_query = ""
        self.sort_mode = "Statut"

        self._request_android_permissions()

        Builder.load_string(KV)
        self.root_screen = RootScreen(name="root")
        self.detail_screen = DetailScreen(name="detail")
        self.form_screen = FormScreen(name="form")
        sm = ScreenManager()
        self.sm = sm
        sm.add_widget(self.root_screen)
        sm.add_widget(self.detail_screen)
        sm.add_widget(self.form_screen)
        self.build_content()
        return sm

    def _request_android_permissions(self):
        try:
            from android.permissions import request_permissions, Permission
            perms = []
            for name in ("CAMERA", "READ_MEDIA_IMAGES", "WRITE_EXTERNAL_STORAGE",
                         "READ_EXTERNAL_STORAGE"):
                p = getattr(Permission, name, None)
                if p:
                    perms.append(p)
            request_permissions(perms)
        except Exception as e:
            print(f"Permissions non demandees (normal hors Android) : {e}")

    # -- root screen (list only) --------------------------------
    def build_content(self):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.factory import Factory

        content = self.root_screen.ids.content
        content.clear_widgets()

        if self.bottles:
            content.add_widget(self.build_stats_bar())

        search_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        search_input = Factory.StyledInput(hint_text="Rechercher (nom, cepage, region)...",
                                            text=self.search_query)
        search_input.bind(text=lambda w, v: self._on_search_change(v))
        search_row.add_widget(search_input)
        sort_spinner = Spinner(text=self.sort_mode, values=["Statut", "Nom", "Prix", "Millesime"],
                                size_hint_x=None, width=dp(110), background_color=(1, 1, 1, 1),
                                color=(0.17, 0.13, 0.11, 1))
        sort_spinner.bind(text=lambda w, v: self._on_sort_change(v))
        search_row.add_widget(sort_spinner)
        content.add_widget(search_row)

        section = Label(text=f"La cave ({len(self.bottles)})", bold=True, font_size=dp(20),
                         color=(0.29, 0.07, 0.13, 1), size_hint_y=None, height=dp(30),
                         halign="left", valign="middle")
        section.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(section)

        self.list_container = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(14))
        self.list_container.bind(minimum_height=self.list_container.setter("height"))
        content.add_widget(self.list_container)

        self.refresh_list()

    # -- form screen (add / edit) --------------------------------
    def build_form_content(self):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.factory import Factory

        content = self.form_screen.ids.form_content
        content.clear_widgets()

        add_card = Factory.RoundCard(orientation="vertical", size_hint_y=None,
                                      padding=dp(14), spacing=dp(8))
        add_card.bind(minimum_height=add_card.setter('height'))

        title_text = "Modifier la bouteille" if self.editing_bottle else "Ajouter une bouteille"
        title = Label(text=title_text, bold=True, font_size=dp(19),
                       color=(0.29, 0.07, 0.13, 1), size_hint_y=None, height=dp(30),
                       halign="left", valign="middle")
        title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        add_card.add_widget(title)

        side_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        side_row.add_widget(Label(text="Etiquette :", font_size=dp(12), size_hint_x=None,
                                   width=dp(70), color=(0.17, 0.13, 0.11, 0.7)))
        self.photo_side_spinner = Spinner(text="Recto", values=["Recto", "Verso"],
                                           size_hint_y=None, height=dp(36),
                                           background_color=(1, 1, 1, 1),
                                           color=(0.17, 0.13, 0.11, 1))
        side_row.add_widget(self.photo_side_spinner)
        add_card.add_widget(side_row)

        photo_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        camera_btn = Factory.PrimaryButton(text="Prendre une photo")
        camera_btn.bind(on_release=lambda *_: self.take_photo())
        photo_row.add_widget(camera_btn)
        gallery_btn = Factory.PrimaryButton(text="Galerie")
        gallery_btn.size_hint_x = 0.45
        gallery_btn.bind(on_release=lambda *_: self.pick_photo())
        photo_row.add_widget(gallery_btn)
        add_card.add_widget(photo_row)

        self.photo_status_label = Label(text=self._photo_status_text(), size_hint_y=None,
                                         height=dp(40), font_size=dp(13), bold=True,
                                         color=(0.29, 0.07, 0.13, 1), halign="left", valign="top")
        self.photo_status_label.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        self.photo_status_label.bind(texture_size=lambda w, v: setattr(w, "height", max(dp(20), v[1])))
        add_card.add_widget(self.photo_status_label)

        analyze_btn = Factory.PrimaryButton(text="Analyser l'etiquette (IA)")
        analyze_btn.bind(on_release=lambda *_: self.analyze_photo())
        add_card.add_widget(analyze_btn)

        type_label = Label(text="Type de vin", font_size=dp(12), color=(0.29, 0.07, 0.13, 0.7),
                            size_hint_y=None, height=dp(18), halign="left", valign="middle")
        type_label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        add_card.add_widget(type_label)
        self.type_spinner = Spinner(text="Rouge", values=TYPE_OPTIONS, size_hint_y=None,
                                     height=dp(44), background_color=(1, 1, 1, 1),
                                     color=(0.17, 0.13, 0.11, 1))
        add_card.add_widget(self.type_spinner)

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

        save_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        save_text = "Enregistrer les modifications" if self.editing_bottle else "Ajouter a la cave"
        add_btn = Factory.PrimaryButton(text=save_text)
        add_btn.bind(on_release=lambda *_: self.add_bottle())
        save_row.add_widget(add_btn)
        cancel_btn = Factory.PrimaryButton(text="Annuler")
        cancel_btn.size_hint_x = 0.35
        cancel_btn.bind(on_release=lambda *_: self.cancel_form())
        save_row.add_widget(cancel_btn)
        add_card.add_widget(save_row)

        content.add_widget(add_card)

        if self.editing_bottle:
            for key, ti in self.inputs.items():
                ti.text = str(self.editing_bottle.get(key, "") or "")
            self.type_spinner.text = self.editing_bottle.get("type", "Rouge")

    def _photo_status_text(self):
        front = "pret" if self.pending_photo and os.path.exists(self.pending_photo) else "manquant"
        back = "pret" if self.pending_photo_back and os.path.exists(self.pending_photo_back) else "aucun"
        return f"Recto: {front}  ·  Verso: {back}"

    def _on_search_change(self, value):
        self.search_query = value
        self.refresh_list()

    def _on_sort_change(self, value):
        self.sort_mode = value
        self.refresh_list()

    def refresh_list(self):
        from kivy.uix.label import Label

        self.list_container.clear_widgets()
        filtered = [b for b in self.bottles if matches_search(b, self.search_query)]
        ordered = sort_bottles(filtered, self.sort_mode)

        if not self.bottles:
            empty = Label(text="Aucune bouteille pour l'instant.", size_hint_y=None,
                           height=dp(60), color=(0.29, 0.07, 0.13, 0.6))
            self.list_container.add_widget(empty)
            return

        if not ordered:
            empty = Label(text="Aucun resultat pour cette recherche.", size_hint_y=None,
                           height=dp(60), color=(0.29, 0.07, 0.13, 0.6))
            self.list_container.add_widget(empty)
            return

        for bottle in ordered:
            self.list_container.add_widget(self.build_bottle_card(bottle))

    def build_stats_bar(self):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.factory import Factory

        top_cepages = compute_top_terms(self.bottles, "cepage", 3)
        top_regions = compute_top_terms(self.bottles, "region", 3)
        extra_lines = 0
        if top_cepages:
            extra_lines += 1
        if top_regions:
            extra_lines += 1

        card = Factory.RoundCard(orientation="vertical", size_hint_y=None,
                                  height=dp(90) + extra_lines * dp(20),
                                  padding=dp(12), spacing=dp(4))
        title = Label(text="Profil de la cave", bold=True, font_size=dp(14),
                      color=(0.29, 0.07, 0.13, 1), size_hint_y=None, height=dp(20),
                      halign="left", valign="middle")
        title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        card.add_widget(title)

        breakdown = compute_type_breakdown(self.bottles)
        bar_row = BoxLayout(size_hint_y=None, height=dp(16), spacing=dp(2))
        for t, count, pct in breakdown:
            seg = Widget()
            seg.size_hint_x = max(pct, 4) / 100
            with seg.canvas:
                Color(*TYPE_COLORS.get(t, (0.5, 0.5, 0.5, 1)))
                rect = Rectangle(pos=seg.pos, size=seg.size)
            seg.bind(pos=lambda w, v, r=rect: setattr(r, "pos", v),
                     size=lambda w, v, r=rect: setattr(r, "size", v))
            bar_row.add_widget(seg)
        card.add_widget(bar_row)

        legend = BoxLayout(size_hint_y=None, height=dp(20), spacing=dp(10))
        for t, count, pct in breakdown[:4]:
            legend.add_widget(Label(text=f"{t} {pct}%", font_size=dp(11),
                                     color=(0.17, 0.13, 0.11, 0.8)))
        card.add_widget(legend)

        if top_cepages:
            txt = "Cepages favoris : " + ", ".join(f"{t} ({c})" for t, c in top_cepages)
            lbl = Label(text=txt, font_size=dp(11), color=(0.17, 0.13, 0.11, 0.75),
                        size_hint_y=None, height=dp(20), halign="left", valign="middle")
            lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            card.add_widget(lbl)

        if top_regions:
            txt = "Regions favorites : " + ", ".join(f"{t} ({c})" for t, c in top_regions)
            lbl = Label(text=txt, font_size=dp(11), color=(0.17, 0.13, 0.11, 0.75),
                        size_hint_y=None, height=dp(20), halign="left", valign="middle")
            lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            card.add_widget(lbl)

        return card

    def build_bottle_card(self, bottle):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.image import Image as KivyImage
        from kivy.factory import Factory

        status, label = compute_status(bottle)
        has_photo = bottle.get("photo_path") and os.path.exists(bottle["photo_path"])
        card_height = dp(150) if not has_photo else dp(190)

        outer = Factory.CardButton(orientation="horizontal", size_hint_y=None, height=card_height)
        outer.bind(on_release=lambda *_: self.open_detail(bottle))
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

    def open_detail(self, bottle):
        self.detail_screen.show_bottle(bottle)
        self.sm.current = "detail"

    def open_add_form(self):
        self._reset_form_state()
        self.build_form_content()
        self.sm.current = "form"

    def start_edit(self, bottle):
        self.editing_bottle = bottle
        self.build_form_content()
        self.pending_photo = bottle.get("photo_path")
        self.pending_photo_back = bottle.get("photo_path_back")
        self._pending_note_ia = bottle.get("note_ia", "")
        self.photo_status_label.text = self._photo_status_text()
        self.sm.current = "form"

    def cancel_form(self):
        self._reset_form_state()
        self.sm.current = "root"

    # -- photo picking / analysis -------------------------------------
    def take_photo(self):
        self.photo_status_label.text = "Ouverture de l'appareil photo..."
        target = TEMP_PHOTO_BACK if self.photo_side_spinner.text == "Verso" else TEMP_PHOTO_FRONT

        def on_captured(path):
            if self.photo_side_spinner.text == "Verso":
                self.pending_photo_back = path
            else:
                self.pending_photo = path
            self.photo_status_label.text = self._photo_status_text()
            self.photo_status_label.color = (0.29, 0.07, 0.13, 1)

        def on_error(msg):
            self.photo_status_label.text = msg
            self.photo_status_label.color = (0.75, 0.15, 0.1, 1)

        open_camera(target, on_captured, on_error)

    def pick_photo(self):
        self.photo_status_label.text = "Ouverture de la galerie..."
        target = TEMP_PHOTO_BACK if self.photo_side_spinner.text == "Verso" else TEMP_PHOTO_FRONT

        def on_picked(path):
            if self.photo_side_spinner.text == "Verso":
                self.pending_photo_back = path
            else:
                self.pending_photo = path
            self.photo_status_label.text = self._photo_status_text()
            self.photo_status_label.color = (0.29, 0.07, 0.13, 1)

        def on_error(msg):
            self.photo_status_label.text = msg
            self.photo_status_label.color = (0.75, 0.15, 0.1, 1)

        pick_image_from_gallery(target, on_picked, on_error)

    def analyze_photo(self):
        if not self.pending_photo:
            self.photo_status_label.text = "Choisissez d'abord une photo recto."
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

        analyze_label([self.pending_photo, self.pending_photo_back], api_key, on_success, on_error)

    # -- persistence helpers ---------------------------------------
    def _persist_photo(self, pending_path, suffix):
        if not pending_path or not os.path.exists(pending_path):
            return None
        if os.path.dirname(pending_path) == PHOTOS_DIR:
            return pending_path  # unchanged during edit
        permanent_path = os.path.join(PHOTOS_DIR, f"bottle_{int(time.time()*1000)}_{suffix}.jpg")
        try:
            with open(pending_path, "rb") as src, open(permanent_path, "wb") as dst:
                dst.write(src.read())
            return permanent_path
        except Exception:
            return None

    def _reset_form_state(self):
        self.pending_photo = None
        self.pending_photo_back = None
        self._pending_note_ia = ""
        self.editing_bottle = None

    # -- CRUD -----------------------------------------------------------
    def add_bottle(self):
        nom = self.inputs["nom"].text.strip()
        if not nom:
            self.photo_status_label.text = "Indiquez au moins un nom de vin."
            return

        bottle = {k: ti.text.strip() for k, ti in self.inputs.items()}
        bottle["type"] = self.type_spinner.text
        bottle["note_ia"] = self._pending_note_ia

        front_path = self._persist_photo(self.pending_photo, "front")
        if front_path:
            bottle["photo_path"] = front_path
        elif self.editing_bottle:
            bottle["photo_path"] = self.editing_bottle.get("photo_path")

        back_path = self._persist_photo(self.pending_photo_back, "back")
        if back_path:
            bottle["photo_path_back"] = back_path
        elif self.editing_bottle:
            bottle["photo_path_back"] = self.editing_bottle.get("photo_path_back")

        if self.editing_bottle is not None:
            idx = self.bottles.index(self.editing_bottle)
            self.bottles[idx] = bottle
        else:
            self.bottles.insert(0, bottle)

        save_bottles(self.bottles)
        self._reset_form_state()
        self.sm.current = "root"
        self.build_content()

    def remove_bottle(self, bottle):
        for key in ("photo_path", "photo_path_back"):
            p = bottle.get(key)
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        self.bottles.remove(bottle)
        save_bottles(self.bottles)
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
