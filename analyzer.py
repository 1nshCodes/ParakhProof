import base64
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional, List, Tuple

from dotenv import load_dotenv
from openai import APIError, OpenAI
from PIL import Image

from models import LabelData

@lru_cache(maxsize=1)
def vision_config() -> Tuple[OpenAI, str]:
    """Load the user's vision provider settings when a scan starts."""
    load_dotenv(Path(__file__).with_name(".env"))
    required = ("VISION_BASE_URL", "VISION_MODEL", "VISION_API_KEY")
    settings = {name: os.getenv(name, "").strip() for name in required}
    missing = [name for name in required if not settings[name]]
    if missing:
        raise ValueError(
            f"Missing vision settings: {', '.join(missing)}. "
            "Copy .env.example to .env and configure your vision provider."
        )
    return (
        OpenAI(api_key=settings["VISION_API_KEY"], base_url=settings["VISION_BASE_URL"]),
        settings["VISION_MODEL"],
    )


SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_FILE_SIZE_MB = 20
# Fields where cross-image disagreement must trigger human review, not silent merge
CRITICAL_FIELDS = {
    "net_quantity",
    "mrp",
    "date_of_pack",
    "manufacturer_name",
    "manufacturer_address",
    "fssai_license",
}

PROMPT = """OCR assistant for Indian product labels (Legal Metrology compliance).

Return ONLY a JSON object with these keys:

{
    "manufacturer_name": "or null",
    "manufacturer_address": "or null",
    "net_quantity": "e.g. '100g', '500ml' or null",
    "mrp": "the Maximum Retail Price for the ENTIRE package, e.g. '₹340.00'. Do NOT return per-gram, per-kg, or per-unit prices. Return null if not visible.",
    "mrp_raw": "exact label line for MRP or null",
    "mrp_inclusive_taxes": "e.g. 'Incl. of all taxes' or null",
    "date_of_pack": "packing/mfg date only (not Use By/Best Before), MM/YY or DD/MM/YY, or null",
    "date_of_pack_raw": "exact label line for date or null",
    "batch_lot_number": "or null",
    "batch_lot_number_raw": "exact label line or null",
    "consumer_care": "phone/email or null",
    "consumer_care_raw": "exact label line or null",
    "country_of_origin": "or null",
    "generic_name": "or null",
    "best_before": "or null",
    "fssai_license": "or null",
    "fssai_license_raw": "exact label line or null"
}

Rules:
- For fields with a '_raw' twin: copy the exact label text verbatim. If unsure, set both to null.
- MRP: return the TOTAL Maximum Retail Price for the entire package. IGNORE per-gram, per-kg, or per-unit prices such as '₹1.70/g'. The MRP is typically the LARGER number printed next to the word 'MRP'.
- Date: use 'Date of Packing'/'Mfg Date', NOT 'Use By'/'Best Before'.
- No prose. No markdown. Only JSON."""

def validate_image(image_path: Path) -> None:
    """Raise ValueError if the image is missing, too large, or corrupted."""
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    size_mb = image_path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"Image too large: {size_mb:.1f}MB (max {MAX_FILE_SIZE_MB}MB)")

    suffix = image_path.suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported image format: {suffix}. Use one of {SUPPORTED_FORMATS}")

    try:
        with Image.open(image_path) as img:
            img.verify()
    except Exception as e:
        raise ValueError(f"Invalid or corrupted image: {e}")


def _extract_json_object(text: str) -> Optional[dict]:
    """Robustly extract a JSON object from a model response."""
    if not text:
        return None

    cleaned = text.strip()

    # Strip markdown fences
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    # Attempt 1: parse the whole thing
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt 2: brace-balance scan, pick the largest parsable object
    candidates = []
    depth = 0
    start = None
    for i, ch in enumerate(cleaned):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(cleaned[start:i + 1])
                start = None

    for candidate in sorted(candidates, key=len, reverse=True):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    return None

def _count_extracted_fields(data: LabelData) -> int:
    """Count how many fields were successfully extracted (non-null, non-empty)."""
    count = 0
    for field_name, value in data.model_dump().items():
        if field_name.endswith("_raw"):
            continue  # Skip raw fields — they're not declarations
        if value is not None and str(value).strip().lower() not in ("", "null", "none"):
            count += 1
    return count


