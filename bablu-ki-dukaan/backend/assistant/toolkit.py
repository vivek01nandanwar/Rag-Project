"""Builds the LangChain tools for one chat turn and decides which tools each kind of request may use."""
import logging

from langchain_core.tools import StructuredTool

from . import ops, schemas

log = logging.getLogger("assistant")

READ = {
    "search_items": (schemas.SearchItemsArgs, ops.r_search_items,
                     "Find products by name, nickname or meaning. Returns id, unit, prices and current stock. Use before selling/buying/updating."),
    "list_low_stock": (schemas.NoArgs, ops.r_list_low_stock, "Products whose stock is at or below their warning level."),
    "find_party": (schemas.FindPartyArgs, ops.r_find_party,
                   "Find a customer or supplier by name; shows what they owe the shop / what the shop owes them."),
    "business_summary": (schemas.NoArgs, ops.r_business_summary,
                         "Today's sales and purchases, all pending money, stock value, low-stock count."),
    "list_pending": (schemas.ListPendingArgs, ops.r_list_pending,
                     "Open bills with outstanding money, oldest first, with real bill ids."),
    "recent_transactions": (schemas.RecentArgs, ops.r_recent, "Latest sales and purchases."),
    "sales_summary": (schemas.SalesSummaryArgs, ops.r_sales_summary,
                      "Sales total, received money, estimated profit and top 5 items for the last N days."),
    "search_documents": (schemas.SearchDocsArgs, ops.r_search_documents,
                         "Search inside the long files (PDF/Excel/Word/photos) the owner uploaded in THIS chat."),
    "list_custom_tables": (schemas.NoArgs, ops.r_list_custom_tables,
                           "List the extra registers (Employees, Expenses...) with their fields and record counts."),
    "list_custom_records": (schemas.ListCustomRecordsArgs, ops.r_list_custom_records,
                            "Rows of one extra register, optionally filtered by text. Returns record ids."),
}

WRITE = {
    "create_item": (schemas.CreateItemArgs, "Propose a NEW product. Needs unit, selling price and buying price."),
    "update_item": (schemas.UpdateItemArgs, "Propose changes to an existing product (price, nicknames, name, category, hide/show). Not stock."),
    "delete_item": (schemas.DeleteItemArgs, "Propose deleting a product that has NO bills. Otherwise suggest hiding it."),
    "create_party": (schemas.CreatePartyArgs, "Propose a new customer or supplier."),
    "update_party": (schemas.UpdatePartyArgs, "Propose changes to a customer/supplier's name, phone or address."),
    "delete_party": (schemas.DeletePartyArgs, "Propose deleting a customer/supplier that has NO bills."),
    "record_sale": (schemas.RecordSaleArgs, "Propose a sale. Reduces stock. Use amount_received for part payment / udhaar."),
    "record_purchase": (schemas.RecordPurchaseArgs, "Propose a purchase from a supplier. Increases stock and updates buying price."),
    "settle_dues": (schemas.SettleDuesArgs, "Propose receiving/paying/refunding pending money for a customer or supplier."),
    "adjust_stock": (schemas.AdjustStockArgs, "Propose correcting a product's stock to the counted quantity."),
    "create_custom_table": (schemas.CreateCustomTableArgs, "Propose a NEW extra register (e.g. Employees) with fields."),
    "add_custom_field": (schemas.AddCustomFieldArgs, "Propose adding one field to an existing extra register."),
    "delete_custom_table": (schemas.DeleteCustomTableArgs, "Propose deleting an extra register and its records."),
    "add_custom_record": (schemas.AddCustomRecordArgs, "Propose adding a row to an extra register."),
    "update_custom_record": (schemas.UpdateCustomRecordArgs, "Propose changing values of one extra-register row (by record id)."),
    "delete_custom_record": (schemas.DeleteCustomRecordArgs, "Propose deleting one extra-register row (by record id)."),
}

TOOLSETS = {
    "shop": ["search_items", "list_low_stock", "find_party", "business_summary", "list_pending", "recent_transactions",
             "sales_summary", "search_documents", "list_custom_tables",
             "create_item", "update_item", "delete_item", "create_party", "update_party", "delete_party",
             "record_sale", "record_purchase", "settle_dues", "adjust_stock"],
    "custom": ["list_custom_tables", "list_custom_records", "search_items", "find_party", "business_summary",
               "create_custom_table", "add_custom_field", "delete_custom_table",
               "add_custom_record", "update_custom_record", "delete_custom_record"],
}


def _read_tool(ctx, name, schema, fn, desc):
    def run(**kwargs):
        try:
            return ops._json(fn(ctx, schema(**kwargs)))
        except ops.ToolFail as e:
            return f"ERROR: {e}"
    return StructuredTool.from_function(func=run, name=name, description=desc, args_schema=schema)


def _write_tool(ctx, name, schema, desc):
    prepare = ops.WRITE_OPS[name][0]

    def run(**kwargs):
        try:
            prepare(ctx, schema(**kwargs))
            return f"QUEUED (nothing is saved until the owner presses Confirm): {ctx.proposals[-1]['summary']}"
        except ops.ToolFail as e:
            return f"ERROR: {e}"
        except Exception as e:  # bad values from the model must never crash the chat
            log.warning("write tool %s failed: %s", name, e)
            return "ERROR: those values could not be understood. Ask the owner to clarify."
    return StructuredTool.from_function(func=run, name=name, description=desc, args_schema=schema)


def build(ctx):
    tools = {n: _read_tool(ctx, n, s, f, d) for n, (s, f, d) in READ.items()}
    tools.update({n: _write_tool(ctx, n, s, d) for n, (s, d) in WRITE.items()})
    return tools
