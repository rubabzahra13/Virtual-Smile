"""
Shared prompts used across all providers.

Two-pass pipeline (default):
  Pass 1 (DETECTION_PROMPT) - vision: strict visual sign detection + scores
  Pass 2 (build_explanation_prompt) - text-only: patient-friendly explanations

Single-pass fallback (VISION_ANALYSIS_PROMPT) - one combined vision call.
"""

IMAGE_LABELS = ["front_smile", "left_profile", "right_profile"]

DETECTION_PROMPT = """You are performing a preliminary, non-diagnostic visual inspection of dental photographs - the same kind of quick visual scan a dentist does before a full clinical exam. This is NOT a diagnosis. You only report what is clearly visible in the photos.

You may receive 1-3 images of the same patient:
- IMAGE 1 = front smile (required)
- IMAGE 2 = left profile (optional)
- IMAGE 3 = right profile (optional)

Treat all images as one patient. Do not double-count the same sign visible in multiple views.

## Your task (complete these steps mentally, then output JSON only)

### Step 1 - Image quality gate (per image)
Check: mouth open enough to see teeth, teeth in focus, adequate lighting, minimal glare/reflection blocking enamel, no heavy filter, lips not fully obscuring teeth.

### Step 2 - Systematic visual inspection (in order)
1. Gingival margins and papillae - inflammation, recession, swelling
2. Tooth-by-tooth sweep - upper then lower, left to right
3. Incisal edges - chips, fractures, wear facets
4. Interproximal zones - crowding overlap, spacing, black triangles
5. Restorations - fillings, crowns, veneers; margin discoloration, gaps, mismatch
6. Missing teeth / edentulous spaces
7. Color - staining vs shadow/artifact (lip shadow, uneven lighting, yellow filter)

### Step 3 - Visual sign library (report ONLY if clearly visible)

| Sign type | What to look for | Common false positives (do NOT report) |
|-----------|------------------|----------------------------------------|
| staining | Localized yellow/brown/gray on enamel | Lip shadow, uneven lighting, warm photo filter |
| calculus | Chalky/yellow hard deposits at gumline or lingual lower anteriors | Food debris, dried saliva film |
| gum_recession | Visible root surface, longer tooth look, papilla loss | Lip pull distortion, camera angle |
| visible_caries | Dark brown/black cavitation, opaque white demineralization | Staining in embrasures, shadow |
| crowding | Rotated/overlapping teeth, irregular arch line | Slight angle exaggerating misalignment |
| spacing | Visible gaps between teeth | Normal diastema if subtle and symmetric |
| chipped_tooth | Discontinuity on incisal edge | Low resolution, JPEG compression |
| worn_teeth | Flattened/incisal edge wear, shortened teeth | Normal variation, angle |
| failing_restoration | Margin stain, gap, fracture, color mismatch | Natural translucency variation |
| missing_tooth | Clear empty space where tooth expected | Shadow mimicking gap |

### Step 3B - Condition-specific inspection rules (all visible problem types)
For each condition below, run the rule before deciding presence/absence:

1) Staining / discoloration
- Inspect cervical third, interproximal zones, and generalized shade mismatch.
- Require persistent color change on enamel surface, not a single shadow band.
- If only lighting artifacts are present, mark not detected.

2) Crowding / malalignment / rotation
- Inspect upper and lower anterior contacts for overlap, displacement, and axial rotation.
- Look for broken arch continuity and unequal contact spacing.
- If tooth edges are mostly hidden by lips/cheeks or severe blur, mark alignment_view_usable=false.

3) Spacing / gaps
- Confirm true open contact or diastema by visible dark space between adjacent teeth.
- Do not call spacing if gap is caused by camera angle, lip pull, or transient shadow.

4) Chipped / fractured teeth
- Inspect incisal edges and visible cusp tips for discontinuity, step defects, or sharp notches.
- Require clear contour interruption in at least one usable view.

5) Missing teeth
- Confirm expected tooth position shows persistent open edentulous space.
- Do not call missing tooth if area is fully obscured.

6) Visible caries (surface-level only)
- Require visible cavitation, undermined enamel, or localized opaque/chalky/black-brown lesion.
- Do not infer interproximal or occult decay from shade alone.
- If uncertain between stain and caries, prefer "not detected" and lower confidence.

7) Gum recession / gingival inflammation
- Only assess when gingival margin and papilla are clearly visible.
- Recession: visible root exposure, elongated clinical crown appearance.
- Inflammation: diffuse erythema/swelling at gingival margin, not flash glare.
- If gingival landmarks are not visible, set gums_visible=false and do not report gum findings.

8) Plaque / calculus (tartar)
- Look for adherent yellow-white deposits along gingival margin/interproximal areas.
- Do not classify loose debris/saliva streaks as plaque/calculus.

9) Worn / eroded teeth
- Inspect for flattened incisal edges, shortened crowns, translucency/wear facets.
- Avoid calling wear when edge visibility is poor.

10) Old / failing restorations
- Look for marginal staining, ditching/gaps, contour mismatch, fracture, or color mismatch.
- Do not assume failure just because restoration is present.

### Step 4 - Evidence rules (critical)
- Every observed_sign MUST cite a location (e.g. upper_anteriors, lower_left_central, gumline_upper)
- Every observed_sign MUST cite evidence_views: ["front_smile"], ["left_profile"], etc.
- evidence_strength: high = unmistakable; medium = clear but partial; low = faint/ambiguous
- If evidence_strength is low OR confidence < 0.6, do NOT include the sign
- Do NOT manufacture findings. Empty observed_signs is valid and expected
- List what CANNOT be assessed from photos in not_assessable_from_photo
- If gingiva/gumline is not visibly shown with enough clarity, set visibility.gums_visible=false, do NOT output gum-related signs, and add gum_health to not_assessable_from_photo
- For each condition not reported, prefer omission over speculation; only include signs with direct visual evidence.

### Step 5 - Scoring rubrics (100 = no visible issue in category)

alignment:
  95-100: straight arch, no obvious crowding/spacing
  80-94: mild crowding or minor spacing, cosmetic only
  60-79: moderate crowding/spacing clearly visible
  40-59: severe crowding or multiple spacing issues
  <40: very irregular alignment

gum_health:
  95-100: healthy pink gums, no recession/calculus/inflammation visible
  80-94: minor plaque or slight inflammation
  60-79: visible calculus, moderate recession, or clear inflammation
  40-59: significant recession or heavy buildup
  <40: severe visible gum disease signs

color:
  95-100: uniform natural tooth color
  80-94: mild staining
  60-79: moderate generalized or localized staining
  40-59: heavy staining or visible discoloration affecting multiple teeth
  <40: severe discoloration

restorations:
  95-100: no visible restorations or damage
  80-94: minor chip or small old restoration
  60-79: visible failing restoration, moderate chip/wear
  40-59: multiple failing restorations or significant wear
  <40: severe structural damage visible

missing_teeth:
  100: no missing teeth visible
  70-99: single missing tooth space
  40-69: two missing teeth or large gap
  <40: multiple missing teeth

If a category truly cannot be assessed from the provided views, score 100 for that category and note it in not_assessable_from_photo.

### Step 6 - Priority triage (for downstream use)
Rank concern types by clinical urgency when visible:
1. Active infection signs (swelling, severe inflammation)
2. Structural damage (fracture, large failing restoration)
3. Progressive disease signs (recession, visible caries)
4. Cosmetic (staining, mild crowding)

Return ONLY valid JSON matching this structure - no markdown, no code fences, no commentary:

{
  "visibility": {
    "teeth_visible": true,
    "gums_visible": true,
    "alignment_view_usable": true
  },
  "image_quality": {
    "usable": true,
    "issues": ["plain English issue if any"],
    "score": 0.0-1.0
  },
  "observed_signs": [
    {
      "sign": "snake_case_sign_type",
      "concern_label": "snake_case_concern_for_report",
      "location": "tooth_region_or_area",
      "evidence_views": ["front_smile"],
      "evidence_strength": "high",
      "confidence": 0.0-1.0,
      "visible_feature": "one plain-English sentence describing exactly what you see"
    }
  ],
  "not_assessable_from_photo": ["interproximal_caries", "bone_loss", "bite_force"],
  "scores": {
    "alignment": 0-100,
    "gum_health": 0-100,
    "color": 0-100,
    "restorations": 0-100,
    "missing_teeth": 0-100
  },
  "confidence_breakdown": {
    "image_quality": 0.0-1.0,
    "overall_assessment": 0.0-1.0
  }
}
"""


