"""Synthetic startup repo: the same business rules rendered heavy (nested) and clean (decomposed).

    python -m tools.synth_repo OUT --profile team3 --heavy-fraction 0.4 --seed 1

Writes OUT/before (a share of the modules heavy, the rest clean, like a real repo before a cleanup)
and OUT/after (every module clean, what an Argon-guided refactor aims for). Each module is a
pricing-rule engine generated from a seeded spec, so no two are textual copies: different
categories, bulk tiers, coupons, regions and shipping bands. Both renderings come from the same
spec and are checked against each other on generated cases before anything is written, and each
repo gets golden tests recorded from the heavy rendering.

Limits: the modules share one shape (a pricing engine), so this models structure and size, not
the variety of a real product. Profiles size the logic-dense core, not a whole startup repo.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

PROFILES = {"team3": 40, "team5": 100}  # modules; ~400 heavy lines each
WORDS = ["billing", "invoices", "subscriptions", "payouts", "loyalty", "shipping", "returns", "bundles", "marketplace", "donations", "rentals", "bookings",
         "tickets", "insurance", "leasing", "tuition", "catering", "freight", "licensing", "memberships", "auctions", "wholesale", "refunds", "credits",
         "vouchers", "gifting", "preorders", "kits", "repairs", "warranty", "events", "classes", "tours", "storage", "laundry", "delivery", "pharmacy",
         "grocery", "florist", "bakery", "print", "signage", "rooms", "parking", "charging", "cleaning", "moving", "tutoring", "coaching", "streaming"]
CATS = ["books", "food", "electronics", "clothing", "toys", "tools", "garden", "music", "sport", "pets", "beauty", "office", "kitchen", "auto", "baby", "art"]
REGIONS = ["US-CA", "US-NY", "US-TX", "US-WA", "EU-DE", "EU-FR", "EU-IT", "EU-ES", "EU-NL", "UK", "CA-ON", "CA-BC", "AU-NSW", "JP-13", "SG", "BR-SP", "MX-CMX", "ZA-GP"]
TIERS = ["silver", "gold", "platinum"]
BANDS = [(500, 500), (2000, 900), (5000, 1500), (None, 2500)]
KINDS = ["pct", "flat", "cat_pct", "tier_pct", "freeship"]


@dataclass
class Spec:
    """Everything that defines one module's behavior; both renderers read only this."""

    name: str
    cats: dict = field(default_factory=dict)  # name -> {"price", "weight", "tiers": [(min_qty, pct)] highest first}
    coupons: dict = field(default_factory=dict)  # code -> (kind, value, min_subtotal, target)
    tier_pcts: dict = field(default_factory=dict)
    regions: dict = field(default_factory=dict)  # name -> {"bp", "exceptions", "surcharge", "pickup"}
    cap_pct: int = 30
    free_over: int = 7500
    express_mult: int = 2
    express_flat: int = 500


