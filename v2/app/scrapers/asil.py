import requests
from bs4 import BeautifulSoup

from app.models import FirmaSonuc, Kalem
from app.scrapers.base import ScraperHatasi, fiyat_sayi, http_get, tarih_bul

ID = "asil"
BASLIK = "Asil Çelik"
URL = "https://asilcelik.com.tr/tedarikci-iliskileri"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "tr-TR,tr;q=0.9",
}


def cek() -> FirmaSonuc:
    try:
        cevap = http_get(URL, robots=False, headers=HEADERS)
    except requests.RequestException as e:
        raise ScraperHatasi(
            f"Asil Çelik: bağlantı hatası: {e}"
        ) from e

    soup = BeautifulSoup(
        cevap.text,
        "html.parser",
    )

    kalemler = []
    tarih = None

    for tablo in soup.find_all("table"):
        for satir in tablo.find_all("tr"):
            h = [
                c.get_text(strip=True)
                for c in satir.find_all("td")
            ]

            if len(h) < 2:
                continue

            fiyat = fiyat_sayi(h[1])

            if fiyat is None:
                continue

            kalemler.append(
                Kalem(
                    cins=h[0],
                    fiyat=fiyat,
                )
            )

            if tarih is None and len(h) > 2:
                tarih = tarih_bul(h[2])

    if not kalemler:
        raise ScraperHatasi(
            "Asil Çelik: fiyat tablosu bulunamadı"
        )

    return FirmaSonuc(
        ID,
        BASLIK,
        URL,
        tarih,
        kalemler,
    )