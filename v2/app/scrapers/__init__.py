from app.scrapers import (
    asil,
    diler,
    erdemir,
    hascelik,
    kroman,
    kardemir,
)

TUMU = [
    ("erdemir", erdemir.erdemir),
    ("isdemir", erdemir.isdemir),
    ("asil", asil.cek),
    ("kroman", kroman.cek),
    ("hascelik", hascelik.cek),
    ("kardemir", kardemir.cek),
    ("diler", diler.cek),
]