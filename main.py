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
from kivy.clock import mainthread, Clock
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.popup import Popup
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.spinner import Spinner
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle, RoundedRectangle
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
    "now": (0.561, 0.659, 0.463, 1),      # #8FA876
    "wait": (0.851, 0.663, 0.424, 1),     # #D9A96C
    "late": (0.851, 0.533, 0.408, 1),     # #D98868
    "unknown": (0.6, 0.6, 0.6, 1),
}
STATUS_ORDER = {"now": 0, "wait": 1, "late": 2, "unknown": 3}
STATUS_SHORT = {"now": "Pret", "wait": "Attendre", "late": "Passe", "unknown": "?"}


def compute_benefice(bottle):
    try:
        paye = float(bottle.get("prix_paye") or "")
        estime = float(bottle.get("prix_estime") or "")
    except (ValueError, TypeError):
        return None
    return round(estime - paye, 2)

TYPE_OPTIONS = ["Rouge", "Blanc", "Rose", "Petillant", "Autre"]
TYPE_COLORS = {
    "Rouge": (0.757, 0.380, 0.420, 1),     # #C1616B
    "Blanc": (0.890, 0.784, 0.471, 1),     # #E3C878
    "Rose": (0.937, 0.690, 0.714, 1),      # #EFB0B6
    "Petillant": (0.851, 0.788, 0.537, 1), # #D9C989
    "Autre": (0.541, 0.541, 0.541, 1),     # #8A8A8A
}

# Brand tokens from the design handoff
CREAM = (0.984, 0.945, 0.894, 1)       # #FBF1E4
CREAM_ALT = (0.969, 0.925, 0.867, 1)   # #F7ECDD
ACCENT = (0.757, 0.482, 0.329, 1)      # #C17B54
ACCENT_DARK = (0.659, 0.384, 0.247, 1) # #A8623F
TEXT_DARK = (0.169, 0.129, 0.110, 1)   # #2B211C
TEXT_MUTED = (0.169, 0.129, 0.110, 0.6)
WHITE = (1, 1, 1, 1)


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
                    on_error_mainthread("Aucune image sélectionnée.")
                    return
                uri = intent.getData()
                if uri is None:
                    on_error_mainthread("Aucune image sélectionnée.")
                    return
                _save_uri_to_file(uri, temp_path)
                activity.unbind(on_activity_result=on_activity_result)
                on_picked_mainthread(temp_path)
            except Exception as e:
                on_error_mainthread(f"Erreur lecture image : {e}")

        @mainthread
        def on_picked_mainthread(path):
            on_picked(path)

        @mainthread
        def on_error_mainthread(msg):
            on_error(msg)

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
    Bitmap = autoclass('android.graphics.Bitmap')
    Matrix = autoclass('android.graphics.Matrix')
    ExifInterface = autoclass('android.media.ExifInterface')

    resolver = mActivity.getContentResolver()

    input_stream = resolver.openInputStream(uri)
    bitmap = BitmapFactory.decodeStream(input_stream)
    input_stream.close()

    orientation = ExifInterface.ORIENTATION_NORMAL
    try:
        exif_stream = resolver.openInputStream(uri)
        exif = ExifInterface(exif_stream)
        orientation = exif.getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
        exif_stream.close()
    except Exception:
        pass

    ROTATE_90 = 6
    ROTATE_180 = 3
    ROTATE_270 = 8
    angle = {ROTATE_90: 90, ROTATE_180: 180, ROTATE_270: 270}.get(orientation)
    if angle:
        matrix = Matrix()
        matrix.postRotate(angle)
        bitmap = Bitmap.createBitmap(bitmap, 0, 0, bitmap.getWidth(), bitmap.getHeight(), matrix, True)

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

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

KV = """
#:import dp kivy.metrics.dp

<RoundCard@BoxLayout>:
    canvas.before:
        Color:
            rgba: 1, 1, 1, 1
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(14)]

<StatCard@BoxLayout>:
    canvas.before:
        Color:
            rgba: 1, 1, 1, 1
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(14)]

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
    foreground_color: 0.169, 0.129, 0.110, 1
    cursor_color: 0.757, 0.482, 0.329, 1
    padding: [dp(10), dp(10), dp(10), dp(10)]
    size_hint_y: None
    height: dp(44)
    multiline: False

<PrimaryButton@Button>:
    background_normal: ''
    background_color: 0.757, 0.482, 0.329, 1
    color: 0.984, 0.945, 0.894, 1
    bold: True
    size_hint_y: None
    height: dp(46)

<GhostButton@Button>:
    background_normal: ''
    background_color: 0.984, 0.945, 0.894, 1
    color: 0.757, 0.482, 0.329, 1
    bold: True
    size_hint_y: None
    height: dp(40)

<CardButton@ButtonBehavior+BoxLayout>:

<TabItem@ButtonBehavior+BoxLayout>:
    orientation: 'vertical'

<ClipBox@BoxLayout>:
    canvas.before:
        StencilPush
        Rectangle:
            pos: self.pos
            size: self.size
        StencilUse
    canvas.after:
        StencilUnUse
        Rectangle:
            pos: self.pos
            size: self.size
        StencilPop

<PhotoStrip@FloatLayout>:
    size_hint_y: None
    height: dp(220)
    Image:
        source: 'assets/vineyard_bg.jpg'
        allow_stretch: True
        keep_ratio: False
        size: self.parent.size
        pos: self.parent.pos
    Widget:
        size: self.parent.size
        pos: self.parent.pos
        canvas:
            Color:
                rgba: 0.984, 0.945, 0.894, 0.4
            Rectangle:
                pos: self.pos
                size: self.size

<SplashScreen>:
    FloatLayout:
        size: root.size
        pos: root.pos
        Image:
            source: 'assets/vineyard_bg.jpg'
            allow_stretch: True
            keep_ratio: False
            size: root.size
            pos: root.pos
        Widget:
            size: root.size
            pos: root.pos
            canvas:
                Color:
                    rgba: 0.13, 0.09, 0.04, 0.38
                Rectangle:
                    pos: self.pos
                    size: self.size
        BoxLayout:
            orientation: 'vertical'
            size: root.size
            pos: root.pos
            padding: dp(24)
            Widget:
            Label:
                text: 'Ma Cave'
                font_size: dp(40)
                bold: True
                color: 1, 0.96, 0.9, 1
                size_hint_y: None
                height: dp(60)
            Label:
                text: 'Suivi de cave a vin'
                font_size: dp(15)
                color: 1, 0.96, 0.9, 0.85
                size_hint_y: None
                height: dp(24)
            Widget:

<HomeScreen>:
    canvas.before:
        Color:
            rgba: 0.969, 0.925, 0.867, 1
        Rectangle:
            pos: self.pos
            size: self.size
    ScrollView:
        do_scroll_x: False
        BoxLayout:
            id: home_content
            orientation: 'vertical'
            size_hint_y: None
            height: self.minimum_height

<CaveScreen>:
    canvas.before:
        Color:
            rgba: 0.969, 0.925, 0.867, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        padding: [dp(20), dp(56), dp(20), 0]
        ScrollView:
            do_scroll_x: False
            BoxLayout:
                id: cave_content
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                spacing: dp(12)
                padding: [0, 0, 0, dp(100)]

<ScanScreen>:
    canvas.before:
        Color:
            rgba: 0.969, 0.925, 0.867, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        padding: [dp(20), dp(56), dp(20), 0]
        ScrollView:
            do_scroll_x: False
            BoxLayout:
                id: scan_content
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                spacing: dp(14)
                padding: [0, 0, 0, dp(100)]

<ProfilScreen>:
    canvas.before:
        Color:
            rgba: 0.969, 0.925, 0.867, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        padding: [dp(20), dp(56), dp(20), 0]
        ScrollView:
            do_scroll_x: False
            BoxLayout:
                id: profil_content
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                spacing: dp(14)
                padding: [0, 0, 0, dp(100)]
"""
class SplashScreen(Screen):
    pass


