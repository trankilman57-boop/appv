import json
import os
from datetime import date

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.metrics import dp

DATA_FILE = os.path.join(os.path.dirname(__file__), "cave.json")


def load_bottles():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_bottles(bottles):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(bottles, f, ensure_ascii=False, indent=2)


def compute_status(bottle):
    try:
        millesime = int(bottle.get("millesime", ""))
        garde_min = int(bottle.get("garde_min", 0) or 0)
        garde_max = int(bottle.get("garde_max", 0) or 0)
    except ValueError:
        return "Fenêtre inconnue"
    age = date.today().year - millesime
    if garde_min and age < garde_min:
        return f"À garder encore {garde_min - age} an(s)"
    if garde_max and age > garde_max:
        return f"Passé son pic depuis {age - garde_max} an(s)"
    return "À boire maintenant"


class BottleRow(BoxLayout):
    def __init__(self, bottle, on_delete, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None, height=dp(90),
                          padding=dp(8), spacing=dp(2), **kwargs)
        title = f'{bottle.get("nom","?")} ({bottle.get("millesime","?")})'
        self.add_widget(Label(text=title, bold=True, size_hint_y=None, height=dp(24),
                               halign="left", valign="middle"))
        meta = f'{bottle.get("appellation","")} · Payé {bottle.get("prix_paye","?")} € · Estimé {bottle.get("prix_estime","?")} €'
        self.add_widget(Label(text=meta, font_size=dp(12), size_hint_y=None, height=dp(20)))
        self.add_widget(Label(text=compute_status(bottle), font_size=dp(13),
                               color=(0.7, 0.55, 0.2, 1), size_hint_y=None, height=dp(20)))
        del_btn = Button(text="Supprimer", size_hint=(None, None), size=(dp(100), dp(24)))
        del_btn.bind(on_release=lambda *_: on_delete(bottle))
        self.add_widget(del_btn)


class MaCaveApp(App):
    def build(self):
        self.bottles = load_bottles()
        root = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))

        root.add_widget(Label(text="Ma Cave", font_size=dp(24), bold=True,
                               size_hint_y=None, height=dp(40)))

        form = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(230), spacing=dp(4))
        self.nom_input = TextInput(hint_text="Nom du vin", multiline=False, size_hint_y=None, height=dp(36))
        self.appellation_input = TextInput(hint_text="Appellation", multiline=False, size_hint_y=None, height=dp(36))
        self.millesime_input = TextInput(hint_text="Millésime", multiline=False, input_filter="int",
                                          size_hint_y=None, height=dp(36))
        prix_row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(4))
        self.prix_paye_input = TextInput(hint_text="Prix payé €", multiline=False, input_filter="float")
        self.prix_estime_input = TextInput(hint_text="Prix estimé €", multiline=False, input_filter="float")
        prix_row.add_widget(self.prix_paye_input)
        prix_row.add_widget(self.prix_estime_input)
        garde_row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(4))
        self.garde_min_input = TextInput(hint_text="Garde min (ans)", multiline=False, input_filter="int")
        self.garde_max_input = TextInput(hint_text="Garde max (ans)", multiline=False, input_filter="int")
        garde_row.add_widget(self.garde_min_input)
        garde_row.add_widget(self.garde_max_input)

        add_btn = Button(text="Ajouter à la cave", size_hint_y=None, height=dp(40))
        add_btn.bind(on_release=self.add_bottle)

        form.add_widget(self.nom_input)
        form.add_widget(self.appellation_input)
        form.add_widget(self.millesime_input)
        form.add_widget(prix_row)
        form.add_widget(garde_row)
        form.add_widget(add_btn)
        root.add_widget(form)

        self.list_layout = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        self.list_layout.bind(minimum_height=self.list_layout.setter("height"))
        scroll = ScrollView()
        scroll.add_widget(self.list_layout)
        root.add_widget(scroll)

        self.refresh_list()
        return root

    def add_bottle(self, *_):
        nom = self.nom_input.text.strip()
        if not nom:
            return
        bottle = {
            "nom": nom,
            "appellation": self.appellation_input.text.strip(),
            "millesime": self.millesime_input.text.strip(),
            "prix_paye": self.prix_paye_input.text.strip(),
            "prix_estime": self.prix_estime_input.text.strip(),
            "garde_min": self.garde_min_input.text.strip(),
            "garde_max": self.garde_max_input.text.strip(),
        }
        self.bottles.insert(0, bottle)
        save_bottles(self.bottles)
        for inp in (self.nom_input, self.appellation_input, self.millesime_input,
                    self.prix_paye_input, self.prix_estime_input,
                    self.garde_min_input, self.garde_max_input):
            inp.text = ""
        self.refresh_list()

    def delete_bottle(self, bottle):
        self.bottles.remove(bottle)
        save_bottles(self.bottles)
        self.refresh_list()

    def refresh_list(self):
        self.list_layout.clear_widgets()
        for bottle in self.bottles:
            self.list_layout.add_widget(BottleRow(bottle, self.delete_bottle))


if __name__ == "__main__":
    MaCaveApp().run()
