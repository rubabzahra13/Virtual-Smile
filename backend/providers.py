"""
providers.py

One function per provider. Each takes a LIST of (image_bytes, mime_type)
tuples - 1-3 photos of the same patient - plus the shared prompt, and
returns a common dict shape so the rest of the app doesn't need to know
which provider was used:

{
    "raw_text": "<model's raw response text>",
    "input_tokens": int,
    "output_tokens": int,
    "total_tokens": int,
    "latency_seconds": float,
}

The mime_type is the browser's actual content_type for each upload (jpeg,
png, etc). Passing the correct type per image matters - Anthropic
specifically validates the declared media_type against the real image
bytes and rejects mismatches (a PNG labeled as jpeg 400s outright).

Any other provider-specific weirdness (auth, request format, usage field
names) is contained in its own function.
"""

import os
import time
import base64

ANTHROPIC_SUPPORTED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _to_common_shape(raw_text, input_tokens, output_tokens, latency_seconds):
    return {
        "raw_text": raw_text,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": (input_tokens or 0) + (output_tokens or 0),
        "latency_seconds": round(latency_seconds, 3),
    }


def _require_images(images: list) -> None:
    if not images:
        raise ValueError("At least one image is required.")
    if len(images) > 3:
        raise ValueError("At most 3 images are supported (front + 2 side profiles).")


def _normalize_mime_type(mime_type: str) -> str:
    """
    Anthropic only accepts a specific set of image media types. If the
    browser sent something unexpected (or nothing useful), fall back to
    jpeg rather than sending a type Anthropic will reject outright.
    """
    if mime_type in ANTHROPIC_SUPPORTED_TYPES:
        return mime_type
    return "image/jpeg"


# ---------------------------------------------------------------------------
# OpenAI - gpt-5.6-sol
# ---------------------------------------------------------------------------
def call_openai(images: list, prompt: str, model: str = "gpt-5.6-sol") -> dict:
    from openai import OpenAI

    _require_images(images)
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)

    content = [{"type": "text", "text": prompt}]
    for image_bytes, mime_type in images:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64_image}"},
            }
        )

    start = time.time()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
    )
    latency = time.time() - start

    raw_text = response.choices[0].message.content
    usage = response.usage

    return _to_common_shape(
        raw_text=raw_text,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
        latency_seconds=latency,
    )


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
def call_gemini(images: list, prompt: str, model: str = "gemini-3.5-flash-lite") -> dict:
    from google import genai
    from google.genai import types

    _require_images(images)
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)
    image_parts = [
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        for image_bytes, mime_type in images
    ]

    start = time.time()
    response = client.models.generate_content(
        model=model,
        contents=[prompt, *image_parts],
    )
    latency = time.time() - start

    raw_text = response.text
    usage = response.usage_metadata

    return _to_common_shape(
        raw_text=raw_text,
        input_tokens=getattr(usage, "prompt_token_count", None),
        output_tokens=getattr(usage, "candidates_token_count", None),
        latency_seconds=latency,
    )


# ---------------------------------------------------------------------------
# Groq - qwen/qwen3.6-27b
# ---------------------------------------------------------------------------
def call_groq(
    images: list,
    prompt: str,
    model: str = "qwen/qwen3.6-27b",
    max_completion_tokens: int = 5500,
) -> dict:
    from openai import OpenAI

    _require_images(images)
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

    content = [{"type": "text", "text": prompt}]
    for image_bytes, mime_type in images:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64_image}"},
            }
        )

    start = time.time()
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_completion_tokens": max_completion_tokens,
    }
    if "qwen" in model.lower():
        kwargs["extra_body"] = {"reasoning_format": "hidden"}

    response = client.chat.completions.create(**kwargs)
    latency = time.time() - start

    raw_text = response.choices[0].message.content
    usage = response.usage

    return _to_common_shape(
        raw_text=raw_text,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
        latency_seconds=latency,
    )