class HomeScreen(Screen):
    pass


class CaveScreen(Screen):
    def open_settings(self):
        App.get_running_app().show_settings_popup()


class ScanScreen(Screen):
    pass


class ProfilScreen(Screen):
    pass


class DetailScreen(Screen):
    def show_bottle(self, bottle):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.floatlayout import FloatLayout
        from kivy.uix.scrollview import ScrollView
        from kivy.uix.label import Label
        from kivy.uix.image import Image as KivyImage
        from kivy.factory import Factory

        self.current_bottle = bottle
        self.clear_widgets()
        status, status_label = compute_status(bottle)

        root = BoxLayout(orientation="vertical")
        with root.canvas.before:
            Color(*CREAM_ALT)
            bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, v: setattr(bg, "pos", v), size=lambda w, v: setattr(bg, "size", v))

        photo_path = bottle.get("photo_path")
        header = FloatLayout(size_hint_y=None, height=dp(200))
        img_source = photo_path if (photo_path and os.path.exists(photo_path)) else 'assets/vineyard_bg.jpg'
        clip = Factory.ClipBox(size=header.size, pos=header.pos)
        header.bind(size=lambda w, v: setattr(clip, "size", v), pos=lambda w, v: setattr(clip, "pos", v))
        img = KivyImage(source=img_source, allow_stretch=True, keep_ratio=True)

        def _fit_cover(*_a):
            if not img.texture:
                return
            tw, th = img.texture.size
            cw, ch = header.size
            if not (tw and th and cw and ch):
                return
            scale = max(cw / tw, ch / th)
            img.size = (tw * scale, th * scale)
            img.pos = (header.x + (cw - img.width) / 2, header.y + (ch - img.height) / 2)

        img.bind(texture=_fit_cover)
        header.bind(size=_fit_cover, pos=_fit_cover)
        clip.add_widget(img)
        header.add_widget(clip)

        overlay = Widget(size=header.size, pos=header.pos)
        with overlay.canvas:
            Color(0.984, 0.945, 0.894, 0.4)
            overlay_rect = Rectangle(pos=overlay.pos, size=overlay.size)
        overlay.bind(pos=lambda w, v: setattr(overlay_rect, "pos", v),
                     size=lambda w, v: setattr(overlay_rect, "size", v))
        header.bind(size=lambda w, v: setattr(overlay, "size", v), pos=lambda w, v: setattr(overlay, "pos", v))
        header.add_widget(overlay)

        back_btn = Factory.GhostButton(text="< Retour", size_hint=(None, None), size=(dp(90), dp(32)))
        back_btn.bind(on_release=lambda *_: App.get_running_app().go_cave())

        def _position_back_btn(*_):
            back_btn.pos = (header.x + dp(16), header.top - dp(48))

        header.bind(pos=_position_back_btn, size=_position_back_btn)
        _position_back_btn()
        header.add_widget(back_btn)
        root.add_widget(header)

        scroll = ScrollView(do_scroll_x=False)
        content = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6),
                             padding=[dp(20), dp(16), dp(20), dp(40)])
        content.bind(minimum_height=content.setter("height"))

        name_lbl = Label(text=bottle.get("nom", "?"), bold=True, font_size=dp(24),
                          color=ACCENT_DARK, size_hint_y=None, height=dp(32),
                          halign="left", valign="middle")
        name_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(name_lbl)

        sub_lbl = Label(text=f'{bottle.get("appellation","")}  ·  {bottle.get("region","")}',
                         font_size=dp(13), color=TEXT_MUTED, size_hint_y=None,
                         height=dp(20), halign="left", valign="middle")
        sub_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(sub_lbl)

        chip_row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(10), padding=[0, dp(6), 0, 0])
        wtype = bottle.get("type") or "Autre"
        type_chip = Label(text=wtype, size_hint=(None, None), size=(dp(80), dp(24)), font_size=dp(11),
                           bold=True, color=(1, 1, 1, 1))
        with type_chip.canvas.before:
            Color(*TYPE_COLORS.get(wtype, (0.5, 0.5, 0.5, 1)))
            chip_rect = RoundedRectangle(pos=type_chip.pos, size=type_chip.size, radius=[dp(8)])
        type_chip.bind(pos=lambda w, v: setattr(chip_rect, "pos", v),
                        size=lambda w, v: setattr(chip_rect, "size", v))
        chip_row.add_widget(type_chip)
        chip_row.add_widget(Label(text=bottle.get("cepage", ""), font_size=dp(12.5), color=TEXT_DARK))
        content.add_widget(chip_row)

        millesime_lbl = Label(text=f'Millesime {bottle.get("millesime","?")}', font_size=dp(16),
                               color=ACCENT, bold=True, size_hint_y=None,
                               height=dp(24), halign="left", valign="middle")
        millesime_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(millesime_lbl)

        status_lbl = Label(text=status_label, font_size=dp(14), bold=True,
                            color=STATUS_COLORS[status], size_hint_y=None, height=dp(22),
                            halign="left", valign="middle")
        status_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(status_lbl)

        price_row = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(24), padding=[0, dp(10), 0, 0])
        if bottle.get("prix_paye"):
            box = BoxLayout(orientation="vertical")
            box.add_widget(Label(text=f'{bottle["prix_paye"]} EUR', bold=True, font_size=dp(18), color=ACCENT))
            box.add_widget(Label(text="Paye", font_size=dp(10.5), color=TEXT_MUTED))
            price_row.add_widget(box)
        if bottle.get("prix_estime"):
            box = BoxLayout(orientation="vertical")
            box.add_widget(Label(text=f'{bottle["prix_estime"]} EUR', bold=True, font_size=dp(18),
                                  color=STATUS_COLORS["wait"]))
            box.add_widget(Label(text="Estime", font_size=dp(10.5), color=TEXT_MUTED))
            price_row.add_widget(box)
        benefice = compute_benefice(bottle)
        if benefice is not None:
            sign = "+" if benefice >= 0 else ""
            bcolor = STATUS_COLORS["now"] if benefice >= 0 else STATUS_COLORS["late"]
            box = BoxLayout(orientation="vertical")
            box.add_widget(Label(text=f'{sign}{benefice:g} EUR', bold=True, font_size=dp(18), color=bcolor))
            box.add_widget(Label(text="Plus-value", font_size=dp(10.5), color=TEXT_MUTED))
            price_row.add_widget(box)
        content.add_widget(price_row)

        if bottle.get("note_ia"):
            note_title = Label(text="Note du sommelier", bold=True, font_size=dp(13), color=ACCENT,
                                size_hint_y=None, height=dp(22), halign="left", valign="middle")
            note_title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            content.add_widget(note_title)
            note_lbl = Label(text=bottle["note_ia"], font_size=dp(12.5), color=TEXT_DARK,
                              size_hint_y=None, halign="left", valign="top")
            note_lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
            note_lbl.bind(texture_size=lambda w, v: setattr(w, "height", v[1]))
            content.add_widget(note_lbl)

        action_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8), padding=[0, dp(16), 0, 0])
        edit_btn = Factory.PrimaryButton(text="Modifier")
        edit_btn.bind(on_release=lambda *_: App.get_running_app().start_edit(bottle))
        action_row.add_widget(edit_btn)
        share_btn = Factory.GhostButton(text="Partager")
        share_btn.bind(on_release=lambda *_: share_bottle(bottle))
        action_row.add_widget(share_btn)
        content.add_widget(action_row)

        scroll.add_widget(content)
        root.add_widget(scroll)
        self.add_widget(root)
