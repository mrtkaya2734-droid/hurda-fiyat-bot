"""
Canlı (gün içi) LME fiyatları — MetalCharts API.

Resmî LME fiyatları ücretsiz kaynaklarda (lme.com, Westmetall) bir gün
gecikmeli yayınlanıyor. MetalCharts gün içi fiyat veriyor:

- ALI (Alüminyum) ve NI (Nikel): LME vadeli, USD/ton
- ZN (Çinko) ve PB (Kurşun): spot, USD/ton (LME 3 ay ile uyumlu)
- Bakır yalnızca COMEX (USD/lb), Kalay yok: bu ikisi resmî veriden gelir.

Ücretsiz planda ayda 200 istek hakkı var. Bu yüzden:
- istekler ziyaretçi isteğiyle değil yalnızca zamanlayıcıdan atılır,
- yalnızca hafta içi iş saatlerinde (İstanbul 09:00-21:00) çekilir,
- aralık, ayın kalan istek hakkına ve kalan iş saatine göre otomatik
  hesaplanır (ücretsiz planda ~1 saat, plan yükseltilince en sık 5 dk),
- son değer data.json'a yazılır; sunucu yeniden başlasa da hak harcanmaz.

Anahtar ortam değişkeninden okunur: METALCHARTS_API_KEY.
Ücretsiz planda fiyatların yanında MetalCharts kaynak linki gösterilmelidir.
"""

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests


API_URL = "https://api.metalcharts.org/v1/prices"
KREDI_URL = "https://metalcharts.org/?via=81ca54d555"
KREDI_METNI = "Metal prices by MetalCharts"

ISTANBUL = ZoneInfo("Europe/Istanbul")

# MetalCharts sembolü -> uygulamadaki LME_METALS anahtarı
SEMBOLLER = {
    "ALI": "Aluminium",
    "NI": "Nickel",
    "ZN": "Zinc",
    "PB": "Lead",
}

PENCERE_BASLANGIC_SAAT = 9
PENCERE_BITIS_SAAT = 21
EN_KISA_ARALIK_SN = 5 * 60
GUVENLIK_PAYI = 5          # ay sonuna bu kadar istek hakkı bırakılır
VARSAYILAN_AYLIK_LIMIT = 200
EN_ESKI_GECERLI_SN = 3 * 24 * 3600  # hafta sonunu kapsar; daha eskisi gösterilmez

_KILIT = threading.Lock()
_DURUM = {
    "veriler": {},
    "zaman": 0.0,          # son başarılı çekim (epoch)
    "son_deneme": 0.0,
    "aylik_limit": None,
    "aylik_kalan": None,
    "ay_sifirlanma": None,  # epoch (UTC)
    "bekle_kadar": 0.0,     # 429 sonrası bu zamana kadar istek atılmaz
    "hata": None,
}
_YUKLENDI = False


def anahtar():
    return os.getenv("METALCHARTS_API_KEY", "").strip()


def etkin():
    return bool(anahtar())


def _pencerede(an):
    yerel = an.astimezone(ISTANBUL)
    return (
        yerel.weekday() < 5
        and PENCERE_BASLANGIC_SAAT <= yerel.hour < PENCERE_BITIS_SAAT
    )


