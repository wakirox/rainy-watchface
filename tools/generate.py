#!/usr/bin/env python3
"""
Generates the Rainy watch face (Watch Face Format, resources only, no code) into watchface/src/main.

The face says the time in words, to the minute, in two centred lines ("Ten" / "oh eight.", "Le dieci" / "e otto."),
in the watch's language and following its 12/24-hour setting. The words come from tools/time_words.json:
the words that carry the number (*marked* there) are set in the heavy City voice, the rest in the light Filler voice.
Under the time is the big slot (Rainy's LONG_TEXT by default, "RAIN · Until 18:00" under the city; without Rainy on
the watch, the face's own answer from the system's weather, "RAIN · Until 18:00" under "14° Rain", see WEATHER_HOURS),
under it a round slot (sunrise / sunset), the date along the top, and utility arcs at 9 and 3 o'clock (steps, battery
by default; the user can change every slot). The face turns 25 degrees for the left or the right wrist, or stays straight (the
Orientation setting, left wrist by default; see TILTS).

Everything in watchface/src/main is written by this script, except res/drawable-nodpi/preview.png (a screenshot
of the face, see tools/build.sh --preview): change the script or the words, never the output.

  tools/generate.py           write the face
  tools/generate.py --check   exit 1 when the files on disk differ from a fresh generation
  tools/generate.py --report  also print how small the longest time lines of each language get
  --pin "it 24 14:54"                   fix the language, 12/24-hour mode and time (to render one case on a watch;
                                        the output then fails --check: run the script again without it)
  --sample                              English sample data in the slots (with --pin, for the editor preview)
  --words FILE / --out DIR              other words / output directory (for experiments)

What the Watch Face Format runtime does (probed on Wear OS 7, WFF renderer 4.0.07, 2026-10-08):
- Localisation: a Condition on [LANGUAGE_LOCALE_NAME] switches language live. res/raw-<lang>/watchface.xml is only
  read when the face loads (a locale change leaves the old language until the face is reloaded), so every language
  lives in the one watchface.xml.
- A Text holds several Font runs (one per voice) and isAutoSize scales them together; CDATA keeps the spaces between
  runs (a no-break space entity is dropped). Template parameters take nested ternaries of string literals, so each
  run is one expression over [HOUR_0_23] / [HOUR_1_12] / [MINUTE] rather than a PartText per minute.
- A line break is dropped wherever it is (its own run, inside a run, &#10; in a Template), and isAutoSize only
  shrinks single-line text (with maxLines 2 it wraps and lets the box clip the rest). So each time line is its own
  one-line PartText.
- A Group scales by an expression (Transform scaleX / scaleY, around pivotX / pivotY), but every box clips its
  content before it is scaled: the scaled time Group and its lines are much wider than the screen. Expressions have
  clamp() but no min().
- A Font run that evaluates to an empty string crashes the renderer (StringIndexOutOfBoundsException, then the system
  falls back to its own face and holds the runtime off for 90 minutes): a run that is empty at some times says
  ZERO_WIDTH_SPACE instead. U+2060 (word joiner) crashes it too.
- A plain Font text that equals a string resource name is replaced by that string: resource names here never look
  like words (watch_face_name, slot_*).
- Custom font families fall back to the system font for glyphs they lack (Devanagari), but without the weight: a
  language whose words the Latin cuts can't set uses SYNC_TO_DEVICE with weights instead.
- The fonts are static instances of Google Sans Flex: WFF can't set variable axes.
- Slots (probed on Wear OS 7 and 6, 2026-10-11): a slot's position is fixed when the face loads, and its angle turns
  its touch area with its drawing. A slot draws only inside its touch area (a side arc's band clips its text). A slot
  hidden by a setting still takes taps under WFF 4; WFF 5's ListOption complicationSlotIds turns it off, drawing and
  taps, but Wear OS 6 doesn't install a WFF 5 face.
- Weather (probed on Wear OS 7, 2026-10-12): a slot whose provider is missing, with defaultSystemProvider EMPTY, gets
  NO_DATA and draws nothing, not its EMPTY Complication. [WEATHER.TEMPERATURE_LOW] / _HIGH are in the v5 schema but
  the runtime rejects them ("Data source ... is invalid") and doesn't load the face. Booleans print "true" / "false";
  "+" adds numbers and doesn't join strings (a Template with several %s does); a ternary may mix numbers and strings.
  A Reference reads a Group's angle as its Transform sets it. The emulator's weather came after a while (first
  IS_AVAILABLE false and IS_ERROR true, the provider's server answering INTERNAL; then 24 hours and 6 days ahead).
- Settings (probed on Wear OS 7, 2026-10-12): an expression reads a ListConfiguration's option id as
  [CONFIGURATION.<id>], a string. A Reference in a ListOption's Group is no way to read one: on a Pixel Watch 4, after
  the face editor, the References of options no longer drawn kept their values.

Needs fontTools 4.66.1 for the font instances: `pip install fonttools==4.66.1`, or run it with
`uv run --no-project --with fonttools==4.66.1 python tools/generate.py`. Fonts are byte-identical across runs with the
same fontTools version.
"""
import argparse
import io
import json
import math
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

try:
    import fontTools
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
except ImportError:
    sys.exit("generate.py needs fontTools: see the header of tools/generate.py")

ROOT = Path(__file__).resolve().parents[1]
WORDS = ROOT / "tools/time_words.json"
OUT = ROOT / "watchface/src/main"
VARIABLE_FONT = ROOT / "fonts/google_sans_flex.ttf"
APP_STRINGS = ROOT / "tools/app_strings.json"  # Rainy app strings the face reuses, by language: RAIN / DRY, the captions
FONTTOOLS_VERSION = "4.66.1"

# Watch Face Format version 5, so the face needs Wear OS 7 (the face's minSdk 37; Wear OS 6 doesn't install a
# v5 face). The orientations need it: a v5 ListOption's complicationSlotIds turns off the slots of the other
# orientations, drawing and taps (under v4 a hidden slot still takes taps).
WFF_VERSION = 5
LANGUAGES = ["en", "it", "fr", "es", "de", "ro", "hi"]  # en is the fallback for every other language
DEFAULT_LANGUAGE = "en"

# The voices: static cuts of Google Sans Flex (axes opsz 6-144, wdth 25-151, wght 1-1000, GRAD 0-100, ROND 0-100,
# slnt -10-0). City: the words that carry the number. Filler: everything else. Time light: the City words in the
# always-on setting's Light, the Filler's weight but wider, so they keep the City voice's widths (wdth 136: the City
# words of every language within 5% of their bold width, 1.00 on the median) and the time doesn't shrink in ambient.
FONTS = {
    "rainy_time": dict(wght=800, wdth=125, ROND=30, opsz=34, GRAD=80, slnt=0),
    "rainy_filler": dict(wght=300, wdth=100, ROND=0, opsz=34, GRAD=80, slnt=0),
    "rainy_time_light": dict(wght=300, wdth=136, ROND=30, opsz=34, GRAD=80, slnt=0),
}
FONT_UNICODES = [*range(0x20, 0x180), *range(0x218, 0x21C), 0x200B, *range(0x2010, 0x2028)]
ZERO_WIDTH_SPACE = "\u200b"
TIME_FONT, FILLER_FONT, TIME_LIGHT_FONT = "rainy_time", "rainy_filler", "rainy_time_light"
# Without the Latin cuts (a language with other glyphs): the system font, these weights.
SYSTEM_TIME_WEIGHT, SYSTEM_FILLER_WEIGHT = "EXTRA_BOLD", "LIGHT"

# Colours: a user setting, "Colour" in the watch face editor. A face can't read the system's theme colours (on Wear OS
# 6 the theme follows the face, or one of the system's palettes), so the setting offers the app's own dark palette and
# the system's eight palettes by name. Each palette: time words, filler words and titles, the rain word (and the bulb's
# background), the dry word. The system ones are Material 3 dark tones (primary 90, neutral variant 80, primary 80,
# tertiary 80) of a tonal-spot scheme seeded with each palette's colour, made with
# materialyoucolor 3.0.4.
PALETTES = [
    ("rainy", ["#FFF6EDFF", "#FFCAC4D0", "#FFBFC1FF", "#FF6CD7DA"]),
    ("peony", ["#FFFFD9E1", "#FFD7C1C5", "#FFF4B6C5", "#FFE0BAE6"]),
    ("moonstone", ["#FFD4E3FF", "#FFC3C6D0", "#FFB1C8EC", "#FFCDBFF0"]),
    ("ivy", ["#FFC7ECC5", "#FFC1C9BD", "#FFABD0AA", "#FFC4CB8D"]),
    ("porcelain", ["#FFFCDFA0", "#FFD0C5B3", "#FFDFC487", "#FFF6BA8D"]),
    ("iris", ["#FFE6DEFF", "#FFC9C4D0", "#FFC9C1ED", "#FFE7B8DF"]),
    ("lemongrass", ["#FFE6E6A8", "#FFC9C7B5", "#FFCACA8F", "#FFE7C182"]),
    ("jade", ["#FFB8EDE2", "#FFBEC9C5", "#FF9CD1C6", "#FF9CCEE2"]),
    ("indigo", ["#FFDBE1FF", "#FFC5C6D1", "#FFBBC5EE", "#FFD8BCEB"]),
    # Not a system palette: yellow time words, a warm light grey, Rainy's rain and dry colours (7:1, 7:1, 5.4:1 on
    # every dark background).
    ("sun", ["#FFFEE141", "#FFE8E2D0", "#FFBFC1FF", "#FF6CD7DA"]),
]
COLOR_SETTING = "themeColor"
CITY, FILLER, RAIN, DRY = (f"[CONFIGURATION.{COLOR_SETTING}.{i}]" for i in range(4))
NEUTRAL = "#FFF6EDFF"  # the slot's text besides the RAIN / DRY word: the same in every palette
BACKGROUND = "#FF000000"  # ambient (always-on) stays black; the active screen draws ACTIVE_BACKGROUND over it
# The active screen's background: a user setting of its own, "Background" in the editor, any palette with any of them.
BACKGROUND_SETTING = "background"
# Black, then nine colours after watchOS 27's Modular face colours (burgundy, magenta, olive, succulent, wildflower
# blue...), as (HCT hue, chroma) at tone 30 (materialyoucolor 3.0.4): every palette's text keeps 5.4:1 or more on all.
BACKGROUNDS = [
    ("black", "#FF000000"), ("burgundy", "#FF832330"), ("magenta", "#FF87016A"), ("clay", "#FF7C2D06"),
    ("olive", "#FF524700"), ("succulent", "#FF1F5029"), ("pine", "#FF005140"), ("wildflower", "#FF00497F"),
    ("midnight", "#FF2B4480"), ("plum", "#FF5E347B"),
]
DEFAULT_BACKGROUND = "midnight"
ACTIVE_BACKGROUND = f"[CONFIGURATION.{BACKGROUND_SETTING}.0]"