def build_explanation_prompt(detection_json: str) -> str:
    return f"""You are writing a patient-friendly preliminary smile assessment report. This is NOT a diagnosis.

You are given structured visual findings from a strict photo inspection (Pass 1). Do NOT add new concerns that are not supported by the observed_signs list. Do NOT contradict the detection data.

Detection data (JSON):
{detection_json}

Write all text in simple plain English. No clinical jargon.

Derive visible_concerns from observed_signs - use the concern_label values, deduplicated.
Only include concerns with evidence_strength high or medium and confidence >= 0.6 in the source data.
If visibility.gums_visible is false, do not include gum-related concerns.
If scores.alignment <= 85 and observed_signs contain alignment evidence, ensure visible_concerns includes an alignment concern label.

Return ONLY valid JSON - no markdown, no code fences:

{{
  "visible_concerns": ["snake_case_labels"],
  "concern_details": [
    {{
      "concern": "matching_label",
      "likely_cause": "one plain-English sentence",
      "treatment_options": ["option 1", "option 2"]
    }}
  ],
  "priority_level": "one of: cosmetic, preventative, cosmetic_and_preventative, urgent_referral",
  "priority_order": ["most_urgent_concern_first", "..."],
  "treatment_roadmap": ["Phase 1: ...", "Phase 2: ..."],
  "notes": "one or two plain-English summary sentences"
}}

Rules:
- concern_details: one entry per visible_concern; empty list if no concerns
- priority_order: rank visible_concerns by clinical urgency (infection/structural first, cosmetic last)
- treatment_roadmap: 2-4 phases; preventative/urgent first, cosmetic later
- If no concerns: visible_concerns empty, priority_level cosmetic, roadmap ["Phase 1: Routine checkup to confirm no treatment needed"]
"""


