"""Deterministic symptom extraction with negation and uncertainty handling.

Only patterns explicitly present in the text are extracted. Nothing is inferred.
"""
from __future__ import annotations

import re

from app.schemas.models import Onset, SymptomMention

# concept -> (label, regexes). Regexes are matched case-insensitively against a sentence.
LEXICON: dict[str, tuple[str, list[str]]] = {
    "chest_pain": ("Chest pain / discomfort", [
        r"chest (?:pain|pains|pressure|tightness|discomfort|heaviness)",
        r"pain in (?:my |the |his |her )?chest", r"tight(?:ness)? in (?:my |the |his |her )?chest",
        r"pressure on (?:my |the |his |her )?chest"]),
    "dyspnea": ("Difficulty breathing", [
        r"short(?:ness)? of breath", r"(?:difficulty|trouble|hard(?: time)?|struggling)(?: with)? breathing",
        r"can(?:'|no)?t breathe", r"cannot breathe", r"hard to breathe", r"breathless(?:ness)?",
        r"gasping", r"wheezing", r"can(?:'|no)?t catch (?:my|his|her) breath"]),
    "facial_droop": ("Facial droop / facial change", [
        r"fac(?:e|ial)[a-z ]{0,12}(?:droop\w*|sag\w*|dropp\w*|numb\w*|weak\w*|uneven|crooked)",
        r"droop\w* (?:on )?(?:one side|face|of (?:the |his |her |my )?face)",
        r"(?:one )?side of (?:my |the |his |her )?face", r"smile (?:is )?(?:uneven|crooked|lopsided)"]),
    "slurred_speech": ("Speech difficulty / slurred speech", [
        r"slurr\w+", r"(?:trouble|difficulty|hard(?: time)?) (?:speaking|talking|getting (?:the |my |his |her |their )?words out)",
        r"can(?:'|no)?t (?:speak|talk|get (?:the |my |his |her |their )?words out)", r"speech (?:is )?(?:garbled|strange|off)",
        r"words (?:are )?(?:coming out )?(?:wrong|garbled|mixed up)", r"mumbl\w+"]),
    "unilateral_weakness": ("Weakness (arm/leg/one side)", [
        r"(?:arm|leg|hand|side|body)[a-z ]{0,10}(?:is |feels |felt |went |has gone )?(?:weak\w*|limp|heavy)",
        r"weak(?:ness)? (?:in|on) (?:my |the |his |her )?(?:left|right|one|arm|leg|hand)",
        r"can(?:'|no)?t (?:lift|move|raise) (?:my |his |her |the )?(?:arm|leg|hand)",
        r"weak(?:ness)?"]),
    "numbness": ("Numbness / tingling", [r"numb\w*", r"tingling", r"pins and needles"]),
    "confusion": ("Confusion", [r"confus\w+", r"disorient\w+", r"not making sense", r"doesn't know where"]),
    "vision_change": ("Vision change", [
        r"(?:blurr?(?:ed|y)|double|lost|loss of|sudden(?:ly)? can(?:'|no)?t see)[a-z ]{0,10}vision",
        r"vision (?:is |went |has gone )?(?:blurr?(?:ed|y)|double|dark|gone)", r"can(?:'|no)?t see"]),
    "headache": ("Headache", [r"head ?ache", r"head (?:is )?(?:pounding|splitting)"]),
    "dizziness": ("Dizziness", [r"dizz\w+", r"light-?headed\w*", r"room (?:is )?spinning", r"vertigo"]),
    "syncope": ("Fainting / loss of consciousness", [
        r"faint(?:ed|ing)", r"passed out", r"blacked out", r"lost consciousness", r"unresponsive",
        r"collapsed"]),
    "seizure": ("Seizure", [r"seizure\w*", r"convuls\w+", r"shaking uncontrollably"]),
    "bleeding": ("Bleeding", [r"bleed\w+", r"blood loss", r"hemorrhag\w+", r"haemorrhag\w+"]),
    "palpitations": ("Palpitations", [r"palpitations?", r"heart (?:is )?(?:racing|pounding|fluttering)",
                                      r"racing heart"]),
    "nausea": ("Nausea / vomiting", [r"nause\w+", r"vomit\w+", r"throwing up"]),
    "fever": ("Fever", [r"fever\w*", r"feverish", r"chills"]),
    "abdominal_pain": ("Abdominal pain", [
        r"(?:abdominal|stomach|belly|abdomen) (?:pain|ache|cramp\w*)", r"pain in (?:my |the |his |her )?(?:stomach|belly|abdomen)"]),
}