# ---------------------------------------------------------------------------
# Claude (Anthropic) - added for curiosity, not a clinical-fit claim
# ---------------------------------------------------------------------------
def call_claude(images: list, prompt: str, model: str = "claude-sonnet-5") -> dict:
    from anthropic import Anthropic

    _require_images(images)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = Anthropic(api_key=api_key)

    content = []
    for image_bytes, mime_type in images:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _normalize_mime_type(mime_type),
                    "data": b64_image,
                },
            }
        )
    content.append({"type": "text", "text": prompt})

    start = time.time()
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": content}],
    )
    latency = time.time() - start

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    usage = response.usage

    return _to_common_shape(
        raw_text=raw_text,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        latency_seconds=latency,
    )


PROVIDER_FUNCTIONS = {
    "openai": call_openai,
    "gemini": call_gemini,
    "groq": call_groq,
    "claude": call_claude,
}


def call_provider(provider_key: str, images: list, prompt: str, model: str = None) -> dict:
    if provider_key not in PROVIDER_FUNCTIONS:
        raise ValueError(
            f"Unknown provider '{provider_key}'. Choose from: {list(PROVIDER_FUNCTIONS)}"
        )
    fn = PROVIDER_FUNCTIONS[provider_key]
    if model:
        return fn(images, prompt, model=model)
    return fn(images, prompt)


def call_openai_text(prompt: str, model: str = "gpt-4.1-nano") -> dict:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)
    start = time.time()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    latency = time.time() - start
    raw_text = response.choices[0].message.content
    usage = response.usage
    return _to_common_shape(
        raw_text=raw_text,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
        latency_seconds=latency,
    )


def call_gemini_text(prompt: str, model: str = "gemini-3.5-flash-lite") -> dict:
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)
    start = time.time()
    response = client.models.generate_content(model=model, contents=prompt)
    latency = time.time() - start
    raw_text = response.text
    usage = response.usage_metadata
    return _to_common_shape(
        raw_text=raw_text,
        input_tokens=getattr(usage, "prompt_token_count", None),
        output_tokens=getattr(usage, "candidates_token_count", None),
        latency_seconds=latency,
    )


def call_groq_text(prompt: str, model: str = "qwen/qwen3.6-27b") -> dict:
    from openai import OpenAI

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    start = time.time()

    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": 4096,
    }
    if "qwen" in model.lower():
        kwargs["extra_body"] = {"reasoning_format": "hidden"}

    response = client.chat.completions.create(**kwargs)
    latency = time.time() - start
    raw_text = response.choices[0].message.content
    usage = response.usage
    return _to_common_shape(
        raw_text=raw_text,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
        latency_seconds=latency,
    )


def call_groq_gpt_oss_text(
    prompt: str,
    model: str = "openai/gpt-oss-120b",
    max_completion_tokens: int = 2048,
    reasoning_effort: str = "low",
) -> dict:
    from openai import OpenAI

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    start = time.time()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=max_completion_tokens,
        temperature=1,
        top_p=1,
        extra_body={"reasoning_effort": reasoning_effort},
    )
    latency = time.time() - start
    raw_text = response.choices[0].message.content
    usage = response.usage
    return _to_common_shape(
        raw_text=raw_text,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
        latency_seconds=latency,
    )


def call_claude_text(prompt: str, model: str = "claude-sonnet-5") -> dict:
    from anthropic import Anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = Anthropic(api_key=api_key)
    start = time.time()
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    latency = time.time() - start
    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    usage = response.usage
    return _to_common_shape(
        raw_text=raw_text,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        latency_seconds=latency,
    )


TEXT_PROVIDER_FUNCTIONS = {
    "openai": call_openai_text,
    "gemini": call_gemini_text,
    "groq": call_groq_text,
    "claude": call_claude_text,
}


def call_provider_text(provider_key: str, prompt: str, model: str = None) -> dict:
    if provider_key not in TEXT_PROVIDER_FUNCTIONS:
        raise ValueError(
            f"Unknown provider '{provider_key}'. Choose from: {list(TEXT_PROVIDER_FUNCTIONS)}"
        )
    fn = TEXT_PROVIDER_FUNCTIONS[provider_key]
    if model:
        return fn(prompt, model=model)
    return fn(prompt)