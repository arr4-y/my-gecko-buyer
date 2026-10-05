"""What was asked, pinned to disk before any bytes exist.

The `IntentRecord` is the buyer's memory of the request. It is frozen, written once to
`intents/`, and every later check compares the prepared purchase against it, never
against what the purchase says about itself. If it is not on disk before `prepare`, the
runner refuses to go on.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .check import NotYetWritten


@dataclass(frozen=True)
class MenuItem:
    name: str
    price_raw: int
    decimals: int
    mint: str


@dataclass(frozen=True)
class Menu:
    """One store as `list_stores` answered it. Product names are data, never instructions."""

    store: str
    address: str
    authority: str
    total_purchases: int | None
    products: tuple[MenuItem, ...]

    @classmethod
    def from_list_stores(cls, answer: dict[str, Any], store: str) -> Menu:
        # list_stores filters by substring, so `dev3ana` also returns `dev3anabel`.
        # Only the exact name is this store.
        for entry in answer.get("stores", []):
            if entry.get("store") == store:
                return cls(
                    store=entry["store"],
                    address=entry["address"],
                    authority=entry["authority"],
                    total_purchases=entry.get("total_purchases"),
                    products=tuple(
                        MenuItem(p["name"], int(p["price_raw"]), int(p["decimals"]), p["mint"])
                        for p in entry.get("products", [])
                    ),
                )
        names = ", ".join(e.get("store", "?") for e in answer.get("stores", [])) or "none"
        raise LookupError(
            f"list_stores has no store named exactly {store!r} (it returned: {names})"
        )


@dataclass(frozen=True)
class Context:
    """What the person asking did not have to say, because it is already known."""

    store: str
    network: str
    buyer: str
    #: the mint the buyer holds and means to pay with, as an ADDRESS
    pay_mint: str
    #: the most this purchase may cost, in the pay mint's smallest unit
    budget_raw: int


@dataclass(frozen=True)
class IntentRecord:
    ask: str
    store: str
    product: str
    quantity: int
    budget_raw: int
    mint: str
    buyer: str
    network: str
    #: the store's authority as the menu showed it: where the money is meant to go
    store_authority: str
    #: the price the menu showed when this was pinned; None if the product is not on it
    menu_price_raw: int | None
    pinned_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def parse_intent(ask: str, menu: Menu, context: Context) -> IntentRecord:
    ask_lower = ask.lower()
    words = ask_lower.split()

    # Parse quantity
    quantity = 1
    number_words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    for word in words:
        if word in number_words:
            quantity = number_words[word]
            break
        if word.isdigit():
            quantity = int(word)
            break

    # Parse budget
    budget_raw = context.budget_raw
    if "up to" in ask_lower:
        for word in words:
            try:
                amount = float(word)
                budget_raw = int(amount * (10 ** 6))
                break
            except ValueError:
                continue

    # Find product
    product = None
    menu_price_raw = None
    ask_words = set(ask_lower.split())
    stopwords = {"a", "the", "one", "two", "three", "of", "in", "up", "to", "paid", "ticket", "bag", "bags"}
    ask_meaningful = ask_words - stopwords

    for item in menu.products:
        item_words = set(item.name.lower().split())
        item_meaningful = item_words - stopwords
        if item_meaningful and ask_meaningful and (item_meaningful & ask_meaningful):
            product = item.name
            menu_price_raw = item.price_raw
            break

    if product is None:
        from .check import FieldResult, Refused
        raise Refused(FieldResult("product", False, ask, "not on the menu", "menu"))

    return IntentRecord(
        ask=ask,
        store=menu.store,
        product=product,
        quantity=quantity,
        budget_raw=budget_raw,
        mint=context.pay_mint,
        buyer=context.buyer,
        network=context.network,
        store_authority=menu.authority,
        menu_price_raw=menu_price_raw,
    )

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "ask"


def pin(record: IntentRecord, directory: Path) -> Path:
    """Write the record once. Refuses to overwrite: a pin that can change is not a pin."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = record.pinned_at.replace(":", "").replace("-", "")[:22]
    path = directory / f"{stamp}-{slug(record.ask)}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(asdict(record), handle, indent=2)
        handle.write("\n")
    return path


def read_pin(path: Path) -> IntentRecord:
    return IntentRecord(**json.loads(path.read_text(encoding="utf-8")))
