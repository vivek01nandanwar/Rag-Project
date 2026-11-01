"""Argument shapes for every tool the AI may call. Keeping them simple (no Optional/None) makes
tool-calling far more reliable: "" / -1 / 0 mean "not given"."""
from typing import Literal

from pydantic import BaseModel, Field

Unit = Literal["kg", "g", "litre", "packet", "piece", "dozen"]
PartyKind = Literal["customer", "supplier"]
FieldType = Literal["text", "number", "date", "yes_no", "choice"]


# ----------------------------------------------------------------- read tools
class NoArgs(BaseModel):
    pass


class SearchItemsArgs(BaseModel):
    query: str = Field(description="Product name or nickname in any language, e.g. 'ghee', 'ghi', 'chai patti'.")


class FindPartyArgs(BaseModel):
    kind: PartyKind
    query: str = Field(description="Name (or part of it). Use an empty string to list everyone.")


class ListPendingArgs(BaseModel):
    kind: Literal["customers_owe_shop", "shop_owes_customers", "shop_owes_suppliers", "suppliers_owe_shop"]


class RecentArgs(BaseModel):
    limit: int = Field(10, ge=1, le=30)


class SalesSummaryArgs(BaseModel):
    days: int = Field(1, ge=1, le=90, description="1 = today, 7 = last week, 30 = last month.")


class SearchDocsArgs(BaseModel):
    query: str = Field(description="What to look for inside the files the owner uploaded in this chat.")


class ListCustomRecordsArgs(BaseModel):
    table: str = Field(description="Register name, code or id.")
    query: str = Field("", description="Optional text to filter rows.")
    limit: int = Field(10, ge=1, le=30)


# ----------------------------------------------------------------- shop write tools (all need owner confirmation)
class LineIn(BaseModel):
    item: str = Field(description="Product name, nickname or id (ITM-0001).")
    quantity: float = Field(description="Quantity exactly as the owner said it, in the unit given in 'unit'.")
    unit: str = Field("", description="Unit of 'quantity' when it differs from the product's own unit, e.g. 'g' for a kg product. "
                                      "Empty = the product's own unit. The system converts it.")
    price: float = Field(0, description="Price per ONE of the product's OWN unit (per kg for a kg product). 0 = use the saved price.")
    total: float = Field(0, description="If the owner stated the money for this whole line (e.g. '500 g for 60 rupees') put it here "
                                        "instead of price. 0 = not given.")


class CreateItemArgs(BaseModel):
    name: str
    unit: Unit
    selling_price: float = Field(description="Price per unit the shop sells at.")
    cost_price: float = Field(description="Price per unit the shop buys at.")
    opening_stock: float = Field(0, description="Stock already on the shelf.")
    aliases: str = Field("", description="Other names, comma separated, e.g. 'ghi, desi ghee'.")
    category: str = Field("", description="e.g. Grocery, Dairy, Snacks, Household, Stationery.")
    low_stock_threshold: float = 5
    emoji: str = ""


class UpdateItemArgs(BaseModel):
    item: str
    name: str = ""
    unit: str = ""
    selling_price: float = Field(-1, description="-1 = unchanged")
    cost_price: float = Field(-1, description="-1 = unchanged")
    aliases: str = Field("", description="Replaces all nicknames. Empty = unchanged.")
    category: str = ""
    low_stock_threshold: float = Field(-1, description="-1 = unchanged")
    emoji: str = ""
    active: str = Field("", description="'yes' to show the product again, 'no' to hide it. Empty = unchanged.")


class DeleteItemArgs(BaseModel):
    item: str


class CreatePartyArgs(BaseModel):
    kind: PartyKind
    name: str
    phone: str = ""
    address: str = ""


class UpdatePartyArgs(BaseModel):
    kind: PartyKind
    party: str = Field(description="Existing name or id.")
    new_name: str = ""
    phone: str = ""
    address: str = ""


class DeletePartyArgs(BaseModel):
    kind: PartyKind
    party: str


class RecordSaleArgs(BaseModel):
    customer: str = Field("", description="Customer name/id. Empty = walk-in. Needed for udhaar or change owed.")
    lines: list[LineIn]
    amount_received: float = Field(-1, description="Money received now. -1 = the full bill was paid. 0 = nothing paid (udhaar).")
    payment_mode: str = Field("", description="'cash', 'upi' or 'udhaar'. Empty = decide automatically.")
    date: str = Field("", description="Sale date YYYY-MM-DD. Empty = today.")


