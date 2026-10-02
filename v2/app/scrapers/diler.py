import re
from datetime import date

from bs4 import BeautifulSoup

from app.models import FirmaSonuc, Kalem
from app.scrapers.base import ScraperHatasi, http_get


ID = "diler"
BASLIK = "Diler Demir Çelik"
URL = "https://www.hammaddepiyasasi.com/fabrika/diler"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "tr-TR,tr;q=0.9",
}


def _tarih_bul(metin: str):
    m = re.search(
        r"Son güncelleme:\s*(\d{1,2})\s+"
        r"(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|"
        r"Eylül|Ekim|Kasım|Aralık)\s+(\d{4})",
        metin,
        re.IGNORECASE,
    )

    if not m:
        return None

    aylar = {
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

    gun = int(m.group(1))
    ay = aylar.get(m.group(2).lower())
    yil = int(m.group(3))

    if not ay:
        return None

    try:
        return date(yil, ay, gun)
    except ValueError:
        return None


def _fiyat_bul(metin: str, kalite: str):
    kalip = rf"{re.escape(kalite)}\s+.*?(\d[\d.]*)\s*₺/ton"
    m = re.search(kalip, metin, re.IGNORECASE)

    if not m:
        return None

    return int(m.group(1).replace(".", ""))


def cek() -> FirmaSonuc:
    import requests

    try:
        cevap = http_get(URL, robots=False, headers=HEADERS)
    except requests.RequestException as e:
        raise ScraperHatasi(
            f"Diler: bağlantı hatası: {e}"
        ) from e

    soup = BeautifulSoup(cevap.text, "html.parser")
    metin = soup.get_text(" ", strip=True)

    tarih = _tarih_bul(metin)

    kalemler = []

    kalite_eslesmeleri = [
        ("DKP", "DKP"),
        ("Ekstra", "Ekstra"),
        ("1. Kalite", "1. Kalite"),
        ("2. Kalite", "2. Kalite"),
        ("3. Kalite", "3. Kalite"),
    ]

    for aranan, cins in kalite_eslesmeleri:
        fiyat = _fiyat_bul(metin, aranan)

        if fiyat is None:
            continue

        kalemler.append(
            Kalem(
                cins=cins,
                fiyat=fiyat,
            )
        )

    if not kalemler:
        raise ScraperHatasi(
            "Diler: fiyat bilgileri bulunamadı "
            "(sayfa yapısı değişmiş olabilir)"
        )

    return FirmaSonuc(
        ID,
        BASLIK,
        URL,
        tarih,
        kalemler,
    )
