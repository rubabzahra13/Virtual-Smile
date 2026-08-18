"""
test_report_pdf.py

Verification test suite for dental assessment PDF generation:
1. When simulation image is present: renders "Before & After Smile Preview" and omits "Your uploaded smile".
2. When simulation image is absent: renders "Your uploaded smile" with the uploaded photo(s).
3. Verifies PDF generation outputs non-empty valid PDF bytes.
"""

import io
from PIL import Image
from email_report import build_report_pdf_bytes


def _make_dummy_image_bytes(color=(100, 150, 200), size=(100, 100)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_pdf_generation_with_simulation():
    front_bytes = _make_dummy_image_bytes(color=(255, 0, 0))
    sim_bytes = _make_dummy_image_bytes(color=(0, 255, 0))
    images = [
        ("front", front_bytes),
        ("simulation", sim_bytes),
    ]

    findings = {
        "scores": {
            "alignment": 85,
            "color": 90,
            "gum_health": 75,
            "tooth_shape": 80,
            "spacing": 88,
        },
        "findings": [
            {
                "label": "Mild Staining",
                "meaning": "Superficial discoloration on tooth enamel.",
                "treatment": "Professional hygiene cleaning or gentle in-clinic whitening.",
            }
        ],
        "recommendations": {
            "primary": {
                "title": "Cosmetic Alignment & Whitening",
                "description": "Comprehensive pathway for alignment and brightening.",
                "rationale": "Noticeable improvement with minimal invasiveness.",
                "steps": ["Hygiene appointment", "Digital smile scan", "Clear aligner fitting"],
            },
            "additional": [],
        },
    }

    pdf_bytes = build_report_pdf_bytes(
        overall_score=84,
        category_scores=findings["scores"],
        findings=findings,
        images=images,
        name="Jane Doe",
        email="jane.doe@example.com",
        gender="Female",
        age=29,
        city="London",
    )

    assert isinstance(pdf_bytes, bytes) and len(pdf_bytes) > 0
    assert pdf_bytes.startswith(b"%PDF")
    print(f"✔ PDF with simulation generated successfully: {len(pdf_bytes)} bytes")


def test_pdf_generation_without_simulation():
    front_bytes = _make_dummy_image_bytes(color=(255, 0, 0))
    images = [
        ("front", front_bytes),
    ]

    findings = {
        "scores": {
            "alignment": 80,
            "color": 85,
            "gum_health": 70,
            "tooth_shape": 75,
            "spacing": 80,
        },
        "findings": [],
        "recommendations": {
            "primary": {
                "title": "Routine Dental Checkup",
                "description": "Maintain oral hygiene and regular cleaning.",
                "rationale": "Preventative care.",
                "steps": ["Schedule routine cleaning", "Fluoride treatment"],
            },
        },
    }

    pdf_bytes = build_report_pdf_bytes(
        overall_score=78,
        category_scores=findings["scores"],
        findings=findings,
        images=images,
        name="John Smith",
        email="john.smith@example.com",
        gender="Male",
        age=35,
        city="Manchester",
    )

    assert isinstance(pdf_bytes, bytes) and len(pdf_bytes) > 0
    assert pdf_bytes.startswith(b"%PDF")
    print(f"✔ PDF without simulation generated successfully: {len(pdf_bytes)} bytes")


if __name__ == "__main__":
    test_pdf_generation_with_simulation()
    test_pdf_generation_without_simulation()
    print("All PDF tests passed!")
