import re
from datetime import date

from bs4 import BeautifulSoup

from app.models import FirmaSonuc, Kalem
from app.scrapers.base import ScraperHatasi, fiyat_sayi, http_get

ID = "hascelik"
BASLIK = "Hasçelik"
URL = "https://hascelik.com/hurda"


_PARCA = r"(?:\d+\.|[A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜa-zçğıöşü]*)"

_SATIR = re.compile(
    rf"({_PARCA}(?:\s+{_PARCA}){{0,2}})\s*₺\s*([\d.,]+)"
)


_AYLAR = {
    "ocak": 1,
    "şubat": 2,
    "mart": 3,
    "nisan": 4,
    "mayıs": 5,
    "haziran": 6,
    "temmuz": 7,
    "ağustos": 8,
    "eylül": 9,
    "ekim": 10,
    "kasım": 11,
    "aralık": 12,
}


_TARIH = re.compile(
    r"(\d{1,2})\s+([A-Za-zçğıöşüÇĞİÖŞÜ]+)\s+(\d{4})"
)


def _tarih_bul_tr(metin: str):
    m = _TARIH.search(metin)

    if not m:
        return None

    gun, ay_adi, yil = m.groups()

    ay = _AYLAR.get(ay_adi.lower())

    if not ay:
        return None

    try:
        return date(
            int(yil),
            ay,
            int(gun),
        )
    except ValueError:
        return None


def cek() -> FirmaSonuc:
    r = http_get(URL)
    r.encoding = r.apparent_encoding

    soup = BeautifulSoup(
        r.text,
        "html.parser",
    )

    metin = soup.get_text(" ")

    kalemler = []

    for ad, fiyat_metni in _SATIR.findall(metin):
        # Hasçelik sayfasında ilk fiyat şu şekilde geliyor:
        #
        # GEÇERLİLİK TARİHİ DKP -> 19.100,00
        #
        # Burada başlığı silip DKP'yi koruyoruz.
        ad = re.sub(
            r"GEÇERLİLİK\s+TARİHİ",
            "",
            ad,
            flags=re.IGNORECASE,
        )

        ad = " ".join(ad.split()).strip()

        fiyat = fiyat_sayi(fiyat_metni)

        if not ad or fiyat is None:
            continue

        if not any(k.cins == ad for k in kalemler):
            kalemler.append(
                Kalem(
                    cins=ad,
                    fiyat=fiyat,
                )
            )

    if not kalemler:
        raise ScraperHatasi(
            "Hasçelik: fiyat cümlesi bulunamadı "
            "(sayfa yapısı değişmiş olabilir)"
        )

    tarih = _tarih_bul_tr(metin)

    return FirmaSonuc(
        ID,
        BASLIK,
        URL,
        tarih,
        kalemler,
    )