def _ay_sonu(an):
    yerel = an.astimezone(timezone.utc)
    if yerel.month == 12:
        return datetime(yerel.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(yerel.year, yerel.month + 1, 1, tzinfo=timezone.utc)


def _kalan_pencere_dakika(an, bitis):
    """`an` ile `bitis` arasındaki hafta içi 09-21 (İstanbul) dakikaları."""
    toplam = 0.0
    gun = an.astimezone(ISTANBUL).replace(hour=0, minute=0, second=0, microsecond=0)
    while gun < bitis:
        if gun.weekday() < 5:
            bas = gun.replace(hour=PENCERE_BASLANGIC_SAAT)
            son = gun.replace(hour=PENCERE_BITIS_SAAT)
            bas = max(bas, an)
            son = min(son, bitis)
            if son > bas:
                toplam += (son - bas).total_seconds() / 60
        gun = (gun + timedelta(days=1, hours=2)).replace(hour=0)
    return toplam


def aralik_saniye(an=None):
    """Bir sonraki istek için beklenecek süre; hak bittiyse None."""
    an = an or datetime.now(timezone.utc)

    if _DURUM["ay_sifirlanma"]:
        bitis = datetime.fromtimestamp(_DURUM["ay_sifirlanma"], tz=timezone.utc)
        if bitis <= an:
            bitis = _ay_sonu(an)
    else:
        bitis = _ay_sonu(an)

    kalan = _DURUM["aylik_kalan"]
    if kalan is None:
        kalan = int(os.getenv("METALCHARTS_AYLIK_LIMIT", VARSAYILAN_AYLIK_LIMIT))

    kullanilabilir = kalan - GUVENLIK_PAYI
    if kullanilabilir <= 0:
        return None

    dakika = _kalan_pencere_dakika(an, bitis)
    return max(EN_KISA_ARALIK_SN, dakika * 60 / kullanilabilir)


def _sayi(deger):
    try:
        return float(deger)
    except (TypeError, ValueError):
        return None


def _ayristir(govde):
    veriler = {}
    for sembol, isim in SEMBOLLER.items():
        kayit = (govde.get("data") or {}).get(sembol)
        if not isinstance(kayit, dict):
            continue
        if str(kayit.get("unit", "")).lower() not in {"mt", "t", "ton", "tonne"}:
            continue
        fiyat = _sayi(kayit.get("price"))
        if fiyat is None or fiyat <= 0:
            continue
        veriler[isim] = {
            "fiyat": round(fiyat, 2),
            "alis": _sayi(kayit.get("bid")),
            "satis": _sayi(kayit.get("ask")),
            "degisim": _sayi(kayit.get("change24h")),
            "degisim_yuzde": _sayi(kayit.get("changePercent24h")),
            "borsa": kayit.get("exchange") or "",
            "zaman": kayit.get("timestamp") or govde.get("timestamp"),
            "eski": bool(kayit.get("stale")),
        }
    return veriler


def _kota_oku(cevap):
    def tam(ad):
        try:
            return int(cevap.headers.get(ad))
        except (TypeError, ValueError):
            return None

    limit = tam("x-ratelimit-month-limit")
    kalan = tam("x-ratelimit-month-remaining")
    sifir = tam("x-ratelimit-month-reset")
    if limit is not None:
        _DURUM["aylik_limit"] = limit
    if kalan is not None:
        _DURUM["aylik_kalan"] = kalan
    if sifir is not None:
        _DURUM["ay_sifirlanma"] = float(sifir)


def _kalici_yukle():
    global _YUKLENDI
    if _YUKLENDI:
        return
    _YUKLENDI = True
    try:
        from app.storage import load_data

        kayit = load_data().get("lme_canli") or {}
        for alan in ("veriler", "zaman", "aylik_limit", "aylik_kalan", "ay_sifirlanma"):
            if alan in kayit:
                _DURUM[alan] = kayit[alan]
    except Exception as exc:
        print(f"CANLI LME YÜKLEME HATASI: {type(exc).__name__}: {exc}")


def _kalici_kaydet():
    try:
        from app.storage import load_data, save_data

        data = load_data()
        data["lme_canli"] = {
            alan: _DURUM[alan]
            for alan in ("veriler", "zaman", "aylik_limit", "aylik_kalan", "ay_sifirlanma")
        }
        save_data(data)
    except Exception as exc:
        print(f"CANLI LME KAYIT HATASI: {type(exc).__name__}: {exc}")


def cek():
    """MetalCharts'tan tek istek atar. Zamanlayıcı dışında çağırmayın."""
    _DURUM["son_deneme"] = time.time()
    cevap = requests.get(
        API_URL,
        headers={
            "Authorization": f"Bearer {anahtar()}",
            "Accept": "application/json",
            "User-Agent": "HurdaFiyatBot/2.0",
        },
        timeout=15,
    )
    _kota_oku(cevap)

    if cevap.status_code == 429:
        try:
            bekle = float(cevap.headers.get("retry-after") or 0)
        except ValueError:
            bekle = 0
        if _DURUM["aylik_kalan"] is not None and _DURUM["aylik_kalan"] <= 0:
            bekle = max(bekle, (_DURUM["ay_sifirlanma"] or 0) - time.time())
        _DURUM["bekle_kadar"] = time.time() + max(bekle, 60)
        raise RuntimeError("MetalCharts istek sınırı aşıldı (429).")

    cevap.raise_for_status()
    veriler = _ayristir(cevap.json())

    if not veriler:
        raise RuntimeError("MetalCharts yanıtında LME metali bulunamadı.")

    _DURUM["veriler"] = veriler
    _DURUM["zaman"] = time.time()
    _DURUM["hata"] = None
    _kalici_kaydet()
    return veriler


def guncelle_gerekirse():
    """Zamanlayıcıdan birkaç dakikada bir çağrılır; gerekiyorsa tek istek atar."""
    if not etkin():
        return False

    with _KILIT:
        _kalici_yukle()
        simdi = time.time()
        an = datetime.now(timezone.utc)

        if simdi < _DURUM["bekle_kadar"] or not _pencerede(an):
            return False

        aralik = aralik_saniye(an)
        if aralik is None:
            return False

        son = max(_DURUM["zaman"] or 0, _DURUM["son_deneme"] or 0)
        if simdi - son < aralik:
            return False

        try:
            cek()
            print(
                "CANLI LME: güncellendi "
                f"(kalan hak {_DURUM['aylik_kalan']}, sonraki ~{int(aralik // 60)} dk)"
            )
            return True
        except Exception as exc:
            _DURUM["hata"] = f"{type(exc).__name__}: {exc}"
            print(f"CANLI LME HATASI: {_DURUM['hata']}")
            return False


def ozet():
    """/lme yanıtına eklenecek canlı veri; yoksa None."""
    if not etkin():
        return None

    _kalici_yukle()
    zaman = _DURUM["zaman"] or 0
    if not _DURUM["veriler"] or time.time() - zaman > EN_ESKI_GECERLI_SN:
        return None

    return {
        "kaynak": "MetalCharts",
        "kredi_url": KREDI_URL,
        "kredi_metni": KREDI_METNI,
        "cekilme": datetime.fromtimestamp(zaman, tz=ISTANBUL).strftime("%d.%m.%Y %H:%M"),
        "veriler": _DURUM["veriler"],
    }