VISION_ANALYSIS_PROMPT = """You are analyzing dental photographs for a preliminary, non-diagnostic visual assessment.

Images may include: front smile (required), left/right profile (optional). One patient, one combined assessment - do not double-count.

## Inspection protocol
1. Image quality - teeth visible, focused, lit, minimal glare
2. Gums - margins, recession, inflammation, calculus
3. Teeth sweep - chips, wear, restorations, missing teeth
4. Alignment - crowding, spacing
5. Color - staining vs shadow/artifact (do not confuse lip shadow with staining)

Report ONLY clearly visible concerns. If ambiguous, omit. Do not diagnose.

## Scoring rubrics (100 = no visible issue)
- alignment: 95-100 straight; 80-94 mild; 60-79 moderate; 40-59 severe; <40 very irregular
- gum_health: 95-100 healthy; 80-94 minor; 60-79 moderate; 40-59 significant; <40 severe
- color: 95-100 uniform; 80-94 mild stain; 60-79 moderate; 40-59 heavy; <40 severe
- restorations: 95-100 none; 80-94 minor; 60-79 moderate damage; 40-59 multiple failures; <40 severe
- missing_teeth: 100 none; 70-99 one gap; 40-69 two gaps; <40 multiple

Return ONLY valid JSON:

{
  "visible_concerns": ["snake_case_labels"],
  "concern_details": [
    {
      "concern": "label",
      "likely_cause": "plain English",
      "treatment_options": ["option"]
    }
  ],
  "scores": {
    "alignment": 0-100,
    "gum_health": 0-100,
    "color": 0-100,
    "restorations": 0-100,
    "missing_teeth": 0-100
  },
  "priority_level": "cosmetic | preventative | cosmetic_and_preventative | urgent_referral",
  "priority_order": ["concern_first", "..."],
  "treatment_roadmap": ["Phase 1: ...", "Phase 2: ..."],
  "confidence": 0.0-1.0,
  "notes": "summary"
}

Plain English only. Empty visible_concerns is valid if nothing obvious is visible.
"""


# ---------------------------------------------------------------------------
# Groq comparison pipeline (separate from main provider-comparison flow)
# ---------------------------------------------------------------------------

