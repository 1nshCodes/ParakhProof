import re
from typing import Optional, Tuple

# Rule 6(1) Mandatory declarations under Legal Metrology (Packaged Commodities) Rules, 2011
MANDATORY_DECLARATIONS = [
    "manufacturer_name",
    "manufacturer_address",
    "net_quantity",
    "mrp",
    "date_of_pack",
    "consumer_care",
]

# Standard pack sizes for regulated commodities (Rule 5, Second Schedule)
STANDARD_PACK_SIZES = {
    "baby_food": {
        "aliases": ["baby food", "infant formula"],
        "sizes_g": [25, 50, 100, 200, 300, 350, 400, 450, 500, 600, 700, 800, 900, 1000, 2000, 5000, 10000]
    },
    "biscuits": {
        "aliases": ["biscuit", "cookie"],
        "sizes_g": [25, 50, 60, 75, 100, 120, 150, 200, 250, 300],
        "pattern_above_300g": "multiples of 100g up to 1kg, then multiples of 500g up to 5kg"
    },
    "bread": {
        "aliases": ["bread", "brown bread"],
        "sizes_g": [50, 100, 150, 200, 250, 300, 350, 400, 450, 500],
        "pattern_above_500g": "multiples of 100g"
    },
    "tea": {
        "aliases": ["tea", "chai"],
        "sizes_g": [25, 50, 100, 125, 250, 500, 1000],
        "pattern_above_1kg": "multiples of 1kg"
    },
    "coffee": {
        "aliases": ["coffee"],
        "sizes_g": [25, 50, 75, 100, 150, 200, 250, 500, 1000, 1500, 2000],
        "pattern_above_2kg": "multiples of 1kg"
    },
    "edible_oils": {
        "aliases": ["edible oil", "vanaspati", "ghee", "butter oil", "sunflower oil", "mustard oil"],
        "sizes_g": [50, 100, 200, 250, 500, 1000, 2000, 3000, 5000],
        "sizes_ml": [50, 100, 200, 250, 500, 1000, 2000, 3000, 5000],
        "pattern_above_5kg": "multiples of 5kg/5L"
    },
    "cereals_pulses": {
        "aliases": ["cereal", "pulse", "dal", "rice", "wheat"],
        "sizes_g": [100, 200, 500, 1000, 2000, 5000],
        "pattern_above_5kg": "multiples of 5kg"
    },
    "salt": {
        "aliases": ["salt", "namak"],
        "sizes_g": [50, 100, 200, 500, 750, 1000, 2000, 5000],
        "pattern_above_5kg": "multiples of 5kg"
    },
    "soaps": {
        "aliases": ["soap", "toilet soap", "laundry soap", "detergent bar"],
        "sizes_g": [15, 25, 50, 60, 75, 100, 125, 150, 200, 250, 300],
        "pattern_above_300g": "multiples of 50g/100g depending on type"
    },
    "mineral_water": {
        "aliases": ["mineral water", "drinking water", "packaged water"],
        "sizes_ml": [100, 150, 200, 250, 300, 500, 750, 1000, 1500, 2000, 3000, 4000, 5000],
        "pattern_above_5L": "multiples of 5L"
    },
    "milk_powder": {
        "aliases": ["milk powder"],
        "sizes_g": [50, 100, 150, 200, 250, 500, 1000],
        "pattern_above_1kg": "multiples of 500g"
    },
    "non_soapy_detergents": {
        "aliases": ["detergent powder", "washing powder"],
        "sizes_g": [50, 75, 100, 150, 200, 250, 400, 500, 700, 750, 1000, 1500, 2000],
        "pattern_above_2kg": "multiples of 1kg"
    },
    "aerated_drinks": {
        "aliases": ["soft drink", "cold drink", "soda", "aerated drink"],
        "sizes_ml": [65, 100, 125, 150, 160, 175, 180, 200, 240, 250, 300, 330, 350, 400, 475, 500, 600, 750, 1000, 1200, 1250, 1500, 1750, 2000, 2250, 2500, 3000, 4000, 5000]
    },
    "cement": {
        "aliases": ["cement"],
        "sizes_kg": [1, 2, 5, 10, 20, 25, 40, 50]
    },
}

# Minimum font height requirements (Rule 7, Table-II)
# Boundaries: A < 100, 100 <= A < 500, 500 <= A < 2500, A >= 2500
FONT_SIZE_REQUIREMENTS = [
    {"area_min_cm2": 0,    "area_max_cm2": 100,    "min_height_mm": 1, "molded_min_height_mm": 2},
    {"area_min_cm2": 100,  "area_max_cm2": 500,    "min_height_mm": 2, "molded_min_height_mm": 4},
    {"area_min_cm2": 500,  "area_max_cm2": 2500,   "min_height_mm": 4, "molded_min_height_mm": 6},
    {"area_min_cm2": 2500, "area_max_cm2": 999999, "min_height_mm": 6, "molded_min_height_mm": 6},
]

