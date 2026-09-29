import re

from bs4 import BeautifulSoup

from app.models import FirmaSonuc, Kalem
from app.scrapers.base import ScraperHatasi, fiyat_sayi, http_get, tarih_bul

ID = "kroman"
BASLIK = "Kroman Çelik"
URL = "https://www.kromancelik.com.tr/hurda-tedarik.php"

_FIYAT = re.compile(r"\d[\d.]*\s*TL", re.IGNORECASE)


def cek() -> FirmaSonuc:
    soup = BeautifulSoup(http_get(URL).text, "html.parser")

    bolum = None
    for tablo in soup.find_all("table"):
        if _FIYAT.search(tablo.get_text(" ")):
            bolum = tablo
            break
    if bolum is None:
        raise ScraperHatasi("Kroman: fiyat tablosu bulunamadı (sayfa yapısı değişmiş olabilir)")

    tarih = tarih_bul(soup.get_text(" "))
    kalemler, isimler = [], None
    for tr in bolum.find_all("tr"):
        hucreler = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if not hucreler:
            continue
        if all(_FIYAT.search(h) for h in hucreler):      # fiyat satırı
            if isimler and len(isimler) == len(hucreler):
                for ad, f in zip(isimler, hucreler):
                    fiyat = fiyat_sayi(f)
                    if fiyat is not None:
                        kalemler.append(Kalem(cins=ad.replace("_", " "), fiyat=fiyat))
            isimler = None
        else:                                            # isim satırı
            isimler = hucreler

    if not kalemler:
        raise ScraperHatasi("Kroman: fiyat çifti bulunamadı")
    return FirmaSonuc(ID, BASLIK, URL, tarih, kalemler)