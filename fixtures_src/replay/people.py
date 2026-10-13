"""Deterministic names, invented domains, addresses and phones (replay.md §4).

`small_co`'s cast is written by hand; `big_co` draws from here with its own `rng`. Every draw is
a pure function of the `random.Random` it is handed. Cities are real (city, state, ZIP, area code)
combinations; streets, companies and domains are invented, and phones use the reserved
555-0100 to 555-0199 range.
"""

import random
import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "BUSINESS_PATTERNS",
    "CITIES",
    "FIRST_NAMES",
    "LAST_NAMES",
    "PLACE_WORDS",
    "STREET_NAMES",
    "TRADE_WORDS",
    "City",
    "address",
    "business_name",
    "contact_email",
    "domain_for",
    "phone",
    "pick_city",
]

FIRST_NAMES = (
    "Aaliyah", "Abdul", "Adaeze", "Adrian", "Aiko", "Alejandro", "Alma", "Amara", "Amir", "Ana",
    "Anders", "Anika", "Arjun", "Aroha", "Bao", "Beatriz", "Benedikt", "Bianca", "Bilal", "Bodhi",
    "Brenna", "Caleb", "Camila", "Carmen", "Chidi", "Chloe", "Dalia", "Damon", "Daniela", "Darius",
    "Deepa", "Delia", "Desmond", "Dmitri", "Ebony", "Eduardo", "Eitan", "Elena", "Eliza", "Emeka",
    "Esme", "Esteban", "Farah", "Felipe", "Fiona", "Gabriel", "Gemma", "Grady", "Gwen", "Hamid",
    "Hana", "Hector", "Helga", "Hiroshi", "Ibrahim", "Ines", "Ingrid", "Isaac", "Ivana", "Jalen",
    "Jamal", "Javier", "Jin", "Joaquin", "Jonah", "Joon", "Kalani", "Kamal", "Keanu", "Keisha",
    "Kenji", "Kofi", "Lakshmi", "Lars", "Leilani", "Lena", "Lucia", "Luka", "Mae", "Malik",
    "Marisol", "Marta", "Mateo", "Mei", "Mira", "Moana", "Nadia", "Naveen", "Nia", "Nikolai",
    "Noor", "Obi", "Olga", "Omar", "Owen", "Paloma", "Pedro", "Priya", "Quinn", "Rafael",
    "Rania", "Ravi", "Reza", "Rhea", "Rosa", "Rowan", "Ruth", "Saanvi", "Salma", "Santiago",
    "Selin", "Seth", "Shira", "Silas", "Soren", "Sunita", "Tamar", "Tariq", "Teodora", "Thabo",
    "Tomas", "Uma", "Valeria", "Vikram", "Wade", "Wen", "Xavier", "Yara", "Yusuf", "Zainab",
    "Zane", "Zofia",
)  # fmt: skip

LAST_NAMES = (
    "Abara", "Acosta", "Adeyemi", "Ahn", "Alvarado", "Amundsen", "Aranda", "Bachman", "Baptiste",
    "Barrios", "Bergstrom", "Bhatt", "Blackwood", "Bose", "Brannigan", "Calloway", "Castellano",
    "Chaudhry", "Cho", "Cisneros", "Dahl", "Delacroix", "Desai", "Dorsey", "Duarte", "Eklund",
    "Esposito", "Farouk", "Fenwick", "Figueroa", "Fujimoto", "Galloway", "Garza", "Gill",
    "Guerrero", "Halvorsen", "Haddad", "Hargrove", "Hayashi", "Holloway", "Ibarra", "Iyer",
    "Jankowski", "Jimenez", "Kaur", "Kemper", "Kowalczyk", "Krishnan", "Lachance", "Lindqvist",
    "Lombardi", "Lozano", "Mahmoud", "Malone", "Marchetti", "Mbeki", "McAllister", "Medina",
    "Moreau", "Mwangi", "Nakagawa", "Navarro", "Nguyen", "Novak", "Nwosu", "Odhiambo", "Okafor",
    "Oyelaran", "Pacheco", "Petrov", "Pham", "Quintero", "Rahman", "Ramos", "Rasmussen",
    "Reyes", "Rinaldi", "Rosales", "Sadeghi", "Salazar", "Sandoval", "Schreiber", "Sellers",
    "Shah", "Sorensen", "Stavros", "Suarez", "Takahashi", "Talbot", "Tran", "Underwood",
    "Valdez", "Varga", "Velasquez", "Vo", "Wainwright", "Whitfield", "Wojcik", "Yamada",
    "Yilmaz", "Zamora", "Zielinski",
)  # fmt: skip

