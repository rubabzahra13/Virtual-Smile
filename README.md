# Dental Vision Provider Comparison — POC

Upload one tooth photo, pick a provider (OpenAI, Gemini, or Groq), and get
back a scored text report plus token usage for that request. Purpose is
to compare providers on quality, latency, and cost — not a production app.

## Architecture

```
Image upload (frontend)
      │
      ▼
FastAPI /analyze endpoint
      │
      ▼
providers.py   → calls the chosen provider's vision model with one shared
                 prompt, returns raw text + token usage + latency
      │
      ▼
scoring.py     → business logic layer (no AI). Parses the model's JSON
                 output and computes the overall score using fixed,
                 code-defined category weights
      │
      ▼
report.py      → formats the final plain-text report
      │
      ▼
Frontend displays report text + token counts
```

Models available in the dropdown (see MODEL_OPTIONS in `backend/main.py` to change):
- OpenAI: `gpt-5.6-sol`
- Gemini: `gemini-3.5-flash`, `gemini-3-pro-preview`, `gemini-3.1-pro-preview`, `gemini-2.5-pro`, `gemini-2.5-flash`
- Groq: `qwen/qwen3.6-27b`
- Claude: `claude-sonnet-5`, `claude-opus-4-8` (included for comparison only — Claude is not being evaluated as a fit for medical/dental use, it's a curiosity data point)

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Create a `.env` file in the project root (same folder as this README):

```dotenv
OPENAI_API_KEY=your_openai_key_here
GEMINI_API_KEY=your_gemini_key_here
GROQ_API_KEY=your_groq_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
```

You don't need all three filled in to run the app — just the one(s) you
want to test. Missing a key only breaks that provider's requests, not the
whole app.

## Run

```bash
cd backend
python main.py
```

Then open http://localhost:8000 in your browser. The FastAPI app serves
both the API and the frontend HTML, so there's no separate frontend
server to run.

## Notes

- Token usage shown (input/output/total tokens, latency) comes directly
  from each provider's own API response — nothing is estimated. This is
  the number to use for your cost comparison, not a token counter run
  locally.
- If a model doesn't return valid JSON (happens more with smaller/open
  models under prompt pressure), the report will say so explicitly and
  show the raw output instead of a fabricated score. This is intentional
  — it's useful data for your "JSON reliability" comparison metric.
- The scoring weights (alignment, gum health, color, restorations,
  missing teeth) live in `backend/scoring.py`, not in the prompt. Change
  them there if you want to reweight what the overall score emphasizes.