from decimal import Decimal

from pricepulse.api.routes.dashboard import qs


def test_qs_drops_empty_and_false_and_renders_true() -> None:
    params = {
        "source": "ikea",
        "category": None,
        "q": "",
        "flagged_only": False,
        "on_sale_only": True,
        "min_discount": Decimal("20"),
        "sort": "name",
    }
    assert qs(params) == "source=ikea&on_sale_only=true&min_discount=20&sort=name"
    assert qs(params, cursor="abc", source=None) == (
        "on_sale_only=true&min_discount=20&sort=name&cursor=abc"
    )
    assert qs({"category": "Baby & kids"}) == "category=Baby+%26+kids"