# Everyday spoken phrasings that the first-pass patterns above do not cover (speech-to-text output is
# conversational: "my chest hurts", "I feel like I'm going to pass out"). Appended, never replacing.
_EXTRA = {
    "chest_pain": [
        r"chest (?:hurts?|is hurting|aches?|is aching|feels? (?:tight|heavy|funny|painful|squeezed))",
        r"(?:pressure|tightness|squeezing|crushing|pain|ache|hurting|burning)(?: \w+){0,3} (?:in|on|across|around) (?:my |the |his |her )?chest",
        r"pressure (?:in|across|around) (?:my |the |his |her )?chest", r"hurts? (?:in|around) (?:my |the )?chest",
        r"heart (?:hurts?|pain)"],
    "dyspnea": [
        r"(?:hurts?|painful|difficult|hard) (?:when|to) (?:i )?breathe", r"breath\w* (?:is )?(?:hard|difficult|labou?red|fast|shallow)",
        r"out of breath", r"can(?:'|no)?t (?:get|take) (?:enough |any )?(?:air|a (?:deep )?breath)",
        r"(?:not|n't) (?:able|getting) (?:to )?(?:breathe|enough air)", r"suffocat\w+", r"chok(?:e|ing)\b",
        r"breathing (?:is )?(?:hard|difficult|fast|bad)"],
    "headache": [r"head (?:is )?(?:hurt\w*|ach\w*|killing me)", r"(?:my )?head hurts", r"pain in (?:my |the )?head", r"migraine"],
    "syncope": [r"(?:going|about) to (?:pass out|faint|black out)", r"pass(?:ed|ing)? out", r"black ?out"],
    "palpitations": [
        r"heart (?:is )?(?:beating|going|beats?) (?:really |very |so |too )?(?:fast|hard|quickly|irregular\w*|funny)",
        r"fast heart ?beat", r"irregular heart ?beat", r"skipp\w+ (?:a )?beat"],
    "nausea": [r"feel(?:ing)? (?:sick|queasy)", r"sick to (?:my|the) stomach", r"upset stomach", r"puk\w+"],
    "fever": [r"(?:high|a) temperature", r"burning up"],
    "unilateral_weakness": [
        r"can(?:'|no)?t (?:move|feel|use|lift) (?:my |his |her |the )?(?:left |right )?(?:arm|leg|hand|foot|side)",
        r"(?:left|right) (?:side|arm|leg) (?:is |feels |went )?(?:weak|numb|paraly\w+|dead)", r"paraly\w+"],
    "slurred_speech": [
        r"(?:words|tongue)[a-z ]{0,20}(?:not clear|unclear|thick|stuck|slurred|fuzzy)",
        r"can(?:'|no)?t (?:say|pronounce|form) (?:my |the )?words"],
    "vision_change": [
        r"(?:can(?:'|no)?t|unable to) see (?:properly|clearly|well)", r"(?:see|seeing) (?:double|spots|stars|flashes)",
        r"vision (?:is )?(?:fuzzy|cloudy)", r"\bblind(?:ness)?\b"],
    "numbness": [r"(?:lost|no) feeling", r"can(?:'|no)?t feel (?:my|his|her)"],
    "confusion": [r"(?:doesn't|does not|can't|cannot) (?:remember|recogni[sz]e)", r"mixed up", r"out of it"],
}
for _c, _p in _EXTRA.items():
    LEXICON[_c][1].extend(_p)
LEXICON.update({
    "cough": ("Cough", [r"cough\w*", r"hack(?:ing)?\b"]),
    "injury": ("Injury / trauma", [
        r"\b(?:fell|fall|fallen)\b", r"hit (?:my|his|her) head", r"(?:car|bike|road) (?:accident|crash)", r"\baccident\b",
        r"injur\w+", r"broke(?:n)? (?:my|his|her) \w+"]),
    "swelling": ("Swelling", [r"swell\w+", r"swollen"]),
    "stroke_concern": ("Patient concern: stroke (self-reported)", [r"(?:having|had|think (?:i|he|she)(?:'m| am| is| has)?(?: having)?) (?:a )?stroke"]),
    "cardiac_concern": ("Patient concern: heart attack (self-reported)", [r"heart attack"]),
    # Last on purpose: a catch-all for pain when no site-specific symptom above matched the same words.
    "pain_general": ("Pain (site not specified)", [r"\b(?:pain|painful|hurts?|hurting|aching|aches?|sore)\b"]),
})

NEGATION_CUES = [
    r"\bno\b", r"\bnot\b", r"\bnever\b", r"\bdenies\b", r"\bdenied\b", r"\bwithout\b", r"\bnone\b",
    r"\bdon't (?:have|feel)\b", r"\bdoesn't (?:have|feel)\b", r"\bdidn't\b", r"\bhasn't\b",
    r"\bhaven't\b", r"\bnor\b", r"\bnegative for\b", r"\bn't\b",
]
UNCERTAIN_CUES = [
    r"\bnot sure\b", r"\bunsure\b", r"\bmaybe\b", r"\bperhaps\b", r"\bpossibly\b", r"\bmight\b",
    r"\bi think\b", r"\bi guess\b", r"\bkind of\b", r"\bsort of\b", r"\bnot certain\b",
    r"\bdon't know\b", r"\bdo not know\b", r"\bwondering\b", r"\bcould be\b", r"\bseems?\b",
    r"\bprobably\b",
]
# A negation cue only applies inside this many tokens before the concept, and stops at a
# contrast conjunction.
NEG_WINDOW_TOKENS = 6
SCOPE_BREAKERS = r"\b(?:but|however|although|except|though)\b|[;:]"

