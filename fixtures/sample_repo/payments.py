"""Checkout flows for the storefront.

Talks to the payments provider. Every function here would break if the
provider changed its response contract or made a param required.
"""


def checkout(stripe, cart_total, card_token):
    """Charge the customer's card and return the provider charge id."""
    charge = stripe.Charge.create(
        amount=cart_total,
        currency="usd",
        source=card_token,
    )
    if charge["status"] != "succeeded":
        raise RuntimeError("payment failed")
    return charge["id"]


def capture_status(stripe, charge_id):
    """Return a human label for whether funds were captured."""
    charge = stripe.Charge.retrieve(charge_id)
    if charge["captured"]:
        return "captured"
    return "authorized"


def create_billing_customer(stripe, email):
    """Create a customer record for recurring billing."""
    customer = stripe.Customer.create()
    customer["balance"] = 0
    customer["email"] = email
    return customer["id"]


def refund_order(stripe, charge_id, amount=None):
    """Refund part or all of a charge."""
    if amount is None:
        return stripe.Refund.create(charge=charge_id)["status"]
    refund = stripe.Refund.create(charge=charge_id, amount=amount)
    return refund["status"]


def charge_amount_cents(stripe, charge_id):
    """Read the charged amount for receipt generation."""
    charge = stripe.Charge.retrieve(charge_id)
    return charge["amount"]