class RecordPurchaseArgs(BaseModel):
    supplier: str = Field("", description="Supplier name/id. Needed when part of the bill is unpaid.")
    lines: list[LineIn] = Field(description="'price' here is the BUYING price per unit (0 = saved buying price).")
    amount_paid: float = Field(-1, description="Money paid now. -1 = the full bill was paid. 0 = nothing paid.")
    date: str = Field("", description="Purchase date YYYY-MM-DD. Empty = today.")


class SettleDuesArgs(BaseModel):
    action: Literal["receive_payment", "give_refund", "pay_supplier", "receive_refund"] = Field(
        description="receive_payment: customer pays pending udhaar. give_refund: shop returns extra money to a customer. "
                    "pay_supplier: shop pays a supplier's pending bill. receive_refund: supplier returns extra money.")
    party: str = Field(description="Customer or supplier name/id (customer for the first two actions, supplier for the last two).")
    amount: float
    bill: str = Field("", description="Optional bill id (SAL-0001 / PUR-0001) to settle only that bill, e.g. right after a return on it.")


class AdjustStockArgs(BaseModel):
    item: str
    new_quantity: float = Field(description="The counted stock on the shelf.")
    note: str = ""


# ----------------------------------------------------------------- extra register write tools
class FieldIn(BaseModel):
    name: str
    type: FieldType = "text"
    required: bool = False
    choices: str = Field("", description="Only for type 'choice': options separated by commas.")


class CreateCustomTableArgs(BaseModel):
    name: str = Field(description="Plural noun like 'Employees', 'Expenses', 'Rent payments'.")
    description: str = ""
    fields: list[FieldIn]


class AddCustomFieldArgs(BaseModel):
    table: str
    field: FieldIn


class DeleteCustomTableArgs(BaseModel):
    table: str


class FieldValue(BaseModel):
    field: str
    value: str


class AddCustomRecordArgs(BaseModel):
    table: str
    values: list[FieldValue]


class UpdateCustomRecordArgs(BaseModel):
    record_id: str
    values: list[FieldValue]


class DeleteCustomRecordArgs(BaseModel):
    record_id: str


# ----------------------------------------------------------------- prices, orders and returns
class PriceQuoteArgs(BaseModel):
    item: str = Field(description="Product name, nickname or id.")
    quantity: float = Field(1, description="How much the owner asks about.")
    unit: str = Field("", description="Unit of that quantity (g, kg, ml, litre, piece, dozen, packet). Empty = the product's own unit.")


class FindBillsArgs(BaseModel):
    kind: Literal["sale", "purchase"]
    party: str = Field("", description="Customer (sale) or supplier (purchase) name/id. Empty = anyone.")
    item: str = Field("", description="Only orders that contain this product. Empty = any.")
    date: str = Field("", description="Exact order date YYYY-MM-DD. Empty = any.")
    date_from: str = Field("", description="Orders on or after this date YYYY-MM-DD.")
    date_to: str = Field("", description="Orders on or before this date YYYY-MM-DD.")
    only_returnable: bool = Field(False, description="True = only orders that still have something that can be returned.")
    limit: int = Field(5, ge=1, le=8)


class RecordPurchaseReturnArgs(BaseModel):
    bill: str = Field(description="The purchase bill id (PUR-0001) that the goods came from. Get it from find_bills and confirm it with the owner.")
    item: str = Field(description="Product being sent back.")
    quantity: float = Field(description="Quantity as the owner said it.")
    unit: str = Field("", description="Unit of the quantity if not the product's own unit. Empty = product's unit.")
    reason: str = Field(description="Why it is sent back (damaged, expired, wrong item...). Required.")
    date: str = Field("", description="Return date YYYY-MM-DD. Empty = today.")


class RecordSaleReturnArgs(BaseModel):
    bill: str = Field(description="The sale bill id (SAL-0001) the customer bought from. Get it from find_bills and confirm it with the owner.")
    item: str = Field(description="Product the customer brings back.")
    quantity: float = Field(description="Quantity as the owner said it.")
    unit: str = Field("", description="Unit of the quantity if not the product's own unit. Empty = product's unit.")
    date: str = Field("", description="Return date YYYY-MM-DD. Empty = today.")
