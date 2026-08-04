"""
Language utility module for Chatbot language detection and script safety.

Rules:
- Case 1: English Input -> Respond in English
- Case 2: Urdu Script Input -> Convert response into Roman Urdu
- Case 3: Roman Urdu Input -> Respond in Roman Urdu
- Case 4: Mixed Roman Urdu + English -> Respond in Plain English

Supported Output Languages: English, Roman Urdu.
Prohibited Output Scripts: Devanagari (Hindi) script, Perso-Arabic (Urdu) script.
"""

import re
from typing import Tuple

# Perso-Arabic / Urdu script unicode ranges
URDU_SCRIPT_PATTERN = re.compile(
    r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]'
)

# Devanagari (Hindi) script unicode ranges
DEVANAGARI_SCRIPT_PATTERN = re.compile(r'[\u0900-\u097F]')

# Common Roman Urdu words (Pakistani Urdu transliterated in Latin script)
ROMAN_URDU_WORDS = {
    # Pronouns & possessives
    "main", "mein", "mujhe", "mujhy", "mera", "meri", "mere", "hum", "humein", "humari", "humara", "humare",
    "tum", "tumhein", "tumhara", "tumhari", "tumhare", "aap", "apko", "aapko", "apki", "aapki", "apke", "aapke",
    "apka", "aapka", "ye", "yeh", "wo", "woh", "is", "iss", "isse", "us", "usse", "un", "unki", "unka", "unke",
    "apna", "apni", "apne", "kisi", "kis",
    # Question words
    "kya", "kia", "kaise", "kese", "kahan", "kahin", "kab", "kyun", "kiun", "kyu", "kon", "kaun",
    "konsa", "konsi", "konse", "kaunsa", "kaunsi", "kaunse", "kitna", "kitni", "kitne",
    # Verbs & auxiliaries
    "hai", "hain", "hian", "ho", "hoon", "hun", "hona", "hoga", "hogi", "hoge", "tha", "thi", "the",
    "karna", "karni", "karne", "karta", "karti", "karte", "kar", "karo", "karein", "karain", "kardo", "karu", "karun",
    "batao", "bataen", "batayein", "bataiye", "bataye", "batai", "pucho", "puchna", "chahiye", "chahie",
    "raha", "rahi", "rahe", "sakta", "sakti", "sakte", "sake", "saken", "aana", "aani", "aane", "aata", "aati", "aate",
    "jaana", "jaani", "jaane", "jaye", "jata", "jati", "jate", "jao", "milega", "milegi", "milege", "milta", "milti",
    "samjh", "samajh", "dena", "deni", "dene", "diya", "diye", "lena", "leni", "lene", "liya", "liye",
    # Prepositions, conjunctions, particles
    "ka", "ki", "ke", "ko", "se", "sy", "par", "pe", "paas", "pas", "tak", "bhi", "bhii", "saath", "sath",
    "pehle", "pahle", "phir", "fir", "magar", "lekin", "agar", "agr", "toh", "to", "aur", "ya", "yani",
    "jab", "tab", "ab", "kuch", "kuchbhi", "wahan", "yahan",
    # Common Nouns & Adjectives (Pakistani dental / conversational context)
    "daant", "dant", "daanto", "danto", "dard", "ilaj", "ilaaj", "saaf", "safai", "kharab", "teedhe", "seedhe",
    "masooray", "masure", "peela", "peelay", "peelapan", "safed", "ganda", "ganday", "dukhraha",
    "zaroori", "zaroorat", "zaruri", "zarurat", "shukriya", "meherbani", "achha", "achi", "ache", "acha",
    "bohot", "bahut", "boht", "ziyada", "zyada", "kam", "bada", "badi", "bare", "chota", "choti", "chote",
    "sahi", "faida", "nuksan", "wala", "wali", "wale", "sab", "sabhi"
}

# Regex patterns matching English question frames, structural clauses, and multi-word English phrases
ENGLISH_STRUCTURAL_PHRASES = [
    r'\bwhat\s+(documents|slots|is|are|do|can|should|would|will|to|need)\b',
    r'\bhow\s+(do|can|to|should|would|will|many|much)\b',
    r'\bwhere\s+(is|are|can|do|should)\b',
    r'\bwhen\s+(is|are|can|do|should)\b',
    r'\bwhy\s+(is|are|do|can|should)\b',
    r'\bwhich\s+(one|ones|documents|slots|is|are|do|can)\b',
    r'\bcan\s+you\b',
    r'\bcould\s+you\b',
    r'\bwould\s+you\b',
    r'\bwill\s+you\b',
    r'\bplease\s+(tell|explain|provide|show|help)\b',
    r'\btell\s+me\b',
    r'\blet\s+me\s+know\b',
    r'\bdo\s+i\s+(need|have|get)\b',
    r'\bdo\s+you\s+(have|know|provide)\b',
    r'\bavailable\s+slots\b',
    r'\brequired\s+documents\b',
    r'\bwhat\s+documents\b',
    r'\bi\s+need\b',
    r'\bi\s+want\s+to\b',
    r'\bhow\s+do\s+i\b',
    r'\bwhat\s+should\s+i\b',
]


def detect_chat_language(question: str) -> str:
    """
    Detect target response language for chatbot based on user input.

    Returns:
      'ENGLISH' or 'ROMAN_URDU'
    """
    if not question or not question.strip():
        return 'ENGLISH'

    # Case 2: User typed in Urdu script -> Convert response into Roman Urdu
    if URDU_SCRIPT_PATTERN.search(question):
        return 'ROMAN_URDU'

    text_lower = question.lower()
    words = re.findall(r'\b[a-z]+\b', text_lower)

    # Check for Roman Urdu words
    roman_urdu_found = [w for w in words if w in ROMAN_URDU_WORDS]

    if not roman_urdu_found:
        # Case 1: Entirely in English
        return 'ENGLISH'

    # Case 4 vs Case 3: Check if mixed Roman Urdu + English
    for pattern in ENGLISH_STRUCTURAL_PHRASES:
        if re.search(pattern, text_lower):
            # Mixed Roman Urdu + English -> Respond in Plain English (Case 4)
            return 'ENGLISH'

    # Case 3: Entirely/Primarily Roman Urdu
    return 'ROMAN_URDU'


def contains_forbidden_script(text: str) -> bool:
    """Check if output contains Devanagari (Hindi) or Perso-Arabic (Urdu) script."""
    if not text:
        return False
    if DEVANAGARI_SCRIPT_PATTERN.search(text):
        return True
    if URDU_SCRIPT_PATTERN.search(text):
        return True
    return False
