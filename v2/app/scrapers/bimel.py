from datetime import datetime

from app.models import FirmaSonuc, Kalem
from app.scrapers.base import ScraperHatasi, http_get

API = "https://www.bimelmetal.com/api_grafik_veri.php?fabrika_id={fid}&yil={yil}"


def cek(fabrika_id: int, firma_id: str, baslik: str, url: str, yil: int = 2026) -> FirmaSonuc:
    veri = http_get(API.format(fid=fabrika_id, yil=yil)).json()
    if not veri.get("success"):
        raise ScraperHatasi(f"{baslik}: veri alınamadı")
    etiketler = {k["key"]: k["label"] for k in veri.get("kaliteler", [])}
    kayitlar = veri.get("data") or []
    if not kayitlar:
        raise ScraperHatasi(f"{baslik}: kayıt yok")
    son = kayitlar[-1]
    try:
        tarih = datetime.strptime(son["tarih"], "%d.%m.%Y").date()
    except (KeyError, ValueError):
        tarih = None
    kalemler = []
    for key, fiyat in son.get("fiyatlar", {}).items():
        cins = etiketler.get(key, key)
        hareket = son.get("hareketler", {}).get(key)
        eski = fiyat - hareket if isinstance(hareket, (int, float)) else None
        kalemler.append(Kalem(cins=cins, fiyat=int(fiyat), eski_fiyat=int(eski) if eski is not None else None))
    if not kalemler:
        raise ScraperHatasi(f"{baslik}: kalem bulunamadı")
    return FirmaSonuc(firma_id, baslik, url, tarih, kalemler)