def make_spec(rng: random.Random, name: str) -> Spec:
    """A seeded random but valid spec."""
    spec = Spec(name, cap_pct=rng.choice([25, 30, 35]), free_over=rng.choice([5000, 7500, 10000]), express_mult=rng.choice([2, 3]), express_flat=rng.choice([300, 500, 800]))
    for cat in rng.sample(CATS, rng.randint(8, 13)):
        mins = sorted(rng.sample([2, 3, 5, 8, 10, 20], rng.randint(1, 3)), reverse=True)
        spec.cats[cat] = {"price": rng.randrange(300, 30000, 50), "weight": rng.randrange(100, 3000, 50), "tiers": [(m, rng.randint(2, 8) + 2 * (len(mins) - i)) for i, m in enumerate(mins)]}
    spec.tier_pcts = {tier: rng.randint(1, 4) + 2 * i for i, tier in enumerate(TIERS[: rng.randint(2, 3)])}
    for region in rng.sample(REGIONS, rng.randint(10, 16)):
        us = region.startswith("US")
        spec.regions[region] = {"bp": rng.randrange(0, 2500, 25), "exceptions": {c: rng.randrange(0, 1200, 25) for c in rng.sample(list(spec.cats), rng.randint(0, 3))},
                                "surcharge": 0 if us else rng.randrange(400, 1200, 100), "pickup": us}
    for _ in range(rng.randint(8, 13)):
        kind, value = rng.choice(KINDS), rng.choice([5, 10, 15, 20, 25])
        target = rng.choice(list(spec.cats)) if kind == "cat_pct" else rng.choice(list(spec.tier_pcts)) if kind == "tier_pct" else None
        code = {"pct": f"SAVE{value}", "flat": f"OFF{value * 20}", "cat_pct": f"{(target or '')[:4].upper()}{value}", "tier_pct": f"{(target or '').upper()}{value}", "freeship": "FREESHIP"}[kind]
        spec.coupons.setdefault(code, (kind, value * 20 if kind == "flat" else value, rng.choice([0, 2000, 5000]), target))
    return spec


# ---------------------------------------------------------------- heavy rendering
def _heavy_items(spec: Spec) -> list[str]:
    """The per-item loop: one long if/elif chain over categories, tiers nested inside each branch."""
    out = ["    for item in items:", '        cat = item.get("cat")', '        qty = item.get("qty", 1)']
    for index, (cat, info) in enumerate(spec.cats.items()):
        out += [f'        {"if" if index == 0 else "elif"} cat == "{cat}":', "            if qty <= 0:", f'                result["errors"].append("bad_qty:{cat}")', "            else:",
                f'                unit = {info["price"]}', "                pct = 0"]
        for tier_index, (min_qty, pct) in enumerate(info["tiers"]):
            out += [f'                {"if" if tier_index == 0 else "elif"} qty >= {min_qty}:', f"                    pct = {pct}"]
        out += ["                net = unit * qty - unit * qty * pct // 100", f'                cat_totals["{cat}"] = cat_totals.get("{cat}", 0) + net', "                subtotal += net",
                f'                weight += {info["weight"]} * qty', f'                lines.append({{"cat": "{cat}", "qty": qty, "net": net}})']
    return out + ["        else:", '            result["errors"].append("unknown_cat:" + str(cat))']


def _heavy_coupon(code: str, kind: str, value: int, minimum: int, target) -> list[str]:
    """One coupon branch as a nested if/else."""
    err = lambda text: [f'                result["errors"].append("{text}")']  # noqa: E731
    bodies = {
        "pct": [f"            if subtotal >= {minimum}:", f"                discount += subtotal * {value} // 100", "            else:", *err("coupon_min")],
        "flat": [f"            if subtotal >= {minimum}:", f"                discount += {value}", "            else:", *err("coupon_min")],
        "cat_pct": [f'            if cat_totals.get("{target}", 0) > 0:', f'                discount += cat_totals["{target}"] * {value} // 100', "            else:", *err("coupon_nocat")],
        "tier_pct": [f'            if tier == "{target}":', f"                discount += subtotal * {value} // 100", "            else:", *err("coupon_tier")],
        "freeship": ["            freeship = True"],
    }
    return [f'        elif coupon == "{code}":', *bodies[kind]]


def _heavy_tax(spec: Spec) -> list[str]:
    """Per-line tax: a chain over regions, each with its own category exceptions."""
    out = ["    for line in lines:", '        share = discount * line["net"] // subtotal', '        taxable = line["net"] - share', '        cat = line["cat"]']
    for index, (region, info) in enumerate(spec.regions.items()):
        out.append(f'        {"if" if index == 0 else "elif"} region == "{region}":')
        for exc_index, (cat, bp) in enumerate(info["exceptions"].items()):
            out += [f'            {"if" if exc_index == 0 else "elif"} cat == "{cat}":', f"                bp = {bp}"]
        out += ["            else:", f"                bp = {info['bp']}"] if info["exceptions"] else [f"            bp = {info['bp']}"]
    return out + ["        else:", '            result["errors"].append("bad_region")', "            return result", "        tax += taxable * bp // 10000"]


