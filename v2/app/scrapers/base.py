import re
import time
from datetime import date
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

USER_AGENT = "HurdaFiyatBot/2.0"
_HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "tr-TR,tr;q=0.9"}
_robots_onbellek = {}


class ScraperHatasi(Exception):
    pass


_ROBOTS_BASARILI_SANIYE = 6 * 3600
_ROBOTS_HATA_SANIYE = 300


def izin_var(url: str) -> bool:
    """Sitenin robots.txt dosyasına bakar.

    - 200: kurallar uygulanır (6 saat önbellek).
    - 4xx: kural yok, serbest.
    - 5xx: temkinli davranılır ama yalnızca 5 dakika hatırlanır.
    - Ağ hatası/zaman aşımı: robots.txt okunamadı demektir; çekim denenir
      ve sonuç önbelleğe alınmaz (geçici bir hata siteyi kalıcı
      engellemesin).
    """
    p = urlparse(url)
    kok = f"{p.scheme}://{p.netloc}"
    simdi = time.time()

    kayit = _robots_onbellek.get(kok)
    if kayit and kayit[1] > simdi:
        return kayit[0].can_fetch(USER_AGENT, url)

    rp = RobotFileParser()
    sure = _ROBOTS_BASARILI_SANIYE

    try:
        r = requests.get(kok + "/robots.txt", headers=_HEADERS, timeout=10)
        if r.status_code == 200:
            rp.parse(r.text.splitlines())
        elif 400 <= r.status_code < 500:
            rp.allow_all = True
        else:
            rp.disallow_all = True
            sure = _ROBOTS_HATA_SANIYE
    except requests.RequestException:
        return True

    _robots_onbellek[kok] = (rp, simdi + sure)
    return rp.can_fetch(USER_AGENT, url)


def http_get(url: str, timeout: int = 20) -> requests.Response:
    if not izin_var(url):
        raise ScraperHatasi(f"robots.txt bu adrese izin vermiyor: {url}")
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


def fiyat_sayi(metin: str) -> Optional[int]:
    """'19.100,00 TL' -> 19100 | '18.605 ₺/ton' -> 18605 | '19450 (TL/ton)' -> 19450"""
    m = re.search(r"\d[\d.,]*", metin or "")
    if not m:
        return None
    s = m.group().split(",")[0].replace(".", "")
    return int(s) if s.isdigit() else None


def tarih_bul(metin: str, once: str = "") -> Optional[date]:
    """Metindeki ilk gg.aa.yyyy tarihini bulur. 'once' verilirse o kelimeden sonrakini arar."""
    kalip = r"(\d{2})\.(\d{2})\.(\d{4})"
    if once:
        m = re.search(once + r"\s*" + kalip, metin)
    else:
        m = re.search(kalip, metin)
    if not m:
        return None
    g, a, y = (int(x) for x in m.groups()[-3:])
    try:
        return date(y, a, g)
    except ValueError:
        return None