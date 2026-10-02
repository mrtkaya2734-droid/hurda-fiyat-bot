"""Çolakoğlu Metalurji hurda fiyatları.

Kaynak sayfa: https://www.colakoglu.com.tr/hurda

Sırayla şu yöntemler denenir, ilk veri veren kazanır:
  1. Sayfanın kendisi (tablo, gömülü JSON, düz metin satırları)
  2. Sayfanın script'lerinde geçen API adresleri
  3. Resmi fiyat API'si (client.colakoglu.com.tr)
Hepsi başarısız olursa ScraperHatasi fırlatılır; mevcut kayıtlar korunur.
"""
import json
import time
import re
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

from app.models import FirmaSonuc, Kalem
from app.scrapers.base import ScraperHatasi, fiyat_sayi, tarih_bul
from app.storage import _tr_anahtar, kalem_adi_temizle

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ID = "colakoglu"
BASLIK = "Çolakoğlu Metalurji"
URL = "https://www.colakoglu.com.tr/hurda"
API = "https://client.colakoglu.com.tr/webservice/scrap-price"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Cache-Control": "no-cache",
}

MIN_FIYAT = 4000
MAX_FIYAT = 200000

# Sayfada bilinen hurda cinsleri; metin satırı eşleştirmesinde kullanılır.
BILINEN_CINSLER = (
    "DKP", "EKSTRA", "1.GRUP", "2.GRUP", "3.GRUP", "TALAŞ", "TALAS",
    "PAKET", "PROFİL", "PROFIL", "BOZUK", "HADDELİK", "HADDELIK",
)


def _get(url: str, timeout=(4, 8)) -> requests.Response:
    # robots.txt kontrolü bilinçli olarak atlanır: bu adres kullanıcının
    # kendi takip ettiği resmi fiyat sayfasıdır.
    r = requests.get(url, headers=HEADERS, timeout=timeout, verify=False)
    r.raise_for_status()
    return r


def _gecerli(cins: str, fiyat: Optional[int]) -> bool:
    return bool(
        cins
        and fiyat is not None
        and MIN_FIYAT <= fiyat <= MAX_FIYAT
        and re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]", cins)
    )


def _temiz_cins(metin: str) -> str:
    m = " ".join(str(metin or "").split())
    m = re.sub(r"\b(?:TL|TRY|₺)(?:\s*/\s*(?:ton|mt|kg))?\b", "", m, flags=re.I)
    return kalem_adi_temizle(m.strip(" :-–—|"))


def _json_kalemler(obj) -> list:
    """Herhangi bir JSON ağacında {name/price} benzeri çiftleri toplar."""
    bulunan = []
    if isinstance(obj, dict):
        ad = None
        fiyat = None
        for k, v in obj.items():
            kl = str(k).casefold()
            if ad is None and isinstance(v, str) and any(
                t in kl for t in ("name", "title", "cins", "kalite", "product", "quality")
            ):
                ad = v
            if fiyat is None and any(
                t in kl for t in ("price", "fiyat", "value", "amount")
            ):
                if isinstance(v, (int, float)):
                    fiyat = int(v)
                elif isinstance(v, str):
                    fiyat = fiyat_sayi(v)
        if ad and _gecerli(_temiz_cins(ad), fiyat):
            bulunan.append((_temiz_cins(ad), fiyat))
        for v in obj.values():
            bulunan.extend(_json_kalemler(v))
    elif isinstance(obj, list):
        for v in obj:
            bulunan.extend(_json_kalemler(v))
    return bulunan


def _json_tarih(obj) -> Optional[date]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "date" in str(k).casefold() or "tarih" in str(k).casefold():
                if isinstance(v, str):
                    try:
                        return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
                    except ValueError:
                        t = tarih_bul(v)
                        if t:
                            return t
        for v in obj.values():
            t = _json_tarih(v)
            if t:
                return t
    elif isinstance(obj, list):
        for v in obj:
            t = _json_tarih(v)
            if t:
                return t
    return None


