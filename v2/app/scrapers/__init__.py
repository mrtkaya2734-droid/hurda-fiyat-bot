from functools import partial

from app.scrapers import (
    asil,
    diler,
    erdemir,
    hascelik,
    kroman,
    kardemir,
)
from app.scrapers.generic import cek_url


COLAKOGLU_URL = "https://www.hammaddepiyasasi.com/fabrika/colakoglu"
CANSAN_URL = "https://www.hammaddepiyasasi.com/fabrika/cansan"


colakoglu = partial(
    cek_url,
    "colakoglu",
    "Çolakoğlu Metalurji",
    COLAKOGLU_URL,
)

cansan = partial(
    cek_url,
    "cansan",
    "Cansan Metalurji",
    CANSAN_URL,
)


TUMU = [
    ("colakoglu", colakoglu),
    ("cansan", cansan),
    ("erdemir", erdemir.erdemir),
    ("isdemir", erdemir.isdemir),
    ("asil", asil.cek),
    ("kroman", kroman.cek),
    ("hascelik", hascelik.cek),
    ("kardemir", kardemir.cek),
    ("diler", diler.cek),
]