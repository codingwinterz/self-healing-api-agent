"""Inventory sync for the storefront catalog.

Reads and writes catalog products through the provider SDK.
"""


def push_product(api, product_id, name, price_cents):
    """Create a catalog product."""
    product = api.Product.create(
        name=name,
        price=price_cents,
    )
    return product["id"]


def is_sellable(api, product_id):
    """Return whether a product can be sold right now."""
    product = api.Product.retrieve(product_id)
    return product["active"]


def receipt_line(api, product_id):
    """Build one receipt line for an order item."""
    product = api.Product.retrieve(product_id)
    label = product["name"]
    cost = product["price"]
    note = product["metadata"].get("source")
    return f"{label}: {cost}"


def archive_product(api, product_id):
    """Take a product offline."""
    product = api.Product.retrieve(product_id)
    if product["active"] and product["metadata"]:
        return True
    return False