def export_cave_text(bottles):
    lines = ["Ma Cave - export", ""]
    for b in bottles:
        status, status_label = compute_status(b)
        lines.append(f'{b.get("nom","?")} {b.get("millesime","")} - {b.get("appellation","")}')
        lines.append(f'  {b.get("cepage","")} | {status_label}')
        if b.get("prix_paye") or b.get("prix_estime"):
            lines.append(f'  Paye: {b.get("prix_paye","-")} EUR | Estime: {b.get("prix_estime","-")} EUR')
        lines.append("")
    return "\n".join(lines)


def share_text(title, text):
    try:
        from jnius import autoclass
        from android import mActivity
        Intent = autoclass('android.content.Intent')
        intent = Intent(Intent.ACTION_SEND)
        intent.setType("text/plain")
        intent.putExtra(Intent.EXTRA_TEXT, text)
        chooser = Intent.createChooser(intent, title)
        mActivity.startActivity(chooser)
    except Exception as e:
        print(f"Partage indisponible: {e}")


class WineApp(App):
    def build(self):
        self.title = "Ma Cave"
        self.bottles = load_bottles()
        self.settings = load_settings()
        self.pending_photo = None
        self.pending_photo_back = None
        self._pending_note_ia = ""
        self.analysis_result = None
        self.editing_bottle = None
        self.search_query = ""
        self.sort_mode = "Statut"
        self.photo_side = "Recto"

        self._request_android_permissions()

        Builder.load_string(KV)
        self.splash_screen = SplashScreen(name="splash")
        self.home_screen = HomeScreen(name="home")
        self.cave_screen = CaveScreen(name="cave")
        self.scan_screen = ScanScreen(name="scan")
        self.profil_screen = ProfilScreen(name="profil")
        self.detail_screen = DetailScreen(name="detail")

        sm = ScreenManager()
        self.sm = sm

        from kivy.uix.boxlayout import BoxLayout
        root_layout = BoxLayout(orientation="vertical")
        self.tab_bar_container = BoxLayout(size_hint_y=None, height=dp(78))

        for scr in (self.splash_screen, self.home_screen, self.cave_screen,
                    self.scan_screen, self.profil_screen, self.detail_screen):
            sm.add_widget(scr)

        root_layout.add_widget(sm)
        root_layout.add_widget(self.tab_bar_container)

        self.build_home_content()
        self.build_cave_content()
        self.build_scan_content()
        self.build_profil_content()
        self.rebuild_tab_bar()

        sm.bind(current=lambda *_: self.on_screen_change())

        Clock.schedule_once(self._go_home_safe, 1.8)
        Window.bind(on_keyboard=self._on_android_back)
        return root_layout

    def _on_android_back(self, window, key, *args):
        if key != 27:
            return False
        if self.sm.current == "detail":
            self.go_cave()
            return True
        if self.sm.current == "scan":
            self.cancel_form()
            return True
        if self.sm.current != "home":
            self.sm.current = "home"
            return True
        return False

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

    def _go_home_safe(self, dt):
        try:
            self.sm.current = "home"
        except Exception:
            import traceback
            self._show_crash_popup(traceback.format_exc())

    def _show_crash_popup(self, text):
        from kivy.uix.label import Label
        from kivy.uix.scrollview import ScrollView
        from kivy.uix.boxlayout import BoxLayout

        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(10))
        lbl = Label(text=text, size_hint_y=None, font_size=dp(11), color=(0, 0, 0, 1),
                    halign="left", valign="top")
        lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        lbl.bind(texture_size=lambda w, v: setattr(w, "height", v[1]))
        sv = ScrollView()
        sv.add_widget(lbl)
        box.add_widget(sv)
        popup = Popup(title="Erreur de demarrage", content=box, size_hint=(0.95, 0.9))
        popup.open()

    def on_screen_change(self):
        if not hasattr(self, "tab_bar_container"):
            return
        try:
            hide = self.sm.current in ("detail", "splash")
            self.tab_bar_container.height = 0 if hide else dp(78)
            self.tab_bar_container.opacity = 0 if hide else 1
            self.tab_bar_container.disabled = hide
            self.rebuild_tab_bar()
            if self.sm.current == "home":
                self.build_home_content()
        except Exception:
            import traceback
            self._show_crash_popup(traceback.format_exc())

    # -- tab bar --------------------------------------------------------
    def rebuild_tab_bar(self):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.factory import Factory

        self.tab_bar_container.clear_widgets()
        if self.tab_bar_container.height == 0:
            return

        bar = BoxLayout(orientation="horizontal", padding=[0, dp(8), 0, 0])
        with bar.canvas.before:
            Color(1, 1, 1, 0.94)
            bar_rect = Rectangle(pos=bar.pos, size=bar.size)
        bar.bind(pos=lambda w, v: setattr(bar_rect, "pos", v),
                 size=lambda w, v: setattr(bar_rect, "size", v))

        current = self.sm.current

        def make_item(icon, text, screen_name, is_scan=False):
            active = current == screen_name
            item = Factory.TabItem()
            color = ACCENT if active else TEXT_MUTED
            if is_scan:
                icon_holder = Widget(size_hint=(None, None), size=(dp(44), dp(44)),
                                      pos_hint={"center_x": 0.5})
                with icon_holder.canvas:
                    Color(*ACCENT)
                    circ = RoundedRectangle(pos=icon_holder.pos, size=icon_holder.size, radius=[dp(22)])
                icon_holder.bind(pos=lambda w, v: setattr(circ, "pos", v),
                                  size=lambda w, v: setattr(circ, "size", v))
                icon_lbl = Label(text=icon, font_size=dp(18), color=(1, 1, 1, 1),
                                  pos=icon_holder.pos, size=icon_holder.size)
                icon_holder.bind(pos=lambda w, v: setattr(icon_lbl, "pos", v),
                                  size=lambda w, v: setattr(icon_lbl, "size", v))
                icon_holder.add_widget(icon_lbl)
                icon_wrap = BoxLayout(size_hint_y=None, height=dp(44))
                icon_wrap.add_widget(Widget())
                icon_wrap.add_widget(icon_holder)
                icon_wrap.add_widget(Widget())
                icon_holder.size_hint_x = None
                icon_holder.width = dp(44)
                item.add_widget(icon_wrap)
            else:
                icon_lbl = Label(text=icon, font_size=dp(19), color=color, size_hint_y=None, height=dp(24))
                item.add_widget(icon_lbl)
            label_lbl = Label(text=text, font_size=dp(10.5), bold=True, color=color,
                               size_hint_y=None, height=dp(18))
            item.add_widget(label_lbl)
            if is_scan:
                item.bind(on_release=lambda *_: self.open_scan_for_add())
            else:
                item.bind(on_release=lambda *_: setattr(self.sm, "current", screen_name))
            return item

        bar.add_widget(make_item("Acc.", "Accueil", "home"))
        bar.add_widget(make_item("Cave", "Ma cave", "cave"))
        bar.add_widget(make_item("+", "Scanner", "scan", is_scan=True))
        bar.add_widget(make_item("Moi", "Profil", "profil"))

        self.tab_bar_container.add_widget(bar)

    def go_cave(self):
        self.sm.current = "cave"

    def open_scan_for_add(self):
        self._reset_form_state()
        self.build_scan_content()
        self.sm.current = "scan"

    # -- HOME -------------------------------------------------------
    def build_home_content(self):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.factory import Factory

        content = self.home_screen.ids.home_content
        content.clear_widgets()
        content.add_widget(Factory.PhotoStrip())

        body = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(14),
                          padding=[dp(20), dp(16), dp(20), dp(100)])
        body.bind(minimum_height=body.setter("height"))

        title = Label(text="Ma Cave", bold=True, font_size=dp(28), color=ACCENT_DARK,
                       size_hint_y=None, height=dp(36), halign="left", valign="middle")
        title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        body.add_widget(title)

        # profile card
        profile_card = Factory.RoundCard(orientation="vertical", size_hint_y=None,
                                          height=dp(96), padding=dp(14), spacing=dp(8))
        p_title = Label(text="Profil de la cave", bold=True, font_size=dp(13), color=ACCENT,
                         size_hint_y=None, height=dp(18), halign="left", valign="middle")
        p_title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        profile_card.add_widget(p_title)

        breakdown = compute_type_breakdown(self.bottles) if self.bottles else []
        bar_row = BoxLayout(size_hint_y=None, height=dp(10), spacing=dp(2))
        for t, count, pct in breakdown:
            seg = Widget()
            seg.size_hint_x = max(pct, 4) / 100
            with seg.canvas:
                Color(*TYPE_COLORS.get(t, (0.5, 0.5, 0.5, 1)))
                rect = RoundedRectangle(pos=seg.pos, size=seg.size, radius=[dp(5)])
            seg.bind(pos=lambda w, v, r=rect: setattr(r, "pos", v),
                     size=lambda w, v, r=rect: setattr(r, "size", v))
            bar_row.add_widget(seg)
        profile_card.add_widget(bar_row)

        legend = BoxLayout(size_hint_y=None, height=dp(18), spacing=dp(10))
        for t, count, pct in breakdown[:4]:
            legend.add_widget(Label(text=f"{t} {pct}%", font_size=dp(11), color=TEXT_MUTED))
        profile_card.add_widget(legend)
        body.add_widget(profile_card)

        # stat cards
        total = len(self.bottles)
        ready = sum(1 for b in self.bottles if compute_status(b)[0] == "now")
        late = sum(1 for b in self.bottles if compute_status(b)[0] == "late")

        stats_row = BoxLayout(size_hint_y=None, height=dp(76), spacing=dp(10))

        def stat_card(value, label_text, color):
            card = Factory.StatCard(orientation="vertical", padding=dp(10))
            val_lbl = Label(text=str(value), bold=True, font_size=dp(22), color=color)
            card.add_widget(val_lbl)
            card.add_widget(Label(text=label_text, font_size=dp(11), color=TEXT_MUTED))
            return card

        stats_row.add_widget(stat_card(total, "Bouteilles", ACCENT))
        stats_row.add_widget(stat_card(ready, "A boire", STATUS_COLORS["now"]))
        stats_row.add_widget(stat_card(late, "Passees", STATUS_COLORS["late"]))
        body.add_widget(stats_row)

        # recent bottles
        recent_title = Label(text="Recemment ajoutees", bold=True, font_size=dp(15), color=ACCENT,
                              size_hint_y=None, height=dp(24), halign="left", valign="middle")
        recent_title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        body.add_widget(recent_title)

        if not self.bottles:
            empty = Label(text="Aucune bouteille pour l'instant.", size_hint_y=None,
                           height=dp(40), color=TEXT_MUTED)
            body.add_widget(empty)
        else:
            for bottle in self.bottles[:2]:
                body.add_widget(self._build_compact_row(bottle))

        content.add_widget(body)

    def _build_compact_row(self, bottle):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.image import Image as KivyImage
        from kivy.factory import Factory

        status, status_label = compute_status(bottle)
        row = Factory.CardButton(orientation="horizontal", size_hint_y=None, height=dp(66),
                                  padding=dp(10), spacing=dp(10))
        row.bind(on_release=lambda *_: self.open_detail(bottle))
        with row.canvas.before:
            Color(1, 1, 1, 1)
            row_rect = RoundedRectangle(pos=row.pos, size=row.size, radius=[dp(14)])
        row.bind(pos=lambda w, v: setattr(row_rect, "pos", v),
                 size=lambda w, v: setattr(row_rect, "size", v))

        photo_path = bottle.get("photo_path")
        has_photo = photo_path and os.path.exists(photo_path)
        thumb = Factory.ClipBox(size_hint_x=None, width=dp(38))
        if has_photo:
            with thumb.canvas.before:
                Color(1, 1, 1, 1)
                thumb_bg = RoundedRectangle(pos=thumb.pos, size=thumb.size, radius=[dp(6)])
            thumb.bind(pos=lambda w, v: setattr(thumb_bg, "pos", v),
                       size=lambda w, v: setattr(thumb_bg, "size", v))
            img = KivyImage(source=photo_path, allow_stretch=True, keep_ratio=False)
            thumb.add_widget(img)
        else:
            with thumb.canvas.before:
                Color(*TYPE_COLORS.get(bottle.get("type") or "Autre", (0.5, 0.5, 0.5, 1)))
                sw_rect = RoundedRectangle(pos=thumb.pos, size=thumb.size, radius=[dp(6)])
            thumb.bind(pos=lambda w, v: setattr(sw_rect, "pos", v),
                       size=lambda w, v: setattr(sw_rect, "size", v))
        row.add_widget(thumb)

        info = BoxLayout(orientation="vertical")
        name_lbl = Label(text=bottle.get("nom", "?"), bold=True, font_size=dp(14), color=TEXT_DARK,
                          halign="left", valign="middle", shorten=True, shorten_from="right")
        name_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        info.add_widget(name_lbl)
        meta = f'{bottle.get("appellation","")} · {bottle.get("millesime","")}'
        meta_lbl = Label(text=meta, font_size=dp(11.5), color=TEXT_MUTED,
                          halign="left", valign="middle", shorten=True, shorten_from="right")
        meta_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        info.add_widget(meta_lbl)
        row.add_widget(info)

        badge = Label(text=STATUS_SHORT[status], font_size=dp(10), bold=True, color=(1, 1, 1, 1),
                      size_hint=(None, None), size=(dp(64), dp(22)))
        with badge.canvas.before:
            Color(*STATUS_COLORS[status])
            badge_rect = RoundedRectangle(pos=badge.pos, size=badge.size, radius=[dp(8)])
        badge.bind(pos=lambda w, v: setattr(badge_rect, "pos", v),
                   size=lambda w, v: setattr(badge_rect, "size", v))
        row.add_widget(badge)
        return row

    # -- CAVE (list, search, sort) ------------------------------------
    def build_cave_content(self):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.factory import Factory

        content = self.cave_screen.ids.cave_content
        content.clear_widgets()

        section = Label(text=f"Ma cave ({len(self.bottles)})", bold=True, font_size=dp(24),
                         color=ACCENT_DARK, size_hint_y=None, height=dp(34),
                         halign="left", valign="middle")
        section.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(section)

        search_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        search_input = Factory.StyledInput(hint_text="Rechercher (nom, cepage, region)...",
                                            text=self.search_query)
        search_input.bind(text=lambda w, v: self._on_search_change(v))
        search_row.add_widget(search_input)
        sort_spinner = Spinner(text=self.sort_mode, values=["Statut", "Nom", "Prix", "Millesime"],
                                size_hint_x=None, width=dp(110), background_color=(1, 1, 1, 1),
                                color=TEXT_DARK)
        sort_spinner.bind(text=lambda w, v: self._on_sort_change(v))
        search_row.add_widget(sort_spinner)
        content.add_widget(search_row)

        self.list_container = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(12))
        self.list_container.bind(minimum_height=self.list_container.setter("height"))
        content.add_widget(self.list_container)

        self.refresh_list()

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
                           height=dp(60), color=TEXT_MUTED)
            self.list_container.add_widget(empty)
            return
        if not ordered:
            empty = Label(text="Aucun resultat pour cette recherche.", size_hint_y=None,
                           height=dp(60), color=TEXT_MUTED)
            self.list_container.add_widget(empty)
            return
        for bottle in ordered:
            self.list_container.add_widget(self.build_bottle_card(bottle))

    def build_bottle_card(self, bottle):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.image import Image as KivyImage
        from kivy.factory import Factory

        status, status_label = compute_status(bottle)

        outer = Factory.CardButton(orientation="horizontal", size_hint_y=None, height=dp(78),
                                    padding=dp(10), spacing=dp(10))
        outer.bind(on_release=lambda *_: self.open_detail(bottle))
        with outer.canvas.before:
            Color(1, 1, 1, 1)
            outer_rect = RoundedRectangle(pos=outer.pos, size=outer.size, radius=[dp(14)])
        outer.bind(pos=lambda w, v: setattr(outer_rect, "pos", v),
                   size=lambda w, v: setattr(outer_rect, "size", v))

        photo_path = bottle.get("photo_path")
        has_photo = photo_path and os.path.exists(photo_path)
        thumb = Factory.ClipBox(size_hint_x=None, width=dp(40))
        if has_photo:
            with thumb.canvas.before:
                Color(1, 1, 1, 1)
                thumb_bg = RoundedRectangle(pos=thumb.pos, size=thumb.size, radius=[dp(6)])
            thumb.bind(pos=lambda w, v: setattr(thumb_bg, "pos", v),
                       size=lambda w, v: setattr(thumb_bg, "size", v))
            img = KivyImage(source=photo_path, allow_stretch=True, keep_ratio=False)
            thumb.add_widget(img)
        else:
            with thumb.canvas.before:
                Color(*TYPE_COLORS.get(bottle.get("type") or "Autre", (0.5, 0.5, 0.5, 1)))
                sw_rect = RoundedRectangle(pos=thumb.pos, size=thumb.size, radius=[dp(6)])
            thumb.bind(pos=lambda w, v: setattr(sw_rect, "pos", v),
                    size=lambda w, v: setattr(sw_rect, "size", v))
        outer.add_widget(thumb)

        info = BoxLayout(orientation="vertical", spacing=dp(3))
        top_row = BoxLayout(size_hint_y=None, height=dp(20))
        name_lbl = Label(text=bottle.get("nom", "?"), font_size=dp(14), bold=True, color=TEXT_DARK,
                          halign="left", valign="middle", shorten=True, shorten_from="right")
        name_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        top_row.add_widget(name_lbl)
        year_lbl = Label(text=bottle.get("millesime", "") or "-", font_size=dp(14), bold=True,
                          color=ACCENT, size_hint_x=None, width=dp(46), halign="right", valign="middle")
        year_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        top_row.add_widget(year_lbl)
        info.add_widget(top_row)

        meta = f'{bottle.get("appellation","")}  ·  {bottle.get("cepage","")}'
        meta_lbl = Label(text=meta, font_size=dp(11.5), color=TEXT_MUTED, size_hint_y=None, height=dp(16),
                          halign="left", valign="middle", shorten=True, shorten_from="right")
        meta_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        info.add_widget(meta_lbl)

        bottom_row = BoxLayout(size_hint_y=None, height=dp(22), spacing=dp(8))
        badge = Label(text=STATUS_SHORT[status], font_size=dp(10), bold=True, color=(1, 1, 1, 1),
                      size_hint=(None, None), size=(dp(66), dp(20)))
        with badge.canvas.before:
            Color(*STATUS_COLORS[status])
            badge_rect = RoundedRectangle(pos=badge.pos, size=badge.size, radius=[dp(8)])
        badge.bind(pos=lambda w, v: setattr(badge_rect, "pos", v),
                   size=lambda w, v: setattr(badge_rect, "size", v))
        bottom_row.add_widget(badge)
        bottom_row.add_widget(BoxLayout())

        benefice = compute_benefice(bottle)
        if benefice is not None:
            sign = "+" if benefice >= 0 else ""
            price_color = STATUS_COLORS["now"] if benefice >= 0 else STATUS_COLORS["late"]
            price_txt = f"{sign}{benefice:g} EUR"
        elif bottle.get("prix_estime"):
            price_color = TEXT_MUTED
            price_txt = f'{bottle["prix_estime"]} EUR'
        elif bottle.get("prix_paye"):
            price_color = TEXT_MUTED
            price_txt = f'{bottle["prix_paye"]} EUR'
        else:
            price_color = TEXT_MUTED
            price_txt = ""
        price_lbl = Label(text=price_txt, font_size=dp(12.5), bold=True, color=price_color,
                           size_hint_x=None, halign="right", valign="middle")
        price_lbl.bind(texture_size=lambda w, v: setattr(w, "width", v[0]))
        bottom_row.add_widget(price_lbl)
        info.add_widget(bottom_row)

        outer.add_widget(info)
        del_btn = Factory.GhostButton(text="X", size_hint=(None, None), size=(dp(26), dp(26)),
                                       font_size=dp(11))
        del_btn.bind(on_release=lambda *_: self.remove_bottle(bottle))
        outer.add_widget(del_btn)
        return outer

    def open_detail(self, bottle):
        self.detail_screen.show_bottle(bottle)
        self.sm.current = "detail"

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
        self.build_cave_content()
        if self.sm.current == "home":
            self.build_home_content()

    # -- SCAN (add / edit) --------------------------------------------
    def build_scan_content(self):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.factory import Factory

        content = self.scan_screen.ids.scan_content
        content.clear_widgets()
        is_edit = self.editing_bottle is not None

        title_text = "Modifier la bouteille" if is_edit else "Scanner une etiquette"
        title = Label(text=title_text, bold=True, font_size=dp(24), color=ACCENT_DARK,
                       size_hint_y=None, height=dp(32), halign="left", valign="middle")
        title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(title)

        photo_card = Factory.RoundCard(orientation="vertical", size_hint_y=None,
                                        padding=dp(14), spacing=dp(10))
        photo_card.bind(minimum_height=photo_card.setter("height"))

        side_row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))

        def side_btn(label_text, side_value):
            active = self.photo_side == side_value
            btn = Factory.PrimaryButton(text=label_text) if active else Factory.GhostButton(text=label_text)
            btn.height = dp(36)
            btn.bind(on_release=lambda *_: self._set_photo_side(side_value))
            return btn

        side_row.add_widget(side_btn("Recto", "Recto"))
        side_row.add_widget(side_btn("Verso", "Verso"))
        photo_card.add_widget(side_row)

        current_photo = self.pending_photo_back if self.photo_side == "Verso" else self.pending_photo
        drop_zone = Factory.ClipBox(size_hint_y=None, height=dp(190))
        with drop_zone.canvas.before:
            Color(0.973, 0.953, 0.922, 1)
            dz_rect = RoundedRectangle(pos=drop_zone.pos, size=drop_zone.size, radius=[dp(12)])
        drop_zone.bind(pos=lambda w, v: setattr(dz_rect, "pos", v),
                        size=lambda w, v: setattr(dz_rect, "size", v))
        if current_photo and os.path.exists(current_photo):
            from kivy.uix.image import Image as KivyImage
            img = KivyImage(source=current_photo, allow_stretch=True, keep_ratio=True)
            drop_zone.add_widget(img)
        else:
            placeholder = BoxLayout(orientation="vertical")
            placeholder.add_widget(Label(text="Aucune photo pour l'instant", font_size=dp(12.5),
                                          color=TEXT_MUTED))
            drop_zone.add_widget(placeholder)
        photo_card.add_widget(drop_zone)

        photo_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        camera_btn = Factory.PrimaryButton(text="Prendre une photo")
        camera_btn.bind(on_release=lambda *_: self.take_photo())
        photo_row.add_widget(camera_btn)
        gallery_btn = Factory.GhostButton(text="Galerie", size_hint_x=None, width=dp(90))
        gallery_btn.height = dp(46)
        gallery_btn.bind(on_release=lambda *_: self.pick_photo())
        photo_row.add_widget(gallery_btn)
        photo_card.add_widget(photo_row)

        self.photo_status_label = Label(text=self._photo_status_text(), size_hint_y=None,
                                         height=dp(18), font_size=dp(11.5), color=TEXT_MUTED)
        photo_card.add_widget(self.photo_status_label)

        if is_edit:
            analyze_btn = Factory.PrimaryButton(text="Analyser l'etiquette")
            analyze_btn.bind(on_release=lambda *_: self.analyze_photo())
            photo_card.add_widget(analyze_btn)

        content.add_widget(photo_card)

        show_preview = (not is_edit) and self.analysis_result
        self.inputs = {}

        type_label = Label(text="Type de vin", font_size=dp(12), color=TEXT_MUTED,
                            size_hint_y=None, height=dp(16), halign="left", valign="middle")
        type_label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(type_label)
        self.type_spinner = Spinner(text="Rouge", values=TYPE_OPTIONS, size_hint_y=None,
                                     height=dp(44), background_color=(1, 1, 1, 1), color=TEXT_DARK)
        content.add_widget(self.type_spinner)

        if show_preview:
            preview_card = Factory.RoundCard(orientation="vertical", size_hint_y=None,
                                              padding=dp(14), spacing=dp(3))
            preview_card.bind(minimum_height=preview_card.setter("height"))
            preview_title = Label(text="Apercu de l'analyse", bold=True, font_size=dp(13), color=ACCENT,
                                   size_hint_y=None, height=dp(20), halign="left", valign="middle")
            preview_title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            preview_card.add_widget(preview_title)
            self.build_analysis_preview(preview_card)
            content.add_widget(preview_card)

            prix_paye_input = Factory.StyledInput(hint_text="Prix paye (EUR)")
            self.inputs["prix_paye"] = prix_paye_input
            content.add_widget(prix_paye_input)
        else:
            form_card = Factory.RoundCard(orientation="vertical", size_hint_y=None,
                                           padding=dp(14), spacing=dp(8))
            form_card.bind(minimum_height=form_card.setter("height"))

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
                form_card.add_widget(ti)
            content.add_widget(form_card)

        save_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        save_text = "Enregistrer les modifications" if is_edit else "Ajouter a la cave"
        add_btn = Factory.PrimaryButton(text=save_text)
        add_btn.bind(on_release=lambda *_: self.add_bottle())
        save_row.add_widget(add_btn)
        cancel_btn = Factory.GhostButton(text="Annuler", size_hint_x=None, width=dp(90))
        cancel_btn.height = dp(46)
        cancel_btn.bind(on_release=lambda *_: self.cancel_form())
        save_row.add_widget(cancel_btn)
        content.add_widget(save_row)

        if is_edit:
            for key, ti in self.inputs.items():
                ti.text = str(self.editing_bottle.get(key, "") or "")
            self.type_spinner.text = self.editing_bottle.get("type", "Rouge")

    def _set_photo_side(self, side):
        self.photo_side = side
        self.build_scan_content()

    def build_analysis_preview(self, container):
        from kivy.uix.label import Label

        a = self.analysis_result

        def row(label_text, value):
            if not value:
                return
            lbl = Label(text=f"{label_text} : {value}", font_size=dp(12.5), color=TEXT_DARK,
                        size_hint_y=None, height=dp(20), halign="left", valign="middle")
            lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            container.add_widget(lbl)

        name_lbl = Label(text=str(a.get("nom") or "?"), bold=True, font_size=dp(17), color=TEXT_DARK,
                          size_hint_y=None, height=dp(26), halign="left", valign="middle")
        name_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        container.add_widget(name_lbl)

        row("Appellation", a.get("appellation"))
        row("Millesime", a.get("millesime"))
        row("Cepage", a.get("cepage"))
        row("Region", a.get("region"))
        if a.get("garde_min") or a.get("garde_max"):
            row("Garde", f'{a.get("garde_min") or "?"}-{a.get("garde_max") or "?"} ans')
        if a.get("prix_estime"):
            row("Prix estime", f'{a["prix_estime"]} EUR')

        if a.get("note_ia"):
            note_lbl = Label(text=str(a["note_ia"]), font_size=dp(11.5), color=TEXT_MUTED,
                              size_hint_y=None, halign="left", valign="top")
            note_lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
            note_lbl.bind(texture_size=lambda w, v: setattr(w, "height", v[1]))
            container.add_widget(note_lbl)

    def _photo_status_text(self):
        front = "pret" if self.pending_photo and os.path.exists(self.pending_photo) else "manquant"
        back = "pret" if self.pending_photo_back and os.path.exists(self.pending_photo_back) else "aucun"
        return f"Recto: {front}  ·  Verso: {back}"

    def start_edit(self, bottle):
        self.editing_bottle = bottle
        self.analysis_result = None
        self.photo_side = "Recto"
        self.build_scan_content()
        self.pending_photo = bottle.get("photo_path")
        self.pending_photo_back = bottle.get("photo_path_back")
        self._pending_note_ia = bottle.get("note_ia", "")
        self.photo_status_label.text = self._photo_status_text()
        self.sm.current = "scan"

    def cancel_form(self):
        self._reset_form_state()
        self.sm.current = "cave"

    # -- photo picking / analysis -------------------------------------
    def take_photo(self):
        self.photo_status_label.text = "Ouverture de l'appareil photo..."
        target = TEMP_PHOTO_BACK if self.photo_side == "Verso" else TEMP_PHOTO_FRONT

        def on_captured(path):
            if self.photo_side == "Verso":
                self.pending_photo_back = path
            else:
                self.pending_photo = path
            self.build_scan_content()
            if self.editing_bottle is None and self.pending_photo:
                self.analyze_photo()

        def on_error(msg):
            self.photo_status_label.text = msg

        open_camera(target, on_captured, on_error)

    def pick_photo(self):
        self.photo_status_label.text = "Ouverture de la galerie..."
        target = TEMP_PHOTO_BACK if self.photo_side == "Verso" else TEMP_PHOTO_FRONT

        def on_picked(path):
            if self.photo_side == "Verso":
                self.pending_photo_back = path
            else:
                self.pending_photo = path
            self.build_scan_content()
            if self.editing_bottle is None and self.pending_photo:
                self.analyze_photo()

        def on_error(msg):
            self.photo_status_label.text = msg

        pick_image_from_gallery(target, on_picked, on_error)

    def analyze_photo(self):
        if not self.pending_photo:
            self.photo_status_label.text = "Choisissez d'abord une photo recto."
            return
        api_key = self.settings.get("api_key", "").strip()
        if not api_key:
            self.photo_status_label.text = "Ajoutez votre cle API (onglet Profil)."
            return

        self.photo_status_label.text = "Analyse en cours..."

        def on_success(parsed):
            if self.editing_bottle is not None:
                self.photo_status_label.text = "Analyse terminee."
                mapping = ["nom", "appellation", "millesime", "cepage", "region",
                           "garde_min", "garde_max", "prix_estime"]
                for key in mapping:
                    val = parsed.get(key)
                    if val not in (None, ""):
                        self.inputs[key].text = str(val)
                self._pending_note_ia = parsed.get("note_ia", "")
                return

            meaningful = any(str(parsed.get(k) or "").strip()
                              for k in ("nom", "appellation", "millesime", "cepage"))
            if meaningful:
                self.analysis_result = parsed
                self.build_scan_content()
                self.photo_status_label.text = "Analyse terminee."
            else:
                self.analysis_result = None
                self.build_scan_content()
                self.photo_status_label.text = "Analyse peu concluante, completez manuellement."
                mapping = ["nom", "appellation", "millesime", "cepage", "region",
                           "garde_min", "garde_max", "prix_estime"]
                for key in mapping:
                    val = parsed.get(key)
                    if val not in (None, "") and key in self.inputs:
                        self.inputs[key].text = str(val)
                self._pending_note_ia = parsed.get("note_ia", "")

        def on_error(msg):
            self.photo_status_label.text = msg
            self.build_scan_content()

        analyze_label([self.pending_photo, self.pending_photo_back], api_key, on_success, on_error)

    # -- persistence helpers ---------------------------------------
    def _persist_photo(self, pending_path, suffix):
        if not pending_path or not os.path.exists(pending_path):
            return None
        if os.path.dirname(pending_path) == PHOTOS_DIR:
            return pending_path
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
        self.analysis_result = None
        self.editing_bottle = None
        self.photo_side = "Recto"

    # -- CRUD -----------------------------------------------------------
    def add_bottle(self):
        using_preview = (not self.editing_bottle) and self.analysis_result

        if using_preview:
            a = self.analysis_result
            nom = str(a.get("nom") or "").strip()
            if not nom:
                self.photo_status_label.text = "Analyse incomplete : nom manquant."
                return
            bottle = {
                "nom": nom,
                "appellation": str(a.get("appellation") or ""),
                "millesime": str(a.get("millesime") or ""),
                "cepage": str(a.get("cepage") or ""),
                "region": str(a.get("region") or ""),
                "garde_min": str(a.get("garde_min") or ""),
                "garde_max": str(a.get("garde_max") or ""),
                "prix_estime": str(a.get("prix_estime") or ""),
                "prix_paye": self.inputs["prix_paye"].text.strip(),
            }
            bottle["note_ia"] = a.get("note_ia", "")
        else:
            nom = self.inputs["nom"].text.strip()
            if not nom:
                self.photo_status_label.text = "Indiquez au moins un nom de vin."
                return
            bottle = {k: ti.text.strip() for k, ti in self.inputs.items()}
            bottle["note_ia"] = self._pending_note_ia

        bottle["type"] = self.type_spinner.text

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
        self.build_cave_content()
        self.build_home_content()
        self.sm.current = "cave"

    # -- PROFIL -------------------------------------------------------
    def build_profil_content(self):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.factory import Factory

        content = self.profil_screen.ids.profil_content
        content.clear_widgets()

        title = Label(text="Profil", bold=True, font_size=dp(24), color=ACCENT_DARK,
                       size_hint_y=None, height=dp(32), halign="left", valign="middle")
        title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(title)

        user_card = Factory.RoundCard(orientation="horizontal", size_hint_y=None,
                                       height=dp(76), padding=dp(14), spacing=dp(14))
        avatar = Widget(size_hint_x=None, width=dp(52))
        with avatar.canvas:
            Color(*ACCENT)
            av_rect = RoundedRectangle(pos=avatar.pos, size=avatar.size, radius=[dp(26)])
        avatar.bind(pos=lambda w, v: setattr(av_rect, "pos", v),
                    size=lambda w, v: setattr(av_rect, "size", v))
        av_label = Label(text="MC", bold=True, font_size=dp(16), color=(1, 1, 1, 1),
                          pos=avatar.pos, size=avatar.size)
        avatar.bind(pos=lambda w, v: setattr(av_label, "pos", v),
                    size=lambda w, v: setattr(av_label, "size", v))
        avatar.add_widget(av_label)
        user_card.add_widget(avatar)
        user_info = BoxLayout(orientation="vertical")
        user_info.add_widget(Label(text="Ma Cave", bold=True, font_size=dp(16), color=TEXT_DARK,
                                    halign="left", valign="middle"))
        user_info.add_widget(Label(text=f"{len(self.bottles)} bouteilles suivies", font_size=dp(12),
                                    color=TEXT_MUTED, halign="left", valign="middle"))
        user_card.add_widget(user_info)
        content.add_widget(user_card)

        settings_title = Label(text="Reglages", bold=True, font_size=dp(13), color=ACCENT,
                                size_hint_y=None, height=dp(20), halign="left", valign="middle")
        settings_title.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(settings_title)

        settings_card = Factory.RoundCard(orientation="vertical", size_hint_y=None,
                                           height=dp(3 * 50), padding=0)

        def setting_row(label_text, value_text, value_color, on_press=None):
            row = Factory.CardButton(orientation="horizontal", size_hint_y=None, height=dp(50),
                                      padding=[dp(16), 0])
            row.add_widget(Label(text=label_text, font_size=dp(13.5), color=TEXT_DARK,
                                  halign="left", valign="middle"))
            val_lbl = Label(text=value_text, font_size=dp(12), bold=True, color=value_color,
                            size_hint_x=None, width=dp(110), halign="right", valign="middle")
            val_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            row.add_widget(val_lbl)
            if on_press:
                row.bind(on_release=lambda *_: on_press())
            return row

        has_key = bool(self.settings.get("api_key", "").strip())
        settings_card.add_widget(setting_row(
            "Cle API (analyse IA)", "Connectee" if has_key else "Non configuree",
            STATUS_COLORS["now"] if has_key else STATUS_COLORS["late"],
            on_press=self.show_settings_popup))
        settings_card.add_widget(setting_row("Unite de prix", "EUR", TEXT_MUTED))
        settings_card.add_widget(setting_row(
            "Exporter la cave", ">", ACCENT,
            on_press=lambda: share_text("Exporter Ma Cave", export_cave_text(self.bottles))))

        content.add_widget(settings_card)

    # -- settings popup ---------------------------------------------
    def show_settings_popup(self):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.factory import Factory

        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(14))
        box.add_widget(Label(text="Cle API Gemini (gratuite)", size_hint_y=None, height=dp(24),
                              color=TEXT_DARK))
        key_input = Factory.StyledInput(text=self.settings.get("api_key", ""), password=True)
        box.add_widget(key_input)
        info = Label(text="Obtenue gratuitement sur aistudio.google.com/apikey. Stockee sur ce telephone.",
                     font_size=dp(11), color=TEXT_MUTED, size_hint_y=None, height=dp(50))
        box.add_widget(info)

        test_status = Label(text="", font_size=dp(12), color=TEXT_DARK, size_hint_y=None, height=dp(30))
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
                test_status.color = STATUS_COLORS["now"]

            def on_error(msg):
                test_status.text = msg
                test_status.color = STATUS_COLORS["late"]

            test_api_key(key, on_success, on_error)

        def do_save(*_):
            self.settings["api_key"] = key_input.text.strip()
            save_settings(self.settings)
            popup.dismiss()
            self.build_profil_content()

        test_btn.bind(on_release=do_test)
        save_btn.bind(on_release=do_save)
        popup.open()


if __name__ == "__main__":
    Window.clearcolor = CREAM_ALT
    WineApp().run()