def render_heavy(spec: Spec) -> str:
    """The monolithic rendering: one price() function with everything inline."""
    top = [f'"""{spec.name} pricing (generated, monolithic)."""', "", "", "def price(order, customer):",
           '    result = {"lines": [], "subtotal": 0, "discount": 0, "tax": 0, "shipping": 0, "total": 0, "errors": []}',
           '    items = order.get("items") or []', "    if not items:", '        result["errors"].append("empty_order")', "        return result",
           f'    region = order.get("region", "{next(iter(spec.regions))}")', '    method = order.get("shipping", "standard")', '    coupon = (order.get("coupon") or "").upper()',
           "    subtotal = 0", "    weight = 0", "    cat_totals = {}", "    lines = []",
           '    tier = customer.get("tier", "none")', '    orders_placed = customer.get("orders", 0)']
    coupon_chain = [f'    if coupon:', "        if False:", "            pass"]
    for code, (kind, value, minimum, target) in spec.coupons.items():
        coupon_chain += _heavy_coupon(code, kind, value, minimum, target)
    coupon_chain += ["        else:", '            result["errors"].append("coupon_unknown")']
    tier_chain = ["    tier_pct = 0"]
    for index, (tier, pct) in enumerate(spec.tier_pcts.items()):
        tier_chain += [f'    {"if" if index == 0 else "elif"} tier == "{tier}":', f"        tier_pct = {pct}"]
    surcharges = [(r, i["surcharge"]) for r, i in spec.regions.items() if i["surcharge"]]
    pickups = [r for r, i in spec.regions.items() if i["pickup"]]
    ship = ["    if weight <= 500:", "        base = 500", "    elif weight <= 2000:", "        base = 900", "    elif weight <= 5000:", "        base = 1500", "    else:", "        base = 2500"]
    for index, (region, extra) in enumerate(surcharges):
        ship += [f'    {"if" if index == 0 else "elif"} region == "{region}":', f"        base += {extra}"]
    ship += ['    if method == "standard":', f'        if subtotal - discount >= {spec.free_over} or freeship or tier == "{list(spec.tier_pcts)[-1]}":', "            shipping = 0", "        else:", "            shipping = base",
             '    elif method == "express":', f"        shipping = base * {spec.express_mult} + {spec.express_flat}", '    elif method == "pickup":', f"        if region in {tuple(pickups)!r}:", "            shipping = 0", "        else:",
             '            result["errors"].append("pickup_unavailable")', "            return result", "    else:", '        result["errors"].append("bad_shipping")', "        return result",
             '    if order.get("gift"):', "        shipping += 300"]
    body = [*top, *_heavy_items(spec), '    if result["errors"]:', "        return result", "    discount = 0", "    freeship = False", *coupon_chain, '    if result["errors"]:', "        return result",
            *tier_chain, "    if tier_pct and orders_placed >= 50:", "        tier_pct += 1", "    discount += subtotal * tier_pct // 100", f"    cap = subtotal * {spec.cap_pct} // 100", "    if discount > cap:", "        discount = cap",
            "    tax = 0", *_heavy_tax(spec), *ship, '    result["lines"] = lines', '    result["subtotal"] = subtotal', '    result["discount"] = discount', '    result["tax"] = tax', '    result["shipping"] = shipping',
            '    result["total"] = subtotal - discount + tax + shipping', "    return result", ""]
    return "\n".join(body)