def _is_extraction_unreliable(data: LabelData, min_fields: int = 4) -> bool:
    """
    Heuristic: if fewer than `min_fields` mandatory declarations were extracted,
    the image is likely unreadable or the model failed to extract meaningfully.
    """
    mandatory = [
        "manufacturer_name",
        "manufacturer_address",
        "net_quantity",
        "mrp",
        "date_of_pack",
        "consumer_care",
    ]
    extracted = sum(
        1 for f in mandatory
        if getattr(data, f, None)
        and str(getattr(data, f)).strip().lower() not in ("", "null", "none")
    )
    return extracted < min_fields

def _media_type_for(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".webp": "image/webp",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(suffix, "image/jpeg")


def extract_label_data(image_path: Path, temperature: float = 0.1) -> LabelData:
    """Extract structured data from a single product label image."""
    validate_image(image_path)

    with open(image_path, "rb") as img:
        b64_image = base64.b64encode(img.read()).decode("utf-8")

    media_type = _media_type_for(image_path.suffix.lower())

    client, model = vision_config()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{b64_image}"}
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
            temperature=temperature,
        )
    except APIError as error:
        status = getattr(error, "status_code", None)
        detail = f"HTTP {status}" if status is not None else type(error).__name__
        raise RuntimeError(f"Vision provider request failed ({detail}).") from None

    response_text = response.choices[0].message.content or ""
    parsed = _extract_json_object(response_text)

    if parsed is None:
        raise ValueError("Vision provider did not return a JSON object.")

    try:
        return LabelData.model_validate(parsed)
    except ValueError:
        raise ValueError("Vision provider returned JSON with an unexpected schema.") from None


def _merge_label_data(
    base: LabelData,
    incoming: LabelData,
    base_source: Optional[str] = None,
    incoming_source: Optional[str] = None,
    source_map: Optional[dict] = None,
    conflicts: Optional[list] = None,
) -> LabelData:
    """
    Merge `incoming` into `base`.
    - Fields only in `incoming` fill gaps in `base`.
    - Fields agreeing in both: base value kept.
    - CRITICAL fields disagreeing: both discarded (set to None), conflict flagged.
    - Non-critical fields disagreeing: first value kept, conflict flagged as warning.
    """
    merged_dict = base.model_dump()
    incoming_dict = incoming.model_dump()

    for field, value in incoming_dict.items():
        current = merged_dict.get(field)
        current_clean = str(current).strip() if current else ""
        value_clean = str(value).strip() if value else ""

        # Only in incoming — safe to fill
        if not current_clean and value_clean:
            merged_dict[field] = value
            if source_map is not None and incoming_source:
                source_map[field] = incoming_source
            continue

        # Only in base — nothing to do
        if current_clean and not value_clean:
            continue

        # Both have values — compare (whitespace + case insensitive)
        if current_clean and value_clean:
            a = re.sub(r'\s+', '', current_clean).lower()
            b = re.sub(r'\s+', '', value_clean).lower()
            if a == b:
                continue  # Agreement — keep base value

            is_critical = field in CRITICAL_FIELDS

            # Capture the source BEFORE potentially popping it below
            first_source = source_map.get(field) if source_map else base_source

            if is_critical:
                merged_dict[field] = None
                if source_map is not None:
                    source_map.pop(field, None)

            if conflicts is not None:
                conflicts.append({
                    "field": field,
                    "first_value": current_clean,
                    "first_source": first_source,
                    "second_value": value_clean,
                    "second_source": incoming_source,
                    "severity": "CRITICAL" if is_critical else "WARNING",
                })

    return LabelData(**merged_dict)

def extract_from_multiple(
    image_paths: List[Path], temperature: float = 0.1
) -> Tuple[LabelData, dict, list]:
    """
    Extract label data from multiple images of the same product and merge them.

    Returns:
        merged_data: LabelData with best-effort merged fields
        source_map: dict mapping field_name -> filename that provided the value
        conflicts: list of dicts describing fields that disagreed across images
    """
    merged = LabelData()
    source_map: dict = {}
    conflicts: list = []
    processed = 0

    for img_path in image_paths:
        try:
            partial = extract_label_data(img_path, temperature=temperature)
        except Exception as e:
            print(f"[warn] Skipping {img_path.name}: {e}")
            continue

        processed += 1
        merged = _merge_label_data(
            merged,
            partial,
            incoming_source=img_path.name,
            source_map=source_map,
            conflicts=conflicts,
        )

    if processed == 0:
        raise ValueError("No images could be processed successfully")

    return merged, source_map, conflicts