# Layout on the 450 x 450 canvas (the runtime scales it to the screen).
SIZE = 450
C = SIZE / 2
TIME_SIZE = 60  # size of the time lines before the scale that makes the longer one fit
# The two time lines: one-line PartTexts in a Group that scales them together (by an expression over the hour and the
# minute, worked out here from the fonts), so both keep one size. The Group is far wider than the screen (every box
# clips before it scales); TIME_WIDTH is how wide the longer line may end up.
TIME = dict(y=80, line_height=78, width=1000)
TIME_WIDTH = 370
TIME_SCALE_STEP = 0.01  # scales are rounded down to this
# The first line sits high, where the round screen narrows: its ends reached the side arcs' ends ("Sept heures" put
# the right arc's end over its last letter like an accent). Its capitals' top corners stay within TIME_TOP_RADIUS of the
# centre, at TIME_CAP_TOP below the line's box (measured on Wear OS 7); the Group scales around its centre, so a
# smaller line also moves down. Clear of the arcs' tracks (radius 217 and out) and of their text near the ends.
TIME_TOP_RADIUS = 195
TIME_CAP_TOP = 14
SLOT = dict(x=60, y=240, width=330, height=76)  # the big slot: title line + text line
SLOT_TITLE = dict(y=0, height=28, size=24)
SLOT_TEXT = dict(y=28, height=46, size=34)
# The text line's margin on each side, inside the slot (which clips it): isAutoSize measures a line of mixed fonts (the
# heavy RAIN word, the light rest) narrower than it draws, so a long one ("PLUIE · Jusqu'à 18:00") lost its first and
# last letters at the slot's edges.
SLOT_TEXT_MARGIN = 20
ARC_TEXT_RADIUS = 206  # where curved text sits
ARC_TEXT_SIZE = 25
# Long text ("Berlin RAIN · Until 18:00", the next event) is smaller and gets a wider part of the arc: about 25 letters.
LONG_TEXT_SIZE = 20
LONG_TEXT_SPAN = 76
# How far the curved text's glyphs reach in and out of ARC_TEXT_RADIUS (measured on Wear OS 7 at 21 px: a comma's tail,
# the caps), at ARC_TEXT_SIZE.
ARC_TEXT_REACH = tuple(r * ARC_TEXT_SIZE / 21 for r in (-11, 10))
# Text on the side arcs is centred on their middle, at 9 and 3 o'clock, clear of the chips the system puts at 6 o'clock
# (Morning brief, a running timer...), in SIDE_TEXT_SPAN degrees.
SIDE_TEXT_SPAN = 60
# The side arcs' touch / editor outline band, from the edge inwards: it also clips the arc's drawing, so it holds the
# text, and it stays clear of the circle (whose edge comes 191 px from the centre in every orientation).
ARC_THICKNESS = 33
# A progress or goal value also draws a track on the outer edge, outside the text (inside it the time lines and the big
# slot's text come within a few pixels): the whole arc but INSET degrees at each end, the value's part in RAIN.
TRACK = dict(radius=220, thickness=6, inset=4, alpha=70)
# A range or goal without a title ("100", the battery) has the provider's icon instead, on the edge where the track
# starts (the track then starts past it): the text stays centred, which it couldn't with the icon beside it, as the
# face can't measure text. Plain text gets no icon: far from short text ("Mon") it would look stray. Gap in degrees.
ARC_ICON = dict(size=18, radius=215, gap=3)
# The utility arcs: (slot id, start, end in degrees clockwise from 12 o'clock, side, default provider, its type, and
# optionally a provider to try first, with its type). 84 degrees each. Their drawing turns with the face (see TILTS),
# but a slot's touch band can't turn and clips the slot's drawing: arc_band() widens it to hold the arc in every
# orientation (check_layout() proves it, and that no band reaches another slot's drawing).
# All arc text runs clockwise, tops outwards: the side arcs read bottom to top on the left, top to bottom on the right.
FITBIT_STEPS = "com.fitbit.FitbitMobile/com.fitbit.complications.offloadable.steps.OffloadableStepsComplicationDataSourceService"
ARCS = [
    # Fitbit's steps (Pixel Watch) send the goal too; the system's step count is plain text.
    (2, 228, 312, "left", "STEP_COUNT", "SHORT_TEXT", (FITBIT_STEPS, "GOAL_PROGRESS")),
    (3, 48, 132, "right", "WATCH_BATTERY", "RANGED_VALUE"),
]
# The date on top ("Sun 11 Oct"): drawn by the face, not a slot (a face has at most 8 slots, and the orientations need
# three copies of the weather slot and of the circle).
DATE_ARC = (318, 402)
# The round slot ("bulb") under the big one, between the side arcs (they leave 6 o'clock free, 132-228 degrees,
# for it): sunrise / sunset by default. Its inner layout is drawn for BULB_DESIGN px and scaled to the circle.
# A filled circle in the rain colour at background_alpha (out of 255), hidden in ambient (a lit disc on an always-on
# screen); padding keeps the contents off its edge.
BULB = dict(slot=4, cx=C, cy=366, size=100, padding=3, background_alpha=56)
BULB_DESIGN = 62
BIG_SLOT_ID = 0
# The orientation setting: how far the face turns, clockwise, around its centre. On the left wrist (the default) a
# forearm on a keyboard or a wheel points the watch's 3 o'clock away from you, so the face turns clockwise to stand
# upright; the right wrist mirrors it. Everything turns, the side arcs too (their touch bands don't, see ARCS). Slot
# positions are fixed when the face loads, so each orientation has its own weather slot and circle (slot ids: weather,
# circle), turned with the slot's angle (its touch area turns too) and enabled by the setting (WFF 5); Straight keeps
# the face's original ids, 0 and 4.
TILT_DEGREES = 25
TILT_SETTING = "orientation"
TILTS = [  # (option id, angle, weather slot id, circle slot id); the first is the default
    ("left_wrist", TILT_DEGREES, 5, 6),
    ("straight", 0, BIG_SLOT_ID, BULB["slot"]),
    ("right_wrist", -TILT_DEGREES, 7, 8),
]
RAINY_PROVIDER = "app.wakirox.rainy/app.wakirox.rainy.rainywear.presentation.complication.CustomComplicationDataSourceService"

# Slot names in the watch face editor, per language (values/ is English).
SLOT_NAMES = {
    "en": dict(slot_weather="Weather", slot_left="Left", slot_right="Right", slot_bulb="Circle"),
    "it": dict(slot_weather="Meteo", slot_left="A sinistra", slot_right="A destra", slot_bulb="Cerchio"),
    "fr": dict(slot_weather="Météo", slot_left="À gauche", slot_right="À droite", slot_bulb="Cercle"),
    "es": dict(slot_weather="Tiempo", slot_left="A la izquierda", slot_right="A la derecha", slot_bulb="Círculo"),
    "de": dict(slot_weather="Wetter", slot_left="Links", slot_right="Rechts", slot_bulb="Kreis"),
    "ro": dict(slot_weather="Vremea", slot_left="În stânga", slot_right="În dreapta", slot_bulb="Cerc"),
    "hi": dict(slot_weather="मौसम", slot_left="बाईं ओर", slot_right="दाईं ओर", slot_bulb="गोला"),
}
# The colour setting and its palettes (Rainy is the app's own, the rest are the system's palette names).
COLOR_NAMES = {
    "en": ["Colour", "Peony", "Moonstone", "Ivy", "Porcelain", "Iris", "Lemongrass", "Jade", "Indigo", "Sun"],
    "it": ["Colore", "Peonia", "Pietra di luna", "Edera", "Porcellana", "Iris", "Citronella", "Giada", "Indaco", "Sole"],
    "fr": ["Couleur", "Pivoine", "Pierre de lune", "Lierre", "Porcelaine", "Iris", "Citronnelle", "Jade", "Indigo", "Soleil"],
    "es": ["Color", "Peonía", "Piedra lunar", "Hiedra", "Porcelana", "Iris", "Hierba limón", "Jade", "Índigo", "Sol"],
    "de": ["Farbe", "Pfingstrose", "Mondstein", "Efeu", "Porzellan", "Iris", "Zitronengras", "Jade", "Indigo", "Sonne"],
    "ro": ["Culoare", "Bujor", "Piatra lunii", "Iederă", "Porțelan", "Iris", "Lemongrass", "Jad", "Indigo", "Soare"],
    "hi": ["रंग", "पियोनी", "मूनस्टोन", "आइवी", "पोर्सलेन", "आइरिस", "लेमनग्रास", "जेड", "इंडिगो", "सूरज"],
}
# The always-on setting: Light (the default) sets the whole time at the Filler's weight while the watch is in ambient
# mode (the City words in the Time light cut, same widths), fewer lit pixels and less burn-in; Bold keeps the face's look.
AMBIENT_SETTING = "ambientStyle"
AMBIENT_OPTIONS = ["light", "bold"]  # the first is the default
BACKGROUND_NAMES = {  # the setting, then BACKGROUNDS in order
    "en": ["Background", "Black", "Burgundy", "Magenta", "Clay", "Olive", "Succulent", "Pine", "Wildflower", "Midnight", "Plum"],
    "it": ["Sfondo", "Nero", "Bordeaux", "Magenta", "Argilla", "Oliva", "Succulenta", "Pino", "Fiordaliso", "Mezzanotte", "Prugna"],
    "fr": ["Arrière-plan", "Noir", "Bordeaux", "Magenta", "Argile", "Olive", "Succulente", "Pin", "Bleuet", "Minuit", "Prune"],
    "es": ["Fondo", "Negro", "Burdeos", "Magenta", "Arcilla", "Oliva", "Suculenta", "Pino", "Aciano", "Medianoche", "Ciruela"],
    "de": ["Hintergrund", "Schwarz", "Bordeaux", "Magenta", "Ton", "Oliv", "Sukkulente", "Kiefer", "Kornblume", "Mitternacht", "Pflaume"],
    "ro": ["Fundal", "Negru", "Vișiniu", "Magenta", "Argilă", "Măsliniu", "Suculentă", "Pin", "Albăstrea", "Miez de noapte", "Prună"],
    "hi": ["पृष्ठभूमि", "काला", "बरगंडी", "मैजेंटा", "मिट्टी", "ज़ैतूनी", "सक्यूलेंट", "चीड़", "जंगली फूल", "आधी रात", "आलूबुखारा"],
}
AMBIENT_NAMES = {
    "en": ["Always-on", "Light", "Bold"],
    "it": ["Sempre attivo", "Leggero", "Grassetto"],
    "fr": ["Toujours activé", "Léger", "Gras"],
    "es": ["Siempre activa", "Fina", "Negrita"],
    "de": ["Always-on", "Leicht", "Fett"],
    "ro": ["Mereu activ", "Subțire", "Aldin"],
    "hi": ["हमेशा चालू", "पतला", "मोटा"],
}
# The orientation setting's name and options, in TILTS order.
TILT_NAMES = {
    "en": ["Orientation", "Left wrist", "Straight", "Right wrist"],
    "it": ["Orientamento", "Polso sinistro", "Dritto", "Polso destro"],
    "fr": ["Orientation", "Poignet gauche", "Normale", "Poignet droit"],
    "es": ["Orientación", "Muñeca izquierda", "Recta", "Muñeca derecha"],
    "de": ["Ausrichtung", "Linkes Handgelenk", "Gerade", "Rechtes Handgelenk"],
    "ro": ["Orientare", "Încheietura stângă", "Normal", "Încheietura dreaptă"],
    "hi": ["दिशा", "बायाँ हाथ", "सीधा", "दायाँ हाथ"],
}
FACE_NAME = "Rainy"
GENERATED = "Generated by tools/generate.py: edit that script or tools/time_words.json, not this file."


# ---------------------------------------------------------------------------------------------------------------------
# XML helpers

def attr(value) -> str:
    return escape(str(value), {'"': "&quot;"})


def lit(text: str) -> str:
    """A string literal in an expression; an empty one is ZERO_WIDTH_SPACE (an empty run crashes the renderer)."""
    if '"' in text or "\\" in text:
        raise ValueError(f"no quotes or backslashes in words: {text!r}")
    return f'"{text or ZERO_WIDTH_SPACE}"'


def cdata(text: str) -> str:
    assert "]]>" not in text
    return f"<![CDATA[{text}]]>"


