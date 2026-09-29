from bs4 import BeautifulSoup

from app.models import FirmaSonuc, Kalem
from app.scrapers.base import ScraperHatasi, fiyat_sayi, http_get, tarih_bul


def _cek(firma_id: str, baslik: str, url: str) -> FirmaSonuc:
    soup = BeautifulSoup(http_get(url).text, "html.parser")
    kalemler = []
    for tablo in soup.find_all("table"):
        satirlar = tablo.find_all("tr")
        if not satirlar:
            continue
        basliklar = [c.get_text(strip=True).lower() for c in satirlar[0].find_all(["th", "td"])]
        i_yeni = next((i for i, b in enumerate(basliklar) if "yeni" in b), None)
        if i_yeni is None:
            continue
        i_eski = next((i for i, b in enumerate(basliklar) if "eski" in b), None)
        for satir in satirlar[1:]:
            h = [c.get_text(strip=True) for c in satir.find_all("td")]
            if len(h) <= i_yeni:
                continue
            fiyat = fiyat_sayi(h[i_yeni])
            if fiyat is None:
                continue
            eski = fiyat_sayi(h[i_eski]) if i_eski is not None and len(h) > i_eski else None
            kalemler.append(Kalem(cins=h[0], fiyat=fiyat, eski_fiyat=eski))
    if not kalemler:
        raise ScraperHatasi(f"{baslik}: fiyat tablosu bulunamadı (sayfa yapısı değişmiş olabilir)")
    tarih = tarih_bul(soup.get_text(" "), once="Fiyatlar")
    return FirmaSonuc(firma_id, baslik, url, tarih, kalemler)


def erdemir() -> FirmaSonuc:
    return _cek("erdemir", "Erdemir Çelik", "https://www.erdemir.com.tr/tedarikci-iliskileri/hurda-alim")


def isdemir() -> FirmaSonuc:
    return _cek("isdemir", "İsdemir Demir Çelik", "https://www.isdemir.com.tr/tedarikci-iliskileri/hurda-alim")