# Exemptions (Rule 26 and related provisions)
EXEMPTIONS = {
    "min_quantity_g_ml": 10,           # Below 10g/10ml exempt from net quantity + MRP declarations
    "industrial_institutional": True,   # Exempt for industrial/institutional consumers
    "above_25kg_l": 25,                 # Exempt above 25kg/25L (cement and fertilizers excepted)
    "above_25kg_l_exceptions": ["cement", "fertilizer", "fertiliser"],
    "agri_produce_threshold_kg": 50,    # Agricultural produce above 50kg exempt
    "agri_produce_aliases": ["rice", "wheat", "pulse", "dal", "cereal", "grain"],
}

# Regex patterns
PATTERNS = {
    "net_quantity": re.compile(r'(\d+(?:\.\d+)?)\s*(g|gm|kg|ml|l|litre|liter|mg)\b', re.IGNORECASE),
    "mrp": re.compile(
        r'(?:MRP|Maximum\s+Retail\s+Price|Rs\.?|₹|rupees?)\s*[:.\s]?\s*([₹]?\d+(?:[.,]\d{1,2})?)',
        re.IGNORECASE
    ),
    "date": re.compile(r'(\d{1,2})\s*[/-]\s*(\d{2,4})'),
    "consumer_care": re.compile(r'(?:consumer|care|customer|helpline|toll.?free|1800|@).{0,100}', re.IGNORECASE),
    "inclusive_taxes": re.compile(r'inclusive\s+of\s+all\s+taxes', re.IGNORECASE),
}

VALID_UNITS = {"g", "kg", "mg", "ml", "l"}


def parse_quantity(text: Optional[str]) -> Tuple[Optional[float], Optional[str]]:
    """Parse net quantity text, return (value, unit). Returns (None, None) on failure."""
    if not text:
        return None, None
    match = PATTERNS["net_quantity"].search(text)
    if not match:
        return None, None
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):
        return None, None
    unit = match.group(2).lower()
    unit_map = {"gm": "g", "liter": "l", "litre": "l"}
    unit = unit_map.get(unit, unit)
    if unit not in VALID_UNITS:
        return None, None
    if value <= 0:
        return None, None
    return value, unit


def _category_is_volume(category_data: dict) -> bool:
    """Determine whether a category expects volume units (ml/l) vs weight (g/kg)."""
    has_weight = "sizes_g" in category_data or "sizes_kg" in category_data
    has_volume = "sizes_ml" in category_data
    if has_weight and not has_volume:
        return False
    if has_volume and not has_weight:
        return True
    # Both available (e.g. edible oils) — caller decides by unit
    return has_volume


def find_standard_pack_category(product_name: Optional[str]) -> Optional[str]:
    """Match product name to standard pack category."""
    if not product_name:
        return None
    product_lower = product_name.lower()
    for category, data in STANDARD_PACK_SIZES.items():
        for alias in data["aliases"]:
            if alias in product_lower:
                return category
    return None


def is_standard_size(category: str, value: float, unit: str) -> bool:
    """
    Check if a given (value, unit) matches the standard pack sizes for a category.
    Returns True if the size matches OR if we cannot check (unknown category).
    Returns False if the size is definitively non-standard for the category.
    """
    if category not in STANDARD_PACK_SIZES:
        return True

    data = STANDARD_PACK_SIZES[category]
    is_volume = unit in ("ml", "l")
    is_weight = unit in ("g", "kg", "mg")

    # --- Volume path ---
    if is_volume:
        if "sizes_ml" not in data:
            return False  # Category does not accept volume units
        ml = value * 1000 if unit == "l" else value
        return ml in data["sizes_ml"]

    # --- Weight path ---
    if is_weight:
        # Normalize to grams
        if unit == "kg":
            grams = value * 1000
        elif unit == "mg":
            grams = value / 1000
        else:
            grams = value

        # If category uses kg for large sizes (e.g. cement)
        if "sizes_kg" in data and "sizes_g" not in data:
            kg_value = grams / 1000
            return kg_value in data["sizes_kg"]

        if "sizes_g" in data:
            # Handle both pure-gram and mixed categories
            if grams in data["sizes_g"]:
                return True
            # If category also declares sizes_kg (e.g. edible oils, cereals)
            if "sizes_kg" in data:
                kg_value = grams / 1000
                if kg_value in data["sizes_kg"]:
                    return True
            return False

        # Category only has sizes_kg
        if "sizes_kg" in data:
            return (grams / 1000) in data["sizes_kg"]

        return False

    return False