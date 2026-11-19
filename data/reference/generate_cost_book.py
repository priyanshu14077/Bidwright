"""Generate the starter construction cost book, data/reference/cost-book-v0.json.

The figures are SYNTHETIC. A handful are published market averages (the same
benchmarks the sample archive was priced from); every other city is a stated
factor of one of those, or an invented average for a country with no benchmark.
The output is the source of truth that every new workspace is seeded with, and
the fee engine reads only the seeded rows, never this script.

Each row says how it was made:
- benchmark  a published figure
- derived    a benchmark market's average x the typology cost ratio (starter pack)
- proxy      another market's figures, used as they are (how the archive priced the city)
- synthetic  invented, or a stated factor of a benchmark

    python3 data/reference/generate_cost_book.py
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
STARTER = HERE / "v0.json"
OUT = HERE / "cost-book-v0.json"
BASE_YEAR = 2025
AED, SAR = 3.6725, 3.75

TT_UAE = "Turner & Townsend, UAE Market Intelligence 2025"
TT_KSA = "Turner & Townsend, KSA Market Intelligence 2025"
TT_ME = "Turner & Townsend, Middle East construction cost 2025"
TT_ASIA = "Turner & Townsend, Asia construction cost 2025"
TT_CN = "Turner & Townsend, China construction cost 2025"
WPR = "WPR 2026, citing Turner & Townsend"

# Published averages (USD/m², 2025) and per-type figures where the source gives them.
BENCHMARKS = {
    "Dubai": dict(average=1926, source=TT_UAE, direct={
        "residential_highrise": (5500 / AED, f"{TT_UAE}: AED 5,500/m² at {AED}"),
        "office": (6200 / AED, f"{TT_UAE}: AED 6,200/m² at {AED}"),
        "hospitality_5star": (10500 / AED, f"{TT_UAE}: AED 10,500/m² at {AED}"),
        "hospitality_3star": (6500 / AED, f"{TT_UAE}: AED 6,500/m² at {AED}"),
    }),
    "Abu Dhabi": dict(average=1872, source=TT_UAE),
    "Riyadh": dict(average=3112, source=TT_ME, direct={
        "residential_highrise": (8500 / SAR, f"{TT_KSA}: SAR 8,500/m² at {SAR}"),
        "office": (9000 / SAR, f"{TT_KSA}: SAR 9,000/m² at {SAR}"),
        "hospitality_5star": (22500 / SAR, f"{TT_KSA}: SAR 22,500/m² at {SAR}"),
    }),
    "Singapore": dict(average=3104, source=TT_ASIA),
    "Ho Chi Minh City": dict(average=1168, source=TT_ASIA),
    "Hanoi": dict(average=1147, source=TT_ASIA),
    "Mumbai": dict(average=723, source=TT_ASIA),
    "China": dict(average=940, source=f"{TT_CN} (national average)"),
    "Madrid": dict(average=2206, source=WPR),
}

# Every starter-pack city, plus one country-wide row per country (city "").
# ("benchmark", name) | ("proxy", benchmark, why) | ("factor", benchmark, factor) | ("average", usd_m2)
PLAN = {
    "AE": {"Dubai": ("benchmark", "Dubai"), "Abu Dhabi": ("benchmark", "Abu Dhabi"),
           "Sharjah": ("factor", "Dubai", 0.88),
           "Ras Al Khaimah": ("proxy", "Abu Dhabi", "the archive priced northern-emirates work on Abu Dhabi"),
           "Al Ain": ("proxy", "Abu Dhabi", "the archive priced Al Ain on Abu Dhabi"),
           "": ("proxy", "Abu Dhabi", "country-wide fallback")},
    "SA": {"Riyadh": ("benchmark", "Riyadh"), "Jeddah": ("factor", "Riyadh", 0.95),
           "Dammam": ("factor", "Riyadh", 0.90), "": ("proxy", "Riyadh", "country-wide fallback")},
    "QA": {"Doha": ("average", 2080), "": ("average", 2080)},
    "KW": {"Kuwait City": ("average", 1830), "": ("average", 1830)},
    "BH": {"Manama": ("average", 1640), "": ("average", 1640)},
    "OM": {"Muscat": ("average", 1580), "": ("average", 1580)},
    "EG": {"Cairo": ("average", 540), "": ("average", 540)},
    "JO": {"Amman": ("average", 860), "": ("average", 860)},
    "IN": {"Mumbai": ("benchmark", "Mumbai"), "New Delhi": ("factor", "Mumbai", 0.97),
           "Gurugram": ("factor", "Mumbai", 0.99), "Bengaluru": ("factor", "Mumbai", 0.95),
           "Pune": ("factor", "Mumbai", 0.85), "Hyderabad": ("factor", "Mumbai", 0.87),
           "": ("proxy", "Mumbai", "country-wide fallback")},
    "SG": {"Singapore": ("benchmark", "Singapore"), "": ("proxy", "Singapore", "country-wide fallback")},
    "VN": {"Ho Chi Minh City": ("benchmark", "Ho Chi Minh City"), "Hanoi": ("benchmark", "Hanoi"),
           "Da Nang": ("proxy", "Hanoi", "the archive priced Da Nang on Hanoi"),
           "": ("proxy", "Hanoi", "country-wide fallback")},
    "CN": {"Shanghai": ("factor", "China", 1.15), "Shenzhen": ("factor", "China", 1.08),
           "Guangzhou": ("proxy", "China", "the archive priced Guangzhou on the national average; "
                                           "WPR 2026 quotes USD 910/m² for the city"),
           "Foshan": ("proxy", "China", "the archive priced Foshan on the national average"),
           "": ("benchmark", "China")},
    "AU": {"Sydney": ("average", 3350), "Melbourne": ("average", 3180), "Brisbane": ("average", 2950),
           "Perth": ("average", 3020), "": ("average", 3150)},
    "ES": {"Madrid": ("benchmark", "Madrid"),
           "Barcelona": ("proxy", "Madrid", "the archive priced Barcelona on Madrid (WPR 2026 calls it the proxy)"),
           "Malaga": ("proxy", "Madrid", "the archive priced Malaga on Madrid"),
           "": ("proxy", "Madrid", "country-wide fallback")},
}

# Per country: cost band (landscape and render rates), the fee % factor, and cost escalation.
ARCHIVE = "calibrated on the sample archive"
TT_GLOBAL = "Turner & Townsend global forecast 2025"
MARKETS = {
    "AE": ("high", 1.0, ARCHIVE, 5.0, "Turner & Townsend UAE 2025"),
    "SA": ("high", 1.0, ARCHIVE, 3.8, TT_GLOBAL),
    "QA": ("high", 1.0, "synthetic", 3.5, "synthetic"),
    "KW": ("high", 1.0, "synthetic", 3.0, "synthetic"),
    "BH": ("high", 1.05, "synthetic", 3.0, "synthetic"),
    "OM": ("high", 1.05, "synthetic", 3.0, "synthetic"),
    "EG": ("low", 1.6, "synthetic", 6.0, "synthetic"),
    "JO": ("low", 1.4, "synthetic", 3.0, "synthetic"),
    "IN": ("low", 1.55, ARCHIVE, 3.8, TT_GLOBAL),
    "SG": ("high", 1.0, ARCHIVE, 3.8, TT_GLOBAL),
    "VN": ("low", 1.5, ARCHIVE, 3.8, TT_GLOBAL),
    "CN": ("low", 1.45, ARCHIVE, 1.0, "Turner & Townsend China 2025"),
    "AU": ("high", 1.15, "synthetic", 4.5, "synthetic"),
    "ES": ("high", 1.1, ARCHIVE, 3.8, TT_GLOBAL),
}


def market_rows(name: str, ratios: dict[str, float]) -> dict[str, tuple[float, str]]:
    """usd_m2 and source per cost type for a benchmark market. Derived rows keep full precision."""
    b = BENCHMARKS[name]
    out = {"average": (b["average"], b["source"])}
    for t, ratio in ratios.items():
        if t in b.get("direct", {}):
            usd, src = b["direct"][t]
            out[t] = (round(usd, 1), src)
        else:
            out[t] = (round(b["average"] * ratio, 3), f"{name} average x {t} ratio {ratio}")
    return out


def build() -> dict:
    starter = json.loads(STARTER.read_text())
    ratios = starter["typology_cost_ratios"]
    cities = {(c, city) for c, city, _, _ in starter["locations"]}
    planned = {(c, city) for c, plan in PLAN.items() for city in plan if city}
    assert cities == planned, f"cities missing from the plan: {cities - planned}; extra: {planned - cities}"
    assert set(PLAN) == {c[0] for c in starter["countries"]} == set(MARKETS)

    costs = []
    for country, plan in PLAN.items():
        for city, how in plan.items():
            kind = how[0]
            if kind in ("benchmark", "proxy"):
                rows = market_rows(how[1], ratios)
                basis = kind
                note = None if kind == "benchmark" else f"{how[1]} figures: {how[2]}"
            elif kind == "factor":
                factor = how[2]
                rows = {t: (round(usd * factor, 1), f"{factor} x {how[1]} ({src})")
                        for t, (usd, src) in market_rows(how[1], ratios).items()}
                rows["average"] = (round(BENCHMARKS[how[1]]["average"] * factor), rows["average"][1])
                basis, note = "synthetic", None
            else:
                avg = how[1]
                rows = {"average": (avg, "synthetic average")}
                rows |= {t: (round(avg * r, 1), f"synthetic average x {t} ratio {r}") for t, r in ratios.items()}
                basis, note = "synthetic", None
            for cost_type, (usd, source) in rows.items():
                published = cost_type == "average" or cost_type in BENCHMARKS.get(how[1], {}).get("direct", {})
                row_basis = "derived" if basis == "benchmark" and not published else basis
                costs.append(dict(country=country, city=city, cost_type=cost_type, usd_m2=usd, basis=row_basis,
                                  source=source, note=note))
    return {
        "version": "v0",
        "note": "SYNTHETIC construction cost book for the starter pack. Building cost per m² of GFA in USD at "
                f"{BASE_YEAR} prices; the fee engine escalates it to the proposal year with each market's rate. "
                "Generated by generate_cost_book.py; edit the script, not this file.",
        "base_year": BASE_YEAR,
        "markets": [dict(country=c, band=band, fee_factor=ff, fee_factor_source=ffs, escalation_pct=esc,
                         escalation_source=escs) for c, (band, ff, ffs, esc, escs) in MARKETS.items()],
        "costs": costs,
    }


if __name__ == "__main__":
    book = build()
    OUT.write_text(json.dumps(book, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT.name}: {len(book['markets'])} markets, {len(book['costs'])} cost rows")