# ---------------------------------------------------------------- clean rendering
CLEAN_BODY = '''

def _bulk_pct(tiers, qty):
    """Discount percent for the highest bulk tier the quantity reaches."""
    return next((pct for min_qty, pct in tiers if qty >= min_qty), 0)


def _price_lines(items, errors):
    """Priced lines and their per-category totals; problems go to errors."""
    lines, cat_totals = [], {}
    for item in items:
        cat, qty = item.get("cat"), item.get("qty", 1)
        if cat not in CATEGORIES:
            errors.append("unknown_cat:" + str(cat))
        elif qty <= 0:
            errors.append("bad_qty:" + cat)
        else:
            info = CATEGORIES[cat]
            gross = info["price"] * qty
            net = gross - gross * _bulk_pct(info["tiers"], qty) // 100
            cat_totals[cat] = cat_totals.get(cat, 0) + net
            lines.append({"cat": cat, "qty": qty, "net": net})
    return lines, cat_totals


def _coupon_discount(code, subtotal, cat_totals, tier, errors):
    """Discount from a coupon code (0 and an error entry when it does not apply)."""
    if code not in COUPONS:
        errors.append("coupon_unknown")
        return 0
    kind, value, minimum, target = COUPONS[code]
    applicable = {"pct": subtotal >= minimum, "flat": subtotal >= minimum, "cat_pct": cat_totals.get(target, 0) > 0, "tier_pct": tier == target, "freeship": True}[kind]
    if not applicable:
        errors.append(COUPON_ERRORS[kind])
        return 0
    return {"pct": subtotal * value // 100, "flat": value, "cat_pct": cat_totals.get(target, 0) * value // 100, "tier_pct": subtotal * value // 100, "freeship": 0}[kind]


def _tier_discount(subtotal, tier, orders_placed):
    """Loyalty discount, with a bonus point for repeat customers."""
    pct = TIER_PCTS.get(tier, 0)
    return subtotal * (pct + (1 if pct and orders_placed >= 50 else 0)) // 100


def _tax(lines, discount, subtotal, region):
    """Total tax over the lines after each line's share of the discount."""
    info = REGIONS[region]
    return sum((line["net"] - discount * line["net"] // subtotal) * info["exceptions"].get(line["cat"], info["bp"]) // 10000 for line in lines)


def _method_cost(method, region, base, free, errors):
    """Cost of a shipping method, or None (with an error entry) when it cannot be used."""
    if method == "standard":
        return 0 if free else base
    if method == "express":
        return base * EXPRESS_MULT + EXPRESS_FLAT
    if method == "pickup" and REGIONS[region]["pickup"]:
        return 0
    errors.append("pickup_unavailable" if method == "pickup" else "bad_shipping")
    return None


def _shipping(weight, region, method, free, gift, errors):
    """Shipping charge: the weight band plus regional surcharge, by method, plus gift wrap."""
    base = next(price for limit, price in BANDS if limit is None or weight <= limit) + REGIONS[region]["surcharge"]
    cost = _method_cost(method, region, base, free, errors)
    return None if cost is None else cost + (300 if gift else 0)


def _discount(subtotal, cat_totals, code, customer, errors):
    """Coupon plus loyalty discount, capped at CAP_PCT of the subtotal."""
    coupon = _coupon_discount(code, subtotal, cat_totals, customer.get("tier", "none"), errors) if code else 0
    loyalty = _tier_discount(subtotal, customer.get("tier", "none"), customer.get("orders", 0))
    return min(coupon + loyalty, subtotal * CAP_PCT // 100)


def _is_free_shipping(subtotal, discount, code, tier):
    """Whether standard shipping is free: big order, free-shipping coupon or top tier."""
    return subtotal - discount >= FREE_OVER or COUPONS.get(code, ("",))[0] == "freeship" or tier == TOP_TIER


def _charges(lines, order, customer, subtotal, discount, errors):
    """(tax, shipping) for the order, or None when shipping cannot be used."""
    region, code = order.get("region", DEFAULT_REGION), _code(order)
    weight = sum(CATEGORIES[line["cat"]]["weight"] * line["qty"] for line in lines)
    free = _is_free_shipping(subtotal, discount, code, customer.get("tier", "none"))
    shipping = _shipping(weight, region, order.get("shipping", "standard"), free, order.get("gift"), errors)
    return None if shipping is None else (_tax(lines, discount, subtotal, region), shipping)


def _code(order):
    """The order's coupon code, upper-cased ('' when none)."""
    return (order.get("coupon") or "").upper()


def _check_region(order, errors):
    """Record bad_region when nothing else has gone wrong and the region is unknown."""
    if not errors and order.get("region", DEFAULT_REGION) not in REGIONS:
        errors.append("bad_region")


def _finish(result, order, customer, lines, cat_totals):
    """Fill in discount, tax, shipping and total on a result whose lines priced cleanly."""
    errors = result["errors"]
    subtotal = sum(line["net"] for line in lines)
    discount = _discount(subtotal, cat_totals, _code(order), customer, errors)
    _check_region(order, errors)
    charges = None if errors else _charges(lines, order, customer, subtotal, discount, errors)
    if charges:
        result.update(lines=lines, subtotal=subtotal, discount=discount, tax=charges[0], shipping=charges[1], total=subtotal - discount + sum(charges))


def price(order, customer):
    """Price an order: lines, coupon and loyalty discounts, tax and shipping."""
    result = {"lines": [], "subtotal": 0, "discount": 0, "tax": 0, "shipping": 0, "total": 0, "errors": []}
    items = order.get("items") or []
    lines, cat_totals = _price_lines(items, result["errors"]) if items else ([], {})
    result["errors"] += [] if items else ["empty_order"]
    if not result["errors"]:
        _finish(result, order, customer, lines, cat_totals)
    return result
'''