SUDDEN_CUES = [r"\bsudden(?:ly)?\b", r"\ball of a sudden\b", r"\bout of nowhere\b", r"\bsuddenly\b",
               r"\bjust (?:started|began|happened)\b", r"\bcame on (?:fast|quickly)\b"]
SEVERE_CUES = [r"\bsevere(?:ly)?\b", r"\bheavy\b", r"\bheavily\b", r"\ba lot\b", r"\bcrushing\b",
               r"\bworst\b", r"\bterrible\b", r"\bexcruciating\b", r"\bwon't stop\b", r"\bnot stopping\b"]

ONSET_PATTERNS = [
    r"(?:since|started|began|starting|began at|started at|began around|started around|"
    r"began about|started about)\s+(?:this |last |yesterday |the |about |around |at )?[a-z0-9: ]{2,40}?"
    r"(?=[.,;!?]|$| and | but )",
    r"(?:for|over|within)\s+(?:the (?:last|past) )?(?:about |around |almost |nearly )?"
    r"(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|few|couple of|several)"
    r"\s+(?:minutes?|hours?|days?|weeks?|months?)",
    r"(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|few|couple of|several)"
    r"\s+(?:minutes?|hours?|days?|weeks?|months?)\s+ago",
    r"\b(?:this (?:morning|afternoon|evening)|last night|yesterday|tonight|just now|earlier today)\b",
]

OBSERVER_CUES = [r"\bhe\b", r"\bshe\b", r"\bhis\b", r"\bher\b", r"\bthey\b", r"\btheir\b",
                 r"\bmy (?:husband|wife|mother|father|mom|dad|son|daughter|friend|brother|sister|"
                 r"partner|patient)\b", r"\bpatient\b"]
PATIENT_CUES = [r"\bi\b", r"\bi'm\b", r"\bi've\b", r"\bmy\b", r"\bme\b"]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _any(patterns: list[str], s: str) -> bool:
    return any(re.search(p, s, re.I) for p in patterns)


def _tokens_before(sentence: str, start: int) -> str:
    """Return the text within the negation window preceding `start`, cut at scope breakers."""
    before = sentence[:start]
    breakers = list(re.finditer(SCOPE_BREAKERS, before, re.I))
    if breakers:
        before = before[breakers[-1].end():]
    toks = before.split()
    return " ".join(toks[-NEG_WINDOW_TOKENS:])


def _polarity(sentence: str, start: int, end: int) -> str:
    window = _tokens_before(sentence, start)
    after = sentence[end:end + 25]
    # "not sure ..." must be read as uncertainty, not negation.
    uncertain = _any(UNCERTAIN_CUES, window)
    cleaned = window
    for p in UNCERTAIN_CUES:
        cleaned = re.sub(p, " ", cleaned, flags=re.I)
    negated = _any(NEGATION_CUES, cleaned) or bool(re.match(r"\s*(?:is |was )?(?:absent|denied)\b", after, re.I))
    if negated and not uncertain:
        return "negated"
    if uncertain or _any(UNCERTAIN_CUES, after[:18]):
        return "uncertain"
    return "affirmed"


def _onset(sentence: str, uncertain_ctx: bool) -> Onset | None:
    for pat in ONSET_PATTERNS:
        m = re.search(pat, sentence, re.I)
        if m:
            text = m.group(0).strip(" ,.")
            hedged = uncertain_ctx or _any(UNCERTAIN_CUES, sentence)
            return Onset(text=text, certain=not hedged)
    return None


def extract(segment_id: str, text: str) -> list[SymptomMention]:
    """Extract symptom mentions from one transcript segment."""
    mentions: list[SymptomMention] = []
    for sentence in split_sentences(text):
        taken: list[tuple[int, int]] = []
        for concept, (label, patterns) in LEXICON.items():
            for pat in patterns:
                m = re.search(pat, sentence, re.I)
                if not m:
                    continue
                if any(not (m.end() <= a or m.start() >= b) for a, b in taken):
                    break  # overlaps a more specific earlier match
                taken.append((m.start(), m.end()))
                polarity = _polarity(sentence, m.start(), m.end())
                observer = _any(OBSERVER_CUES, sentence)
                patient = _any(PATIENT_CUES, sentence)
                reporter = "observer" if observer else "patient" if patient else "unspecified"
                if polarity == "negated":
                    onset = None
                else:
                    onset = _onset(sentence, polarity == "uncertain")
                mentions.append(SymptomMention(
                    concept=concept, label=label, matched_text=m.group(0), sentence=sentence,
                    segment_id=segment_id, polarity=polarity, reporter=reporter,
                    sudden=_any(SUDDEN_CUES, sentence) and polarity != "negated",
                    severity_hint="severe" if _any(SEVERE_CUES, sentence) and polarity != "negated" else None,
                    onset=onset,
                ))
                break
    return mentions


def suggest_chief_complaint(mentions: list[SymptomMention]) -> str:
    """Machine suggestion only (pending clinician confirmation)."""
    for m in mentions:
        if m.polarity == "affirmed" and m.status != "rejected":
            return m.label
    return ""
