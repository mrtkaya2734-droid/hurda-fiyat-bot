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
EKINCILER_URL = "https://www.hammaddepiyasasi.com/fabrika/ekinciler"
CANSAN_URL = "https://www.hammaddepiyasasi.com/fabrika/cansan"


colakoglu = partial(
    cek_url,
    "colakoglu",
    "Çolakoğlu Metalurji",
    COLAKOGLU_URL,
)

ekinciler = partial(
    cek_url,
    "ekinciler",
    "Ekinciler Demir Çelik",
    EKINCILER_URL,
)

cansan = partial(
    cek_url,
    "cansan",
    "Cansan",
    CANSAN_URL,
)


TUMU = [
    ("colakoglu", colakoglu),
    ("ekinciler", ekinciler),
    ("cansan", cansan),
    ("erdemir", erdemir.erdemir),
    ("isdemir", erdemir.isdemir),
    ("asil", asil.cek),
    ("kroman", kroman.cek),
    ("hascelik", hascelik.cek),
    ("kardemir", kardemir.cek),
    ("diler", diler.cek),
]