def render_clean(spec: Spec) -> str:
    """The decomposed rendering: rule tables as data plus small single-purpose functions."""
    head = [f'"""{spec.name} pricing (generated, decomposed)."""', "", "CATEGORIES = {"]
    head += [f'    "{c}": {{"price": {i["price"]}, "weight": {i["weight"]}, "tiers": {i["tiers"]!r}}},' for c, i in spec.cats.items()] + ["}", "", "COUPONS = {"]
    head += [f'    "{code}": {rule!r},' for code, rule in spec.coupons.items()] + ["}", "", f"TIER_PCTS = {spec.tier_pcts!r}", "", "REGIONS = {"]
    head += [f'    "{r}": {i!r},' for r, i in spec.regions.items()] + ["}", ""]
    head += [f"BANDS = {BANDS!r}", f"CAP_PCT = {spec.cap_pct}", f"FREE_OVER = {spec.free_over}", f"EXPRESS_MULT = {spec.express_mult}", f"EXPRESS_FLAT = {spec.express_flat}",
             f'DEFAULT_REGION = "{next(iter(spec.regions))}"', f'TOP_TIER = "{list(spec.tier_pcts)[-1]}"',
             'COUPON_ERRORS = {"pct": "coupon_min", "flat": "coupon_min", "cat_pct": "coupon_nocat", "tier_pct": "coupon_tier", "freeship": "coupon_unknown"}']
    return "\n".join(head) + CLEAN_BODY


# ---------------------------------------------------------------- cases, verification, writing
def random_cases(spec: Spec, rng: random.Random, count: int) -> list[dict]:
    """Seeded orders that hit normal paths and the error paths."""
    cats, regions, codes = list(spec.cats), list(spec.regions), list(spec.coupons)
    cases = []
    for _ in range(count):
        items = [{"cat": rng.choice([*cats, *cats, "unknown"] if rng.random() < 0.05 else cats), "qty": rng.choice([0, 1, 1, 2, 3, 5, 6, 10, 12, 25])} for _ in range(rng.randint(1, 4))]
        order = {"items": items, "region": rng.choice([*regions, "MARS"] if rng.random() < 0.05 else regions), "shipping": rng.choice(["standard", "standard", "express", "pickup", "drone"]),
                 "coupon": rng.choice([None, None, *codes, rng.choice(codes).lower(), "NOPE"]), "gift": rng.random() < 0.2}
        cases.append({"order": order, "customer": {"tier": rng.choice(["none", "none", *spec.tier_pcts]), "orders": rng.choice([0, 3, 49, 50, 80])}})
    return cases


