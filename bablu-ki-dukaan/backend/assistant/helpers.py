"""Plain-Python helpers (no Django) so they can be tested on their own.

* unit conversion: 500 g of a kg product -> 0.5 kg; 2 dozen of a piece product -> 24 pieces ...
* date parsing and the rules for dates on shop records.
The AI never does this arithmetic itself: it passes what the owner said and this code converts.
"""
import re
from datetime import datetime, timedelta
from decimal import Decimal

THREE_PLACES = Decimal("0.001")


class HelperError(Exception):
    """A problem the owner should hear about, in plain words."""


UNIT_ALIAS = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "g": "g", "gm": "g", "gms": "g", "gr": "g", "gram": "g", "grams": "g",
    "l": "litre", "lt": "litre", "ltr": "litre", "litre": "litre", "litres": "litre", "liter": "litre", "liters": "litre",
    "ml": "ml", "millilitre": "ml", "millilitres": "ml", "milliliter": "ml", "milliliters": "ml",
    "piece": "piece", "pieces": "piece", "pc": "piece", "pcs": "piece", "no": "piece", "nos": "piece",
    "dozen": "dozen", "dozens": "dozen", "doz": "dozen", "dz": "dozen",
    "packet": "packet", "packets": "packet", "pkt": "packet", "pkts": "packet", "pack": "packet", "packs": "packet",
}
# unit -> (family, size in the smallest unit of that family)
FAMILY = {
    "kg": ("weight", Decimal("1000")), "g": ("weight", Decimal("1")),
    "litre": ("volume", Decimal("1000")), "ml": ("volume", Decimal("1")),
    "piece": ("count", Decimal("1")), "dozen": ("count", Decimal("12")),
    "packet": ("packet", Decimal("1")),
}


def normalize_unit(unit):
    return UNIT_ALIAS.get(re.sub(r"[\s.]+", "", str(unit or "").lower()))


def convert_qty(qty, from_unit, to_unit):
    """Convert `qty` given in `from_unit` into the product's own unit `to_unit` (3 decimal places)."""
    f, t = normalize_unit(from_unit), normalize_unit(to_unit)
    if f is None:
        raise HelperError(f"I don't know the unit '{from_unit}'. Use kg, g, litre, ml, piece, dozen or packet.")
    if t is None:
        raise HelperError(f"The product's unit '{to_unit}' is not one I can convert.")
    ff, fv = FAMILY[f]
    tf, tv = FAMILY[t]
    if ff != tf:
        raise HelperError(f"This product is counted in {t}, so '{from_unit}' cannot be converted to {t}. "
                          f"Ask the owner for the quantity in {t}.")
    result = (Decimal(qty) * fv / tv).quantize(THREE_PLACES)
    if result <= 0:
        raise HelperError("That quantity is too small to record (smallest is 0.001).")
    return result


def parse_date(raw, today, *, not_before=None, what="date", for_entry=True):
    """Empty -> today. Accepts 2026-10-12, 12-10-2026, 12/10/2026.
    for_entry=True (saving a record): not in the future, not older than 3 years, not before `not_before`."""
    s = str(raw or "").strip()
    if not s:
        return today
    d = None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            d = datetime.strptime(s, fmt).date()
            break
        except ValueError:
            pass
    if d is None:
        raise HelperError(f"I couldn't understand the {what} '{raw}'. Please give it like 2026-10-12.")
    if for_entry:
        if d > today:
            raise HelperError(f"The {what} {d:%d %b %Y} is in the future (today is {today:%d %b %Y}).")
        if d < today - timedelta(days=3 * 366):
            raise HelperError(f"The {what} {d:%d %b %Y} is more than 3 years old. Please check it.")
        if not_before and d < not_before:
            raise HelperError(f"The {what} {d:%d %b %Y} cannot be before the bill date {not_before:%d %b %Y}.")
    return d


def fmt_date(d, today):
    return f"{d:%d %b %Y}" + (" (today)" if d == today else "")