TRADE_WORDS = (
    "Plumbing", "Heating & Air", "Electric", "Mechanical", "Roofing", "HVAC", "Comfort Systems",
    "Drain & Sewer", "Air Conditioning", "Home Services", "Garage Doors", "Pest Control",
)  # fmt: skip

PLACE_WORDS = (
    "Summit", "Front Range", "Blue Mesa", "Cedar Creek", "Red Rock", "Prairie", "Lakeshore",
    "Ridgeline", "Iron Valley", "Copper Canyon", "Pine Hollow", "River Bend", "High Plains",
    "Stone Bridge", "Bluebonnet", "Gulf Coast", "Tri-County", "Piedmont", "Timberline", "Sandhill",
)  # fmt: skip

BUSINESS_PATTERNS = (
    "{last} {trade}",
    "{last} & Sons {trade}",
    "{place} {trade}",
    "{place} {trade} Co.",
    "{last} Brothers {trade}",
    "{first} {last} {trade}",
)

STREET_NAMES = (
    "Larkin Ave", "Old Mill Rd", "Harrow St", "Bramble Ln", "Quarry Rd", "Fenmore Dr",
    "Kestrel Way", "Tolliver St", "Ashgrove Ct", "Wickham Blvd", "Corran Ave", "Saddle Ridge Rd",
    "Millbrook Dr", "Penstock Ln", "Yarrow St", "Calder Ave", "Briar Gate Rd", "Halsey Pl",
    "Thornbury Ln", "Gristmill Ct", "Orchard Bend Dr", "Marlow St", "Dunmore Ave", "Coldwater Rd",
)  # fmt: skip

_SUFFIX_WORDS = frozenset({"Co.", "Inc.", "LLC", "Company", "Group"})

_DOMAIN_TLDS = ((".com", 80), (".net", 10), (".co", 5), (".us", 5))


@dataclass(frozen=True)
class City:
    city: str
    state: str
    postal_code: str
    area_code: str


CITIES = tuple(
    City(*row)
    for row in (
        ("Houston", "TX", "77002", "713"), ("Dallas", "TX", "75201", "214"),
        ("Austin", "TX", "78701", "512"), ("San Antonio", "TX", "78205", "210"),
        ("Fort Worth", "TX", "76102", "817"), ("El Paso", "TX", "79901", "915"),
        ("Lubbock", "TX", "79401", "806"), ("Corpus Christi", "TX", "78401", "361"),
        ("Plano", "TX", "75074", "972"), ("Sacramento", "CA", "95814", "916"),
        ("Fresno", "CA", "93721", "559"), ("San Diego", "CA", "92101", "619"),
        ("Riverside", "CA", "92501", "951"), ("Bakersfield", "CA", "93301", "661"),
        ("San Jose", "CA", "95113", "408"), ("Oakland", "CA", "94612", "510"),
        ("Long Beach", "CA", "90802", "562"), ("Anaheim", "CA", "92805", "714"),
        ("Stockton", "CA", "95202", "209"), ("Tampa", "FL", "33602", "813"),
        ("Orlando", "FL", "32801", "407"), ("Jacksonville", "FL", "32202", "904"),
        ("Miami", "FL", "33130", "305"), ("Fort Myers", "FL", "33901", "239"),
        ("Tallahassee", "FL", "32301", "850"), ("Sarasota", "FL", "34236", "941"),
        ("Gainesville", "FL", "32601", "352"), ("Denver", "CO", "80202", "303"),
        ("Colorado Springs", "CO", "80903", "719"), ("Fort Collins", "CO", "80524", "970"),
        ("Boulder", "CO", "80302", "303"), ("Pueblo", "CO", "81003", "719"),
        ("Grand Junction", "CO", "81501", "970"), ("Phoenix", "AZ", "85004", "602"),
        ("Tucson", "AZ", "85701", "520"), ("Mesa", "AZ", "85201", "480"),
        ("Flagstaff", "AZ", "86001", "928"), ("Chandler", "AZ", "85225", "480"),
        ("Atlanta", "GA", "30303", "404"), ("Savannah", "GA", "31401", "912"),
        ("Augusta", "GA", "30901", "706"), ("Macon", "GA", "31201", "478"),
        ("Charlotte", "NC", "28202", "704"), ("Raleigh", "NC", "27601", "919"),
        ("Greensboro", "NC", "27401", "336"), ("Wilmington", "NC", "28401", "910"),
        ("Columbus", "OH", "43215", "614"), ("Cleveland", "OH", "44113", "216"),
        ("Cincinnati", "OH", "45202", "513"), ("Dayton", "OH", "45402", "937"),
        ("Toledo", "OH", "43604", "419"), ("Akron", "OH", "44308", "330"),
        ("Pittsburgh", "PA", "15222", "412"), ("Philadelphia", "PA", "19107", "215"),
        ("Allentown", "PA", "18101", "610"), ("Harrisburg", "PA", "17101", "717"),
        ("Erie", "PA", "16501", "814"), ("Buffalo", "NY", "14202", "716"),
        ("Rochester", "NY", "14604", "585"), ("Albany", "NY", "12207", "518"),
        ("Syracuse", "NY", "13202", "315"), ("Seattle", "WA", "98101", "206"),
        ("Spokane", "WA", "99201", "509"), ("Tacoma", "WA", "98402", "253"),
        ("Chicago", "IL", "60602", "312"), ("Springfield", "IL", "62701", "217"),
        ("Peoria", "IL", "61602", "309"), ("Rockford", "IL", "61101", "815"),
        ("Nashville", "TN", "37203", "615"), ("Memphis", "TN", "38103", "901"),
        ("Knoxville", "TN", "37902", "865"), ("Chattanooga", "TN", "37402", "423"),
        ("Salt Lake City", "UT", "84101", "801"), ("Boise", "ID", "83702", "208"),
        ("Las Vegas", "NV", "89101", "702"), ("Reno", "NV", "89501", "775"),
        ("Oklahoma City", "OK", "73102", "405"), ("Tulsa", "OK", "74103", "918"),
        ("Kansas City", "MO", "64106", "816"), ("St. Louis", "MO", "63101", "314"),
        ("Omaha", "NE", "68102", "402"), ("Albuquerque", "NM", "87102", "505"),
        ("Indianapolis", "IN", "46204", "317"), ("Louisville", "KY", "40202", "502"),
        ("Birmingham", "AL", "35203", "205"), ("Richmond", "VA", "23219", "804"),
        ("Minneapolis", "MN", "55401", "612"), ("Portland", "OR", "97204", "503"),
        ("Charleston", "SC", "29401", "843"), ("Columbia", "SC", "29201", "803"),
        ("Little Rock", "AR", "72201", "501"), ("Des Moines", "IA", "50309", "515"),
        ("Milwaukee", "WI", "53202", "414"), ("Detroit", "MI", "48226", "313"),
        ("Grand Rapids", "MI", "49503", "616"), ("New Orleans", "LA", "70112", "504"),
        ("Baton Rouge", "LA", "70801", "225"),
    )
)  # fmt: skip

