from functools import partial

from app.scrapers import (
    asil,
    colakoglu as colakoglu_modul,
    diler,
    erdemir,
    hascelik,
    kroman,
    kardemir,
)
from app.scrapers.generic import cek_url


EKINCILER_URL = "https://www.hammaddepiyasasi.com/fabrika/ekinciler"
CANSAN_URL = "https://www.hammaddepiyasasi.com/fabrika/cansan"


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
    ("colakoglu", colakoglu_modul.cek),
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