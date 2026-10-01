from app.scrapers import (
    asil,
    colakoglu,
    diler,
    erdemir,
    hascelik,
    kroman,
    kardemir,
)

TUMU = [
    ("colakoglu", colakoglu.cek),
    ("erdemir", erdemir.erdemir),
    ("isdemir", erdemir.isdemir),
    ("asil", asil.cek),
    ("kroman", kroman.cek),
    ("hascelik", hascelik.cek),
    ("kardemir", kardemir.cek),
    ("diler", diler.cek),
]