# Weight per city by state: the large trades markets draw more often.
_STATE_WEIGHT = {"TX": 5, "CA": 4, "FL": 4, "CO": 5, "AZ": 4, "GA": 3, "NC": 3}


def pick_city(rng: random.Random, state: str | None = None) -> City:
    pool = [c for c in CITIES if state is None or c.state == state]
    if not pool:
        raise ValueError(f"no city in state {state!r}")
    return rng.choices(pool, weights=[_STATE_WEIGHT.get(c.state, 2) for c in pool])[0]


def business_name(rng: random.Random) -> str:
    pattern = rng.choice(BUSINESS_PATTERNS)
    return pattern.format(
        first=rng.choice(FIRST_NAMES),
        last=rng.choice(LAST_NAMES),
        place=rng.choice(PLACE_WORDS),
        trade=rng.choice(TRADE_WORDS),
    )


def domain_for(name: str, rng: random.Random) -> str:
    """The name's slug without a trailing suffix word, on a weighted TLD."""
    words = name.split()
    if len(words) > 1 and words[-1] in _SUFFIX_WORDS:
        words = words[:-1]
    slug = re.sub(r"[^a-z0-9]", "", "".join(words).lower().replace("&", "and"))
    tld = rng.choices([t for t, _ in _DOMAIN_TLDS], weights=[w for _, w in _DOMAIN_TLDS])[0]
    return slug + tld


def contact_email(first: str, last: str, domain: str, rng: random.Random) -> str:
    first_slug = re.sub(r"[^a-z]", "", first.lower())
    last_slug = re.sub(r"[^a-z]", "", last.lower())
    local = rng.choice(("billing", "office", first_slug, f"{first_slug}.{last_slug}"))
    return f"{local}@{domain}"


def address(
    rng: random.Random, state: str | None = None, *, city: City | None = None
) -> dict[str, Any]:
    """An invented street on a real city, state and ZIP."""
    where = city if city is not None else pick_city(rng, state)
    return {
        "line1": f"{rng.randint(100, 9899)} {rng.choice(STREET_NAMES)}",
        "city": where.city,
        "state": where.state,
        "postal_code": where.postal_code,
        "country": "US",
    }


def phone(rng: random.Random, area_code: str) -> str:
    """`+1{area}55501xx`: the reserved 555-0100 to 555-0199 range."""
    return f"+1{area_code}55501{rng.randint(0, 99):02d}"
