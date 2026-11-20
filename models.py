from pydantic import BaseModel, Field
from typing import Optional


class LabelData(BaseModel):
    """Raw data extracted from a product label image."""
    manufacturer_name: Optional[str] = Field(None, description="Manufacturer/Packer/Importer name")
    manufacturer_address: Optional[str] = Field(None, description="Manufacturer/Packer full address")
    net_quantity: Optional[str] = Field(None, description="Net quantity with unit, e.g. '100g'")
    mrp: Optional[str] = Field(None, description="Maximum Retail Price, e.g. '₹50.00'")
    mrp_inclusive_taxes: Optional[str] = Field(
        None,
        description="Statement that MRP includes all taxes, e.g. 'Incl. of all taxes'"
    )
    date_of_pack: Optional[str] = Field(None, description="Month/Year of manufacture/packing, e.g. '01/2025'")
    batch_lot_number: Optional[str] = Field(None, description="Batch/Lot number")
    consumer_care: Optional[str] = Field(None, description="Consumer care phone/email/address")
    country_of_origin: Optional[str] = Field(None, description="Country of origin (for imported products)")
    generic_name: Optional[str] = Field(None, description="Common or generic name of commodity")
    best_before: Optional[str] = Field(None, description="Best before / Use by date (if applicable)")
    fssai_license: Optional[str] = Field(None, description="FSSAI License number (if applicable)")
        # Raw source lines for hallucination detection
    mrp_raw: Optional[str] = Field(None, description="Exact label line where MRP was read from")
    date_of_pack_raw: Optional[str] = Field(None, description="Exact label line where the packing date was read from")
    fssai_license_raw: Optional[str] = Field(None, description="Exact label line where FSSAI license was read from")
    consumer_care_raw: Optional[str] = Field(None, description="Exact label line where consumer care details were read from")
    batch_lot_number_raw: Optional[str] = Field(None, description="Exact label line where batch/lot number was read from")