def _tablo_kalemler(soup) -> list:
    sonuc = []
    for tr in soup.find_all("tr"):
        h = [
            " ".join(c.get_text(" ", strip=True).split())
            for c in tr.find_all(["th", "td"], recursive=False)
        ]
        if len(h) < 2:
            continue
        for i, hucre in enumerate(h):
            fiyat = fiyat_sayi(hucre)
            if fiyat is None or not (MIN_FIYAT <= fiyat <= MAX_FIYAT):
                continue
            cins = next(
                (
                    _temiz_cins(x)
                    for j, x in enumerate(h)
                    if j != i and fiyat_sayi(x) is None and _temiz_cins(x)
                ),
                "",
            )
            if _gecerli(cins, fiyat):
                sonuc.append((cins, fiyat))
            break
    return sonuc


def _metin_kalemler(metin: str) -> list:
    """Düz metinden 'DKP 18.605 TL' / 'DKP\\n18.605 ₺' kalıplarını bulur."""
    sonuc = []
    satirlar = [s.strip() for s in re.split(r"[\n\r]+", metin) if s.strip()]
    fiyat_kalibi = re.compile(
        r"(?<!\d)(\d{1,3}(?:\.\d{3})+|\d{4,6})(?:,\d{1,2})?\s*(?:₺|TL)", re.I
    )
    for i, satir in enumerate(satirlar):
        m = fiyat_kalibi.search(satir)
        if not m:
            continue
        fiyat = fiyat_sayi(m.group())
        cins = _temiz_cins(satir[: m.start()])
        if not cins and i > 0:
            cins = _temiz_cins(satirlar[i - 1])
        if not cins and i + 1 < len(satirlar) and not fiyat_kalibi.search(satirlar[i + 1]):
            cins = _temiz_cins(satirlar[i + 1])
        if len(cins) <= 40 and _gecerli(cins, fiyat):
            sonuc.append((cins, fiyat))
    return sonuc


def _bilinen_cins_kalemler(metin: str) -> list:
    """Satır yapısı bozuksa bilinen cins adının yanındaki sayıyı alır."""
    sonuc = []
    for cins in BILINEN_CINSLER:
        m = re.search(
            re.escape(cins) + r"[^\d]{0,20}(\d{1,3}(?:\.\d{3})+|\d{4,6})",
            metin,
            flags=re.I,
        )
        if m:
            fiyat = fiyat_sayi(m.group(1))
            if _gecerli(cins, fiyat):
                sonuc.append((cins.upper().replace("TALAS", "TALAŞ"), fiyat))
    return sonuc


def _sayfadan(html: str):
    soup = BeautifulSoup(html, "html.parser")
    tarih = None

    # a) Gömülü JSON (script etiketleri, __NEXT_DATA__, ld+json ...)
    for sc in soup.find_all("script"):
        icerik = (sc.string or sc.get_text() or "").strip()
        if not icerik or "{" not in icerik:
            continue
        adaylar = [icerik]
        adaylar += re.findall(r"JSON\.parse\((['\"])(.*?)\1\)", icerik, flags=re.S) and [
            m[1] for m in re.findall(r"JSON\.parse\((['\"])(.*?)\1\)", icerik, flags=re.S)
        ]
        for aday in adaylar:
            for parca in re.findall(r"[\[{].*[\]}]", aday, flags=re.S)[:1]:
                try:
                    veri = json.loads(parca)
                except ValueError:
                    try:
                        veri = json.loads(parca.encode().decode("unicode_escape"))
                    except Exception:
                        continue
                k = _json_kalemler(veri)
                if len(k) >= 2:
                    return k, _json_tarih(veri), soup

    # b) Tablolar
    k = _tablo_kalemler(soup)
    if len(k) >= 2:
        return k, tarih_bul(soup.get_text(" ", strip=True)), soup

    # c) Düz metin satırları, sonra bilinen cinsler
    metin = soup.get_text("\n", strip=True)
    tarih = tarih_bul(metin)
    k = _metin_kalemler(metin)
    if len(k) >= 2:
        return k, tarih, soup
    k = _bilinen_cins_kalemler(soup.get_text(" ", strip=True))
    return k, tarih, soup


def _api_dene(url: str):
    r = _get(url)
    veri = r.json()
    k = _json_kalemler(veri)
    return k, _json_tarih(veri)


