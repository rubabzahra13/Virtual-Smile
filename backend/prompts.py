"""
Single shared prompt used across all three providers.

Keeping this identical across OpenAI, Gemini, and Groq is the whole point
of the comparison — if the prompt changes per-provider, you're no longer
comparing the models, you're comparing your prompt engineering.
"""

VISION_ANALYSIS_PROMPT = """You are analyzing one or more photographs of the same patient's teeth for a preliminary, non-diagnostic visual assessment. You may be given a single front-smile photo, or a front-smile photo plus one or two side-profile photos. Treat all provided images as views of the same patient and produce one single, combined assessment — do not report separate findings per image, and do not double-count the same issue if it's visible in more than one photo.

For this version, focus on clearly and obviously visible concerns only. Do not strain to detect subtle or ambiguous findings, and do not attempt complex clinical judgments that would normally require an in-person exam or X-rays. If something is only faintly or ambiguously visible, leave it out rather than guessing.

Common issues in photos like this include things like staining or discoloration, crowding, spacing/gaps, chipped or fractured teeth, missing teeth, visible cavities, gum recession, plaque or calculus (tartar) buildup, worn or eroded teeth, and old or failing restorations. These are examples to give you a sense of the range of things worth looking for, not a checklist you need to confirm or rule out one by one. If a photo genuinely shows no obvious concerns, it's fine and expected for visible_concerns to be short or empty — do not manufacture a finding just to fill it out.

Write all explanatory text in simple, plain English a patient with no dental background could understand. No clinical jargon.

Return ONLY a single JSON object, no markdown formatting, no code fences, no commentary before or after it. The JSON must match this exact structure:

{
  "visible_concerns": ["short_snake_case_labels_of_anything_visible"],
  "concern_details": [
    {
      "concern": "short_snake_case_label_matching_visible_concerns",
      "likely_cause": "one plain-English sentence on why this commonly happens",
      "treatment_options": ["short plain-English option", "another option if relevant"]
    }
  ],
  "scores": {
    "alignment": 0-100,
    "gum_health": 0-100,
    "color": 0-100,
    "restorations": 0-100,
    "missing_teeth": 0-100
  },
  "priority_level": "one of: cosmetic, preventative, cosmetic_and_preventative, urgent_referral",
  "priority_order": ["concern_label_to_address_first", "concern_label_next", "..."],
  "treatment_roadmap": ["Phase 1: short description", "Phase 2: short description", "Phase 3: short description"],
  "confidence": 0.0-1.0,
  "notes": "one or two plain-English sentences summarizing what you observed"
}

Notes on the added fields:
- concern_details should have one entry per item in visible_concerns. If visible_concerns is empty, concern_details should be an empty list too.
- priority_order should rank the concerns in visible_concerns from most to least important to address, using the same labels. If there are no concerns, this can be an empty list.
- treatment_roadmap should be a short, realistic phased plan (2-4 phases) in plain language, e.g. addressing urgent/preventative issues first before cosmetic ones. If there are no concerns, a single phase like "Phase 1: Routine checkup to confirm no treatment needed" is fine.

Roughly how findings map into the five score categories (a single finding can affect more than one):
- alignment: crowding, spacing/gaps, bite irregularities
- gum_health: gum recession, plaque or calculus buildup, visible inflammation
- color: staining, discoloration, visible cavities affecting tooth surface color
- restorations: old or failing restorations, chipped/fractured teeth, worn or eroded teeth
- missing_teeth: any visibly missing teeth

Scoring guide: 100 = no visible issue in that category, based only on what's actually visible. Lower scores mean more visible concern in that category. If a category genuinely can't be assessed from the photo (e.g. gum health not visible), give your best-effort estimate and lower the confidence field rather than omitting the category or defaulting to a low score without evidence.

Return valid JSON only.
"""