def _load(source: str):
    """Execute a rendered module and return its price function."""
    scope: dict = {}
    exec(compile(source, "<module>", "exec"), scope)  # noqa: S102 - our own generated code
    return scope["price"]


def build(count: int, heavy_fraction: float, seed: int) -> tuple[dict, dict, dict]:
    """(heavy sources, clean sources, golden cases) per module; raises if the renderings ever disagree."""
    rng = random.Random(seed)
    names = [w if i < len(WORDS) else f"{w}_v2" for i, w in enumerate((WORDS * 3)[:count])]
    heavy, clean, golden = {}, {}, {}
    for name in names:
        spec = make_spec(rng, name)
        heavy[name], clean[name] = render_heavy(spec), render_clean(spec)
        cases = random_cases(spec, rng, 60)
        fast, slow = _load(heavy[name]), _load(clean[name])
        for case in cases:
            case["expected"] = fast(case["order"], case["customer"])
            assert slow(case["order"], case["customer"]) == case["expected"], (name, case)
        golden[name] = cases
    return heavy, clean, golden


def write_repo(root: Path, sources: dict, golden: dict) -> None:
    """A repo: app/<module>.py, one golden-backed test per module, and a registry."""
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "golden").mkdir(parents=True, exist_ok=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "registry.py").write_text("from importlib import import_module\n\nMODULES = " + repr(sorted(sources)) + "\n\n\ndef get(name):\n    return import_module(f\"app.{name}\").price\n", encoding="utf-8")
    for name, text in sources.items():
        (root / "app" / f"{name}.py").write_text(text, encoding="utf-8")
        (root / "tests" / "golden" / f"{name}.json").write_text(json.dumps(golden[name]), encoding="utf-8")
        (root / "tests" / f"test_{name}.py").write_text(
            f'import json\nfrom pathlib import Path\n\nimport pytest\n\nfrom app.{name} import price\n\nCASES = json.loads((Path(__file__).parent / "golden" / "{name}.json").read_text(encoding="utf-8"))\n\n\n'
            '@pytest.mark.parametrize("case", CASES)\ndef test_matches_recorded_behavior(case):\n    assert price(case["order"], case["customer"]) == case["expected"]\n', encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Generate the before/after repos and print their sizes."""
    parser = argparse.ArgumentParser(description="Synthetic startup repo, heavy and clean renderings.")
    parser.add_argument("out", type=Path)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="team3")
    parser.add_argument("--modules", type=int, help="override the profile's module count")
    parser.add_argument("--heavy-fraction", type=float, default=0.4, help="share of modules heavy in the 'before' repo")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    heavy, clean, golden = build(args.modules or PROFILES[args.profile], args.heavy_fraction, args.seed)
    names = sorted(heavy)
    heavy_names = set(random.Random(args.seed).sample(names, round(len(names) * args.heavy_fraction)))
    before = {n: heavy[n] if n in heavy_names else clean[n] for n in names}
    write_repo(args.out / "before", before, golden)
    write_repo(args.out / "after", clean, golden)
    (args.out / "heavy_modules.json").write_text(json.dumps(sorted(heavy_names)), encoding="utf-8")
    lines = lambda sources: sum(text.count("\n") for text in sources.values())  # noqa: E731
    print(f"{len(names)} modules; heavy-fraction {args.heavy_fraction}: {len(heavy_names)} heavy")
    print(f"lines: before {lines(before):,}  after {lines(clean):,}  (all-heavy would be {lines(heavy):,})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
