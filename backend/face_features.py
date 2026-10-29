"""Gender of a face by counting visible features (Ali's method, October 2026).

Instead of asking the AI "male or female?" (it answered "woman, high confidence" for a pilot whose face was covered by an oxygen mask),
the AI only answers yes / no / can't see to plain questions about what is visible in ONE cut-out face. Every feature question belongs to
one gender (M or F); a "yes" gives that gender one point. The gender with more points wins; equal points is "unsure".

Before any point counts, the face must be judgeable at all (GATE): a face is visible, the mouth is not covered, and there is light enough
to see the skin. A masked or dark face gives no gender ("cannot judge"), however the other answers look.

No single feature belongs to only one gender (sexual dimorphism is about averages), which is why many features are asked and counted.
Nothing here names or identifies anyone. Pure Python; the AI call is passed in (ask) so tests run without any network.
"""
import base64
import json
from pathlib import Path

GATE = (("G1", "Is a human face visible in this image?"),)
GATE_REQUIRED = ("G1",)

# One-sided questions: a "yes" gives the gender written next to it one point. Only signs that belong to one gender.
FEATURES = (
    ("M1", "Is there a beard or a moustache?", "M"),
    ("M2", "Is there stubble or a shaving shadow (the beard area darker than the rest of the skin)?", "M"),
    ("M3", "Is the hairline receded at the temples in an M shape, or is the top of the head bald?", "M"),
    ("M4", "Is there a clear Adam's apple, a bump on the front of the throat?", "M"),
    ("M5", "Is chest hair visible at an open collar?", "M"),
    ("F1", "Is there lipstick or lip gloss?", "F"),
    ("F2", "Is there eye makeup: eyeliner, eyeshadow or mascara?", "F"),
    ("F3", "Is there blush or visible foundation on the cheeks?", "F"),
    ("F4", "Is a headscarf (hijab) closely framing the face, covering the hair, ears and neck?", "F"),
    ("F5", "Are there dangling earrings?", "F"),
    ("F6", "Are there hair accessories (clips, a hairband, bows, flowers) or is the hair styled in a bun or braids?", "F"),
)
# Two-sided questions: ONE feature, the answer picks the side ("M", "F", or "neither" / "cant_see" for no point).
CHOICES = (
    ("C1", "Eyebrows: M = low, straight and thick; F = high, thin and arched."),
    ("C2", "Forehead and brow: M = a brow ridge that sticks out, eyes set deep, flat forehead; F = smooth rounded forehead, no brow ridge."),
    ("C3", "Jaw: M = broad, square, with sharp corners below the ears; F = narrow, V-shaped or softly rounded."),
    ("C4", "Chin: M = wide and prominent; F = small and pointed."),
    ("C5", "Cheeks: M = flat and angular; F = high, full, rounded cheekbones."),
    ("C6", "Nose: M = large, with a wide bridge or base; F = small and narrow, tip slightly turned up."),
    ("C7", "Lips: M = thin and flat; F = full."),
    ("C8", "Distance from the base of the nose to the upper lip: M = long; F = short."),
)
QUESTIONS = GATE + tuple((q, t) for q, t, _ in FEATURES) + CHOICES
ANSWERS = ("yes", "no", "cant_see")
CHOICE_ANSWERS = ("M", "F", "neither", "cant_see")

PROMPT = ("Each image is one face cut from a film or video. For EVERY image id answer EVERY question below. "
          "Questions G and M/F-numbered ones: answer yes, no or cant_see. Questions C1-C8: answer M or F for the description that fits, "
          "neither when neither fits clearly, cant_see when that part of the face cannot be seen. "
          "Answer only from what is visible; when a detail is covered, turned away, too dark or too small answer cant_see, never guess. "
          "Do not say whether the person is male or female, and never name, recognise or identify anyone. Return JSON only.\nQUESTIONS:\n"
          + "\n".join(f"{q}: {t}" for q, t in QUESTIONS))

_PROPS = {q: {"type": "STRING", "enum": list(ANSWERS)} for q, _ in GATE + tuple((q, t) for q, t, _ in FEATURES)}
_PROPS.update({q: {"type": "STRING", "enum": list(CHOICE_ANSWERS)} for q, _ in CHOICES})
SCHEMA = {"type": "OBJECT", "properties": {"faces": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "id": {"type": "STRING"},
    "answers": {"type": "OBJECT", "properties": _PROPS, "required": list(_PROPS)}},
    "required": ["id", "answers"]}}}, "required": ["faces"]}


def score(answers):
    """answers: {question id: answer} for one face -> {'gender': 'male'|'female'|'unsure'|'cannot_judge', 'M', 'F', 'points': [...]}.
    No face visible: 'cannot_judge'. Each one-sided "yes" and each two-sided M/F choice gives its side one point; more points wins."""
    answers = answers if isinstance(answers, dict) else {}
    points = [(q, g) for q, _, g in FEATURES if answers.get(q) == "yes"]
    points += [(q, answers[q]) for q, _ in CHOICES if answers.get(q) in ("M", "F")]
    m = sum(1 for _, g in points if g == "M")
    f = sum(1 for _, g in points if g == "F")
    if any(answers.get(g) != "yes" for g in GATE_REQUIRED):
        return {"gender": "cannot_judge", "M": m, "F": f, "points": points, "gate_failed": list(GATE_REQUIRED)}
    return {"gender": "male" if m > f else ("female" if f > m else "unsure"), "M": m, "F": f, "points": points}


def ask(images, api_key, call=None, thinking="low"):
    """images: [{'id', 'path'}] (one request). Returns ({id: answers dict}, usage or None). call(key, payload, timeout) -> (data, err)."""
    parts = []
    for c in images:
        try:
            data = base64.b64encode(Path(c["path"]).read_bytes()).decode("ascii")
        except Exception:
            continue
        parts.append({"text": f"Image {c['id']}:"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": data}})
    if not parts:
        return {}, None
    parts.append({"text": PROMPT})
    payload = {"contents": [{"parts": parts}],
               "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA,
                                    "thinkingConfig": {"thinkingLevel": thinking}}}
    if call is None:
        import gemini_service
        call = lambda key, p, timeout: gemini_service.call_gemini(key, p, timeout=timeout)
    data, _ = call(api_key, payload, 180)
    if data is None:            # the answer shape refused: once more without it (the parser below still checks everything)
        data, _ = call(api_key, dict(payload, generationConfig={"responseMimeType": "application/json",
                                                                 "thinkingConfig": {"thinkingLevel": thinking}}), 180)
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"] if isinstance(p, dict) and not p.get("thought"))
        text = text.strip()
        if text.startswith("```"):
            text = "\n".join(text.splitlines()[1:-1])
        out = {}
        wanted = {c["id"] for c in images}
        for face in json.loads(text).get("faces") or []:
            if isinstance(face, dict) and face.get("id") in wanted and isinstance(face.get("answers"), dict):
                out[face["id"]] = {q: (v if v in ANSWERS or v in CHOICE_ANSWERS else "cant_see") for q, v in face["answers"].items()}
        return out, (data.get("usageMetadata") if isinstance(data, dict) else None)
    except Exception:
        return {}, (data.get("usageMetadata") if isinstance(data, dict) else None)
