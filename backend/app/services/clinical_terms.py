"""Colloquial / regional distress phrases -> standardized symptom tags, and language detection.

Deterministic lexicon matching (no model, no network): every tag traces to a phrase that is
literally present in the text. Coverage is deliberately small and the romanised Tamil / Hindi
spellings vary widely, so this is a starting lexicon that needs native-speaker and clinical
review. A missing tag does NOT mean the symptom is absent. Tags are suggestions for staff.
"""
from __future__ import annotations

import re

# tag -> regexes (case-insensitive). Romanised Tamil (ta) / Hindi (hi) are included next to English.
TERMS: dict[str, list[str]] = {
    "Substernal Chest Pressure": [
        r"chest[a-z ]{0,12}(?:heavy|heaviness|like a (?:big |huge |heavy )?(?:rock|stone|weight|elephant))",
        r"(?:rock|stone|weight|elephant) (?:is )?(?:on|sitting on) (?:my )?chest",
        r"chest (?:is )?(?:being )?(?:squeez\w+|crush\w+)", r"band (?:around|across) (?:my )?chest",
        r"nenju\s*(?:bara\w*|baara\w*|baram\w*|kanam\w*|adaipp\w*)", r"nenju\s+(?:ya|vali|valikkudhu)\b.*(?:baram|bara)",
        r"seene (?:par|mein|me) (?:bojh|bhaar|dabav)", r"chhati (?:par|mein|me) (?:bojh|bhaar|dabav)",
        "நெஞ்சு\\s*பாரம்", "सीने\\s*(?:पर|में)\\s*(?:बोझ|भार|दबाव)",
    ],
    "Chest Pain": [
        r"nenju\s*vali", r"seene (?:mein|me) dard", r"chhati (?:mein|me) dard", "நெஞ்சு\\s*வலி", "सीने\\s*में\\s*दर्द",
    ],
    "Acute Dyspnea": [
        r"short(?:ness)? of breath", r"can(?:'|no)?t (?:catch|get) (?:my|his|her) breath", r"barely (?:catch|get) (?:my )?breath",
        r"can(?:'|no)?t breathe", r"hard to breathe", r"struggling to breathe", r"gasping for (?:air|breath)",
        r"moochu\s*(?:thinaral|thinaru|vaang\w*|vara\w*|ellai|vitta\w*)", r"moosu\s*(?:thinaral|vaang\w*)",
        r"saans\s*(?:phool\w*|lene (?:mein|me) (?:takleef|dikkat|taklif)|nahi|nahin)", r"dam\s*phool\w*",
        "மூச்சு\\s*(?:திணறல்|வாங்க)", "सांस\\s*(?:फूल|लेने में)",
    ],
    "Dizziness / Presyncope": [
        r"thalai\s*suth\w*", r"sir\s*ghoom\w*", r"chakkar\s*(?:aa|aa\s*raha|aa\s*rahe)?", "தலை\\s*சுற்ற", "चक्कर",
        r"room (?:is )?spinning", r"feel(?:ing)? (?:like I(?:'m| am) )?(?:going to|about to) (?:faint|pass out)",
    ],
    "Headache": [r"thalai\s*vali", r"sir\s*(?:mein\s*)?dard", r"head (?:is )?(?:splitting|pounding)", "தலை\\s*வலி", "सिर\\s*(?:में\\s*)?दर्द"],
    "Abdominal Pain": [r"vayiru\s*vali", r"pet\s*(?:mein\s*)?dard", r"stomach (?:is )?(?:cramping|killing me)", "வயிறு\\s*வலி", "पेट\\s*(?:में\\s*)?दर्द"],
    "Palpitations": [r"nenju\s*(?:padapad\w*|thudi\w*)", r"dil\s*(?:ki\s*)?dhadkan", r"heart (?:is )?(?:racing|pounding|fluttering)", r"dhadkan\s*(?:tez|badh)"],
    "Fever": [r"kaichal", r"bukhar", r"jwaram", "காய்ச்சல்", "बुखार"],
    "Facial Droop / Speech Difficulty": [
        r"mugam\s*(?:vakk\w*|saiv\w*)", r"chehra\s*(?:tedha|tedha\s*ho|latak\w*)", r"vaai\s*(?:vakk\w*|saiv\w*)",
        r"(?:words|speech) (?:are |is )?(?:coming out )?(?:all )?(?:jumbled|mushy|garbled)",
    ],
    "Limb Weakness / Numbness": [
        r"(?:kai|kal)\s*(?:balam|marathu|thimir)\w*", r"(?:haath|pair)\s*(?:mein\s*)?(?:kamzori|sunn|sun)\w*",
        r"arm (?:feels|is) (?:like )?(?:lead|dead|heavy)",
    ],
}
_COMPILED = {tag: [re.compile(p, re.IGNORECASE) for p in pats] for tag, pats in TERMS.items()}

# Romanised marker words per language (lower-case tokens). Heuristic, used only to LABEL code-switching.
MARKERS = {
    "ta": {"nenju", "barama", "baram", "irukku", "iruku", "moochu", "thinaral", "vali", "vayiru", "thalai",
           "kaichal", "enakku", "romba", "illa", "konjam", "padapadappu", "suthudhu", "kai", "kal", "mugam"},
    "hi": {"seene", "seena", "chhati", "dard", "saans", "mujhe", "mera", "meri", "hai", "bahut", "nahi", "nahin",
           "bukhar", "chakkar", "pet", "sir", "dhadkan", "kamzori", "takleef", "bojh"},
}
SCRIPTS = {
    "ta": re.compile("[஀-௿]"),
    "hi": re.compile("[ऀ-ॿ]"),
}
ENGLISH_STOP = {"the", "and", "my", "is", "i", "can", "barely", "catch", "breath", "chest", "pain", "feel", "like", "with"}
_TOKEN = re.compile(r"[a-zA-Z']+")


def normalize(text: str) -> list[str]:
    """Standardized symptom tags found in ``text`` (ordered by first appearance, de-duplicated)."""
    hits: list[tuple[int, str]] = []
    for tag, regs in _COMPILED.items():
        pos = [m.start() for r in regs if (m := r.search(text))]
        if pos:
            hits.append((min(pos), tag))
    tags = [t for _, t in sorted(hits)]
    if "Substernal Chest Pressure" in tags and "Chest Pain" in tags:
        tags.remove("Chest Pain")  # the more specific description wins
    return tags


def detect_languages(text: str, model_language: str | None = None) -> list[str]:
    """ISO 639-1 codes present in ``text``. Heuristic: script blocks, marker words, and English tokens.

    Whisper reports ONE language per utterance, so ``model_language`` is only added as a hint; the
    romanised marker words are what reveal Tamil/Hindi mixed into English.
    """
    langs: list[str] = []
    for code, rx in SCRIPTS.items():
        if rx.search(text):
            langs.append(code)
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    for code, words in MARKERS.items():
        # A single marker word is enough only for distinctive words; require 2 to avoid "pet"/"sir" noise.
        if code not in langs and len({t for t in tokens if t in words}) >= 2:
            langs.append(code)
    english_like = [t for t in tokens if t not in MARKERS["ta"] | MARKERS["hi"] and len(t) > 1]
    if english_like and (not langs or len(english_like) >= 2 or model_language == "en"):
        if model_language == "en" or any(t in ENGLISH_STOP for t in english_like) or len(english_like) >= 3:
            langs.append("en")
    if model_language and model_language not in langs and not langs:
        langs.append(model_language)
    return list(dict.fromkeys(langs))