GROQ_VISION_CAPTURE_PROMPT = """You are a dental photo observer. This is NOT a diagnosis.

You receive 1-3 photos of the same patient (front smile required; left/right profile optional).
Describe ONLY what is clearly visible. Do not guess hidden conditions.

Inspect systematically:
- image quality (focus, lighting, teeth visibility)
- alignment (crowding, spacing, rotation, arch line)
- color/staining
- chips, wear, fractures
- restorations (fillings, crowns, veneers)
- missing teeth
- gums ONLY if gingival margin/papilla are clearly visible
- plaque/calculus ONLY if clearly visible deposits

For each visible finding include: condition, exact location, which view(s), and why you think it is visible (not shadow/artifact).

Also list conditions that cannot be assessed from these photos (e.g. interproximal caries, bone loss, bite force).

Return plain text only - no JSON, no markdown fences. Be factual and concise.
Keep the whole description under 350 words. Use short bullet-style lines.
"""


def build_groq_metrics_prompt(vision_description: str) -> str:
    return f"""You are a dental metrics structurer. This is NOT a diagnosis.

Convert the visual description below into structured metrics JSON.
Use ONLY facts present in the description. Do not add new findings.

Visual description:
{vision_description}

Return ONLY valid JSON matching this schema:

{{
  "visibility": {{
    "teeth_visible": true,
    "gums_visible": true,
    "alignment_view_usable": true
  }},
  "observed_signs": [
    {{
      "sign": "snake_case_sign_type",
      "concern_label": "snake_case_concern",
      "location": "tooth_region",
      "evidence_views": ["front_smile"],
      "evidence_strength": "high",
      "confidence": 0.0-1.0,
      "visible_feature": "plain English"
    }}
  ],
  "not_assessable_from_photo": ["interproximal_caries"],
  "scores": {{
    "alignment": 0-100,
    "gum_health": 0-100,
    "color": 0-100,
    "restorations": 0-100,
    "missing_teeth": 0-100
  }},
  "confidence_breakdown": {{
    "image_quality": 0.0-1.0,
    "overall_assessment": 0.0-1.0
  }}
}}

Rules:
- 100 score = no visible issue in that category
- If gums not visible in description, set gums_visible=false, gum_health=100, add gum_health to not_assessable_from_photo
- If evidence_strength is low or confidence < 0.6, omit the sign
- Empty observed_signs is valid
"""


def build_groq_report_prompt(metrics_json: str) -> str:
    return f"""You are writing a patient-friendly preliminary smile assessment report. This is NOT a diagnosis.

You are given structured metrics from a photo inspection. Do NOT add concerns not supported by observed_signs.

Metrics JSON:
{metrics_json}

Write plain English only. Return ONLY valid JSON:

{{
  "visible_concerns": ["snake_case_labels"],
  "concern_details": [
    {{
      "concern": "label",
      "likely_cause": "one sentence",
      "treatment_options": ["option 1", "option 2"]
    }}
  ],
  "priority_level": "cosmetic | preventative | cosmetic_and_preventative | urgent_referral",
  "priority_order": ["concern_first"],
  "treatment_roadmap": ["Phase 1: ...", "Phase 2: ..."],
  "notes": "one or two summary sentences"
}}

If no concerns: empty visible_concerns, priority_level cosmetic, roadmap ["Phase 1: Routine checkup to confirm no treatment needed"]
"""


def build_image_quality_prompt(labels: list) -> str:
    label_list = ", ".join(labels)
    return f"""You are a fast image quality gate for dental smile photos. This is NOT a diagnosis.

You receive {len(labels)} image(s) in order: {label_list}.

Reject photos that are unusable for a preliminary visual smile assessment. Check each image for:
- Resolution too low or heavily compressed (blurry, blocky teeth)
- Too dark, overexposed, or strong color filter
- Mouth closed or teeth not visible
- Heavy glare/reflection blocking teeth
- Face cropped so teeth are mostly out of frame
- Corrupt or not a face/smile photo

Accept photos that are good enough for a preliminary non-diagnostic visual scan even if not perfect.

Output JSON only (no markdown fences):
{{
  "ok": true,
  "issues": [],
  "per_image": [
    {{"label": "{labels[0]}", "ok": true, "issues": []}}
  ]
}}

Set ok=false if ANY image fails. List specific issues per image in per_image[].issues and also in top-level issues.
"""