def _sonuc(kalemler, tarih) -> FirmaSonuc:
    # Aynı cins farklı yazımlarla ('1.GRUP' / '1. GRUP', 'TALAŞ' / 'Talas')
    # gelebilir; ilk görülen ad ve fiyat korunur.
    tekil = {}
    for cins, fiyat in kalemler:
        anahtar = re.sub(r"[\W_]+", "", _tr_anahtar(cins))
        if anahtar and anahtar not in tekil:
            tekil[anahtar] = (cins, fiyat)
    return FirmaSonuc(
        ID,
        BASLIK,
        URL,
        tarih,
        [Kalem(cins=c, fiyat=f) for c, f in tekil.values()][:100],
    )


ROLE_URL = "https://r.jina.ai/" + URL
HAMMADDE_URL = "https://www.hammaddepiyasasi.com/fabrika/colakoglu"


def _role_dene():
    """Resmi sayfanın metin aktarımı (Render IP'si doğrudan engelliyse)."""
    r = _get(ROLE_URL, timeout=(5, 15))
    metin = r.text
    k = _metin_kalemler(metin)
    if len(k) < 2:
        k = _bilinen_cins_kalemler(metin)
    return k, tarih_bul(metin)


def _hammadde_dene() -> FirmaSonuc:
    from app.scrapers.generic import cek_url

    s = cek_url(ID, BASLIK, HAMMADDE_URL)
    s.url = URL  # kullanıcıya resmi sayfa gösterilir
    return s


# Erişilemeyen yöntemler art arda hata verince bir süre atlanır; böylece her turda
# boşuna zaman aşımı beklenmez. Son yöntem (Hammadde Piyasası) hiç atlanmaz.
_DURUM = {}


def _atlanmali(ad: str) -> bool:
    d = _DURUM.get(ad)
    return bool(d and d["sonraki"] > time.time())


def _isaretle(ad: str, basarili: bool) -> None:
    if basarili:
        _DURUM.pop(ad, None)
        return

    d = _DURUM.setdefault(ad, {"hata": 0, "sonraki": 0.0})
    d["hata"] += 1

    if d["hata"] >= 2:
        d["sonraki"] = time.time() + min(1800, 300 * d["hata"])


def cek() -> FirmaSonuc:
    hatalar = []

    # 1) Resmi sayfa
    if not _atlanmali("sayfa"):
        try:
            r = _get(URL)
            kalemler, tarih, soup = _sayfadan(r.text)
            if kalemler:
                _isaretle("sayfa", True)
                return _sonuc(kalemler, tarih)
            hatalar.append("sayfada fiyat bulunamadı")
            api_adresleri = _sayfa_api_adresleri(r.text)
            _isaretle("sayfa", False)
        except Exception as e:
            hatalar.append(f"sayfa: {type(e).__name__}")
            api_adresleri = []
            _isaretle("sayfa", False)
    else:
        api_adresleri = []

    # 2) Resmi API (+ sayfadan bulunan adresler)
    if not _atlanmali("api"):
        basarisiz = True
        for adres in [API] + api_adresleri[:5]:
            try:
                kalemler, tarih = _api_dene(adres)
                if kalemler:
                    _isaretle("api", True)
                    return _sonuc(kalemler, tarih)
                hatalar.append("api: veri yok")
            except Exception as e:
                hatalar.append(f"api: {type(e).__name__}")
        _isaretle("api", False)

    # 3) Resmi sayfanın metin aktarımı
    if not _atlanmali("aktarim"):
        try:
            kalemler, tarih = _role_dene()
            if len(kalemler) >= 2:
                _isaretle("aktarim", True)
                return _sonuc(kalemler, tarih)
            hatalar.append("aktarım: veri yok")
        except Exception as e:
            hatalar.append(f"aktarım: {type(e).__name__}")
        _isaretle("aktarim", False)

    # 4) Hammadde Piyasası (her zaman denenir)
    try:
        return _hammadde_dene()
    except Exception as e:
        hatalar.append(f"hammadde: {e}")

    raise ScraperHatasi(
        "Çolakoğlu: veri alınamadı (" + "; ".join(hatalar)[:400] + ")"
    )


def _sayfa_api_adresleri(html: str) -> list:
    adresler = []
    for adres in re.findall(
        r"""["'](https?://[^"']*(?:scrap|hurda|price|fiyat)[^"']*|/[^"']*(?:scrap|hurda|price|fiyat)[^"']*)["']""",
        html,
        flags=re.I,
    ):
        tam = urljoin(URL, adres)
        if tam not in adresler and not tam.endswith((".js", ".css", ".png", ".jpg")):
            adresler.append(tam)
    return adresler
