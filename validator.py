import re
from typing import Optional

from models import LabelData
from rules import (
    MANDATORY_DECLARATIONS,
    PATTERNS,
    EXEMPTIONS,
    parse_quantity,
    find_standard_pack_category,
    is_standard_size,
)


def _digits_only(s: str) -> str:
    """Extract only digits from a string for comparison."""
    return re.sub(r'\D', '', s or "")


def _verify_against_raw(
    field_name: str,
    value: Optional[str],
    raw_line: Optional[str],
    warnings: list,
) -> None:
    """
    Verify that the digits of `value` appear as a prefix-substring in `raw_line`.
    Tolerates trailing zeros and formatting differences.
    """
    if not value or not raw_line:
        return

    v_clean = str(value).strip()
    r_clean = str(raw_line).strip()
    if not v_clean or not r_clean:
        return

    v_digits = _digits_only(v_clean).rstrip("0")  # strip trailing zeros
    r_digits = _digits_only(r_clean)

    if not v_digits:
        return

    if v_digits not in r_digits:
        warnings.append(
            f"SUSPECTED HALLUCINATION in '{field_name}': value '{v_clean}' "
            f"was not found in its source text. Raw line: '{r_clean[:120]}'"
        )


def _is_industrial_or_bulk_exempt(
    product_hint: Optional[str],
    net_value: Optional[float],
    net_unit: Optional[str],
) -> Optional[str]:
    """
    Return a reason string if the package is exempt under Rule 26 / related
    provisions, otherwise None.
    """
    if net_value is None or net_unit is None:
        return None

    product_lower = (product_hint or "").lower()

    # Above 25kg/25L exemption (cement + fertilizers excepted)
    if net_unit == "kg" and net_value > EXEMPTIONS["above_25kg_l"]:
        if not any(exc in product_lower for exc in EXEMPTIONS["above_25kg_l_exceptions"]):
            return f"Package exceeds {EXEMPTIONS['above_25kg_l']}kg - exempt under Rule 26"
    if net_unit == "l" and net_value > EXEMPTIONS["above_25kg_l"]:
        return f"Package exceeds {EXEMPTIONS['above_25kg_l']}L - exempt under Rule 26"

    # Agricultural produce above 50kg exemption
    if net_unit == "kg" and net_value > EXEMPTIONS["agri_produce_threshold_kg"]:
        if any(alias in product_lower for alias in EXEMPTIONS["agri_produce_aliases"]):
            return (
                f"Agricultural produce above {EXEMPTIONS['agri_produce_threshold_kg']}kg "
                f"- exempt under Rule 26"
            )

    return None


def validate_compliance(
    data: LabelData,
    product_hint: Optional[str] = None,
    unreliable: bool = False,
    ) -> dict:
    """
    Validate extracted data against Legal Metrology (Packaged Commodities) Rules, 2011.
    Returns {"is_compliant": bool, "violations": [...], "warnings": [...]}
    """
    # === Unverifiable image check ===
    if unreliable:
        return {
            "is_compliant": None,
            "violations": [],
            "warnings": [
                "Image is unreadable or too degraded — could not extract "
                "meaningful declarations. Please collect a clearer photo."
            ],
            "verdict": "UNVERIFIABLE",
        }

    violations: list[str] = []
    warnings: list[str] = []

    net_value, net_unit = parse_quantity(data.net_quantity)

    # === Exemption 1: below 10g/10ml ===
    if net_value is not None and net_unit in ("g", "ml"):
        if net_value < EXEMPTIONS["min_quantity_g_ml"]:
            return {
                "is_compliant": True,
                "violations": [],
                "warnings": [
                    f"Package below 10{net_unit} - exempt from net quantity "
                    f"and MRP declarations under Rule 26"
                ],
                "verdict": "COMPLIANT",
            }

    # === Exemption 2 & 3: bulk / agricultural ===
    bulk_reason = _is_industrial_or_bulk_exempt(product_hint, net_value, net_unit)
    if bulk_reason:
        return {
            "is_compliant": True,
            "violations": [],
            "warnings": [bulk_reason],
            "verdict": "COMPLIANT",
        }

    # === Mandatory declaration checks ===
    for field in MANDATORY_DECLARATIONS:
        value = getattr(data, field, None)
        if value is None or str(value).strip().lower() in ("", "null", "none"):
            violations.append(f"CRITICAL: Missing mandatory declaration - {field}")

    # === Cross-image conflict detection ===
    additional_critical_fields = ["fssai_license", "manufacturer_name", "manufacturer_address"]
    for field in additional_critical_fields:
        if field in MANDATORY_DECLARATIONS:
            continue
        value = getattr(data, field, None)
        if value is None or str(value).strip().lower() in ("", "null", "none"):
            violations.append(
                f"CRITICAL: '{field}' could not be confirmed — "
                f"multiple label images disagreed. Manual review required."
            )

    # === Net quantity format validation ===
    if data.net_quantity:
        if not PATTERNS["net_quantity"].search(data.net_quantity):
            warnings.append(
                f"Net quantity format unusual: '{data.net_quantity}' "
                f"- expected format like '100g' or '500ml'"
            )
    elif net_value is not None and net_value <= 0:
        violations.append(
            f"CRITICAL: Invalid net quantity value: {data.net_quantity} (must be > 0)"
        )

    # === MRP format validation ===
    if data.mrp:
        if not PATTERNS["mrp"].search(data.mrp):
            warnings.append(
                f"MRP format unusual: '{data.mrp}' - expected 'MRP ₹XX' format"
            )

        combined = " ".join(filter(None, [data.mrp, data.mrp_inclusive_taxes or ""]))
        has_inclusive = (
            PATTERNS["inclusive_taxes"].search(combined)
            or "inclusive" in combined.lower()
            or "incl." in combined.lower()
        )
        if not has_inclusive:
            warnings.append(
                "MRP does not state 'inclusive of all taxes' "
                "(required under Rule 6(1)(e))"
            )

    # === Date format validation ===
    if data.date_of_pack:
        if not PATTERNS["date"].search(data.date_of_pack):
            warnings.append(
                f"Date format unusual: '{data.date_of_pack}' - expected MM/YYYY format"
            )

    # === Standard pack size check ===
    if product_hint and net_value is not None and net_unit is not None:
        category = find_standard_pack_category(product_hint)
        if category and not is_standard_size(category, net_value, net_unit):
            violations.append(
                f"CRITICAL: '{product_hint}' packed in non-standard size "
                f"{data.net_quantity}. This commodity is regulated under Rule 5 "
                f"(Second Schedule)."
            )

    # === Raw-text anchoring: verify extracted values appear in their source lines ===
    _verify_against_raw("mrp", data.mrp, data.mrp_raw, warnings)
    _verify_against_raw("date_of_pack", data.date_of_pack, data.date_of_pack_raw, warnings)
    _verify_against_raw("fssai_license", data.fssai_license, data.fssai_license_raw, warnings)
    _verify_against_raw("consumer_care", data.consumer_care, data.consumer_care_raw, warnings)
    _verify_against_raw("batch_lot_number", data.batch_lot_number, data.batch_lot_number_raw, warnings)

    return {
        "is_compliant": len(violations) == 0,
        "violations": violations,
        "warnings": warnings,
        "verdict": "COMPLIANT" if len(violations) == 0 else "NON-COMPLIANT",
    }