def fmt(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def tag(name: str, attrs: dict, children=None, indent=0) -> list:
    """An element as lines; children is a list of lines (already indented one level deeper) or inline text."""
    pad = "    " * indent
    a = "".join(f' {k}="{attr(v)}"' for k, v in attrs.items() if v is not None)
    if children is None:
        return [f"{pad}<{name}{a} />"]
    if isinstance(children, str):
        return [f"{pad}<{name}{a}>{children}</{name}>"]
    return [f"{pad}<{name}{a}>", *children, f"{pad}</{name}>"]


# ---------------------------------------------------------------------------------------------------------------------
# Words

def any_of(source: str, values) -> str:
    return " || ".join(f"{source} == {v}" for v in sorted(values))


def split_runs(text: str) -> list:
    """'oh *eight*.' -> ['oh ', 'eight', '.']: even indices plain (Filler), odd ones marked (City)."""
    parts = text.split("*")
    if len(parts) % 2 == 0:
        raise ValueError(f"unbalanced * in {text!r}")
    return parts


def run_of(text: str, i: int) -> str:
    parts = split_runs(text)
    return parts[i] if i < len(parts) else ""


def run_expression(first: list, regular: dict, source: str):
    """
    One run of a line as an expression: the special cases first ((condition, run text)), then the run text by the
    value of source (regular: value -> run text), grouped by text with the most common one last. Returns
    (expression, None), or (None, text) when the run is the same at every time.
    """
    by_run = {}
    for key, text in regular.items():
        by_run.setdefault(text, []).append(key)
    default = max(by_run, key=lambda v: (len(by_run[v]), -min(by_run[v])))
    cases = [(any_of(source, keys), v) for v, keys in by_run.items() if v != default]
    if not first and not cases:
        return None, default
    expr = lit(default)
    for cond, value in reversed(first + cases):
        expr = f"({cond}) ? {lit(value)} : ({expr})"
    return expr, None


def exceptions_for(words: dict, mode: str) -> list:
    """The whole-time overrides of one mode: (hour, minute, line 1, line 2), the midnight ones ("12h 0:MM") first."""
    out = []
    for key, (line1, line2) in words.get("exceptions", {}).items():
        m = re.fullmatch(r"(12h|24h) (\d{1,2}):(\d{2})", key)
        if not m:
            raise ValueError(f"bad exception key {key!r}")
        if m.group(1) == f"{mode}h":
            out.append((int(m.group(2)), int(m.group(3)), line1, line2))
    return sorted(out, key=lambda e: e[0] != 0)


def midnight12(words: dict):
    """12h mode's own words for the midnight hour (hour12 / hour12_at_00 key "0"): (line 1, line 1 at :00), or None
    when 0:xx reads like 12:xx. With them, key "12" is the noon hour only."""
    if "0" not in words["hour12"]:
        return None
    return words["hour12"]["0"], words["hour12_at_00"]["0"]


def hour_is(mode: str, h: int) -> str:
    """The condition for hour h: in 12h mode hour 0 is midnight ([HOUR_1_12] says 12 for both 0 and 12)."""
    return "[HOUR_0_23] == 0" if mode == "12" and h == 0 else f"{'[HOUR_0_23]' if mode == '24' else '[HOUR_1_12]'} == {h}"


def line_runs(words: dict, mode: str) -> tuple:
    """Line 1 and line 2 of one language in one mode ("12", "24"): lists of (voice, expression, constant text)."""
    source = "[HOUR_0_23]" if mode == "24" else "[HOUR_1_12]"
    hours = range(0, 24) if mode == "24" else range(1, 13)
    period = words.get("period", "")
    hour = {h: words[f"hour{mode}"][str(h)] for h in hours}
    at_00 = {h: words[f"hour{mode}_at_00"][str(h)] for h in hours}
    minute = {m: words["minute"][str(m)] + period for m in range(60)}
    exceptions = exceptions_for(words, mode)
    midnight = midnight12(words) if mode == "12" else None

    def plain_line1(h, mm):
        """Line 1 without the exceptions."""
        if h == 0 and mode == "12":
            h0 = midnight or (hour[12], at_00[12])
            return h0[1] if mm == 0 else h0[0]
        return at_00[h] if mm == 0 else hour[h]

    def runs(texts, make):
        count = max(len(split_runs(t)) for t in texts)
        return [("time" if i % 2 else "filler", *make(i)) for i in range(count)]

    def line1(i):
        first = [(f"{hour_is(mode, h)} && [MINUTE] == {mm}", run_of(l1, i)) for h, mm, l1, _ in exceptions
                 if run_of(l1, i) != run_of(plain_line1(h, mm), i)]
        if midnight:
            regular, on_the_hour = run_of(midnight[0], i), run_of(midnight[1], i)
            if regular != run_of(hour[12], i) or on_the_hour != run_of(at_00[12], i):
                # :00 first: the second case covers the whole hour.
                first.append(("[HOUR_0_23] == 0 && [MINUTE] == 0", on_the_hour))
                first.append(("[HOUR_0_23] == 0", regular))
        # The :00 forms, one case per distinct run text ("[MINUTE] == 0" alone when every hour has it).
        groups = {}
        for h in hours:
            if run_of(at_00[h], i) != run_of(hour[h], i):
                groups.setdefault(run_of(at_00[h], i), []).append(h)
        for text, hs in groups.items():
            first.append(("[MINUTE] == 0" if len(hs) == len(hours) else f"[MINUTE] == 0 && ({any_of(source, hs)})", text))
        return run_expression(first, {h: run_of(t, i) for h, t in hour.items()}, source)

    def line2(i):
        first = []
        for h, mm, _, l2 in exceptions:
            text = l2 + ("" if l2.endswith(period) else period)
            if run_of(text, i) != run_of(minute[mm], i):
                first.append((f"{hour_is(mode, h)} && [MINUTE] == {mm}", run_of(text, i)))
        return run_expression(first, {m: run_of(t, i) for m, t in minute.items()}, "[MINUTE]")

    texts1 = [*hour.values(), *at_00.values(), *(midnight or ()), *(e[2] for e in exceptions)]
    texts2 = [*minute.values(), *(e[3] for e in exceptions)]
    return runs(texts1, line1), runs(texts2, line2)


def needs_system_font(words: dict, cmap) -> bool:
    chars = set()

    def walk(x):
        if isinstance(x, str):
            chars.update(x)
        elif isinstance(x, dict):
            for y in x.values():
                walk(y)
        elif isinstance(x, list):
            for y in x:
                walk(y)

    walk(words)
    chars.discard("*")
    return any(ord(c) not in cmap for c in chars)


# ---------------------------------------------------------------------------------------------------------------------
# The face

def font_attrs(voice: str, size: float, system: bool, color=None, light=False) -> dict:
    """light: the City words keep their colour and widths but take the Filler's weight (the always-on setting's Light)."""
    heavy = voice == "time" and not light
    color = color or (CITY if voice == "time" else FILLER)
    if system:
        return dict(family="SYNC_TO_DEVICE", size=fmt(size), weight=SYSTEM_TIME_WEIGHT if heavy else SYSTEM_FILLER_WEIGHT, color=color)
    family = FILLER_FONT if voice != "time" else TIME_LIGHT_FONT if light else TIME_FONT
    return dict(family=family, size=fmt(size), color=color)


def line_fonts(runs, size: float, system: bool, indent: int, light=False) -> list:
    fonts = []
    for voice, expr, const in runs:
        if expr is None and not const:
            continue
        body = cdata(const) if expr is None else f'<Template>{cdata("%s")}<Parameter expression="{attr(expr)}" /></Template>'
        fonts += tag("Font", font_attrs(voice, size, system, light=light), body, indent)
    return fonts


class Measure:
    """Line widths in the Latin cuts at TIME_SIZE (glyph advances, no kerning; TIME_WIDTH leaves room for it)."""

    def __init__(self, fonts: dict):
        self.faces = {}
        for name in (TIME_FONT, FILLER_FONT):
            f = TTFont(io.BytesIO(fonts[f"res/font/{name}.ttf"]))
            self.faces[name] = (f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm)

    def width(self, text: str) -> float:
        total = 0.0
        for i, part in enumerate(split_runs(text)):
            cmap, hmtx, upm = self.faces[TIME_FONT if i % 2 else FILLER_FONT]
            total += sum(hmtx[cmap[ord(ch)]][0] for ch in part) / upm * TIME_SIZE
        return total

    def fit(self, text: str, top=False) -> float:
        """The scale (at most 1) at which the line is TIME_WIDTH wide or less, rounded down; the first line (top) also
        keeps its top corners within TIME_TOP_RADIUS."""
        w = self.width(text)
        if w > TIME["width"]:
            raise ValueError(f"{text!r} is wider than the time box ({w:.0f}px)")
        steps = min(int(TIME_WIDTH / w / TIME_SCALE_STEP) if w else 10 ** 6, round(1 / TIME_SCALE_STEP))
        pivot = TIME["y"] + TIME["line_height"]  # the Group's centre: between the lines
        while top and steps > 0:
            k = steps * TIME_SCALE_STEP
            if math.hypot(k * w / 2, C - (pivot - k * (TIME["line_height"] - TIME_CAP_TOP))) <= TIME_TOP_RADIUS:
                break
            steps -= 1
        return steps * TIME_SCALE_STEP


def number_by(source: str, values: dict) -> str:
    """values (key -> number) as an expression over source, the most common number last."""
    by_value = {}
    for key, v in values.items():
        by_value.setdefault(v, []).append(key)
    default = max(by_value, key=lambda v: (len(by_value[v]), v))
    expr = fmt_scale(default)
    for v, keys in sorted(by_value.items(), reverse=True):
        if v != default:
            expr = f"({any_of(source, keys)}) ? {fmt_scale(v)} : ({expr})"
    return expr


def fmt_scale(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def time_scale(words: dict, mode: str, measure: Measure) -> str:
    """The time Group's scale: the fit of the longer of the two lines at this hour and minute."""
    source = "[HOUR_0_23]" if mode == "24" else "[HOUR_1_12]"
    hours = range(0, 24) if mode == "24" else range(1, 13)
    period = words.get("period", "")
    hour = {h: measure.fit(words[f"hour{mode}"][str(h)], top=True) for h in hours}
    minute = {m: measure.fit(words["minute"][str(m)] + period) for m in range(1, 60)}
    at_00 = {h: min(measure.fit(words[f"hour{mode}_at_00"][str(h)], top=True), measure.fit(words["minute"]["0"] + period)) for h in hours}
    minutes = number_by("[MINUTE]", minute)
    expr = f"([MINUTE] == 0) ? ({number_by(source, at_00)}) : clamp({number_by(source, hour)}, 0, {minutes})"
    midnight = midnight12(words) if mode == "12" else None
    if midnight:
        at_00_fit = min(measure.fit(midnight[1], top=True), measure.fit(words["minute"]["0"] + period))
        expr = (f"([HOUR_0_23] == 0) ? (([MINUTE] == 0) ? {fmt_scale(at_00_fit)} : clamp({fmt_scale(measure.fit(midnight[0], top=True))}, 0, {minutes}))"
                f" : ({expr})")
    # The first exception (midnight's) ends up outermost, so it wins.
    for h, m, line1, line2 in reversed(exceptions_for(words, mode)):
        fit = min(measure.fit(line1, top=True), measure.fit(line2 + ("" if line2.endswith(period) else period)))
        expr = f"({hour_is(mode, h)} && [MINUTE] == {m}) ? {fmt_scale(fit)} : ({expr})"
    return expr


def time_lines(words: dict, mode: str, system: bool, measure: Measure, name: str, indent: int, light=False) -> list:
    """The two lines. Latin cuts: in a Group scaled by time_scale (light keeps the bold lines' scale, so the time
    doesn't jump in size). System font (can't be measured here): each line shrinks on its own with isAutoSize."""
    line1, line2 = line_runs(words, mode)
    h = TIME["line_height"]
    if system:
        parts = []
        for i, runs in enumerate((line1, line2)):
            text = tag("Text", dict(align="CENTER", maxLines="1", isAutoSize="TRUE"), line_fonts(runs, TIME_SIZE, True, indent + 2, light), indent + 1)
            parts += tag("PartText", dict(name=f"{name}_{i + 1}", x=fmt(C - TIME_WIDTH / 2), y=TIME["y"] + i * h, width=TIME_WIDTH, height=h), text, indent)
        return parts
    scale = time_scale(words, mode, measure)
    body = tag("Transform", dict(target="scaleX", value=scale), None, indent + 1)
    body += tag("Transform", dict(target="scaleY", value=scale), None, indent + 1)
    for i, runs in enumerate((line1, line2)):
        text = tag("Text", dict(align="CENTER", maxLines="1"), line_fonts(runs, TIME_SIZE, False, indent + 3, light), indent + 2)
        body += tag("PartText", dict(name=f"{name}_{i + 1}", x=0, y=i * h, width=TIME["width"], height=h), text, indent + 1)
    return tag("Group", dict(name=name, x=fmt(C - TIME["width"] / 2), y=TIME["y"], width=TIME["width"], height=2 * h,
                             pivotX="0.5", pivotY="0.5"), body, indent)


def clock(all_words: dict, cmap, measure: Measure, light=False, indent=1) -> list:
    """The two time lines, per language (live switch on [LANGUAGE_LOCALE_NAME]) and per 12/24-hour mode."""
    prefix = "ambient_time" if light else "time"

    def language(lang, indent):
        words = all_words[lang]
        system = needs_system_font(words, cmap)
        modes = []
        for mode in ("24", "12"):
            body = time_lines(words, mode, system, measure, f"{prefix}_{lang}_{mode}_lines", indent + 3, light)
            modes.append(tag("Group", dict(name=f"{prefix}_{lang}_{mode}", x=0, y=0, width=SIZE, height=SIZE), body, indent + 2))
        cond = tag("Expressions", {}, tag("Expression", dict(name="h24"), cdata("[IS_24_HOUR_MODE]"), indent + 2), indent + 1)
        cond += tag("Compare", dict(expression="h24"), modes[0], indent + 1)
        cond += tag("Default", {}, modes[1], indent + 1)
        return tag("Condition", {}, cond, indent)

    others = [lang for lang in all_words if lang != DEFAULT_LANGUAGE]
    exprs = [tag("Expression", dict(name=f"lang_{lang}"), cdata(f'subText([LANGUAGE_LOCALE_NAME], 0, 2) == "{lang}"'), indent + 2)[0] for lang in others]
    body = tag("Expressions", {}, exprs, indent + 1)
    for lang in others:
        body += tag("Compare", dict(expression=f"lang_{lang}"), wrap_group(f"{prefix}_{lang}", language(lang, indent + 3), indent + 2), indent + 1)
    body += tag("Default", {}, wrap_group(f"{prefix}_{DEFAULT_LANGUAGE}", language(DEFAULT_LANGUAGE, indent + 3), indent + 2), indent + 1)
    return tag("Condition", {}, body, indent)


def ambient_clock(all_words: dict, cmap, measure: Measure) -> list:
    """The always-on setting. Light: a Group drawn only in ambient mode (alpha 0, 255 in ambient) that covers the bold
    time with a black band (see the tint below) and sets the time again in the light voice. Bold: nothing (the bold time stays). So the
    time's expressions are in the face twice, not three times (a Variant can't depend on a setting)."""
    top, height = TIME["y"], 2 * TIME["line_height"]
    cover = tag("PartDraw", dict(name="ambient_cover", x=0, y=top, width=SIZE, height=height),
                tag("Rectangle", dict(x=0, y=0, width=SIZE, height=height), tag("Fill", dict(color=BACKGROUND), None, 6), 5), 4)
    # Over the black, the active background, gone in ambient: while the screen wakes, this Group fades out as the
    # background fades in, and the band blends like the background around it (a black band alone shows as a dark
    # stripe across the time for a moment).
    tint = tag("Rectangle", dict(x=0, y=0, width=SIZE, height=height), tag("Fill", dict(color=ACTIVE_BACKGROUND), None, 6), 5)
    tint += tag("Variant", dict(mode="AMBIENT", target="alpha", value="0"), None, 5)
    cover += tag("PartDraw", dict(name="ambient_cover_tint", x=0, y=top, width=SIZE, height=height), tint, 4)
    light = tag("Group", dict(name="ambient_time", x=0, y=0, width=SIZE, height=SIZE, alpha=0),
                [*tag("Variant", dict(mode="AMBIENT", target="alpha", value="255"), None, 4), *cover, *clock(all_words, cmap, measure, light=True, indent=4)], 3)
    # An option can't be empty: Bold gets an empty Group.
    options = {"light": light, "bold": tag("Group", dict(name="ambient_bold", x=0, y=0, width=1, height=1), [], 3)}
    return tag("ListConfiguration", dict(id=AMBIENT_SETTING),
               [line for option in AMBIENT_OPTIONS for line in tag("ListOption", dict(id=option), options[option], 2)], 1)


def wrap_group(name, children, indent):
    return tag("Group", dict(name=name, x=0, y=0, width=SIZE, height=SIZE), children, indent)


def hero_words() -> tuple:
    """The app's RAIN and DRY words in every language, as the complication sends them (upper case)."""
    strings = json.loads(APP_STRINGS.read_text(encoding="utf-8")).values()
    rain = {s["hero_rain"].strip().upper() for s in strings}
    dry = {s["hero_dry"].strip().upper() for s in strings}
    return sorted(rain, key=lambda w: (-len(w), w)), sorted(dry, key=lambda w: (-len(w), w))


def prefix_cond(word: str) -> str:
    t, n = "[COMPLICATION.TEXT]", len(word)
    return f'(subText({t}, 0, {n}) == "{word}" && (textLength({t}) == {n} || subText({t}, {n}, {n + 1}) == " "))'


def prefix_length(words) -> str:
    e = "0"
    for w in reversed(words):
        e = f"{prefix_cond(w)} ? {len(w)} : ({e})"
    return e


def slot_text(width: int, indent: int) -> list:
    """The slot's text: the app's RAIN / DRY word in its colour, the rest in the Filler voice; other text in City.
    Apart from that word it is NEUTRAL in every palette."""
    rain, dry = hero_words()
    t = "[COMPLICATION.TEXT]"
    box = dict(x=SLOT_TEXT_MARGIN, y=SLOT_TEXT["y"], width=width - 2 * SLOT_TEXT_MARGIN, height=SLOT_TEXT["height"])

    def split(words, color, name):
        n = prefix_length(words)
        fonts = tag("Font", dict(family=TIME_FONT, size=fmt(SLOT_TEXT["size"]), color=color),
                    f'<Template>{cdata("%s")}<Parameter expression="{attr(f"subText({t}, 0, {n})")}" /></Template>', indent + 4)
        fonts += tag("Font", dict(family=FILLER_FONT, size=fmt(SLOT_TEXT["size"]), color=NEUTRAL),
                     f'<Template>{cdata("%s")}<Parameter expression="{attr(f"subText({t}, {n}, textLength({t}))")}" /></Template>', indent + 4)
        text = tag("Text", dict(align="CENTER", maxLines="1", isAutoSize="TRUE", ellipsis="TRUE"), fonts, indent + 3)
        return tag("PartText", dict(name=name, **box), text, indent + 2)

    plain = tag("PartText", dict(name="slot_text", **box), tag("Text", dict(align="CENTER", maxLines="1", isAutoSize="TRUE", ellipsis="TRUE"),
                tag("Font", dict(family=TIME_FONT, size=fmt(SLOT_TEXT["size"]), color=NEUTRAL),
                    f'<Template>{cdata("%s")}<Parameter expression="{t}" /></Template>', indent + 4), indent + 3), indent + 2)
    exprs = tag("Expressions", {}, [
        *tag("Expression", dict(name="rain"), cdata(" || ".join(prefix_cond(w) for w in rain)), indent + 2),
        *tag("Expression", dict(name="dry"), cdata(" || ".join(prefix_cond(w) for w in dry)), indent + 2),
    ], indent + 1)
    body = exprs + tag("Compare", dict(expression="rain"), split(rain, RAIN, "slot_text_rain"), indent + 1)
    body += tag("Compare", dict(expression="dry"), split(dry, DRY, "slot_text_dry"), indent + 1)
    body += tag("Default", {}, plain, indent + 1)
    return tag("Condition", {}, body, indent)


def slot_title(width: int, indent: int) -> list:
    """The slot's title (the city, with Rainy) in the heavy City voice, like the time's number words."""
    font = tag("Font", dict(family=TIME_FONT, size=fmt(SLOT_TITLE["size"]), color=CITY),
               f'<Template>{cdata("%s")}<Parameter expression="[COMPLICATION.TITLE]" /></Template>', indent + 2)
    text = tag("Text", dict(align="CENTER", maxLines="1", ellipsis="TRUE"), font, indent + 1)
    return tag("PartText", dict(name="slot_title", x=0, y=SLOT_TITLE["y"], width=width, height=SLOT_TITLE["height"]), text, indent)


# The face's own answer, for when Rainy isn't on the watch: the weather slot falls back to no provider (not another
# one), and the face says RAIN / DRY where the slot is (weather_answer) from the system's weather ([WEATHER.*]):
# the current conditions and chance of precipitation, and an hourly forecast, [WEATHER.HOURS.<i>.*], i = 0 the
# current hour (hourly entries have a condition but no chance of precipitation). The docs promise 8 hours ahead, Wear
# OS 7 gives 24 (probed 2026-10-12); the face looks WEATHER_HOURS ahead, as far as there are hours, and so finds the
# rain that comes later today. Rainy's complication (RainWords.next in core) says it from 15-minute steps of rain:
# rain now "Until 15:30", rain later "14:45-15:30" or "From 14:45". The face says it the same way by the hour: an
# hour is wet when its condition is precipitation (Rainy counts snow as precipitation too), the current hour also when
# its chance of precipitation is likely (Rainy's 60%). With no wet hour but a possible chance now (Rainy's 30%) it
# says "RAIN · Possible", as Rainy's day word does. Rain through every forecast hour gets no time (the forecast doesn't
# say when it stops); no wet hour at all is DRY alone (Rainy's "DRY · Until 18:45", the end of its steps, would be
# half a day away here). A tap on the answer opens the watch's weather app (WEATHER_APP).
WEATHER_HOURS = 12  # [WEATHER.HOURS.0] to [WEATHER.HOURS.11]
# The Pixel Weather app (Wear OS 6 and later; the older com.google.android.wearable.weather takes no new installs
# there). A Launch with a package that isn't installed opens its Play Store page.
WEATHER_APP = "com.google.android.apps.weather"
RAIN_LIKELY, RAIN_POSSIBLE = 60, 30  # DaySummary's LIKELY and POSSIBLE, in percent
NONE = 99  # "no such hour" in the rule's References
# The app's strings (tools/app_strings.json, from the Rainy app): the words and the captions, %1$s / %2$s the times.
TILE_STRINGS = ["tile_rain_until", "tile_rain_from", "tile_rain_between", "tile_rain_possible"]
# The face's own, per language: when there's no weather on the watch, and the 12-hour clock's AM / PM (CLDR's short
# forms; the hours are whole, so "6 PM", where 24-hour mode says "18:00" like the app).
NO_FORECAST = {"en": "No forecast", "it": "Nessuna previsione", "fr": "Pas de prévisions", "es": "Sin previsión",
               "de": "Keine Vorhersage", "ro": "Fără prognoză", "hi": "पूर्वानुमान नहीं"}
AM_PM = {"en": ("AM", "PM"), "it": ("AM", "PM"), "fr": ("AM", "PM"), "es": ("a. m.", "p. m."), "de": ("AM", "PM"),
         "ro": ("a.m.", "p.m."), "hi": ("am", "pm")}


def app_string(lang: str, name: str) -> str:
    """One of the Rainy app's strings, in tools/app_strings.json."""
    try:
        return json.loads(APP_STRINGS.read_text(encoding="utf-8"))[lang][name]
    except KeyError:
        raise SystemExit(f"no string {name} for {lang} in {APP_STRINGS}")


def weather_words(lang: str) -> dict:
    """The fallback's words in one language: rain / dry (upper case, as Rainy's complication sends them), the captions
    split around their times ("Until %1$s" -> ["Until ", ""]), possible, no forecast, AM / PM."""
    words = dict(rain=app_string(lang, "hero_rain").upper(), dry=app_string(lang, "hero_dry").upper())
    for name in TILE_STRINGS:
        words[name.removeprefix("tile_rain_")] = re.split(r"%\d\$s", app_string(lang, name))
    words["no_forecast"], words["am_pm"] = NO_FORECAST[lang], AM_PM[lang]
    return words


def by_language(value) -> str:
    """value(lang) (an expression) in the watch's language, English for any other."""
    expr = value(DEFAULT_LANGUAGE)
    for lang in reversed([lang for lang in LANGUAGES if lang != DEFAULT_LANGUAGE]):
        mine = value(lang)
        if mine != expr:
            expr = f'(subText([LANGUAGE_LOCALE_NAME], 0, 2) == "{lang}") ? {mine} : ({expr})'
    return expr


def wet(condition: str) -> str:
    """A condition (WEATHER.CONDITION's enum) that is precipitation: heavy rain 4, heavy snow 5, rain 6, snow 7,
    thunderstorm 9, sleet 10, light snow 11, light rain 12."""
    return f"(({condition} >= 4 && {condition} <= 7) || ({condition} >= 9 && {condition} <= 12))"


def dry(condition: str) -> str:
    return f"({condition} < 4 || {condition} == 8 || {condition} > 12)"


def hour(i: int, key: str) -> str:
    return f"[WEATHER.HOURS.{i}.{key}]"


# The rule, worked out once per frame into References (Groups turned by the value; the slots read them):
#   known: how many forecast hours there are (1 = the current hour only);
#   start: the first wet hour (0 = now), NONE without one;
#   end: the first dry hour after it, NONE when it rains through the known hours;
#   case: what the slot says (WEATHER_CASES).
WEATHER_CASES = dict(until=1, rain=2, between=3, since=4, possible=5, dry=6)


def weather_rule() -> list:
    ref = "[REFERENCE.weather_{}]".format
    known = str(WEATHER_HOURS)
    for i in reversed(range(1, WEATHER_HOURS)):
        known = f"{hour(i, 'IS_AVAILABLE')} ? ({known}) : {i}"
    now = (f"{wet('[WEATHER.CONDITION]')} || [WEATHER.CHANCE_OF_PRECIPITATION] >= {RAIN_LIKELY}"
           f" || ({hour(0, 'IS_AVAILABLE')} && {wet(hour(0, 'CONDITION'))})")
    start = str(NONE)
    for i in reversed(range(1, WEATHER_HOURS)):
        start = f"({ref('known')} > {i} && {wet(hour(i, 'CONDITION'))}) ? {i} : ({start})"
    start = f"({now}) ? 0 : ({start})"
    end = str(NONE)
    for i in reversed(range(1, WEATHER_HOURS)):
        end = f"({ref('start')} < {i} && {ref('known')} > {i} && {dry(hour(i, 'CONDITION'))}) ? {i} : ({end})"
    c = WEATHER_CASES
    case = (f"({ref('start')} == 0) ? (({ref('end')} < {NONE}) ? {c['until']} : {c['rain']})"
            f" : (({ref('start')} < {NONE}) ? (({ref('end')} < {NONE}) ? {c['between']} : {c['since']})"
            f" : (([WEATHER.CHANCE_OF_PRECIPITATION] >= {RAIN_POSSIBLE}) ? {c['possible']}"
            f" : {c['dry']}))")
    body = []
    for name, expr in (("known", known), ("start", start), ("end", end), ("case", case)):
        inner = tag("Transform", dict(target="angle", value=expr), None, 3)
        inner += tag("Reference", dict(name=f"weather_{name}", source="angle", defaultValue=NONE if name != "known" else 1), None, 3)
        body += tag("Group", dict(name=f"weather_{name}", x=0, y=0, width=1, height=1), inner, 2)
    return tag("Group", dict(name="weather_rule", x=0, y=0, width=1, height=1), body, 1)


def weather_answer(indent: int) -> list:
    """The face's own answer where the weather slot is, while the slot draws nothing (no provider, Empty, or a provider
    with no data): in the layout and voices of Rainy's, the temperature and the system's condition name as the title
    ("18° Partly cloudy"), "RAIN · Until 18:00" under it. No weather on the watch: "No forecast". (A failed refresh with
    older data still available draws the older data, the forecast hours that have passed left out.) It turns with the
    face, so it lands on the slot of every orientation; whether the slot draws comes from References in its text
    complications (a part that isn't drawn gives its Reference's default), a NO_DATA slot
    drawing nothing at all, its EMPTY complication included (Wear OS 7)."""
    width = SLOT["width"]
    ref = "[REFERENCE.weather_{}]".format
    c = WEATHER_CASES
    case = ref("case")
    words = {lang: weather_words(lang) for lang in LANGUAGES}
    zwsp = lit("")

    def say(pick):
        """An expression of the words, by language: pick(words) -> literal text."""
        return by_language(lambda lang: lit(pick(words[lang])))

    def cases(table: dict) -> str:
        """By case: {case name: expression} (one test per expression), ZERO_WIDTH_SPACE otherwise."""
        by_value = {}
        for name, value in table.items():
            by_value.setdefault(value, []).append(name)
        expr = zwsp
        for value, names in reversed(by_value.items()):
            expr = f"({' || '.join(f'{case} == {c[n]}' for n in names)}) ? {value} : ({expr})"
        return expr

    def hour_number(t):
        return f"[IS_24_HOUR_MODE] ? (([HOUR_0_23] + {t}) % 24) : ((([HOUR_0_23] + {t} + 11) % 12) + 1)"

    def hour_suffix(t):
        # No string concatenation in expressions ("a" + "b" is a number): the space goes in the literals.
        am, pm = say(lambda w: " " + w["am_pm"][0]), say(lambda w: " " + w["am_pm"][1])
        return f'[IS_24_HOUR_MODE] ? ":00" : ((([HOUR_0_23] + {t}) % 24 < 12) ? {am} : {pm})'

    # The caption: " · " + before + time 1 + between + time 2 + after (Template runs, so a time is a number and its
    # suffix: ":00", or " PM").
    sep = " · "
    first = f"(({case} == {c['until']}) ? {ref('end')} : {ref('start')})"
    timed = ("until", "between", "since")
    params = [
        cases({"until": say(lambda w: sep + w["until"][0]), "between": say(lambda w: sep + w["between"][0]),
               "since": say(lambda w: sep + w["from"][0]), "possible": say(lambda w: sep + w["possible"][0])}),
        cases({n: f"({hour_number(first)})" for n in timed}),
        cases({n: f"({hour_suffix(first)})" for n in timed}),
        cases({"until": say(lambda w: w["until"][1]), "between": say(lambda w: w["between"][1]),
               "since": say(lambda w: w["from"][1])}),
        cases({"between": f"({hour_number(ref('end'))})"}),
        cases({"between": f"({hour_suffix(ref('end'))})"}),
        cases({"between": say(lambda w: w["between"][2])}),
    ]
    box = dict(x=0, y=SLOT_TEXT["y"], width=width, height=SLOT_TEXT["height"])
    size = fmt(SLOT_TEXT["size"])

    def template(exprs):
        return f'<Template>{cdata("%s" * len(exprs))}' + "".join(f'<Parameter expression="{attr(e)}" />' for e in exprs) + "</Template>"

    def answer(word, color, name, indent):
        fonts = tag("Font", dict(family=TIME_FONT, size=size, color=color), template([say(lambda w: w[word])]), indent + 2)
        fonts += tag("Font", dict(family=FILLER_FONT, size=size, color=NEUTRAL), template(params), indent + 2)
        text = tag("Text", dict(align="CENTER", maxLines="1", isAutoSize="TRUE", ellipsis="TRUE"), fonts, indent + 1)
        return tag("PartText", dict(name=name, **box), text, indent)

    title_fonts = tag("Font", dict(family=TIME_FONT, size=fmt(SLOT_TITLE["size"]), color=CITY),
                      template(["[WEATHER.TEMPERATURE]", '"°"']), indent + 7)
    title_fonts += tag("Font", dict(family=FILLER_FONT, size=fmt(SLOT_TITLE["size"]), color=FILLER),
                       template(['" "', "[WEATHER.CONDITION_NAME]"]), indent + 7)
    title = tag("PartText", dict(name="weather_title", x=0, y=SLOT_TITLE["y"], width=width, height=SLOT_TITLE["height"]),
                tag("Text", dict(align="CENTER", maxLines="1", ellipsis="TRUE"), title_fonts, indent + 6), indent + 5)
    none = tag("PartText", dict(name="weather_none", **box), tag("Text", dict(align="CENTER", maxLines="1", isAutoSize="TRUE", ellipsis="TRUE"),
               tag("Font", dict(family=FILLER_FONT, size=size, color=NEUTRAL), template([say(lambda w: w["no_forecast"])]), indent + 7),
               indent + 6), indent + 5)
    rain = tag("Expressions", {}, tag("Expression", dict(name="rain"), cdata(f"{case} <= {c['possible']}"), indent + 7), indent + 6)
    rain += tag("Compare", dict(expression="rain"), answer("rain", RAIN, "weather_rain", indent + 7), indent + 6)
    rain += tag("Default", {}, answer("dry", DRY, "weather_dry", indent + 7), indent + 6)
    answer_body = tag("Expressions", {}, tag("Expression", dict(name="available"), cdata("[WEATHER.IS_AVAILABLE]"), indent + 5), indent + 4)
    answer_body += tag("Compare", dict(expression="available"), title + tag("Condition", {}, rain, indent + 5), indent + 4)
    answer_body += tag("Default", {}, none, indent + 4)
    # A tap opens the weather app: the slot passes taps through while it has no provider (or no data); one that draws
    # takes them itself, and then this isn't drawn (probed on Wear OS 7, all three orientations).
    group = tag("Group", dict(name="weather_answer", x=SLOT["x"], y=SLOT["y"], width=width, height=SLOT["height"]),
                tag("Launch", dict(target=WEATHER_APP), None, indent + 3)
                + tag("Condition", {}, answer_body, indent + 3) + tag("Variant", dict(mode="AMBIENT", target="alpha", value="190"), None, indent + 3), indent + 2)
    # Only the selected orientation's slot counts: the others are off, and their References may keep old values.
    silent = "(" + by_orientation(lambda option, angle, big, bulb: " + ".join(f"[REFERENCE.{weather_drawn(big, kind)}]" for kind in WEATHER_KINDS)) + ") == 0"
    body = tag("Expressions", {}, tag("Expression", dict(name="silent"), cdata(silent), indent + 2), indent + 1)
    body += tag("Compare", dict(expression="silent"), group, indent + 1)
    return tag("Condition", {}, body, indent)


def turned(x, y, w, h, angle) -> tuple:
    """Where a w x h box at x, y goes when the face turns by angle (clockwise, around its centre): the box's top left
    corner once its centre has moved, in whole pixels; the slot then turns by the same angle around its own centre."""
    dx, dy, a = x + w / 2 - C, y + h / 2 - C, math.radians(angle)
    cx, cy = C + dx * math.cos(a) - dy * math.sin(a), C + dx * math.sin(a) + dy * math.cos(a)
    return round(cx - w / 2), round(cy - h / 2)


def turn_attrs(angle) -> dict:
    return dict(pivotX="0.5", pivotY="0.5", angle=fmt(angle)) if angle else {}


WEATHER_KINDS = ("LONG_TEXT", "SHORT_TEXT")  # what the weather slot draws (and EMPTY: nothing)


def weather_drawn(slot_id, kind) -> str:
    return f"weather_drawn_{slot_id}_{kind.lower()}"


def big_slot(slot_id=BIG_SLOT_ID, angle=0, option="") -> list:
    w, h = SLOT["width"], SLOT["height"]
    x, y = turned(SLOT["x"], SLOT["y"], w, h, angle)
    # Rainy's complication; without Rainy on the watch, no provider: the system then sends the slot no data (NO_DATA,
    # which WFF has no Complication for) and the face draws its own answer under the slot (weather_answer).
    body = tag("DefaultProviderPolicy", dict(defaultSystemProvider="EMPTY", defaultSystemProviderType="EMPTY",
                                             primaryProvider=RAINY_PROVIDER, primaryProviderType="LONG_TEXT"), None, 2)
    body += tag("BoundingRoundBox", dict(x=0, y=0, width=w, height=h, cornerRadius=fmt(h / 2)), None, 2)
    for kind in WEATHER_KINDS:
        # What the slot draws says that it draws something (see weather_answer; Reference names are unique).
        drawn = tag("Group", dict(name=f"weather_drawn{option}_{kind.lower()}", x=0, y=0, width=1, height=1, angle=1),
                    tag("Reference", dict(name=weather_drawn(slot_id, kind), source="angle", defaultValue=0), None, 4), 3)
        body += tag("Complication", dict(type=kind), drawn + slot_title(w, 3) + slot_text(w, 3), 2)
    body += tag("Complication", dict(type="EMPTY"), [], 2)
    body += tag("Variant", dict(mode="AMBIENT", target="alpha", value="190"), None, 2)
    return tag("ComplicationSlot", dict(slotId=slot_id, name=f"weather{option}", displayName="slot_weather", x=x, y=y,
                                        width=w, height=h, **turn_attrs(angle), supportedTypes="LONG_TEXT SHORT_TEXT EMPTY",
                                        isCustomizable="TRUE"), body, 1)


def text_span(start, end, side, span=SIDE_TEXT_SPAN) -> tuple:
    """Where an arc's text goes, clockwise: all of the top arc, [span] degrees centred on a side arc's middle."""
    if side == "top":
        return start, end
    middle = (start + end) / 2
    return middle - span / 2, middle + span / 2


def icon_degrees() -> float:
    """How much of the edge the icon takes, from the arc's start: the track's inset, the icon, a gap."""
    return TRACK["inset"] + math.degrees(ARC_ICON["size"] / ARC_ICON["radius"]) + ARC_ICON["gap"]


def arc_icon(start, end, indent) -> list:
    """An arc's icon (the provider's: battery...), on the edge at the arc's start, turned like the text there: tops
    outwards, or inwards in the orientations where the text runs counter-clockwise (see flipped_tilts)."""
    r, size = ARC_ICON["radius"], ARC_ICON["size"]
    degrees = start + TRACK["inset"] + math.degrees(size / 2 / r)
    a = math.radians(degrees)
    x, y = C + r * math.sin(a) - size / 2, C - r * math.cos(a) - size / 2

    def part(turn, indent):
        return tag("PartImage", dict(x=round(x), y=round(y), width=size, height=size, pivotX="0.5", pivotY="0.5",
                                     angle=fmt(degrees + turn), tintColor=FILLER),
                   tag("Image", dict(resource="[COMPLICATION.MONOCHROMATIC_IMAGE]"), None, indent + 2), indent + 1)

    return by_direction((start + end) / 2, lambda direction, i: part(0 if direction == "CLOCKWISE" else 180, i), indent, "icon")


def arc_text(start, end, side, expressions, indent, size=ARC_TEXT_SIZE, span=SIDE_TEXT_SPAN) -> list:
    """Curved text, centred: (voice, expression) runs; the "day" voice is the filler's colour in the time's font."""
    fonts = []
    for voice, expr in expressions:
        family = FILLER_FONT if voice == "filler" else TIME_FONT
        color = CITY if voice == "time" else FILLER
        fonts += tag("Font", dict(family=family, size=fmt(size), color=color),
                     f'<Template>{cdata("%s")}<Parameter expression="{attr(expr)}" /></Template>', indent + 3)
    d = ARC_TEXT_RADIUS * 2
    middle = (start + end) / 2
    start, end = text_span(start, end, side, span)

    def part(direction, indent):
        a, b = (start, end) if direction == "CLOCKWISE" else (end, start)
        tc = dict(centerX=fmt(C), centerY=fmt(C), width=fmt(d), height=fmt(d), startAngle=fmt(a), endAngle=fmt(b),
                  direction=direction, align="CENTER", ellipsis="TRUE")
        return tag("PartText", dict(x=0, y=0, width=SIZE, height=SIZE), tag("TextCircular", tc, fonts, indent + 2), indent + 1)

    return by_direction(middle, part, indent, "text")


def by_direction(middle, part, indent, name) -> list:
    """part(direction, indent) clockwise; or, when turned past sideways it would read upside down, also
    counter-clockwise (tops inwards), the orientation setting picking one by alpha."""
    flips = flipped_tilts(middle)
    if not flips:
        return part("CLOCKWISE", indent)
    flipped = " || ".join(f'[CONFIGURATION.{TILT_SETTING}] == "{option}"' for option in flips)
    out = []
    for direction, shown in (("CLOCKWISE", "0 : 255"), ("COUNTER_CLOCKWISE", "255 : 0")):
        alpha = tag("Transform", dict(target="alpha", value=f"({flipped}) ? {shown}"), None, indent + 2)
        out += tag("Group", dict(name=f"arc_{name}_{direction.lower()}", x=0, y=0, width=SIZE, height=SIZE),
                   alpha + ["    " + line for line in part(direction, indent)], indent + 1)
    return out


def flipped_tilts(middle) -> list:
    """The tilted orientations in which clockwise text centred at [middle] degrees would turn past sideways
    (its tops more than 90 degrees from up): the right arc on the left wrist, the left arc on the right wrist."""
    return [option for option, angle, _, _ in TILTS if angle and abs((middle + angle + 180) % 360 - 180) > 90]


# How far a value has come, 0 to 1 (0 when the provider sends no range or no target).
PROGRESS = {
    "RANGED_VALUE": "([COMPLICATION.RANGED_VALUE_MAX] > [COMPLICATION.RANGED_VALUE_MIN]) ? clamp(([COMPLICATION.RANGED_VALUE_VALUE]"
                    " - [COMPLICATION.RANGED_VALUE_MIN]) / ([COMPLICATION.RANGED_VALUE_MAX] - [COMPLICATION.RANGED_VALUE_MIN]), 0, 1) : 0",
    "GOAL_PROGRESS": "([COMPLICATION.GOAL_PROGRESS_TARGET_VALUE] > 0)"
                     " ? clamp([COMPLICATION.GOAL_PROGRESS_VALUE] / [COMPLICATION.GOAL_PROGRESS_TARGET_VALUE], 0, 1) : 0",
}


def arc_track(start, end, progress, indent, after_icon=False) -> list:
    """The track on an arc's outer edge, faint, and over it the part [progress] (0 to 1) has covered, clockwise; with
    after_icon it starts past the icon."""
    r, inset = TRACK["radius"], TRACK["inset"]
    a, b = start + (icon_degrees() if after_icon else inset), end - inset

    def arc(color, alpha, transform):
        stroke = tag("Stroke", dict(color=color, thickness=TRACK["thickness"], cap="ROUND"), None, indent + 2)
        return tag("PartDraw", dict(x=0, y=0, width=SIZE, height=SIZE, alpha=alpha),
                   tag("Arc", dict(centerX=fmt(C), centerY=fmt(C), width=fmt(2 * r), height=fmt(2 * r), startAngle=fmt(a), endAngle=fmt(b)),
                       transform + stroke, indent + 1), indent)

    covered = tag("Transform", dict(target="endAngle", value=f"{fmt(a)} + {fmt(b - a)} * ({progress})"), None, indent + 2)
    return arc(FILLER, TRACK["alpha"], []) + arc(RAIN, None, covered)


def polar(degrees, r) -> tuple:
    a = math.radians(degrees)
    return C + r * math.sin(a), C - r * math.cos(a)


def arange(lo, hi, step) -> list:
    n = max(1, math.ceil((hi - lo) / step))
    return [lo + (hi - lo) * i / n for i in range(n + 1)]


def arc_points(start, end) -> list:
    """Points on the outline of everything a side arc draws, straight: its text (as far as the wider of SIDE_TEXT_SPAN
    and LONG_TEXT_SPAN and ARC_TEXT_REACH let it go), its track with the stroke's round ends, its icon (where arc_icon puts it)."""
    points = []
    lo, hi = text_span(start, end, "side", max(SIDE_TEXT_SPAN, LONG_TEXT_SPAN))
    for r in (ARC_TEXT_RADIUS + ARC_TEXT_REACH[0], ARC_TEXT_RADIUS + ARC_TEXT_REACH[1]):
        points += [polar(d, r) for d in arange(lo, hi, 0.25)]
    half = TRACK["thickness"] / 2
    cap = math.degrees(half / TRACK["radius"])
    for r in (TRACK["radius"] - half, TRACK["radius"] + half):
        points += [polar(d, r) for d in arange(start + TRACK["inset"] - cap, end - TRACK["inset"] + cap, 0.25)]
    r, size = ARC_ICON["radius"], ARC_ICON["size"]
    degrees = start + TRACK["inset"] + math.degrees(size / 2 / r)
    x, y = polar(degrees, r)
    cx, cy = round(x - size / 2) + size / 2, round(y - size / 2) + size / 2
    a = math.radians(degrees)
    for u in arange(-size / 2, size / 2, 1):
        for v in (-size / 2, size / 2):
            for du, dv in ((u, v), (v, u)):
                points.append((cx + du * math.cos(a) - dv * math.sin(a), cy + du * math.sin(a) + dv * math.cos(a)))
    return points


def arc_cap() -> float:
    """How far, in degrees, a touch band's round end may reach past its end angle (or short of it), at its inner edge."""
    return math.degrees(ARC_THICKNESS / 2 / (C - ARC_THICKNESS))


def arc_band(start, end) -> tuple:
    """A side arc's touch band: what the arc draws, turned to either side by TILT_DEGREES, and the band's round ends."""
    middle = (start + end) / 2
    offsets = [(math.degrees(math.atan2(x - C, C - y)) - middle + 180) % 360 - 180 for x, y in arc_points(start, end)]
    margin = TILT_DEGREES + arc_cap()
    return math.floor(middle + min(offsets) - margin), math.ceil(middle + max(offsets) + margin)


def arc_slot(slot_id, start, end, side, provider, provider_type, primary=(None, None)) -> list:
    body = tag("DefaultProviderPolicy", dict(defaultSystemProvider=provider, defaultSystemProviderType=provider_type,
                                             primaryProvider=primary[0], primaryProviderType=primary[1]), None, 2)
    band = arc_band(start, end)
    body += tag("BoundingArc", dict(centerX=fmt(C), centerY=fmt(C), width=SIZE, height=SIZE, thickness=ARC_THICKNESS,
                                    startAngle=fmt(band[0]), endAngle=fmt(band[1]), isRoundEdge="TRUE"), None, 2)
    # The title and the value ("Thu Oct 8", "Steps 5,910"). Long text ("Berlin RAIN · Until 17:00", the next event) has
    # no room for a title: the text alone, smaller and wider, cut short with an ellipsis. A 0-100 value gets its "%"
    # (the watch battery sends a bare "100"); a range or a goal draws the track, which shows how far it has come (a
    # goal's target as text, "5,910 / 10000", overflowed a titled arc), and without a title gets the provider's icon
    # ("100%" with the battery). A text complication with no text (a countdown with no date set) draws nothing. A
    # range or a goal without text still draws its track and icon.
    text = "[COMPLICATION.TEXT]"
    percent = (f'([COMPLICATION.RANGED_VALUE_MAX] != 100 || subText({text}, textLength({text}) - 1, textLength({text})) == "%")'
               f' ? "{ZERO_WIDTH_SPACE}" : "%"')
    titled = "textLength([COMPLICATION.TITLE]) > 0"
    with_title = [("filler", "[COMPLICATION.TITLE]"), ("filler", f'({titled}) ? " " : "{ZERO_WIDTH_SPACE}"'), ("time", text)]
    # supportedTypes is in order of preference: the system hands a provider the first one it supports, so a step count
    # with a goal comes as the goal, not as a bare range.
    for kind in ("RANGED_VALUE", "GOAL_PROGRESS", "SHORT_TEXT", "LONG_TEXT"):
        end_runs = [("time", percent)] if kind == "RANGED_VALUE" else []

        def track(indent, after_icon=False):
            return arc_track(start, end, PROGRESS[kind], indent, after_icon) if kind in PROGRESS else []

        # "textLength(...) == 0" is never true for a missing text (it is no text at all), so the Compares ask for text.
        cond = tag("Expressions", {}, tag("Expression", dict(name="titled"), cdata(titled), 4)
                   + tag("Expression", dict(name="valued"), cdata(f"textLength({text}) > 0"), 4), 3)
        if kind == "LONG_TEXT":
            cond += tag("Compare", dict(expression="valued"),
                        arc_text(start, end, side, [("time", text)], 3, size=LONG_TEXT_SIZE, span=LONG_TEXT_SPAN), 3)
        else:
            cond += tag("Compare", dict(expression="titled"), track(4) + arc_text(start, end, side, with_title + end_runs, 3), 3)
            iconed = track(4, after_icon=True) + arc_icon(start, end, 3) if kind in PROGRESS else []
            cond += tag("Compare", dict(expression="valued"), iconed + arc_text(start, end, side, [("time", text)] + end_runs, 3), 3)
            if kind in PROGRESS:
                cond += tag("Default", {}, iconed, 3)
        # Turned with the face, around its centre (the slot is the whole face).
        content = tag("Group", dict(name=f"arc_{side}_{kind.lower()}", x=0, y=0, width=SIZE, height=SIZE, pivotX="0.5", pivotY="0.5"),
                      tag("Transform", dict(target="angle", value=tilt_expression()), None, 4) + ["    " + line for line in tag("Condition", {}, cond, 2)], 3)
        body += tag("Complication", dict(type=kind), content, 2)
    body += tag("Complication", dict(type="EMPTY"), [], 2)
    body += tag("Variant", dict(mode="AMBIENT", target="alpha", value="170"), None, 2)
    return tag("ComplicationSlot", dict(slotId=slot_id, name=f"arc_{side}", displayName=f"slot_{side}", x=0, y=0, width=SIZE, height=SIZE,
                                        supportedTypes="GOAL_PROGRESS RANGED_VALUE SHORT_TEXT LONG_TEXT EMPTY", isCustomizable="TRUE"), body, 1)


def bulb_slot(slot_id=BULB["slot"], angle=0, option="") -> list:
    s = BULB["size"]
    x, y = turned(BULB["cx"] - s / 2, BULB["cy"] - s / 2, s, s, angle)
    body = tag("DefaultProviderPolicy", dict(defaultSystemProvider="SUNRISE_SUNSET", defaultSystemProviderType="SHORT_TEXT"), None, 2)
    body += tag("BoundingOval", dict(x=0, y=0, width=s, height=s), None, 2)
    disc = tag("Ellipse", dict(x=0, y=0, width=s, height=s), tag("Fill", dict(color=RAIN), None, 5), 4)
    disc += tag("Variant", dict(mode="AMBIENT", target="alpha", value="0"), None, 4)
    background = tag("PartDraw", dict(x=0, y=0, width=s, height=s, alpha=BULB["background_alpha"]), disc, 3)

    # The inner layout below is drawn for a BULB_DESIGN px circle, scaled by k into this one (inside its padding).
    k = (s - 2 * BULB["padding"]) / BULB_DESIGN
    inset = (s - BULB_DESIGN * k) / 2

    # The texts keep `side` px (of BULB_DESIGN) from the circle's edge, and the value sits on the circle's middle,
    # where it is widest: "18:42" keeps about 10 px clear of the edge.
    side = 7

    def text(expr, size, y, h, voice, indent=3):
        f = tag("Font", dict(family=TIME_FONT if voice == "time" else FILLER_FONT, size=fmt(size * k), color=CITY if voice == "time" else FILLER),
                f'<Template>{cdata("%s")}<Parameter expression="{expr}" /></Template>', indent + 2)
        # WFF types a part's geometry as integers: round the edges, keeping the text centred in the circle.
        left = round(inset + side * k)
        return tag("PartText", dict(x=left, y=round(inset + y * k), width=s - 2 * left, height=round(h * k)),
                   tag("Text", dict(align="CENTER", maxLines="1", isAutoSize="TRUE", ellipsis="TRUE"), f, indent + 1), indent)

    # Above the text: the title, or the provider's icon when it sends none (sunrise / sunset sends the time and a sun).
    icon_size = round(14 * k)
    icon = tag("PartImage", dict(x=round((s - icon_size) / 2), y=round(inset + 6 * k), width=icon_size, height=icon_size, tintColor=FILLER),
               tag("Image", dict(resource="[COMPLICATION.MONOCHROMATIC_IMAGE]"), None, 6), 5)
    top = tag("Expressions", {}, tag("Expression", dict(name="titled"), cdata("textLength([COMPLICATION.TITLE]) > 0"), 5), 4)
    top += tag("Compare", dict(expression="titled"), text("[COMPLICATION.TITLE]", 12, 5, 16, "filler", 5), 4)
    top += tag("Default", {}, icon, 4)
    short = background + tag("Condition", {}, top, 3) + text("[COMPLICATION.TEXT]", 16, 21, 24, "time")
    body += tag("Complication", dict(type="SHORT_TEXT"), short, 2)
    body += tag("Complication", dict(type="MONOCHROMATIC_IMAGE"), background + tag("PartImage", dict(x=round(s * 0.2), y=round(s * 0.2), width=round(s * 0.6), height=round(s * 0.6), tintColor=CITY),
                                                                             tag("Image", dict(resource="[COMPLICATION.MONOCHROMATIC_IMAGE]"), None, 4), 3), 2)
    body += tag("Complication", dict(type="SMALL_IMAGE"), tag("PartImage", dict(x=round(s * 0.1), y=round(s * 0.1), width=round(s * 0.8), height=round(s * 0.8)),
                                                              tag("Image", dict(resource="[COMPLICATION.SMALL_IMAGE]"), None, 4), 3), 2)
    body += tag("Complication", dict(type="EMPTY"), [], 2)
    body += tag("Variant", dict(mode="AMBIENT", target="alpha", value="170"), None, 2)
    return tag("ComplicationSlot", dict(slotId=slot_id, name=f"bulb{option}", displayName="slot_bulb", x=x, y=y, width=s, height=s,
                                        **turn_attrs(angle), supportedTypes="SHORT_TEXT MONOCHROMATIC_IMAGE SMALL_IMAGE EMPTY", isCustomizable="TRUE"), body, 1)


def user_configurations() -> list:
    options = [tag("ColorOption", dict(id=pid, displayName=f"color_{pid}", colors=" ".join(colors)), None, 3)[0]
               for pid, colors in PALETTES]
    setting = tag("ColorConfiguration", dict(id=COLOR_SETTING, displayName="color_setting", defaultValue=PALETTES[0][0]), options, 2)
    backgrounds = [tag("ColorOption", dict(id=bid, displayName=f"background_{bid}", colors=color), None, 3)[0] for bid, color in BACKGROUNDS]
    setting += tag("ColorConfiguration", dict(id=BACKGROUND_SETTING, displayName="background_setting", defaultValue=DEFAULT_BACKGROUND), backgrounds, 2)
    ambient = [tag("ListOption", dict(id=option, displayName=f"ambient_{option}"), None, 3)[0] for option in AMBIENT_OPTIONS]
    setting += tag("ListConfiguration", dict(id=AMBIENT_SETTING, displayName="ambient_setting", defaultValue=AMBIENT_OPTIONS[0]), ambient, 2)
    # Each orientation enables its own weather slot and circle (the side arcs are in no option: always on).
    tilts = [tag("ListOption", dict(id=option, displayName=f"orientation_{option}", complicationSlotIds=f"{big} {bulb}"), None, 3)[0]
             for option, _, big, bulb in TILTS]
    setting += tag("ListConfiguration", dict(id=TILT_SETTING, displayName="orientation_setting", defaultValue=TILTS[0][0]), tilts, 2)
    return tag("UserConfigurations", {}, setting, 1)


def date_arc() -> list:
    """The date along the top ("Sun 11 Oct"): the day in the Filler colour, the rest in City, in the watch's language."""
    runs = [("day", 'icuText("EEE")'), ("day", '" "'), ("time", 'icuBestText("d MMM")')]
    body = arc_text(*DATE_ARC, "top", runs, 2)
    return tag("Group", dict(name="date", x=0, y=0, width=SIZE, height=SIZE),
               body + tag("Variant", dict(mode="AMBIENT", target="alpha", value="170"), None, 3), 2)


def by_orientation(value) -> str:
    """value(option, angle, weather slot id, circle slot id) (an expression) in the selected orientation, read from
    the setting itself. (Not from References published by Groups in the options: on a Pixel Watch 4 they kept the
    values of options no longer drawn after the face editor, and the face turned by their sum.)"""
    *others, last = TILTS
    expr = value(*last)
    for tilt in reversed(others):
        expr = f'([CONFIGURATION.{TILT_SETTING}] == "{tilt[0]}") ? ({value(*tilt)}) : ({expr})'
    return expr


def tilt_expression() -> str:
    return by_orientation(lambda option, angle, big, bulb: str(angle))


def active_background() -> list:
    """The palette's dark tint behind everything, hidden in ambient, where the Scene's black shows."""
    fill = tag("Rectangle", dict(x=0, y=0, width=SIZE, height=SIZE), tag("Fill", dict(color=ACTIVE_BACKGROUND), None, 4), 3)
    fill += tag("Variant", dict(mode="AMBIENT", target="alpha", value="0"), None, 3)
    return tag("PartDraw", dict(x=0, y=0, width=SIZE, height=SIZE), fill, 1)


def watchface_xml(all_words: dict, cmap, measure: Measure) -> str:
    scene = active_background()
    scene += weather_rule()
    face = tag("Transform", dict(target="angle", value=tilt_expression()), None, 2)
    face += date_arc() + clock(all_words, cmap, measure, indent=2) + ["    " + line for line in ambient_clock(all_words, cmap, measure)]
    face += weather_answer(2)
    scene += tag("Group", dict(name="turning", x=0, y=0, width=SIZE, height=SIZE, pivotX="0.5", pivotY="0.5"), face, 1)
    for option, angle, big, bulb in TILTS:
        scene += big_slot(big, angle, "" if not angle else f"_{option}")
        scene += bulb_slot(bulb, angle, "" if not angle else f"_{option}")
    for a in ARCS:
        scene += arc_slot(*a)
    lines = ['<?xml version="1.0" encoding="utf-8"?>', f"<!-- {GENERATED} -->",
             f'<WatchFace width="{SIZE}" height="{SIZE}">',
             '    <Metadata key="CLOCK_TYPE" value="DIGITAL" />',
             '    <Metadata key="PREVIEW_TIME" value="10:08:32" />',
             *user_configurations(),
             f'    <Scene backgroundColor="{BACKGROUND}">', *["    " + s for s in scene], "    </Scene>", "</WatchFace>"]
    return "\n".join(lines) + "\n"


def manifest() -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!-- {GENERATED}
     The package, version and SDK levels come from watchface/build.gradle.kts. -->
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <uses-feature android:name="android.hardware.type.watch" />

    <application
        android:label="@string/watch_face_name"
        android:hasCode="false">
        <property
            android:name="com.google.wear.watchface.format.version"
            android:value="{WFF_VERSION}" />
        <property
            android:name="com.google.wear.watchface.format.publisher"
            android:value="Rainy" />
    </application>
</manifest>
"""


def watch_face_info() -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!-- {GENERATED} -->
<WatchFaceInfo>
    <Preview value="@drawable/preview" />
    <Category value="CATEGORY_EMPTY" />
    <AvailableInRetail value="false" />
    <MultipleInstancesAllowed value="true" />
    <Editable value="true" />
</WatchFaceInfo>
"""


def strings_xml(lang: str) -> str:
    names = dict(SLOT_NAMES[lang])
    setting, *palettes = COLOR_NAMES[lang]
    names["color_setting"] = setting
    names["color_rainy"] = FACE_NAME
    names.update({f"color_{pid}": name for (pid, _), name in zip(PALETTES[1:], palettes, strict=True)})
    background, *backgrounds = BACKGROUND_NAMES[lang]
    names["background_setting"] = background
    names.update({f"background_{bid}": name for (bid, _), name in zip(BACKGROUNDS, backgrounds, strict=True)})
    tilt, *tilts = TILT_NAMES[lang]
    names["orientation_setting"] = tilt
    names.update({f"orientation_{option}": name for (option, *_), name in zip(TILTS, tilts, strict=True)})
    ambient, *options = AMBIENT_NAMES[lang]
    names["ambient_setting"] = ambient
    names.update({f"ambient_{option}": name for option, name in zip(["light", "bold"], options, strict=True)})
    if lang == "en":
        names = {"watch_face_name": FACE_NAME, **names}
    lines = ['<?xml version="1.0" encoding="utf-8"?>', f"<!-- {GENERATED} -->", "<resources>"]
    for k, v in names.items():
        lines.append(f'    <string name="{k}">{escape(v).replace(chr(39), chr(92) + chr(39))}</string>')
    lines.append("</resources>")
    return "\n".join(lines) + "\n"


def make_fonts() -> dict:
    out = {}
    for name, axes in FONTS.items():
        font = TTFont(VARIABLE_FONT, recalcTimestamp=False)
        inst = instantiateVariableFont(font, axes, updateFontNames=False)
        options = subset.Options()
        options.layout_features = ["*"]
        options.name_IDs = ["*"]
        options.notdef_outline = True
        s = subset.Subsetter(options)
        s.populate(unicodes=FONT_UNICODES)
        s.subset(inst)
        buf = io.BytesIO()
        inst.save(buf)
        out[f"res/font/{name}.ttf"] = buf.getvalue()
    return out


def report(all_words: dict, fonts: dict):
    """How small the time gets: the size of the longest lines of each language (Latin cuts)."""
    measure = Measure(fonts)
    cmap = TTFont(io.BytesIO(fonts[f"res/font/{TIME_FONT}.ttf"])).getBestCmap()
    for lang, words in all_words.items():
        if needs_system_font(words, cmap):
            print(f"{lang}: system font, each line shrinks on its own")
            continue
        period = words.get("period", "")
        exc = words.get("exceptions", {}).values()
        for label, texts in (("line 1", [*words["hour12"].values(), *words["hour24"].values(), *words["hour12_at_00"].values(),
                                          *words["hour24_at_00"].values(), *(e[0] for e in exc)]),
                             ("line 2", [t + period for t in [*words["minute"].values(), *(e[1] for e in exc)]])):
            sizes = sorted((measure.fit(t) * TIME_SIZE, t) for t in texts)
            small = sum(1 for size, _ in sizes if size < TIME_SIZE)
            print(f"{lang} {label}: smallest {sizes[0][0]:.0f}px {sizes[0][1]!r}; {small}/{len(sizes)} below {TIME_SIZE}px")


# The layout's rules, checked on every run

def turn_point(point, angle, cx=C, cy=C) -> tuple:
    dx, dy, a = point[0] - cx, point[1] - cy, math.radians(angle)
    return cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a)


class Area:
    """A slot's touch area on the screen. inside(): strictly inside it (by more than EPSILON px); holds(): inside it
    far enough from a round end that the area surely clips nothing there."""
    EPSILON = 0.01

    def __init__(self, name, inside, holds=None):
        self.name, self.inside, self.holds = name, inside, holds


def box_area(name, x, y, w, h, angle, corner) -> Area:
    """A w x h box at x, y with round corners (BoundingRoundBox, BoundingOval when round), turned around its centre."""
    cx, cy = x + w / 2, y + h / 2

    def inside(p):
        u, v = turn_point(p, -angle, cx, cy)
        qx, qy = max(abs(u - cx) - (w / 2 - corner), 0), max(abs(v - cy) - (h / 2 - corner), 0)
        outside_core = abs(u - cx) > w / 2 or abs(v - cy) > h / 2
        return not outside_core and math.hypot(qx, qy) < corner - Area.EPSILON
    return Area(name, inside)


def band_area(name, start, end) -> Area:
    """A BoundingArc touch band, ARC_THICKNESS from the edge: its round ends may reach a cap's width past its angles
    (inside() counts them) or stop short of them (holds() keeps clear of them)."""
    r0, r1, cap = C - ARC_THICKNESS, C, arc_cap()
    ends = [polar(d, C - ARC_THICKNESS / 2) for d in (start, end)]

    def angle_in(p, lo, hi):
        d = math.degrees(math.atan2(p[0] - C, C - p[1]))
        return (d - lo) % 360 <= (hi - lo) % 360

    def inside(p):
        r = math.hypot(p[0] - C, p[1] - C)
        if any(math.hypot(p[0] - e[0], p[1] - e[1]) < ARC_THICKNESS / 2 - Area.EPSILON for e in ends):
            return True
        return r0 + Area.EPSILON < r < r1 - Area.EPSILON and angle_in(p, start, end)

    def holds(p):
        return r0 <= math.hypot(p[0] - C, p[1] - C) <= r1 and angle_in(p, start + cap, end - cap)
    return Area(name, inside, holds)


def check_layout(xml: str):
    """The rules the orientations must keep (exits when one breaks):
    - every part's geometry is whole pixels (WFF types it as integers: "'24.3' is not a valid value for 'integer'");
    - in each orientation, no enabled slot's touch area reaches another enabled slot's drawing, so a tap on what a slot
      shows opens that slot;
    - a side arc's touch band, which clips its drawing, holds all of it in every orientation."""
    errors = []
    for m in re.finditer(r"<(Part\w+|Group|ComplicationSlot|BoundingRoundBox|BoundingOval|Ellipse|Rectangle)\b([^>]*)>", xml):
        for key, value in re.findall(r'\b(x|y|width|height)="([^"]*)"', m.group(2)):
            if not re.fullmatch(r"-?\d+", value):
                errors.append(f"<{m.group(1)}> {key}=\"{value}\" is not a whole number")
    w, h, s = SLOT["width"], SLOT["height"], BULB["size"]
    text_height = SLOT_TEXT["y"] + SLOT_TEXT["height"]
    for option, angle, big, bulb in TILTS:
        x, y = turned(SLOT["x"], SLOT["y"], w, h, angle)
        bx, by = turned(BULB["cx"] - s / 2, BULB["cy"] - s / 2, s, s, angle)
        # What each slot draws: the weather slot's title and text boxes, the circle's disc, the arcs' text, track, icon.
        weather = [turn_point((x + u, y + v), angle, x + w / 2, y + h / 2) for u in arange(0, w, 2) for v in arange(0, text_height, 2)]
        disc = [(bx + s / 2 + r * math.sin(math.radians(d)), by + s / 2 - r * math.cos(math.radians(d)))
                for r in arange(0, s / 2, 2) for d in arange(0, 360, 1)]
        drawn = {f"weather {big}": weather, f"circle {bulb}": disc}
        areas = [box_area(f"weather {big}", x, y, w, h, angle, h / 2), box_area(f"circle {bulb}", bx, by, s, s, angle, s / 2)]
        for slot_id, start, end, side, *_ in ARCS:
            drawn[f"{side} arc"] = [turn_point(p, angle) for p in arc_points(start, end)]
            areas.append(band_area(f"{side} arc", *arc_band(start, end)))
        for area in areas:
            for name, points in drawn.items():
                if name == area.name:
                    if area.holds and not all(area.holds(p) for p in points):
                        errors.append(f"{option}: the {name}'s band clips its drawing")
                elif any(area.inside(p) for p in points):
                    errors.append(f"{option}: the {area.name}'s touch area reaches the {name}'s drawing")
    if errors:
        raise SystemExit("The layout breaks its rules:\n  " + "\n  ".join(errors))


# What the side arcs must show whole: titled values at ARC_TEXT_SIZE in SIDE_TEXT_SPAN, long text (no title) at
# LONG_TEXT_SIZE in LONG_TEXT_SPAN. *marked* runs are the value's (City) voice.
ARC_SAMPLES = {
    "short": ["Steps *12,345*", "Sunset *18:42*", "NYC *06:07*", "Mon *Oct 12*", "BPM *176*", "UV *30%*", "*100%*"],
    "long": ["*RAIN · Until 18:00*", "*DRY · Until 16:30*", "*Media controls*"],
}


def check_arc_texts(measure: Measure):
    """Exits when a sample in ARC_SAMPLES is longer than the part of the arc its kind gets (no kerning measured)."""
    errors = []
    for kind, size, span in (("short", ARC_TEXT_SIZE, SIDE_TEXT_SPAN), ("long", LONG_TEXT_SIZE, LONG_TEXT_SPAN)):
        room = math.radians(span) * ARC_TEXT_RADIUS
        for text in ARC_SAMPLES[kind]:
            width = measure.width(text) * size / TIME_SIZE
            if width > room:
                errors.append(f"{text.replace('*', '')!r} is {width:.0f} px, the arc has {room:.0f}")
    if errors:
        raise SystemExit("Arc text is cut short:\n  " + "\n  ".join(errors))


def generate(words_path: Path) -> dict:
    all_words = json.loads(words_path.read_text(encoding="utf-8"))
    missing = [lang for lang in LANGUAGES if lang not in all_words]
    if missing:
        raise SystemExit(f"{words_path}: no words for {missing}")
    all_words = {lang: all_words[lang] for lang in LANGUAGES}
    fonts = make_fonts()
    cmap = TTFont(io.BytesIO(fonts[f"res/font/{TIME_FONT}.ttf"])).getBestCmap()
    files = dict(fonts)
    files["AndroidManifest.xml"] = manifest().encode()
    measure = Measure(fonts)
    xml = watchface_xml(all_words, cmap, measure)
    check_layout(xml)
    check_arc_texts(measure)
    files["res/raw/watchface.xml"] = xml.encode()
    files["res/xml/watch_face_info.xml"] = watch_face_info().encode()
    files["res/values/strings.xml"] = strings_xml("en").encode()
    for lang in LANGUAGES:
        if lang != "en":
            files[f"res/values-{lang}/strings.xml"] = strings_xml(lang).encode()
    return files, all_words


SAMPLE_DATE = ("Thu", "Oct 8")  # the date's day and the rest, in place of the watch's (in its language)
SAMPLE = {2: ("", "5,910"), **{big: ("Berlin", "RAIN · Until 18:00") for _, _, big, _ in TILTS},
          **{bulb: ("", "18:42") for _, _, _, bulb in TILTS}}


def pin(xml: str, spec: str) -> str:
    """Fixes language, mode and time ("it 24 14:54") in a generated watchface.xml."""
    m = re.fullmatch(r"([a-z]{2}) (12|24) (\d{1,2}):(\d{2})", spec)
    if not m:
        raise SystemExit(f'--pin wants "<language> <12|24> <H:MM>", not {spec!r}')
    lang, mode, h, minute = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
    # The language is a string literal: quotes in CDATA, &quot; in attributes.
    parts = re.split(r"(<!\[CDATA\[.*?\]\]>)", xml, flags=re.S)
    for i, part in enumerate(parts):
        q = '"' if part.startswith("<![CDATA[") else "&quot;"
        for name, value in (("LANGUAGE_LOCALE_NAME", f"{q}{lang}{q}"), ("IS_24_HOUR_MODE", "1" if mode == "24" else "0"),
                            ("HOUR_0_23", h), ("HOUR_1_12", h % 12 or 12), ("MINUTE", minute)):
            part = part.replace(f"[{name}]", str(value))
        parts[i] = part
    return "".join(parts)


def sample(xml: str) -> str:
    """SAMPLE as the slots' titles and texts, SAMPLE_DATE as the date (string literals: quotes in CDATA, &quot; in
    attributes)."""
    for expr, text in zip(('icuText("EEE")', 'icuBestText("d MMM")'), SAMPLE_DATE, strict=True):
        xml = xml.replace(attr(expr), attr(f'"{text}"'))
    def slot(m):
        if int(m.group(1)) not in SAMPLE:
            return m.group(0)
        title, text = SAMPLE[int(m.group(1))]

        def sub(part, q):
            return part.replace("[COMPLICATION.TITLE]", q + title + q).replace("[COMPLICATION.TEXT]", q + text + q)
        parts = re.split(r"(<!\[CDATA\[.*?\]\]>)", m.group(0), flags=re.S)
        return "".join(sub(part, '"' if part.startswith("<![CDATA[") else "&quot;") for part in parts)
    return re.sub(r'<ComplicationSlot slotId="(\d+)".*?</ComplicationSlot>', slot, xml, flags=re.S)


def owned(path: Path, out: Path) -> bool:
    """Files this script writes (anything under res/ but the preview)."""
    rel = path.relative_to(out).as_posix()
    return rel == "AndroidManifest.xml" or (rel.startswith("res/") and not rel.startswith("res/drawable"))


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--check", action="store_true")
    p.add_argument("--report", action="store_true")
    p.add_argument("--pin")
    p.add_argument("--sample", action="store_true")
    p.add_argument("--words", type=Path, default=WORDS)
    p.add_argument("--out", type=Path, default=OUT)
    args = p.parse_args()
    if fontTools.version != FONTTOOLS_VERSION:
        print(f"warning: fontTools {fontTools.version}, the fonts were made with {FONTTOOLS_VERSION}", file=sys.stderr)
    files, all_words = generate(args.words)
    xml = files["res/raw/watchface.xml"].decode()
    if args.pin:
        xml = pin(xml, args.pin)
    if args.sample:
        xml = sample(xml)
    files["res/raw/watchface.xml"] = xml.encode()
    out = args.out
    on_disk = {f.relative_to(out).as_posix() for f in out.rglob("*") if f.is_file() and owned(f, out)} if out.exists() else set()
    if args.check:
        bad = sorted(rel for rel, data in files.items() if not (out / rel).is_file() or (out / rel).read_bytes() != data)
        stale = sorted(on_disk - set(files))
        for rel in bad:
            print(f"differs: {rel}")
        for rel in stale:
            print(f"not generated: {rel}")
        if not (out / "res/drawable-nodpi/preview.png").is_file():
            print("missing: res/drawable-nodpi/preview.png (tools/build.sh --preview)")
            bad.append("preview")
        if bad or stale:
            print("The watch face is out of date: run tools/generate.py", file=sys.stderr)
            sys.exit(1)
        print("The watch face is up to date.")
    else:
        for rel in sorted(on_disk - set(files)):
            (out / rel).unlink()
        for rel, data in files.items():
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or target.read_bytes() != data:
                target.write_bytes(data)
        size = len(files["res/raw/watchface.xml"])
        print(f"Wrote {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} (watchface.xml {size // 1024} KB)")
    if args.report:
        report(all_words, {k: v for k, v in files.items() if k.startswith("res/font/")})


if __name__ == "__main__":
    main()
