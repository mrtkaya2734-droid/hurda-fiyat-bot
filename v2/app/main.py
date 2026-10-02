from fastapi import (
    FastAPI,
    Response,
    Form,
    Depends,
    HTTPException,
    status,
    Request,
)

from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import uvicorn
import os
import json
import io
import zipfile
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from starlette.middleware.gzip import GZipMiddleware
import base64
import urllib.parse
import secrets
import html as html_lib
import uuid
import shutil
import re
import gc
import requests
import xml.etree.ElementTree as ET

from app.scrapers import TUMU
from app import webpush
from app import adminauth
import app.storage as storage_module
from app.scrapers.generic import cek_url as generic_url_cek

from app.storage import (
    load_data,
    save_data,
    now_string,
    firma_sil,
    fiyat_kaydet,
    fiyatlari_toplu_kaydet,
    kalem_adi_temizle,
    veriyi_duzelt,
    gecmise_fiyat_yaz,
    manuel_gecmisi_tamamla,
    manuel_fiyat_kaydet,
    manuel_fiyat_sil,
    bildirim_ekle,
    bildirim_okundu,
    bildirim_sil,
    bildirimleri_okundu_yap,
    bildirimleri_sil,
    gecmis_ekle,
    sistem_ozeti,
    DATA_FILE,
    BACKUP_DIR,
    supabase_storage_download,
    supabase_storage_upload,
    supabase_restore_file,
    supabase_upload_json,
)


# =========================================================
# AYARLAR
# =========================================================

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "hurda123")

STALE_MINUTES = 1440

security = HTTPBasic()

if ADMIN_PASS == "hurda123":
    print(
        "UYARI: ADMIN_PASS ortam değişkeni tanımlı değil, "
        "varsayılan admin şifresi kullanılıyor!"
    )


# =========================================================
# PROJE ANA DİZİNİ
# =========================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


# =========================================================
# REKLAM DOSYALARI
# =========================================================

ADS_FILE = os.path.join(
    BASE_DIR,
    "ads.json",
)

ADS_UPLOAD_DIR = os.path.join(
    BASE_DIR,
    "static",
    "ads",
)

os.makedirs(
    ADS_UPLOAD_DIR,
    exist_ok=True,
)

_ADS_SUPABASE_SYNCED = False


ISTANBUL = ZoneInfo("Europe/Istanbul")


def now_istanbul():
    return datetime.now(ISTANBUL)


def now_istanbul_string():
    return now_istanbul().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def son_24_saatte_mi(deger, simdi=None):
    if not deger:
        return False

    try:
        zaman = datetime.fromisoformat(
            str(deger).replace("Z", "+00:00")
        )

        if zaman.tzinfo is None:
            zaman = zaman.replace(tzinfo=ISTANBUL)

        simdi = simdi or now_istanbul()
        fark = (simdi - zaman).total_seconds()

        return 0 <= fark <= 24 * 60 * 60

    except Exception:
        return False


# =========================================================
# LME RESMİ GECİKMELİ FİYATLARI
# =========================================================

LME_URL = (
    "https://www.lme.com/en/market-data/"
    "reports-and-data/lme-official-prices"
)

LME_METALS = {
    "Aluminium": "Alüminyum",
    "Copper": "Bakır",
    "Zinc": "Çinko",
    "Nickel": "Nikel",
    "Lead": "Kurşun",
    "Tin": "Kalay",
    "Cobalt": "Kobalt",
}

_LME_CACHE = {
    "tarih": None,
    "veriler": [],
    "cekilme": None,
}


def _lme_sayi(value):

    try:

        return float(
            str(value)
            .replace(",", "")
            .replace(" ", "")
        )

    except (
        TypeError,
        ValueError,
    ):

        return None


def _lme_sonraki_tarih(metin):

    eslesme = re.search(
        r"Data valid for\s+"
        r"(\d{1,2})\s+"
        r"([A-Za-z]{3})\\s+"
        r"(\d{4})",
        metin or "",
        re.IGNORECASE,
    )

    if not eslesme:
        return None

    try:
        return datetime.strptime(
            " ".join(
                eslesme.groups()
            ),
            "%d %b %Y",
        ).strftime(
            "%d.%m.%Y"
        )
    except ValueError:
        return None


def _lme_verilerini_html(html):

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    hedef_tablo = None

    for table in soup.find_all("table"):

        tablo_metni = " ".join(
            table.stripped_strings
        ).casefold()

        if (
            "aluminium" in tablo_metni
            and "copper" in tablo_metni
            and (
                "3 month" in tablo_metni
                or "3 months" in tablo_metni
            )
        ):

            hedef_tablo = table
            break

    if hedef_tablo is None:
        raise RuntimeError(
            "LME fiyat tablosu bulunamadı."
        )

    fiyatlar = {}

    for row in hedef_tablo.find_all("tr"):

        hucreler = [
            " ".join(
                cell.stripped_strings
            ).strip()
            for cell in row.find_all(
                ["th", "td"]
            )
        ]

        if len(hucreler) < 5:
            continue

        metal_adi = hucreler[0]

        eslesen = None

        for isim, turkce in LME_METALS.items():

            if (
                isim.casefold()
                == metal_adi.casefold()
            ):

                eslesen = (
                    isim,
                    turkce,
                )
                break

        if eslesen is None:
            continue

        fiyatlar[eslesen[0]] = {
            "ad": eslesen[1],
            "cash_bid": _lme_sayi(
                hucreler[1]
            ),
            "cash_ask": _lme_sayi(
                hucreler[2]
            ),
            "three_month_bid": _lme_sayi(
                hucreler[3]
            ),
            "three_month_ask": _lme_sayi(
                hucreler[4]
            ),
        }

    if not fiyatlar:
        raise RuntimeError(
            "LME tablosundan fiyat okunamadı."
        )

    return (
        fiyatlar,
        _lme_sonraki_tarih(
            " ".join(
                soup.stripped_strings
            )
        ),
    )


def _westmetall_lme_verilerini_cek():

    westmetall_url = (
        "https://www.westmetall.com/en/markdaten.php"
    )

    try:

        cevap = requests.get(
            westmetall_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/140 Safari/537.36"
                ),
                "Accept-Language": "en-GB,en;q=0.9",
            },
            timeout=25,
        )

        cevap.raise_for_status()

    except requests.RequestException as exc:

        raise RuntimeError(
            f"Yedek LME kaynağına ulaşılamadı: {exc}"
        ) from exc

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        cevap.text,
        "html.parser",
    )

    page_text = soup.get_text(
        " ",
        strip=True,
    )

    fiyatlar = {}

    # Westmetall, LME Official Prices tablosunda
    # metal + cash settlement + 3 months sütunlarını yayınlıyor.
    for table in soup.find_all("table"):

        rows = table.find_all("tr")

        if not rows:
            continue

        tablo_metni = " ".join(
            table.stripped_strings
        ).casefold()

        if (
            "official lme-prices" not in tablo_metni
            and "settlement kasse" not in tablo_metni
        ):
            continue

        for row in rows:

            cells = [
                " ".join(
                    cell.stripped_strings
                ).strip()
                for cell in row.find_all(
                    ["th", "td"]
                )
            ]

            if len(cells) < 3:
                continue

            metal_eslesmesi = None

            for isim, turkce in LME_METALS.items():

                if cells[0].casefold() == isim.casefold():

                    metal_eslesmesi = (
                        isim,
                        turkce,
                    )
                    break

            if metal_eslesmesi is None:
                continue

            cash = _lme_sayi(
                cells[1]
            )
            three_month = _lme_sayi(
                cells[2]
            )

            if (
                cash is None
                and three_month is None
            ):
                continue

            fiyatlar[
                metal_eslesmesi[0]
            ] = {
                "ad": metal_eslesmesi[1],
                "cash_bid": cash,
                "cash_ask": cash,
                "three_month_bid": three_month,
                "three_month_ask": three_month,
            }

        if fiyatlar:
            break

    if not fiyatlar:

        raise RuntimeError(
            "Westmetall LME fiyat tablosundan veri okunamadı."
        )

    tarih = None

    tarih_eslesmesi = re.search(
        r"Official LME-Prices in US Dollar\s*"
        r"\|?\s*"
        r"(\d{1,2})\.\s*"
        r"([A-Za-z]+)\s+"
        r"(\d{4})",
        page_text,
        re.IGNORECASE,
    )

    if tarih_eslesmesi:

        aylar = {
            "january": 1,
            "february": 2,
            "march": 3,
            "april": 4,
            "may": 5,
            "june": 6,
            "july": 7,
            "august": 8,
            "september": 9,
            "october": 10,
            "november": 11,
            "december": 12,
        }

        ay = aylar.get(
            tarih_eslesmesi.group(2).casefold()
        )

        if ay:

            try:
                tarih = datetime(
                    int(tarih_eslesmesi.group(3)),
                    ay,
                    int(tarih_eslesmesi.group(1)),
                ).strftime(
                    "%d.%m.%Y"
                )
            except ValueError:
                tarih = None

    if not tarih:

        tarih = (
            re.search(
                r"Official LME-Prices.*?"
                r"(\d{1,2})\.\s*"
                r"([A-Za-z]+)\s+"
                r"(\d{4})",
                page_text,
                re.IGNORECASE,
            )
        )

        if tarih:
            ay = {
                "january": 1,
                "february": 2,
                "march": 3,
                "april": 4,
                "may": 5,
                "june": 6,
                "july": 7,
                "august": 8,
                "september": 9,
                "october": 10,
                "november": 11,
                "december": 12,
            }.get(
                tarih.group(2).casefold()
            )

            if ay:
                try:
                    tarih = datetime(
                        int(tarih.group(3)),
                        ay,
                        int(tarih.group(1)),
                    ).strftime(
                        "%d.%m.%Y"
                    )
                except ValueError:
                    tarih = None

    return fiyatlar, tarih


def _lme_api_verilerini_cek():
    url = (
        "https://www.lme.com/api/trading-data/"
        "day-delayed"
    )

    response = requests.get(
        url,
        params={
            "datasourceId": (
                "1a0ef0b6-3ee6-4e44-a415-7a313d5bd771"
            )
        },
        headers={
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-GB,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
            "Referer": (
                "https://www.lme.com/"
                "market-data/reports-and-data/"
                "lme-official-prices"
            ),
        },
        timeout=20,
    )

    response.raise_for_status()

    payload = response.json()

    rows = payload.get("Rows") or []

    if not rows:
        raise RuntimeError(
            "LME day-delayed API boş veri döndürdü."
        )

    fiyatlar = {}

    for row in rows:
        metal_adi = str(
            row.get("RowTitle") or ""
        ).strip()

        if not metal_adi:
            continue

        eslesen = None

        for isim, turkce in LME_METALS.items():
            if metal_adi.casefold() == isim.casefold():
                eslesen = (
                    isim,
                    turkce,
                )
                break

        if eslesen is None:
            continue

        values = row.get("Values") or []

        if not isinstance(values, list):
            continue

        # LME day-delayed tablosunda ilk dört değer:
        # Cash Bid, Cash Ask, 3 Month Bid, 3 Month Ask.
        sayilar = [
            _lme_sayi(x)
            for x in values[:4]
        ]

        if len(sayilar) < 4:
            continue

        fiyatlar[eslesen[0]] = {
            "ad": eslesen[1],
            "cash_bid": sayilar[0],
            "cash_ask": sayilar[1],
            "three_month_bid": sayilar[2],
            "three_month_ask": sayilar[3],
        }

    if not fiyatlar:
        raise RuntimeError(
            "LME day-delayed API içinden metal fiyatları okunamadı."
        )

    tarih = payload.get("DateOfData")

    if tarih:
        try:
            tarih = datetime.fromisoformat(
                str(tarih).replace("Z", "+00:00")
            ).strftime("%d.%m.%Y")
        except Exception:
            tarih = str(tarih)
    else:
        tarih = None

    return fiyatlar, tarih





def _smm_lme_3m_verilerini_cek():

    # SMM/Metal.com, LMEselect 3-month kotasyonunu 15 dakika
    # gecikmeli olarak yayınlıyor.
    metal_url = {
        "Aluminium": "https://www-old.metal.com/Aluminum/",
        "Copper": "https://www-old.metal.com/Copper/",
        "Zinc": "https://www-old.metal.com/Zinc",
        "Nickel": "https://www-old.metal.com/Nickel/",
        "Lead": "https://www-old.metal.com/Lead",
        "Tin": "https://www-old.metal.com/Tin/",
        "Cobalt": "https://www-old.metal.com/Cobalt/",
    }

    from bs4 import BeautifulSoup

    fiyatlar = {}
    tarihler = []

    for isim, turkce in LME_METALS.items():

        url = metal_url.get(isim)

        if not url:
            continue

        try:
            cevap = requests.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/140 Safari/537.36"
                    ),
                    "Accept-Language": "en-GB,en;q=0.9",
                },
                timeout=15,
            )
            cevap.raise_for_status()

            soup = BeautifulSoup(
                cevap.text,
                "html.parser",
            )

            metin = " ".join(
                soup.stripped_strings
            )

            desen = (
                r"LMEselect\s+"
                + re.escape(isim)
                + r"\s+3\s+Month,\s*USD/mt\s+"
                r"([0-9][0-9,]*(?:\.[0-9]+)?)"
            )

            eslesme = re.search(
                desen,
                metin,
                re.IGNORECASE,
            )

            if not eslesme:
                continue

            fiyat = _lme_sayi(
                eslesme.group(1)
            )

            if fiyat is None:
                continue

            tarih_eslesmesi = re.search(
                r"(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                r"\s+\d{1,2},\s+\d{4})\b",
                metin,
                re.IGNORECASE,
            )

            if tarih_eslesmesi:
                try:
                    tarihler.append(
                        datetime.strptime(
                            tarih_eslesmesi.group(1),
                            "%b %d, %Y",
                        )
                    )
                except ValueError:
                    pass

            fiyatlar[isim] = {
                "ad": turkce,
                "cash_bid": None,
                "cash_ask": None,
                "three_month_bid": fiyat,
                "three_month_ask": fiyat,
            }

        except (
            requests.RequestException,
            ValueError,
            RuntimeError,
        ):
            continue

    if not fiyatlar:
        raise RuntimeError(
            "SMM/Metal.com LMEselect 3M verisi okunamadı."
        )

    tarih = None

    if tarihler:
        tarih = max(
            tarihler
        ).strftime(
            "%d.%m.%Y"
        )

    return fiyatlar, tarih

def lme_gecmisi_kaydet(veriler, tarih):
    """Günlük LME değerlerini (nakit satış + 3 ay satış) data.json'a biriktirir."""
    try:
        gun = datetime.fromisoformat(str(tarih)[:10]).date().isoformat()
    except (TypeError, ValueError):
        try:
            g, a, y = str(tarih)[:10].split(".")
            gun = f"{y}-{a}-{g}"
        except Exception:
            gun = now_istanbul().date().isoformat()

    with _LME_GECMIS_KILIT:
        _lme_gecmisi_kaydet_kilitli(veriler, gun)


def _lme_gecmisi_kaydet_kilitli(veriler, gun):
    data = load_data()
    gecmis = data.setdefault("lme_gecmisi", {})
    degisti = False

    for item in veriler:
        ad = item.get("ad")
        nakit = item.get("cash_ask")
        uc_ay = item.get("three_month_ask")

        if not ad or nakit is None:
            continue

        seri = gecmis.setdefault(ad, [])
        kayit = [gun, nakit, uc_ay]

        mevcut = next((i for i, k in enumerate(seri) if k[0] == gun), None)
        if mevcut is not None:
            if seri[mevcut] != kayit:
                seri[mevcut] = kayit
                degisti = True
        else:
            seri.append(kayit)
            seri.sort(key=lambda k: k[0])
            degisti = True

        if len(seri) > 400:
            del seri[:-400]

    if degisti:
        save_data(data)


_WESTMETALL_ALANLAR = {
    "Aluminium": "LME_Al_cash",
    "Copper": "LME_Cu_cash",
    "Zinc": "LME_Zn_cash",
    "Nickel": "LME_Ni_cash",
    "Lead": "LME_Pb_cash",
    "Tin": "LME_Sn_cash",
}

_LME_DOLDURMA = {"son_deneme": 0.0, "calisiyor": False}
_LME_DOLDURMA_BEKLEME = 3600
_LME_GECMIS_KILIT = threading.Lock()
_LME_MIN_NOKTA = 10


def _westmetall_gecmisi_cek(alan, gun_sayisi=120):
    """Westmetall günlük tablosu: [[YYYY-AA-GG, nakit, 3 ay], ...] (eskiden yeniye)."""
    from bs4 import BeautifulSoup

    cevap = requests.get(
        "https://www.westmetall.com/en/markdaten.php",
        params={"action": "table", "field": alan},
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
            "Accept-Language": "en-GB,en;q=0.9",
        },
        timeout=20,
    )
    cevap.raise_for_status()

    aylar = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    kayitlar = {}

    for satir in BeautifulSoup(cevap.text, "html.parser").find_all("tr"):
        hucreler = [
            " ".join(h.stripped_strings).strip()
            for h in satir.find_all(["th", "td"])
        ]
        if len(hucreler) < 3:
            continue

        eslesme = re.match(r"(\d{1,2})\.\s*([A-Za-z]+)\s+(\d{4})", hucreler[0])
        if not eslesme:
            continue
        ay = aylar.get(eslesme.group(2).casefold())
        if not ay:
            continue
        try:
            gun = datetime(int(eslesme.group(3)), ay, int(eslesme.group(1))).date().isoformat()
        except ValueError:
            continue

        nakit = _lme_sayi(hucreler[1])
        uc_ay = _lme_sayi(hucreler[2])
        if nakit is None:
            continue
        kayitlar[gun] = [gun, nakit, uc_ay]

    return [kayitlar[g] for g in sorted(kayitlar)][-gun_sayisi:]


def _lme_eksik_metaller():
    mevcut = load_data().get("lme_gecmisi", {})
    return [
        (isim, alan)
        for isim, alan in _WESTMETALL_ALANLAR.items()
        if len(mevcut.get(LME_METALS[isim], [])) < _LME_MIN_NOKTA
    ]


def _lme_gecmisi_doldur(eksikler):
    """Geçmişi kısa kalan metalleri Westmetall günlük tablosundan tamamlar."""
    try:
        def indir(oge):
            isim, alan = oge
            try:
                return isim, _westmetall_gecmisi_cek(alan)
            except Exception as exc:
                print(f"LME GEÇMİŞ DOLDURMA ({isim}): {type(exc).__name__}: {exc}")
                return isim, []

        with ThreadPoolExecutor(max_workers=len(eksikler)) as havuz:
            indirilen = {
                LME_METALS[isim]: seri
                for isim, seri in havuz.map(indir, eksikler)
                if seri
            }

        if not indirilen:
            return

        with _LME_GECMIS_KILIT:
            data = load_data()
            gecmis = data.setdefault("lme_gecmisi", {})
            for ad, seri in indirilen.items():
                birlesik = {k[0]: k for k in seri}
                for k in gecmis.get(ad, []):
                    birlesik[k[0]] = k  # bizim kayıtlarımız öncelikli
                gecmis[ad] = [birlesik[g] for g in sorted(birlesik)][-400:]
            save_data(data)
        print(f"LME geçmişi dolduruldu: {', '.join(indirilen)}")
    finally:
        _LME_DOLDURMA["calisiyor"] = False


def lme_gecmisi_doldurmayi_baslat():
    """Arka planda, en fazla saatte bir kez çalışır; /lme yanıtını geciktirmez."""
    simdi = time.time()
    if _LME_DOLDURMA["calisiyor"]:
        return
    if simdi - _LME_DOLDURMA["son_deneme"] < _LME_DOLDURMA_BEKLEME:
        return

    eksikler = _lme_eksik_metaller()
    if not eksikler:
        return

    _LME_DOLDURMA["son_deneme"] = simdi
    _LME_DOLDURMA["calisiyor"] = True
    threading.Thread(
        target=_lme_gecmisi_doldur, args=(eksikler,), daemon=True
    ).start()


LME_GRAFIK_MODLARI = {
    "acik": "Açık gelsin",
    "kapali": "Kapalı gelsin (düğmeyle açılır)",
    "pasif": "Pasif (ana sayfada gizli)",
}


def lme_grafik_modu():
    """'acik' (varsayılan) | 'kapali' | 'pasif'. Eski aktif/pasif ayarıyla uyumlu."""
    ayarlar = load_data().get("ayarlar")
    if not isinstance(ayarlar, dict):
        return "acik"
    mod = ayarlar.get("lme_grafik_mod")
    if mod in LME_GRAFIK_MODLARI:
        return mod
    return "pasif" if ayarlar.get("lme_grafik_aktif", True) is False else "acik"


def lme_grafik_aktif_mi():
    return lme_grafik_modu() != "pasif"


def lme_gecmisi_oku(gun_sayisi=180):
    gecmis = load_data().get("lme_gecmisi", {})
    return {ad: seri[-gun_sayisi:] for ad, seri in gecmis.items()}


def lme_verilerini_cek():
    global _LME_CACHE

    simdi = now_istanbul()

    if (
        _LME_CACHE["veriler"]
        and _LME_CACHE["cekilme"]
    ):
        gecen = (
            simdi - _LME_CACHE["cekilme"]
        ).total_seconds()

        if gecen < 900:
            return _LME_CACHE

    try:
        fiyatlar, tarih = _lme_api_verilerini_cek()
        kaynak = "LME Official Prices · day-delayed API"

    except Exception as exc:
        try:
            fiyatlar, tarih = _westmetall_lme_verilerini_cek()
            kaynak = "Westmetall · Official LME Prices"
        except Exception as yedek_exc:
            print(
                "LME HATASI: "
                f"{type(exc).__name__}: {exc} | "
                f"{type(yedek_exc).__name__}: {yedek_exc}"
            )

            # Kaynaklar geçici olarak erişilemezse son bilinen veriyi göster.
            if _LME_CACHE["veriler"]:
                return {
                    "tarih": _LME_CACHE["tarih"],
                    "veriler": _LME_CACHE["veriler"],
                    "cekilme": _LME_CACHE["cekilme"].strftime(
                        "%d.%m.%Y %H:%M:%S"
                    ),
                    "kaynak": (_LME_CACHE["kaynak"] or "LME")
                    + " · son bilinen veri",
                    "usd_tl": _LME_CACHE["usd_tl"],
                }

            raise RuntimeError(
                "LME verisi şu anda alınamıyor."
            ) from yedek_exc

    usd_tl = None

    try:
        doviz = doviz_kurlarini_getir()
        usd_tl = (
            doviz.get("veriler", {})
            .get("USD", {})
            .get("alis")
        )
    except Exception:
        usd_tl = None

    veriler = []

    for key in LME_METALS:
        if key not in fiyatlar:
            continue

        item = dict(fiyatlar[key])
        bid = item.get("three_month_bid")
        ask = item.get("three_month_ask")

        if (
            usd_tl is not None
            and bid is not None
            and ask is not None
        ):
            orta = (
                float(bid) + float(ask)
            ) / 2

            item["three_month_tl"] = round(
                orta * float(usd_tl),
                2,
            )
        else:
            item["three_month_tl"] = None

        veriler.append(item)

    sonuc = {
        "tarih": tarih or "-",
        "veriler": veriler,
        "cekilme": simdi.strftime(
            "%d.%m.%Y %H:%M:%S"
        ),
        "kaynak": kaynak,
        "usd_tl": usd_tl,
    }

    try:
        lme_gecmisi_kaydet(veriler, sonuc["tarih"])
    except Exception as exc:
        print(f"LME GEÇMİŞ KAYIT HATASI: {type(exc).__name__}: {exc}")

    lme_gecmisi_doldurmayi_baslat()

    _LME_CACHE = {
        "tarih": sonuc["tarih"],
        "veriler": sonuc["veriler"],
        "cekilme": simdi,
        "kaynak": sonuc["kaynak"],
        "usd_tl": sonuc["usd_tl"],
    }

    return sonuc


DEFAULT_ADS = {
    "left_top": {
        "title": "Sol Üst Reklam",
        "image_url": "",
        "target_url": "#",
        "active": True,
    },
    "left_middle": {
        "title": "Sol Orta Reklam",
        "image_url": "",
        "target_url": "#",
        "active": True,
    },
    "left_bottom": {
        "title": "Sol Alt Reklam",
        "image_url": "",
        "target_url": "#",
        "active": True,
    },
    "right_top": {
        "title": "Sağ Üst Reklam",
        "image_url": "",
        "target_url": "#",
        "active": True,
    },
    "right_middle": {
        "title": "Sağ Orta Reklam",
        "image_url": "",
        "target_url": "#",
        "active": True,
    },
    "right_bottom": {
        "title": "Sağ Alt Reklam",
        "image_url": "",
        "target_url": "#",
        "active": True,
    },
}


# =========================================================
# YARDIMCI FONKSİYONLAR
# =========================================================

def site_base_url(request):
    """Render TLS'i proxy'de sonlandırdığı için gerçek adres https olur."""
    base = str(request.base_url).rstrip("/")
    host = request.url.hostname or ""

    if host not in ("localhost", "127.0.0.1") and base.startswith("http://"):
        base = "https://" + base[len("http://"):]

    return base


def esc(value):
    return html_lib.escape(
        str(value or ""),
        quote=True,
    )


def firma_scraperini_bul(
    firma_id,
    data=None,
):

    for kayit_id, fonksiyon in TUMU:
        if kayit_id == firma_id:
            return fonksiyon

    if data is None:
        data = load_data()

    firma = data.get(
        "firms",
        {},
    ).get(
        firma_id
    )

    if not firma:
        return None

    url = str(
        firma.get(
            "url",
            "",
        )
        or ""
    ).strip()

    baslik = str(
        firma.get(
            "baslik",
            firma_id,
        )
        or firma_id
    ).strip()

    otomatik = bool(
        firma.get(
            "otomatik",
            False,
        )
    )

    if not otomatik or not url:
        return None

    def _ozel_url_cek():
        return generic_url_cek(
            firma_id=firma_id,
            baslik=baslik,
            url=url,
        )

    return _ozel_url_cek


def firmalari_sirala(data):

    firmalar = list(
        data.get(
            "firms",
            {},
        ).values()
    )

    def sira_degeri(firma):

        try:
            return int(
                firma.get(
                    "sira",
                    999999,
                )
            )
        except Exception:
            return 999999

    firmalar.sort(
        key=lambda firma: (
            sira_degeri(firma),
            str(
                firma.get(
                    "firma_id",
                    "",
                )
            ).lower(),
        )
    )

    return firmalar


def firma_siralarini_duzelt(data):
    """
    Mevcut sıralamayı korur.
    Sadece sırası bulunmayan/geçersiz kayıtları mevcut listenin sonuna yerleştirir.
    Kullanıcının elle verdiği sıra numarasını yeniden yazmaz.
    """
    firmalar = list(
        data.get(
            "firms",
            {},
        ).values()
    )

    if not firmalar:
        return data

    try:
        firmalar.sort(
            key=lambda firma: (
                int(firma.get("sira")),
                str(firma.get("firma_id", "")).lower(),
            )
        )
    except Exception:
        firmalar = firmalari_sirala(data)

    sonraki_sira = len(firmalar)

    for firma in firmalar:
        firma_id = firma.get("firma_id")
        if firma_id not in data.get("firms", {}):
            continue

        try:
            int(firma.get("sira"))
        except (TypeError, ValueError):
            data["firms"][firma_id]["sira"] = sonraki_sira
            sonraki_sira += 1

    return data


def firma_sirasini_uygula(data, firma_id, istenen_sira):
    """
    Admin panelindeki 'Ana Sayfa Sıra Numarası' alanını gerçek bir
    konumlandırma komutu olarak uygular.

    Örn. 5 firmada bir firmaya 2 yazılırsa firma 2. sıraya gelir;
    diğer firmalar otomatik olarak bir basamak aşağı kayar.
    """
    firmalar = firmalari_sirala(data)

    hedef = None

    for firma in firmalar:
        if firma.get("firma_id") == firma_id:
            hedef = firma
            break

    if hedef is None:
        return data

    try:
        hedef_index = max(
            0,
            int(istenen_sira) - 1,
        )
    except (TypeError, ValueError):
        hedef_index = 0

    firmalar = [
        firma
        for firma in firmalar
        if firma.get("firma_id") != firma_id
    ]

    hedef_index = min(
        hedef_index,
        len(firmalar),
    )

    firmalar.insert(
        hedef_index,
        hedef,
    )

    for index, firma in enumerate(firmalar):
        kayit_id = firma.get("firma_id")

        if kayit_id in data.get("firms", {}):
            data["firms"][kayit_id]["sira"] = index

    return data


def parse_datetime(value):

    if not value:
        return None

    try:

        dt = datetime.fromisoformat(
            str(value)
        )

        if dt.tzinfo is not None:

            dt = dt.astimezone(
                ISTANBUL
            ).replace(
                tzinfo=None
            )

        return dt

    except Exception:

        return None


def firma_stale_mi(firma):

    if not firma:
        return True

    son_cekim = parse_datetime(
        firma.get(
            "son_basarili_cekme"
        )
    )

    if not son_cekim:
        return True

    simdi = now_istanbul().replace(
        tzinfo=None
    )

    dakika = (
        simdi - son_cekim
    ).total_seconds() / 60

    return dakika > STALE_MINUTES

def fiyat_tarih_yaz(value):

    if not value:
        return "-"

    try:

        dt = datetime.fromisoformat(
            str(value)
        )

        return dt.strftime(
            "%d.%m.%Y"
        )

    except Exception:

        return str(value)


def fiyat_format(fiyat):

    if fiyat is None:
        return "-"

    try:

        return (
            f"{int(fiyat):,}".replace(
                ",",
                ".",
            )
            + " TL/Ton"
        )

    except Exception:

        return str(fiyat)


def kalem_kanonik_adi(value):
    """Gösterim/karşılaştırma için kalem adını tek biçime getirir."""
    return kalem_adi_temizle(value)


def gecmis_indeksi(data):
    """(firma, kalem) -> zamana göre sıralı [(zaman, fiyat)]. Tek geçişte kurulur."""
    indeks = {}

    for item in data.get("history", []):
        try:
            zaman = datetime.fromisoformat(
                str(item.get("tarih", "")).replace("Z", "+00:00")
            )
            zaman = (
                zaman.replace(tzinfo=ISTANBUL)
                if zaman.tzinfo is None
                else zaman.astimezone(ISTANBUL)
            )
            deger = float(item.get("fiyat"))
        except (TypeError, ValueError):
            continue

        anahtar = (
            str(item.get("firma_id", "")).strip().casefold(),
            kalem_kanonik_adi(item.get("kalem", "")).casefold(),
        )
        indeks.setdefault(anahtar, []).append((zaman, deger))

    for kayitlar in indeks.values():
        kayitlar.sort(key=lambda pair: pair[0])

    return indeks


def son_fiyat_degisim_detay(
    data,
    firma_id,
    kalem,
    fiyat,
    indeks=None,
):
    """
    Kayıtlı geçmişe göre mevcut fiyatın son gerçek değişimini bulur.

    Aynı fiyatın tekrarları atlanır; mevcut fiyata geçişten önceki farklı
    fiyatla kıyaslanır. Dönüş: (fark, değişim_zamanı) ya da None.
    """
    if indeks is None:
        indeks = gecmis_indeksi(data)

    kayitlar = indeks.get(
        (
            str(firma_id or "").strip().casefold(),
            kalem_kanonik_adi(kalem).casefold(),
        ),
        [],
    )

    if not kayitlar:
        return None

    try:
        mevcut_fiyat = float(fiyat)
    except (TypeError, ValueError):
        return None

    son_ayni_index = None

    for index in range(len(kayitlar) - 1, -1, -1):
        if kayitlar[index][1] == mevcut_fiyat:
            son_ayni_index = index
            break

    if son_ayni_index is None:
        return None

    # Mevcut fiyat döneminin başlangıcı: art arda aynı fiyatlı kayıtların ilki.
    donem_basi = son_ayni_index
    while donem_basi > 0 and kayitlar[donem_basi - 1][1] == mevcut_fiyat:
        donem_basi -= 1

    if donem_basi == 0:
        return None

    fark = mevcut_fiyat - kayitlar[donem_basi - 1][1]

    if fark == 0:
        return None

    return fark, kayitlar[donem_basi][0]


def _fark_metni(fark):
    fark_int = int(fark)
    metin = f"{abs(fark_int):,}".replace(",", ".") + " TL"
    return ("+" if fark_int > 0 else "-") + metin


def son_fiyat_degisim(
    data,
    firma_id,
    kalem,
    fiyat,
    indeks=None,
):
    """Yalnızca son 24 saat içindeki gerçek değişimi metin olarak döndürür."""
    detay = son_fiyat_degisim_detay(data, firma_id, kalem, fiyat, indeks)

    if not detay:
        return ""

    fark, zaman = detay
    saniye = (now_istanbul() - zaman).total_seconds()

    if not (0 <= saniye <= 24 * 60 * 60):
        return ""

    return _fark_metni(fark)


# ADMİN GİRİŞİ
# =========================================================

ADMIN_COOKIE = "hurda_admin"


def istemci_ip(request):
    ileri = request.headers.get("x-forwarded-for", "")
    if ileri:
        return ileri.split(",")[0].strip()[:64]
    return request.client.host if request.client else "?"


def verify_admin(request: Request):
    """
    Admin doğrulaması: 1) oturum çerezi, 2) Basic Authorization başlığı.
    Tarayıcı (HTML GET) giriş yapmamışsa giriş sayfasına yönlendirilir.
    """
    data = load_data()

    token = request.cookies.get(ADMIN_COOKIE, "")
    if token and adminauth.oturum_gecerli_mi(data, token, ADMIN_USER):
        return ADMIN_USER

    ip = istemci_ip(request)
    baslik = request.headers.get("authorization", "")

    if baslik.lower().startswith("basic "):
        if adminauth.engelli_mi(ip):
            raise HTTPException(
                status_code=429,
                detail="Çok fazla hatalı deneme. Biraz bekleyin.",
            )
        try:
            kullanici, _, sifre = (
                base64.b64decode(baslik[6:]).decode("utf-8").partition(":")
            )
        except Exception:
            kullanici, sifre = "", ""

        if adminauth.sifre_dogru_mu(data, kullanici, sifre, ADMIN_USER, ADMIN_PASS):
            adminauth.basari_sifirla(ip)
            return kullanici

        adminauth.hata_kaydet(ip)

    if (
        request.method == "GET"
        and "text/html" in request.headers.get("accept", "")
    ):
        hedef = request.url.path + (
            "?" + request.url.query if request.url.query else ""
        )
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Giriş gerekli.",
            headers={
                "Location": "/admin/giris?next=" + urllib.parse.quote(hedef, safe="")
            },
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Yetkisiz erişim!",
        headers={"WWW-Authenticate": "Basic"},
    )


# =========================================================
# REKLAMLAR / BANNERLAR
# =========================================================

def normalize_ads(data):

    result = {}

    if not isinstance(
        data,
        dict,
    ):

        data = {}

    for key, default in DEFAULT_ADS.items():

        item = data.get(
            key,
            {},
        )

        if not isinstance(
            item,
            dict,
        ):

            item = {}

        result[key] = {
            "title": item.get(
                "title",
                default["title"],
            ),
            "image_url": item.get(
                "image_url",
                default["image_url"],
            ),
            "target_url": item.get(
                "target_url",
                default["target_url"],
            ),
            "active": bool(
                item.get(
                    "active",
                    default["active"],
                )
            ),
        }

    return result


def load_ads():

    global _ADS_SUPABASE_SYNCED

    if not _ADS_SUPABASE_SYNCED:
        _ADS_SUPABASE_SYNCED = True

        remote = supabase_storage_download(
            "ads.json"
        )

        if remote is not None:
            try:
                remote_data = json.loads(
                    remote.decode("utf-8")
                )

                if isinstance(remote_data, dict):
                    temporary = (
                        ADS_FILE
                        + ".supabase.tmp"
                    )

                    with open(
                        temporary,
                        "w",
                        encoding="utf-8",
                    ) as file:
                        json.dump(
                            remote_data,
                            file,
                            ensure_ascii=False,
                            indent=4,
                        )

                    os.replace(
                        temporary,
                        ADS_FILE,
                    )

            except Exception as exc:
                print(
                    "SUPABASE REKLAM VERİSİ HATASI: "
                    f"{type(exc).__name__}: {exc}"
                )

        elif os.path.exists(ADS_FILE):
            # İlk Supabase çalıştırmasında repodaki mevcut reklamları
            # kalıcı depoya seed et.
            supabase_upload_json(
                ADS_FILE,
                "ads.json",
            )

            try:
                with open(
                    ADS_FILE,
                    "r",
                    encoding="utf-8",
                ) as file:
                    seed_ads = normalize_ads(
                        json.load(file)
                    )

                for item in seed_ads.values():
                    image_url = str(
                        item.get("image_url", "")
                        if isinstance(item, dict)
                        else ""
                    ).strip()

                    if not image_url.startswith("/static/ads/"):
                        continue

                    filename = os.path.basename(image_url)
                    local_image = os.path.join(
                        ADS_UPLOAD_DIR,
                        filename,
                    )

                    if os.path.exists(local_image):
                        supabase_storage_upload(
                            local_image,
                            f"ads/{filename}",
                        )

            except Exception as exc:
                print(
                    "SUPABASE REKLAM SEED HATASI: "
                    f"{type(exc).__name__}: {exc}"
                )

    if not os.path.exists(
        ADS_FILE
    ):

        save_ads(
            DEFAULT_ADS
        )

        return normalize_ads(
            DEFAULT_ADS
        )

    try:

        with open(
            ADS_FILE,
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(
                file
            )

        if not isinstance(
            data,
            dict,
        ):

            return normalize_ads(
                DEFAULT_ADS
            )

        data = normalize_ads(
            data
        )

        # Supabase private bucket'taki reklam görsellerini
        # yerel statik klasöre geri getir.
        for item in data.values():
            image_url = str(
                item.get("image_url", "")
                if isinstance(item, dict)
                else ""
            ).strip()

            if not image_url.startswith("/static/ads/"):
                continue

            filename = os.path.basename(image_url)
            local_image = os.path.join(
                ADS_UPLOAD_DIR,
                filename,
            )

            if not os.path.exists(local_image):
                supabase_restore_file(
                    local_image,
                    f"ads/{filename}",
                )

        return data

    except Exception:

        return normalize_ads(
            DEFAULT_ADS
        )


def save_ads(data):

    data = normalize_ads(
        data
    )

    temporary = (
        ADS_FILE
        + ".tmp"
    )

    with open(
        temporary,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=4,
        )

    os.replace(
        temporary,
        ADS_FILE,
    )

    # Reklam ayarlarını ve kullanılan görselleri kalıcı depoya gönder.
    if supabase_upload_json(
        ADS_FILE,
        "ads.json",
    ):

        for item in data.values():
            image_url = str(
                item.get("image_url", "")
                if isinstance(item, dict)
                else ""
            ).strip()

            if not image_url.startswith("/static/ads/"):
                continue

            filename = os.path.basename(image_url)
            local_image = os.path.join(
                ADS_UPLOAD_DIR,
                filename,
            )

            if os.path.exists(local_image):
                supabase_storage_upload(
                    local_image,
                    f"ads/{filename}",
                )

def ad_html(ad):

    if not ad:
        return ""

    if not ad.get(
        "active"
    ):

        return ""

    title = esc(
        ad.get(
            "title"
        )
    )

    image_url = esc(
        ad.get(
            "image_url"
        )
    )

    target_url = esc(
        ad.get(
            "target_url"
        )
    )

    if not image_url:
        return ""

    return f"""
    <a
        href="{target_url or '#'}"
        target="_blank"
        rel="noopener noreferrer"
        title="{title}"
        class="block w-full"
    >
        <img
            src="{image_url}"
            alt="{title}"
            loading="lazy"
            class="block w-full h-auto object-contain rounded-2xl shadow-sm"
        >
    </a>
    """


def mobile_ad_html(ad, slot_title):
    """
    Mobilde 6 reklam slotunu korur.
    Görseli olmayan yönetim paneli slotları da yer tutucu olarak görünür.
    """

    icerik = ad_html(ad)

    if icerik.strip():
        return (
            '<div class="mobile-ad-slot ad-box rounded-2xl overflow-hidden">'
            + icerik
            + "</div>"
        )

    return (
        '<div class="mobile-ad-slot ad-box rounded-2xl overflow-hidden '
        'border border-dashed border-slate-300 bg-slate-50 '
        'flex items-center justify-center min-h-[120px]">'
        '<span class="text-[9px] font-black uppercase tracking-[0.12em] '
        'text-slate-400">'
        + esc(slot_title)
        + "</span>"
        "</div>"
    )


# =========================================================
# FİYAT VERİLERİ
# =========================================================

GUNCEL_VERILER = []

SON_GUNCELLEME = "Henüz yapılmadı"


def firma_verisini_cek(
    fonksiyon
):
    return firma_sonucunu_isle(fonksiyon())


def firma_sonucunu_isle(
    sonuc
):

    # Kaynak boş/eksik cevap döndürürse mevcut son kayıt korunur.
    # Böylece başarısız veya geçici boş cevaplar eski fiyatları silmez.
    if not sonuc.kalemler:
        raise RuntimeError(
            "Kaynak fiyat döndürmedi; mevcut son kayıt korundu."
        )

    data = load_data()

    if "firms" not in data:
        data["firms"] = {}

    firma = data[
        "firms"
    ].get(
        sonuc.firma_id
    )

    if not firma:

        mevcut_firma_sayisi = len(
            data[
                "firms"
            ]
        )

        firma = {
            "firma_id": sonuc.firma_id,
            "baslik": sonuc.baslik,
            "url": sonuc.url,
            "otomatik": True,
            "aktif": True,
            "son_basarili_cekme": None,
            "kaynak_fiyat_tarihi": None,
            "durum": "bekliyor",
            "sira": mevcut_firma_sayisi,
        }

        data[
            "firms"
        ][
            sonuc.firma_id
        ] = firma

    kalemler = []

    fiyat_tarihi = (
        sonuc.fiyat_tarihi.isoformat()
        if sonuc.fiyat_tarihi
        else None
    )

    gizlenen_kalemler = {
        " ".join(
            str(x or "").strip().split()
        ).casefold()
        for x in data.get(
            "gizlenen_kalemler",
            {}
        ).get(
            sonuc.firma_id,
            []
        )
    }

    kaydedilecek = []
    gorunen = []

    for kalem in sonuc.kalemler:

        kalem_anahtari = (
            " ".join(
                str(kalem.cins or "").strip().split()
            ).casefold()
        )

        if kalem_anahtari in gizlenen_kalemler:
            continue

        kaydedilecek.append(
            (kalem.cins, kalem.fiyat, kalem.eski_fiyat)
        )
        gorunen.append(kalem)

    # Tüm kalemler tek load/save ile yazılır; geçmişe yalnızca
    # gerçekten değişen fiyatlar eklenir.
    onceki_fiyatlar = fiyatlari_toplu_kaydet(
        sonuc.firma_id,
        kaydedilecek,
        fiyat_tarihi,
    )

    for kalem in gorunen:

        eski = (
            kalem.eski_fiyat
            if kalem.eski_fiyat is not None
            else onceki_fiyatlar.get(kalem.cins)
        )

        degisim = ""

        if eski is not None:

            fark = kalem.fiyat - eski

            if fark > 0:
                degisim = "+" + f"{fark:,}".replace(",", ".") + " TL"
            elif fark < 0:
                degisim = f"{fark:,}".replace(",", ".") + " TL"
            else:
                degisim = "0 TL"

        kalemler.append(
            {
                "cins": kalem.cins,
                "fiyat": fiyat_format(
                    kalem.fiyat
                ),
                "degisim": degisim,
            }
        )

    data = load_data()

    if "firms" not in data:
        data["firms"] = {}

    if (
        sonuc.firma_id
        not in data["firms"]
    ):

        mevcut_firma_sayisi = len(
            data[
                "firms"
            ]
        )

        data[
            "firms"
        ][
            sonuc.firma_id
        ] = {
            "firma_id": sonuc.firma_id,
            "baslik": sonuc.baslik,
            "url": sonuc.url,
            "otomatik": True,
            "aktif": True,
            "son_basarili_cekme": None,
            "kaynak_fiyat_tarihi": None,
            "durum": "bekliyor",
            "sira": mevcut_firma_sayisi,
        }

    if "sira" not in data[
        "firms"
    ][
        sonuc.firma_id
    ]:

        data[
            "firms"
        ][
            sonuc.firma_id
        ][
            "sira"
        ] = len(
            data[
                "firms"
            ]
        ) - 1

    data[
        "firms"
    ][
        sonuc.firma_id
    ][
        "baslik"
    ] = sonuc.baslik

    data[
        "firms"
    ][
        sonuc.firma_id
    ][
        "url"
    ] = sonuc.url or ""

    data[
        "firms"
    ][
        sonuc.firma_id
    ][
        "son_basarili_cekme"
    ] = now_istanbul_string()

    data[
        "firms"
    ][
        sonuc.firma_id
    ][
        "kaynak_fiyat_tarihi"
    ] = fiyat_tarihi

    data[
        "firms"
    ][
        sonuc.firma_id
    ][
        "durum"
    ] = "basarili"

    # Bu fonksiyonun kendi kapsamında firma_id değişkeni yoktur.
    # Sıralama için scraper sonucundaki gerçek firma kimliğini kullan.
    mevcut_sira = (
        data.get("firms", {})
        .get(sonuc.firma_id, {})
        .get("sira", 0)
    )

    firma_sirasini_uygula(
        data,
        sonuc.firma_id,
        int(mevcut_sira) + 1,
    )

    save_data(
        data
    )

    return {
        "firma_id": sonuc.firma_id,
        "baslik": sonuc.baslik,
        "url": sonuc.url or "",
        "tarih": (
            sonuc.fiyat_tarihi.strftime(
                "%d.%m.%Y"
            )
            if sonuc.fiyat_tarihi
            else "-"
        ),
        "kalemler": kalemler,
    }


_GUNCELLEME_KILIDI = threading.Lock()


def firma_hata_isle(data, firma_id, mesaj):
    """Firma durumunu 'hata' yapar ve son hatayı/hata geçmişini (en fazla 10) saklar."""
    firma = data.get("firms", {}).get(firma_id)

    if not firma:
        return

    firma["durum"] = "hata"

    metin = " ".join(str(mesaj or "").split())[:300]
    simdi = now_istanbul_string()
    gecmis = firma.setdefault("hata_gecmisi", [])

    if gecmis and gecmis[-1].get("mesaj") == metin:
        gecmis[-1]["zaman"] = simdi
        gecmis[-1]["adet"] = gecmis[-1].get("adet", 1) + 1
    else:
        gecmis.append({"zaman": simdi, "mesaj": metin, "adet": 1})

    firma["hata_gecmisi"] = gecmis[-10:]
    firma["son_hata"] = firma["hata_gecmisi"][-1]


def verileri_guncelle():
    """Aynı anda yalnızca bir güncelleme turu çalışır (zamanlayıcı + admin düğmesi)."""
    if not _GUNCELLEME_KILIDI.acquire(blocking=False):
        print("Güncelleme zaten çalışıyor, atlandı.")
        return

    try:
        _verileri_guncelle_calistir()
    finally:
        _GUNCELLEME_KILIDI.release()


def _paralel_cek(isler, en_fazla=6):
    """
    [(firma_id, fonksiyon)] -> {firma_id: (sonuc, hata, saniye)}.
    Yalnızca ağ/ayrıştırma paralel çalışır; veri dosyasına yazılmaz.
    """
    def calistir(is_):
        firma_id, fonksiyon = is_
        baslangic = time.perf_counter()
        try:
            return firma_id, (fonksiyon(), None, time.perf_counter() - baslangic)
        except Exception as exc:
            return firma_id, (None, exc, time.perf_counter() - baslangic)

    if not isler:
        return {}

    with ThreadPoolExecutor(max_workers=min(en_fazla, len(isler))) as havuz:
        return dict(havuz.map(calistir, isler))


def _firma_hatasi_isle(firma_id, e):
    print(f"HATA: {firma_id} -> {type(e).__name__}: {e}")

    try:
        data = load_data()

        if firma_id in data.get("firms", {}):
            firma_hata_isle(data, firma_id, e)
            save_data(data)

        bildirim_ekle(
            firma_id=firma_id,
            tur="scraper_hatasi",
            mesaj=str(e),
        )

    except Exception as bildirim_hatasi:
        print(f"BİLDİRİM HATASI: {bildirim_hatasi}")


def _verileri_guncelle_calistir():

    tur_baslangic = time.perf_counter()

    global GUNCEL_VERILER
    global SON_GUNCELLEME

    print()

    print(
        f"[{now_istanbul().strftime('%d.%m.%Y %H:%M:%S')}] "
        "Fiyatlar güncelleniyor..."
    )

    data_baslangic = load_data()

    kaynaklar = list(
        TUMU
    )

    tanimli_idler = {
        firma_id
        for firma_id, _ in kaynaklar
    }

    for firma in firmalari_sirala(
        data_baslangic
    ):

        firma_id = firma.get(
            "firma_id"
        )

        if not firma_id:
            continue

        if firma_id in tanimli_idler:
            continue

        if not firma.get(
            "aktif",
            True,
        ):
            continue

        if firma_id in data_baslangic.get(
            "silinen_firmalar",
            [],
        ):
            continue

        if not firma.get(
            "otomatik",
            False,
        ):
            continue

        if not str(
            firma.get(
                "url",
                "",
            )
            or ""
        ).strip():
            continue

        kaynaklar.append(
            (
                firma_id,
                None,
            )
        )

    # 1. aşama (hızlı, sıralı): hangi firmalar çekilecek?
    uygunlar = []

    for firma_id, kayitli_fonksiyon in kaynaklar:

        try:
            data = load_data()

            if firma_id in data.get("silinen_firmalar", []):
                print(f"SİLİNMİŞ: {firma_id}")
                continue

            firma = data.get("firms", {}).get(firma_id)

            if firma and not firma.get("aktif", True):
                print(f"PASİF: {firma_id}")
                continue

            if firma and not firma.get("otomatik", True):
                print(f"MANUEL: {firma_id}")
                continue

            fonksiyon = firma_scraperini_bul(firma_id, data)

            if fonksiyon is None:
                print(f"OTOMATİK KAYNAK YOK: {firma_id}")
                continue

            uygunlar.append((firma_id, fonksiyon))

        except Exception as e:
            _firma_hatasi_isle(firma_id, e)

    # 2. aşama (paralel): ağ istekleri aynı anda yapılır.
    sonuclar = _paralel_cek(uygunlar)

    # 3. aşama (sıralı): sonuçlar tek tek işlenir; veri dosyasına yazma çakışmaz.
    for firma_id, _ in uygunlar:

        ham, hata, sure = sonuclar[firma_id]

        try:
            if hata is not None:
                raise hata

            sonuc = firma_sonucunu_isle(ham)

            print(
                f"OK: {sonuc['baslik']} "
                f"({len(sonuc['kalemler'])} kalem) [{sure:.1f} sn]"
            )

            # Başarılı güncelleme bildirimi yalnızca gerçekten fiyat
            # değişikliği olduğunda oluşturulur.
            fiyat_degisti = any(
                str(kalem.get("degisim", "")).strip() not in {"", "0 TL"}
                for kalem in sonuc.get("kalemler", [])
            )

            if fiyat_degisti:
                bildirim_ekle(
                    firma_id,
                    "basarili_guncelleme",
                    (
                        f"{sonuc['baslik']} fiyatları değişti. "
                        f"{len(sonuc['kalemler'])} fiyat kalemi güncellendi."
                    ),
                )

        except Exception as e:
            print(f"[{sure:.1f} sn]", end=" ")
            _firma_hatasi_isle(firma_id, e)

    SON_GUNCELLEME = (
        now_istanbul().strftime(
            "%d.%m.%Y %H:%M"
        )
    )

    GUNCEL_VERILER = []

    try:
        push_alarmlarini_kontrol()
    except Exception as exc:
        print(f"PUSH KONTROL HATASI: {type(exc).__name__}: {exc}")

    # Tarama sırasında oluşan geçici Python nesnelerini
    # mümkün olduğunca hemen temizle.
    gc.collect()

    print(
        f"Güncelleme tamamlandı. (toplam {time.perf_counter() - tur_baslangic:.1f} sn)"
    )


# =========================================================
# ANASAYFA FİYAT VERİLERİ
# =========================================================

def en_yuksek_isaretle(sonuc):
    """
    Aynı cinste (örn. DKP) birden fazla fabrikanın güncel fiyatı varsa en
    yükseğini 'en_yuksek' işaretler. Fiyatı 3 günden eski fabrikalar ve
    hepsinin eşit olduğu gruplar dışarıda bırakılır.
    """
    gruplar = {}

    for firma in sonuc:
        yas = firma.get("fiyat_yasi_gun")
        firma["en_yuksek_sayisi"] = 0

        if yas is not None and yas > 3:
            continue

        for kalem in firma.get("kalemler", []):
            deger = (
                kalem.get("manuel_fiyat")
                if kalem.get("manuel_fiyat") is not None
                else kalem.get("otomatik_fiyat")
            )
            anahtar = re.sub(
                r"[^a-z0-9]", "", storage_module._tr_anahtar(kalem.get("cins"))
            )

            if deger is None or not anahtar:
                continue

            gruplar.setdefault(anahtar, []).append(
                (float(deger), firma, kalem)
            )

    for uyeler in gruplar.values():
        if len({id(f) for _, f, _ in uyeler}) < 2:
            continue

        fiyatlar = [d for d, _, _ in uyeler]

        if max(fiyatlar) == min(fiyatlar):
            continue

        for deger, firma, kalem in uyeler:
            if deger == max(fiyatlar):
                kalem["en_yuksek"] = True
                firma["en_yuksek_sayisi"] += 1


def fiyat_verilerini_olustur():

    data = load_data()
    gecmis_idx = gecmis_indeksi(data)

    sonuc = []

    firmalar = firmalari_sirala(
        data
    )

    # Fiyat kaydı mevcut olup firma kaydı eksikse,
    # son bilinen fiyatları ana sayfada kaybetme.
    firma_ids = {
        str(
            firma.get(
                "firma_id",
                ""
            )
        ).strip().lower()
        for firma in firmalar
    }

    for fiyat_firma_id in data.get(
        "prices",
        {}
    ):
        canonical_id = str(
            fiyat_firma_id or ""
        ).strip().lower()

        if (
            canonical_id
            and canonical_id not in firma_ids
        ):
            firmalar.append(
                {                    "firma_id": canonical_id,
                    "baslik": canonical_id.replace(                        "_",
                        " "
                    ).title(),
                    "url": "",
                    "otomatik": False,
                    "aktif": True,
                    "son_basarili_cekme": None,
                    "kaynak_fiyat_tarihi": None,
                    "durum": "manuel",
                    "sira": 999999,
                }
            )
            firma_ids.add(canonical_id)

    for firma in firmalar:

        firma_id = firma.get(
            "firma_id"
        )

        if not firma_id:
            continue

        if not firma.get(
            "aktif",
            True,
        ):

            continue

        fiyatlar = data.get(
            "prices",
            {},
        ).get(
            firma_id,
            {},
        )

        if not fiyatlar:
            continue

        stale = firma_stale_mi(
            firma
        )

        # Manuel yönetilen firmalar bayat sayılmaz; otomatik firmalar
        # son başarılı çekimden bu yana STALE_MINUTES geçtiyse bayat gösterilir.
        if not firma.get("otomatik", True):
            stale = False

        firma_kalemleri = []

        # Kaynakta gelen / kayıtlı kalem sırasını koru.
        # Eski kayıtlarda aynı kalem iki kez veya tekrarlı adla tutulmuşsa
        # yalnızca ekranda tek, kanonik ad gösterilir. Kalıcı veri silinmez.
        sirali_fiyatlar = []
        gorulen_kalemler = {}

        for kayit_kalemi, bilgi in fiyatlar.items():
            kanonik_kalem = kalem_kanonik_adi(kayit_kalemi)

            if not kanonik_kalem:
                continue

            anahtar = kanonik_kalem.casefold()

            if anahtar in gorulen_kalemler:
                onceki_index, onceki_kalem = gorulen_kalemler[anahtar]

                # Temiz/kanonik kayıt varsa onu tercih et.
                if (
                    str(kayit_kalemi).strip().casefold()
                    == kanonik_kalem.casefold()
                    and str(onceki_kalem).strip().casefold()
                    != kanonik_kalem.casefold()
                ):
                    sirali_fiyatlar[onceki_index] = (
                        kanonik_kalem,
                        bilgi,
                    )
                    gorulen_kalemler[anahtar] = (
                        onceki_index,
                        kanonik_kalem,
                    )

                continue

            gorulen_kalemler[anahtar] = (
                len(sirali_fiyatlar),
                kayit_kalemi,
            )
            sirali_fiyatlar.append(
                (
                    kanonik_kalem,
                    bilgi,
                )
            )

        for kalem, bilgi in sirali_fiyatlar:

            manuel = bilgi.get(
                "manuel_fiyat"
            )

            otomatik = bilgi.get(
                "otomatik_fiyat"
            )

            if manuel is not None:

                kullanilan = manuel
                kaynak_tipi = "manuel"

            elif otomatik is not None:

                kullanilan = otomatik

                kaynak_tipi = (
                    "stale"
                    if stale
                    else "current"
                )

            else:

                continue

            degisim = son_fiyat_degisim(
                data,
                firma_id,
                kalem,
                kullanilan,
                gecmis_idx,
            )

            # Süreden bağımsız, kayıtlı eski fiyata göre son değişim.
            onceki_degisim = ""
            degisim_tarihi = ""
            detay = son_fiyat_degisim_detay(
                data,
                firma_id,
                kalem,
                kullanilan,
                gecmis_idx,
            )

            # Kaynağın kendi yayınladığı önceki fiyat (Erdemir/İsdemir):
            # güncel fiyatla farkı en yeni duyurulan değişimdir.
            kaynak_fark = ""
            if (
                manuel is None
                and otomatik is not None
                and bilgi.get("kaynak_eski_fiyat") not in (None, otomatik)
            ):
                kaynak_fark = _fark_metni(
                    otomatik - bilgi["kaynak_eski_fiyat"]
                )

            if detay:
                onceki_degisim = _fark_metni(detay[0])
                degisim_tarihi = detay[1].strftime("%d.%m.%Y %H:%M")
            elif kaynak_fark:
                onceki_degisim = kaynak_fark
                degisim_tarihi = fiyat_tarih_yaz(
                    bilgi.get("fiyat_tarihi")
                    or firma.get("kaynak_fiyat_tarihi")
                )

            # Tüm fabrikalar için aynı kural: 24 saatlik değişim yoksa en son
            # bilinen değişim (kayıtlı geçmiş ya da kaynağın duyurduğu önceki
            # fiyat) sayılır. Böylece kartlar, sayaçlar ve panel aynı veriyi
            # gösterir; değişimin tarihi panelde yazar.
            if not degisim and onceki_degisim:
                degisim = onceki_degisim

            # Dünün kapanışına göre fark: bugün 00:00'dan önceki son kayıtlı fiyat.
            kayitlar_dun = gecmis_idx.get(
                (
                    str(firma_id or "").strip().casefold(),
                    kalem_kanonik_adi(kalem).casefold(),
                ),
                [],
            )
            bugun_basi = now_istanbul().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            dun_fiyat = None
            for zaman_k, deger_k in kayitlar_dun:
                if zaman_k < bugun_basi:
                    dun_fiyat = deger_k
            try:
                dun_fark = (
                    int(float(kullanilan) - float(dun_fiyat))
                    if dun_fiyat is not None
                    else None
                )
            except (TypeError, ValueError):
                dun_fark = None

            # Son 30 günlük fiyat çizgisi (yalnızca değişim varsa).
            trend = []
            try:
                sinir = now_istanbul() - timedelta(days=30)
                noktalar = [(z, d) for z, d in kayitlar_dun if z >= sinir]
                oncesi = [d for z, d in kayitlar_dun if z < sinir]
                if oncesi:
                    noktalar.insert(0, (sinir, oncesi[-1]))
                noktalar.append((now_istanbul(), float(kullanilan)))
                if len({round(d) for _, d in noktalar}) >= 2:
                    trend = [
                        [int(z.timestamp()), int(round(d))]
                        for z, d in noktalar[-40:]
                    ]
            except (TypeError, ValueError):
                trend = []

            firma_kalemleri.append(
                {
                    "cins": kalem,
                    "trend": trend,
                    "en_yuksek": False,
                    "dun_fiyat": (
                        int(dun_fiyat) if dun_fiyat is not None else None
                    ),
                    "dun_fark": dun_fark,
                    "fiyat": fiyat_format(
                        kullanilan
                    ),
                    "degisim": degisim,
                    "onceki_degisim": onceki_degisim,
                    "degisim_tarihi": degisim_tarihi,
                    "durum": kaynak_tipi,
                    "otomatik_fiyat": otomatik,
                    "manuel_fiyat": manuel,
                    "fiyat_tarihi": (
                        (
                            str(bilgi.get("guncelleme") or "")[:10]
                            or now_istanbul().strftime("%Y-%m-%d")
                        )
                        if manuel is not None
                        else (
                            bilgi.get(
                                "fiyat_tarihi"
                            )
                            or firma.get(
                                "kaynak_fiyat_tarihi"
                            )
                        )
                    ),
                }
            )

        if not firma_kalemleri:
            continue

        # Kaynağın yayınladığı en yeni fiyat tarihine göre fiyat yaşı (gün).
        fiyat_yasi_gun = None
        kaynak_tarihleri = [
            k.get("fiyat_tarihi")
            for k in firma_kalemleri
            if k.get("fiyat_tarihi") and k.get("durum") != "manuel"
        ]
        if kaynak_tarihleri:
            try:
                en_yeni = datetime.fromisoformat(
                    str(max(kaynak_tarihleri))
                ).date()
                fiyat_yasi_gun = max(
                    0, (now_istanbul().date() - en_yeni).days
                )
            except ValueError:
                fiyat_yasi_gun = None

        sonuc.append(
            {
                "firma_id": firma_id,
                "fiyat_yasi_gun": fiyat_yasi_gun,
                "baslik": firma.get(
                    "baslik",
                    firma_id,
                ),
                "url": firma.get(
                    "url",
                    "",
                ),
                "tarih": fiyat_tarih_yaz(
                    max(
                        [
                            k.get(
                                "fiyat_tarihi"
                            )
                            for k in firma_kalemleri
                            if k.get(
                                "fiyat_tarihi"
                            )
                        ],
                        default=firma.get(
                            "kaynak_fiyat_tarihi"
                        ),
                    )
                ),
                "son_kontrol": firma.get(
                    "son_basarili_cekme"
                ),
                "son_24_saatte_guncellendi": son_24_saatte_mi(
                    firma.get("son_basarili_cekme")
                ),
                "durum": (
                    "manuel"
                    if not firma.get(
                        "otomatik",
                        True,
                    )
                    else (
                        "stale"
                        if stale
                        else "current"
                    )
                ),
                "kalemler": firma_kalemleri,
            }
        )

    en_yuksek_isaretle(sonuc)

    return sonuc


# =========================================================
# FASTAPI
# =========================================================

scheduler = BackgroundScheduler()


def kaynak_migrasyonunu_uygula():
    """
    Kaynak değişikliklerini canlı kalıcı veriye bir kez uygular.

    - Eski Çolakoğlu kayıtlarını tamamen temizler.
    - Eski Ekinciler kayıtlarını temizler ve aynı firmayı yeni Hammadde
      Piyasası kaynağından otomatik çekilecek şekilde yeniden oluşturur.
    - Cansan'ı yeni otomatik kaynak olarak ekler.
    """
    data = load_data()

    if data.get("kaynak_migrasyonu_20261001_v2"):
        return

    firms = data.setdefault("firms", {})
    prices = data.setdefault("prices", {})
    history = data.setdefault("history", {})
    notifications = data.setdefault("notifications", [])

    def remove_firm_records(firma_ids):
        ids = {str(item).casefold() for item in firma_ids}

        for key in list(firms.keys()):
            if str(key).casefold() in ids:
                firms.pop(key, None)

        for key in list(prices.keys()):
            if str(key).casefold() in ids:
                prices.pop(key, None)

        if isinstance(history, dict):
            for key in list(history.keys()):
                if str(key).casefold() in ids:
                    history.pop(key, None)
        elif isinstance(history, list):
            history[:] = [
                item for item in history
                if str(item.get("firma_id", "")).casefold() not in ids
            ]

        if isinstance(notifications, list):
            notifications[:] = [
                item for item in notifications
                if str(item.get("firma_id", "")).casefold() not in ids
            ]

    # Çolakoğlu: eski fiyatlar tamamen silinir; yeni kaynak ilk çekimde
    # güncel fiyatları yeniden oluşturur.
    remove_firm_records({"colakoglu"})

    # Ekinciler: eski manuel/otomatik kayıtlar tamamen silinir.
    # Firma yeniden yeni otomatik kaynak olarak aşağıda oluşturulur.
    remove_firm_records({"ekinciler", "Ekinciler"})

    # Cansan: varsa eski/yarım kayıt temizlenir ve doğru kaynakla yeniden kurulur.
    remove_firm_records({"cansan"})

    firms["ekinciler"] = {
        "firma_id": "ekinciler",
        "baslik": "Ekinciler Demir Çelik",
        "url": "https://www.hammaddepiyasasi.com/fabrika/ekinciler",
        "otomatik": True,
        "aktif": True,
        "son_basarili_cekme": None,
        "kaynak_fiyat_tarihi": None,
        "durum": "bekliyor",
        "sira": len(firms),
    }

    firms["cansan"] = {
        "firma_id": "cansan",
        "baslik": "Cansan",
        "url": "https://www.hammaddepiyasasi.com/fabrika/cansan",
        "otomatik": True,
        "aktif": True,
        "son_basarili_cekme": None,
        "kaynak_fiyat_tarihi": None,
        "durum": "bekliyor",
        "sira": len(firms),
    }

    data["kaynak_migrasyonu_20261001_v2"] = True

    save_data(data)


def veri_bakimi_uygula():
    """Açılışta: tekrarlı kalem adlarını birleştirir, geçmişi sıkıştırır."""
    data = load_data()
    onceki = len(data.get("history", []))

    degisti = veriyi_duzelt(data)

    if degisti:
        print(
            "VERİ BAKIMI: kalem adları temizlendi, geçmiş "
            f"{onceki} -> {len(data.get('history', []))} kayıt."
        )

    tamamlanan = manuel_gecmisi_tamamla(data)

    if tamamlanan:
        print(f"VERİ BAKIMI: {tamamlanan} elle fiyat geçmişe işlendi.")

    if degisti or tamamlanan:
        save_data(data)


def yedek_temizligi_uygula(bayrak="yedek_final2_20261002", etiket="final2-20261002"):
    """
    Tek seferlik: güncel durumun (veri + reklam ayarları) yedeğini alır, doğrular,
    ardından önceki tüm yedekleri siler. Başarılı olunca bayrak yazılır.
    """
    data = load_data()

    if data.get(bayrak):
        return

    if storage_module._supabase_enabled() and not storage_module._SUPABASE_DATA_SYNCED:
        print("YEDEK TEMİZLİĞİ: Supabase senkronu tamam değil, sonraki açılışta denenecek.")
        return

    try:
        ozet = storage_module.yedekleri_yenile(etiket, {"ads": ADS_FILE})
        storage_module._SUPABASE_YEDEK_SON = time.time()

        data = load_data()
        data[bayrak] = {"zaman": now_istanbul_string(), **ozet}
        save_data(data)

        print(
            "YEDEK TEMİZLİĞİ: yeni yedek alındı "
            f"({', '.join(ozet['uzak_yeni']) or 'yalnızca yerel'}); "
            f"{ozet['uzak_silinen']} uzak, {ozet['yerel_silinen']} yerel eski yedek silindi."
        )
    except Exception as exc:
        print(f"YEDEK TEMİZLİĞİ HATASI: {type(exc).__name__}: {exc}")


def colakoglu_kaynagini_duzelt():
    """
    Çolakoğlu'nun resmi sayfadan otomatik çekilmesini garanti eder.
    Firma kaydı yoksa oluşturur; manuel/pasif kalmışsa otomatiğe alır.
    Tek seferliktir (bayrak ile).
    """
    data = load_data()

    if data.get("colakoglu_otomatik_20261002"):
        return

    firms = data.setdefault("firms", {})
    firma = firms.get("colakoglu")

    if not firma:
        firma = {
            "firma_id": "colakoglu",
            "baslik": "Çolakoğlu Metalurji",
            "sira": len(firms),
            "son_basarili_cekme": None,
            "kaynak_fiyat_tarihi": None,
        }
        firms["colakoglu"] = firma

    firma["url"] = "https://www.colakoglu.com.tr/hurda"
    firma["otomatik"] = True
    firma["aktif"] = True
    firma["durum"] = "bekliyor"

    silinen = data.get("silinen_firmalar")
    if isinstance(silinen, list) and "colakoglu" in silinen:
        silinen.remove("colakoglu")

    data["colakoglu_otomatik_20261002"] = True
    save_data(data)


# Otomatik fiyat çekimi aktiftir.
# AUTO_UPDATE_ENABLED=0 verilirse tamamen kapatılabilir.
# Mevcut manuel fiyatlar fiyat_kaydet() tarafından korunur.
AUTO_UPDATE_ENABLED = (
    os.getenv(
        "AUTO_UPDATE_ENABLED",
        "1",
    ).strip().lower()
    in {"1", "true", "yes", "on"}
)


@asynccontextmanager
async def lifespan(app):

    load_ads()

    # Kaynak değişikliklerini canlı kalıcı veriye deploy sırasında bir kez uygula.
    kaynak_migrasyonunu_uygula()
    colakoglu_kaynagini_duzelt()
    veri_bakimi_uygula()
    yedek_temizligi_uygula()

    if AUTO_UPDATE_ENABLED:
        print(
            "İlk fiyat güncellemesi başlıyor..."
        )

        verileri_guncelle()

        scheduler.add_job(
            verileri_guncelle,
            "interval",
            minutes=15,
            id="fiyat_guncelleme",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
        )

        scheduler.add_job(
            gunluk_ozet_gonder,
            "cron",
            hour=8,
            minute=30,
            timezone=ISTANBUL,
            id="gunluk_ozet",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1800,
        )

        scheduler.start()
    else:
        print(
            "Otomatik fiyat güncellemesi kapalı. "
            "Mevcut fiyatlar korunuyor."
        )

    yield

    if scheduler.running:

        scheduler.shutdown(
            wait=False
        )


app = FastAPI(
    title="Hurda Fiyatları",
    lifespan=lifespan,
)


# UptimeRobot gibi izleme servisleri HEAD isteği gönderir.
# HEAD isteklerini GET gibi işleyip gövdesiz yanıt döndürür (405 yerine 200).
class HeadAsGetMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "HEAD":
            scope = dict(scope)
            scope["method"] = "GET"

            async def send_without_body(message):
                if message["type"] == "http.response.body":
                    message = {**message, "body": b""}
                await send(message)

            await self.app(scope, receive, send_without_body)
            return

        await self.app(scope, receive, send)


app.add_middleware(HeadAsGetMiddleware)


# =========================================================
# STATİK DOSYALAR
# =========================================================

app.mount(
    "/static",
    StaticFiles(
        directory=os.path.join(
            BASE_DIR,
            "static",
        )
    ),
    name="static",
)


# =========================================================
# DÖVİZ KURLARI
# =========================================================

DOVIZ_CACHE = {}
DOVIZ_SON_CEKME = None
DOVIZ_CACHE_SANIYE = 120


DOVIZ_HATA_ZAMANI = None
DOVIZ_HATA_BEKLEME_SANIYE = 60
_KUR_KAYNAK_SOGUMA = {}


def _tcmb_kurlari_cek():
    """TCMB günlük kur XML'i (Frankfurter yedeği): {'USD': {...}, 'EUR': {...}}"""
    r = requests.get(
        "https://www.tcmb.gov.tr/kurlar/today.xml",
        headers={"User-Agent": "HurdaFiyatBot/2.0"},
        timeout=8,
    )
    r.raise_for_status()
    kok = ET.fromstring(r.content)
    tarih = kok.attrib.get("Tarih", "")
    sonuc = {}

    for cur in kok.findall("Currency"):
        kod = cur.attrib.get("CurrencyCode")
        if kod not in ("USD", "EUR"):
            continue
        alis = cur.findtext("ForexBuying")
        satis = cur.findtext("ForexSelling")
        if not alis or not satis:
            continue
        sonuc[kod] = {
            "kod": kod,
            "birim": "1",
            "alis": float(alis),
            "satis": float(satis),
            "kur": float(alis),
            "kur_turu": "TCMB döviz kuru",
            "tarih": tarih,
        }

    return sonuc


class _AltinHazir(Exception):
    pass


def _kur_sayisi(deger):
    """12.345,67 / 12,5 / 12.5 / 12.5 (sayı) biçimlerini float'a çevirir."""
    if deger is None or isinstance(deger, bool):
        return None
    if isinstance(deger, (int, float)):
        return float(deger)
    metin = str(deger).strip().replace(" ", "")
    if not metin:
        return None
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", metin):
        metin = metin.replace(".", "")
    elif "," in metin and "." in metin:
        metin = metin.replace(".", "").replace(",", ".")
    elif "," in metin:
        metin = metin.replace(",", ".")
    try:
        return float(metin)
    except ValueError:
        return None


_KUR_ARALIK = {"USD": (5, 500), "EUR": (5, 600), "ALTIN": (500, 100000)}


def _kur_kaydi(kod, alis, satis, tur, tarih):
    alis = _kur_sayisi(alis)
    satis = _kur_sayisi(satis)
    if alis is None and satis is None:
        return None
    alis = alis if alis is not None else satis
    satis = satis if satis is not None else alis
    # Diğer kaynaklarla tutarlı olsun diye tek (orta) kur kullanılır.
    orta = (alis + satis) / 2
    alis = satis = orta
    alt, ust = _KUR_ARALIK[kod]
    if not (alt <= alis <= ust and alt <= satis <= ust):
        raise ValueError(f"{kod} değeri makul aralık dışında: {alis}/{satis}")
    return {
        "kod": kod,
        "birim": "1 gram" if kod == "ALTIN" else "1",
        "alis": alis,
        "satis": satis,
        "kur": alis,
        "kur_turu": tur,
        "tarih": tarih,
        "canli": True,
    }


def _truncgil_kurlari_cek():
    """Truncgil Finans (dakikalık güncellenen serbest piyasa): USD, EUR, gram altın."""
    r = requests.get(
        "https://finans.truncgil.com/v4/today.json",
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 HurdaFiyatBot/2.0"},
        timeout=4,
    )
    r.raise_for_status()
    veri = r.json()
    meta = veri.get("Meta_Data") if isinstance(veri.get("Meta_Data"), dict) else {}
    tarih = str(veri.get("Update_Date") or meta.get("Update_Date") or "")
    govde = veri.get("Rates") if isinstance(veri.get("Rates"), dict) else veri

    def kalem(*anahtarlar):
        for a in anahtarlar:
            k = govde.get(a)
            if isinstance(k, dict):
                return k
        return {}

    def al(k, *adlar):
        for a in adlar:
            if k.get(a) is not None:
                return k[a]
        return None

    sonuc = {}
    for kod, anahtarlar, tur in (
        ("USD", ("USD",), "Serbest piyasa kuru"),
        ("EUR", ("EUR",), "Serbest piyasa kuru"),
        ("ALTIN", ("GRA", "gram-altin", "GRAM"), "Gram altın (serbest piyasa)"),
    ):
        k = kalem(*anahtarlar)
        try:
            kayit = _kur_kaydi(
                kod,
                al(k, "Buying", "Alış", "Alis"),
                al(k, "Selling", "Satış", "Satis"),
                tur,
                tarih,
            )
        except ValueError as e:
            print(f"KUR DOĞRULAMA ({kod}): {e}")
            kayit = None
        if kayit:
            sonuc[kod] = kayit

    return sonuc


def _yahoo_kur_tek(kod, sembol):
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sembol}",
        params={"interval": "1m", "range": "1d"},
        headers={"User-Agent": "Mozilla/5.0 HurdaFiyatBot/2.0"},
        timeout=4,
    )
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    fiyat = meta.get("regularMarketPrice")
    zaman = meta.get("regularMarketTime")
    tarih = (
        datetime.fromtimestamp(zaman, ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d %H:%M:%S")
        if zaman else ""
    )
    return _kur_kaydi(kod, fiyat, fiyat, "Anlık piyasa kuru", tarih)


def _yahoo_kurlari_cek():
    """Yahoo Finance anlık USD/TRY ve EUR/TRY (Truncgil yedeği); her sembol bağımsız."""
    sonuc = {}
    for kod, sembol in (("USD", "USDTRY=X"), ("EUR", "EURTRY=X")):
        try:
            kayit = _yahoo_kur_tek(kod, sembol)
            if kayit:
                sonuc[kod] = kayit
        except Exception as e:
            print(f"KUR HATASI (Yahoo {kod}): {type(e).__name__}: {e}")
    return sonuc


def doviz_kurlarini_getir(force=False):

    global DOVIZ_CACHE
    global DOVIZ_SON_CEKME
    global DOVIZ_HATA_ZAMANI

    simdi = datetime.now()

    if (
        not force
        and DOVIZ_CACHE
        and DOVIZ_SON_CEKME
        and (
            simdi - DOVIZ_SON_CEKME
        ).total_seconds() < DOVIZ_CACHE_SANIYE
    ):
        return DOVIZ_CACHE

    # Kaynaklar kapalıyken her istekte 10-20 sn bekletme.
    if (
        not force
        and DOVIZ_HATA_ZAMANI
        and (simdi - DOVIZ_HATA_ZAMANI).total_seconds()
        < DOVIZ_HATA_BEKLEME_SANIYE
    ):
        return DOVIZ_CACHE or {
            "status": "error",
            "kaynak": "Frankfurter",
            "kur_turu": "Günlük referans kuru",
            "tarih": "",
            "veriler": {},
            "hata": "USD/EUR kurları şu anda alınamadı.",
        }

    API_URL = "https://api.frankfurter.dev/v2/rate"
    basliklar = {
        "Accept": "application/json",
        "User-Agent": "HurdaFiyatBot/2.0",
    }

    bulunan = {}
    kaynaklar = []

    # 0) En güncel kaynaklar: Truncgil (USD, EUR, gram altın), sonra Yahoo.
    # Çöken kaynak 5 dk boyunca atlanır; her istek zaman aşımı beklemez.
    def _sogumada(ad):
        return time.time() < _KUR_KAYNAK_SOGUMA.get(ad, 0)

    if not _sogumada("Truncgil"):
        try:
            gelen = _truncgil_kurlari_cek()
            if not gelen:
                raise ValueError("geçerli kur yok")
            bulunan.update(gelen)
            kaynaklar.append("Truncgil")
        except Exception as e:
            _KUR_KAYNAK_SOGUMA["Truncgil"] = time.time() + 300
            print(f"DÖVİZ KUR HATASI (Truncgil): {type(e).__name__}: {e}")

    if not all(k in bulunan for k in ("USD", "EUR")) and not _sogumada("Yahoo"):
        gelen = _yahoo_kurlari_cek()
        yeni = {k: v for k, v in gelen.items() if k not in bulunan}
        bulunan.update(yeni)
        if yeni:
            kaynaklar.append("Yahoo Finance")
        else:
            _KUR_KAYNAK_SOGUMA["Yahoo"] = time.time() + 300

    # 1) Frankfurter: yalnızca eksik kalan kurlar için denenir.
    for kod in ("USD", "EUR"):
        if kod in bulunan:
            continue
        try:
            response = requests.get(
                f"{API_URL}/{kod.lower()}/try",
                headers=basliklar,
                timeout=6,
            )
            response.raise_for_status()
            veri = response.json()
            rate = veri.get("rate")

            if rate is None:
                raise ValueError(f"{kod}/TRY kuru boş döndü.")

            bulunan[kod] = {
                "kod": kod,
                "birim": "1",
                "alis": float(rate),
                "satis": float(rate),
                "kur": float(rate),
                "kur_turu": "Referans kur",
                "tarih": veri.get("date", ""),
            }
            if "Frankfurter" not in kaynaklar:
                kaynaklar.append("Frankfurter")

        except Exception as e:
            print(f"DÖVİZ KUR HATASI ({kod}): {type(e).__name__}: {e}")

    # 2) Eksik kalanlar için TCMB yedeği.
    if not all(k in bulunan for k in ("USD", "EUR")):
        try:
            for kod, kayit in _tcmb_kurlari_cek().items():
                bulunan.setdefault(kod, kayit)
            kaynaklar.append("TCMB")
        except Exception as e:
            print(f"DÖVİZ KUR HATASI (TCMB): {type(e).__name__}: {e}")

    # 3) Altın: kur kaynaklarından bağımsız; hatası dövizi düşürmez.
    # Truncgil gram altını verdiyse ons tabanlı hesaplamaya gerek yok.
    try:
        if "ALTIN" in bulunan:
            raise _AltinHazir()
        gold_response = requests.get(
            "https://api.gold-api.com/price/XAU",
            headers=basliklar,
            timeout=6,
        )
        gold_response.raise_for_status()
        gold_data = gold_response.json()
        gold_usd_ons = gold_data.get("price")

        if gold_usd_ons is not None and bulunan.get("USD"):
            gold_gram_try = (
                float(gold_usd_ons)
                / 31.1034768
                * float(bulunan["USD"]["kur"])
            )
            bulunan["ALTIN"] = {
                "kod": "ALTIN",
                "birim": "1 gram",
                "alis": gold_gram_try,
                "satis": gold_gram_try,
                "kur": gold_gram_try,
                "kur_turu": "24 ayar gram altın referans fiyatı",
                "tarih": gold_data.get("updatedAt") or gold_data.get("timestamp") or "",
            }
    except _AltinHazir:
        pass
    except Exception as e:
        print(f"ALTIN FİYATI HATASI: {type(e).__name__}: {e}")
        # Önceki başarılı altın değerini koru.
        eski_altin = (DOVIZ_CACHE or {}).get("veriler", {}).get("ALTIN")
        if eski_altin and bulunan.get("USD"):
            bulunan["ALTIN"] = eski_altin

    if not any(k in bulunan for k in ("USD", "EUR")):
        DOVIZ_HATA_ZAMANI = simdi

        if DOVIZ_CACHE:
            return DOVIZ_CACHE

        return {
            "status": "error",
            "kaynak": "Frankfurter / TCMB",
            "kur_turu": "Günlük referans kuru",
            "tarih": "",
            "veriler": {},
            "hata": "USD/EUR kurları şu anda alınamadı.",
        }

    # Formatlar kaynağa göre farklı olduğundan karşılaştırma yapmadan
    # öncelikli kurun (USD, yoksa EUR) tarihi gösterilir.
    tarihler = [
        bulunan[k].get("tarih", "")
        for k in ("USD", "EUR")
        if k in bulunan and bulunan[k].get("tarih", "")
    ]

    DOVIZ_CACHE = {
        "status": "success",
        "kaynak": " + ".join(kaynaklar) or "Frankfurter",
        "kur_turu": (
            "Güncel piyasa kuru"
            if all(
                bulunan[k].get("canli")
                for k in ("USD", "EUR")
                if k in bulunan
            )
            else "Günlük referans kuru"
        ),
        "tarih": tarihler[0] if tarihler else "",
        "veriler": bulunan,
    }

    DOVIZ_SON_CEKME = simdi
    DOVIZ_HATA_ZAMANI = None

    return DOVIZ_CACHE


# =========================================================
# WEB PUSH (FİYAT ALARMI BİLDİRİMLERİ)
# =========================================================

PUSH_KONU = os.getenv("VAPID_SUBJECT", "mailto:admin@hurda.local")
PUSH_MAX_ABONE = 2000
PUSH_MAX_ALARM = 30


def push_vapid_anahtari(data):
    push = data.setdefault("push", {})

    if not push.get("vapid"):
        push["vapid"] = webpush.vapid_anahtari_uret()
        save_data(data)

    return push["vapid"]


def push_alarmlarini_temizle(alarmlar, eskiler=None):
    """İstemciden gelen alarm listesini doğrular; sunucudaki 'fired' durumunu korur."""
    eski_durum = {
        str(a.get("id")): bool(a.get("fired"))
        for a in (eskiler or [])
    }
    temiz = []

    for a in (alarmlar or [])[:PUSH_MAX_ALARM]:
        try:
            deger = float(a.get("value"))
            yon = "above" if a.get("direction") == "above" else "below"
            firma_id = str(a.get("firma_id", "")).strip()[:80]
            kalem = str(a.get("kalem", "")).strip()[:120]
        except (TypeError, ValueError, AttributeError):
            continue

        if not firma_id or not kalem or deger <= 0:
            continue

        alarm_id = str(a.get("id", ""))[:40]
        temiz.append({
            "id": alarm_id,
            "firma_id": firma_id,
            "kalem": kalem,
            "direction": yon,
            "value": deger,
            "fired": eski_durum.get(alarm_id, False),
        })

    return temiz


def push_alarmlarini_kontrol():
    """Her fiyat turundan sonra: koşulu sağlanan alarmlara bildirim gönderir."""
    data = load_data()
    push = data.get("push") or {}
    abonelikler = push.get("abonelikler") or {}
    vapid = push.get("vapid")

    if not abonelikler or not vapid:
        return

    fiyatlar = {}
    for firma in fiyat_verilerini_olustur():
        for kalem in firma.get("kalemler", []):
            deger = (
                kalem.get("manuel_fiyat")
                if kalem.get("manuel_fiyat") is not None
                else kalem.get("otomatik_fiyat")
            )
            if deger is not None:
                fiyatlar[
                    (str(firma["firma_id"]).casefold(), kalem["cins"].casefold())
                ] = (float(deger), firma.get("baslik", ""), kalem["cins"])

    degisti = False
    gecersiz = []

    for endpoint, kayit in list(abonelikler.items()):
        for alarm in kayit.get("alarmlar", []):
            bulunan = fiyatlar.get(
                (alarm["firma_id"].casefold(), alarm["kalem"].casefold())
            )
            if not bulunan:
                continue

            fiyat, baslik, kalem_adi = bulunan
            tetik = (
                fiyat >= alarm["value"]
                if alarm["direction"] == "above"
                else fiyat <= alarm["value"]
            )

            if tetik and not alarm.get("fired"):
                yuk = {
                    "title": "Hurda fiyat alarmı",
                    "body": (
                        f"{baslik} · {kalem_adi} · "
                        f"{int(fiyat):,}".replace(",", ".")
                        + " TL ("
                        + ("≥ " if alarm["direction"] == "above" else "≤ ")
                        + f"{int(alarm['value']):,}".replace(",", ".")
                        + ")"
                    ),
                    "url": "/",
                    "tag": f"alarm-{alarm.get('id')}",
                }
                try:
                    basarili, abone_gecersiz = webpush.gonder(
                        kayit["subscription"], yuk, vapid, PUSH_KONU
                    )
                except Exception as exc:
                    print(f"PUSH HATASI: {type(exc).__name__}: {exc}")
                    continue

                if abone_gecersiz:
                    gecersiz.append(endpoint)
                    break

                if basarili:
                    alarm["fired"] = True
                    degisti = True

            elif not tetik and alarm.get("fired"):
                # Fiyat koşuldan çıktı: alarm yeniden kurulur.
                alarm["fired"] = False
                degisti = True

    for endpoint in gecersiz:
        abonelikler.pop(endpoint, None)
        degisti = True

    if degisti:
        save_data(data)


def gunluk_ozet_gonder():
    """Her sabah: 'günlük özet' isteyen abonelere son 24 saatin değişim özetini gönderir."""
    data = load_data()
    push = data.get("push") or {}
    abonelikler = push.get("abonelikler") or {}
    vapid = push.get("vapid")
    hedefler = {e: k for e, k in abonelikler.items() if k.get("ozet")}

    if not hedefler or not vapid:
        return

    degisimler = get_today_changes()["data"]
    yukselen = [d for d in degisimler if d["fark"] > 0]
    dusen = [d for d in degisimler if d["fark"] < 0]

    if degisimler:
        en_buyuk = max(degisimler, key=lambda d: abs(d["fark"]))
        isaret = "+" if en_buyuk["fark"] > 0 else "-"
        govde = (
            f"▲ {len(yukselen)} yükseliş · ▼ {len(dusen)} düşüş. En büyük: "
            f"{en_buyuk['firma']} {en_buyuk['kalem']} {isaret}"
            + f"{int(abs(en_buyuk['fark'])):,}".replace(",", ".")
            + " TL"
        )
    else:
        govde = "Son 24 saatte kayıtlı fiyat değişimi yok."

    yuk = {
        "title": "Günlük hurda özeti",
        "body": govde,
        "url": "/",
        "tag": "gunluk-ozet",
    }

    gecersiz = []
    for endpoint, kayit in hedefler.items():
        try:
            _, abone_gecersiz = webpush.gonder(
                kayit["subscription"], yuk, vapid, PUSH_KONU
            )
        except Exception as exc:
            print(f"PUSH ÖZET HATASI: {type(exc).__name__}: {exc}")
            continue

        if abone_gecersiz:
            gecersiz.append(endpoint)

    if gecersiz:
        data = load_data()
        for endpoint in gecersiz:
            ((data.get("push") or {}).get("abonelikler") or {}).pop(endpoint, None)
        save_data(data)

    print(f"GÜNLÜK ÖZET: {len(hedefler)} aboneye gönderildi.")


@app.get("/push/public-key")
def push_public_key():
    return {"key": push_vapid_anahtari(load_data())["public"]}


@app.post("/push/subscribe")
async def push_subscribe(request: Request):
    try:
        govde = await request.json()
        abonelik = govde["subscription"]
        endpoint = str(abonelik["endpoint"])
        p256dh = str(abonelik["keys"]["p256dh"])
        auth = str(abonelik["keys"]["auth"])
    except Exception:
        raise HTTPException(status_code=400, detail="Geçersiz abonelik.")

    if (
        not endpoint.startswith("https://")
        or len(endpoint) > 700
        or len(p256dh) > 200
        or len(auth) > 100
    ):
        raise HTTPException(status_code=400, detail="Geçersiz abonelik.")

    data = load_data()
    push_vapid_anahtari(data)
    push = data.setdefault("push", {})
    abonelikler = push.setdefault("abonelikler", {})

    if endpoint not in abonelikler and len(abonelikler) >= PUSH_MAX_ABONE:
        raise HTTPException(status_code=503, detail="Abonelik sınırı doldu.")

    onceki = abonelikler.get(endpoint, {})
    gunluk = govde.get("daily")
    abonelikler[endpoint] = {
        "ozet": bool(gunluk) if gunluk is not None else bool(onceki.get("ozet")),
        "subscription": {
            "endpoint": endpoint,
            "keys": {"p256dh": p256dh, "auth": auth},
        },
        "alarmlar": push_alarmlarini_temizle(
            govde.get("alarms"), onceki.get("alarmlar")
        ),
        "olusturma": onceki.get("olusturma") or now_istanbul_string(),
        "guncelleme": now_istanbul_string(),
    }

    save_data(data)

    return {
        "status": "ok",
        "alarm_sayisi": len(abonelikler[endpoint]["alarmlar"]),
    }


@app.post("/push/unsubscribe")
async def push_unsubscribe(request: Request):
    try:
        endpoint = str((await request.json())["endpoint"])
    except Exception:
        raise HTTPException(status_code=400, detail="Geçersiz istek.")

    data = load_data()
    abonelikler = (data.get("push") or {}).get("abonelikler") or {}

    if abonelikler.pop(endpoint, None) is not None:
        save_data(data)

    return {"status": "ok"}


# =========================================================
# FİYAT API
# =========================================================

def son_guncelleme_metni():
    """
    Ana sayfadaki "Son Güncelleme" değeri (İstanbul saati).
    Zamanlayıcı henüz tur tamamlamadıysa (yeni deploy/yeniden başlatma)
    kayıtlı firmaların son başarılı çekim zamanı kullanılır.
    """
    if SON_GUNCELLEME and not str(SON_GUNCELLEME).startswith("Henüz"):
        return SON_GUNCELLEME

    zamanlar = [
        parse_datetime(firma.get("son_basarili_cekme"))
        for firma in load_data().get("firms", {}).values()
        if firma.get("son_basarili_cekme")
    ]
    zamanlar = [z for z in zamanlar if z]

    if zamanlar:
        return max(zamanlar).strftime("%d.%m.%Y %H:%M")

    return "-"


@app.get(
    "/prices"
)
def get_prices():

    return {
        "status": "success",
        "son_guncelleme": son_guncelleme_metni(),
        "data": fiyat_verilerini_olustur(),
    }


@app.get(
    "/currency"
)
def get_currency():

    return doviz_kurlarini_getir()


# =========================================================
# PİYASA GEÇMİŞİ / KARŞILAŞTIRMA / DURUM
# =========================================================

@app.get(
    "/history"
)
def get_history(
    firma_id: str = None,
    kalem: str = None,
    limit: int = 90,
):
    data = load_data()

    kayitlar = data.get(
        "history",
        [],
    )

    if firma_id:
        hedef = firma_id.strip().lower()
        kayitlar = [
            item
            for item in kayitlar
            if str(item.get("firma_id", "")).strip().lower() == hedef
        ]

    if kalem:
        hedef_kalem = kalem.strip().casefold()
        kayitlar = [
            item
            for item in kayitlar
            if str(item.get("kalem", "")).strip().casefold() == hedef_kalem
        ]

    # Aynı fiyatın dakika dakika tekrar yazıldığı kayıtları grafik için
    # gereksiz yere çoğaltma. Sonraki farklı fiyatları koru.
    ters = list(reversed(kayitlar))
    benzersiz = []
    son_deger = object()

    for item in ters:
        deger = item.get("fiyat")
        if deger == son_deger:
            continue
        benzersiz.append(item)
        son_deger = deger

    benzersiz.reverse()

    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 90

    return {
        "status": "success",
        "data": benzersiz[-limit:],
    }


@app.get(
    "/today-changes"
)
def get_today_changes():
    """
    Son 24 saatte fiyatı gerçekten değişen kalemler.
    Ana sayfa kartlarıyla aynı mantık: mevcut fiyat, kayıtlı önceki farklı
    fiyatla kıyaslanır; ara değişimler ayrı ayrı sayılmaz.
    """
    data = load_data()
    indeks = gecmis_indeksi(data)
    simdi = now_istanbul()
    sonuc = []

    for firma in fiyat_verilerini_olustur():
        for kalem in firma.get("kalemler", []):
            deger = (
                kalem.get("manuel_fiyat")
                if kalem.get("manuel_fiyat") is not None
                else kalem.get("otomatik_fiyat")
            )

            detay = son_fiyat_degisim_detay(
                data,
                firma["firma_id"],
                kalem["cins"],
                deger,
                indeks,
            )

            if not detay:
                continue

            fark, zaman = detay

            if not (0 <= (simdi - zaman).total_seconds() <= 24 * 60 * 60):
                continue

            sonuc.append({
                "firma_id": firma["firma_id"],
                "firma": firma.get("baslik", firma["firma_id"]),
                "kalem": kalem["cins"],
                "eski": deger - fark,
                "yeni": deger,
                "fark": fark,
                "tarih": zaman.strftime("%Y-%m-%d %H:%M:%S"),
            })

    sonuc.sort(key=lambda item: item["tarih"], reverse=True)

    return {
        "status": "success",
        "yukselen": sum(1 for i in sonuc if i["fark"] > 0),
        "dusen": sum(1 for i in sonuc if i["fark"] < 0),
        "data": sonuc[:500],
    }


@app.get(
    "/compare"
)
def get_compare(
    kalem: str = None,
):
    data = load_data()

    mevcut = {}
    kalemler = set()

    for firma_id, fiyatlar in data.get("prices", {}).items():
        firma = data.get("firms", {}).get(firma_id, {})

        for ad, bilgi in fiyatlar.items():
            kalemler.add(ad)

            if kalem and ad.casefold() != kalem.strip().casefold():
                continue

            manuel = bilgi.get("manuel_fiyat")
            otomatik = bilgi.get("otomatik_fiyat")
            fiyat = manuel if manuel is not None else otomatik

            if fiyat is None:
                continue

            mevcut.setdefault(firma_id, {
                "firma_id": firma_id,
                "firma": firma.get("baslik", firma_id),
                "fiyat": fiyat,
                "kalem": ad,
            })

    rows = []

    if kalem:
        for firma_id, fiyatlar in data.get("prices", {}).items():
            firma = data.get("firms", {}).get(firma_id, {})
            bilgi = None

            for ad, kayit in fiyatlar.items():
                if ad.casefold() == kalem.strip().casefold():
                    bilgi = kayit
                    break

            if bilgi is None:
                continue

            fiyat = (
                bilgi.get("manuel_fiyat")
                if bilgi.get("manuel_fiyat") is not None
                else bilgi.get("otomatik_fiyat")
            )

            if fiyat is None:
                continue

            rows.append({
                "firma_id": firma_id,
                "firma": firma.get("baslik", firma_id),
                "fiyat": fiyat,
            })

        rows.sort(key=lambda x: str(x.get("firma", "")).casefold())

    return {
        "status": "success",
        "kalemler": sorted(
            kalemler,
            key=lambda x: x.casefold(),
        ),
        "kalem": kalem,
        "data": rows,
    }


@app.get(
    "/admin/data-backups",
    response_class=HTMLResponse,
)
def admin_data_backups(
    username: str = Depends(
        verify_admin
    ),
):
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        backups = sorted(
            [
                os.path.join(BACKUP_DIR, isim)
                for isim in os.listdir(BACKUP_DIR)
                if isim.endswith(".json")
            ],
            key=lambda yol: os.path.getmtime(yol),
            reverse=True,
        )
    except Exception:
        backups = []

    rows = "".join(
        f"""
<div class="flex items-center justify-between gap-3 rounded-xl border border-slate-200 p-3">
<div>
<div class="text-xs font-black text-slate-800">{esc(os.path.basename(yol))}</div>
<div class="text-[10px] text-slate-500">{esc(datetime.fromtimestamp(os.path.getmtime(yol)).strftime("%Y-%m-%d %H:%M:%S"))}</div>
</div>
</div>
"""
        for yol in backups[:10]
    ) or '<div class="text-sm text-slate-500">Henüz otomatik yedek oluşmadı.</div>'

    return f"""
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Veri Yedekleri</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 min-h-screen p-4">
<div class="max-w-3xl mx-auto space-y-4">
<div class="bg-slate-900 text-white rounded-3xl p-5">
<h1 class="text-2xl font-black">Veri Yedekleri</h1>
<p class="text-sm text-slate-300 mt-1">Mevcut fiyat/firma verisi korunarak son otomatik yedeğe dönülebilir.</p>
</div>
<div class="bg-white rounded-3xl border border-slate-200 p-5 shadow-sm">
<form method="post" action="/admin/data-rollback" onsubmit="return confirm('Mevcut veri dosyası son otomatik yedekle değiştirilecek. Devam edilsin mi?');">
<button type="submit" class="w-full rounded-xl bg-red-600 hover:bg-red-700 text-white py-3 font-black">
Son Yedeğe Geri Dön
</button>
</form>
<div class="mt-4 space-y-2">{rows}</div>
</div>
<a href="/admin" class="inline-block rounded-xl bg-slate-900 text-white px-4 py-2 font-bold">← Admin</a>
</div>
</body>
</html>
"""


@app.post(
    "/admin/data-rollback"
)
async def admin_data_rollback(
    username: str = Depends(
        verify_admin
    ),
):
    os.makedirs(
        BACKUP_DIR,
        exist_ok=True,
    )

    backups = sorted(
        [
            os.path.join(BACKUP_DIR, isim)
            for isim in os.listdir(BACKUP_DIR)
            if isim.endswith(".json")
        ],
        key=lambda yol: os.path.getmtime(yol),
        reverse=True,
    )

    if not backups:
        raise HTTPException(
            status_code=404,
            detail="Henüz geri dönülebilecek otomatik yedek yok.",
        )

    shutil.copy2(
        backups[0],
        DATA_FILE,
    )

    bildirim_ekle(
        "sistem",
        "veri_rollback",
        "Mevcut veri dosyası son otomatik yedeğe geri döndürüldü.",
    )

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


@app.get(
    "/system-status"
)
def get_system_status():
    data = load_data()

    firmalar = list(
        data.get("firms", {}).values()
    )

    fiyat_sayisi = sum(
        len(x)
        for x in data.get("prices", {}).values()
        if isinstance(x, dict)
    )

    return {
        "status": "success",
        "otomatik_guncelleme": AUTO_UPDATE_ENABLED,
        "son_fiyat_guncellemesi": SON_GUNCELLEME,
        "firma_sayisi": len(firmalar),
        "aktif_firma": len([
            x for x in firmalar
            if x.get("aktif", True)
        ]),
        "otomatik_firma": len([
            x for x in firmalar
            if x.get("otomatik", True)
        ]),
        "manuel_firma": len([
            x for x in firmalar
            if not x.get("otomatik", True)
        ]),
        "fiyat_kalemi": fiyat_sayisi,
        "gecmis_kaydi": len(data.get("history", [])),
        "bildirim": len(data.get("notifications", [])),
    }


@app.get(
    "/robots.txt"
)
def robots_txt():
    return Response(
        content=(
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /admin\n"
            "Disallow: /system-status\n"
            "Disallow: /history\n"
            "Disallow: /compare\n"
        ),
        media_type="text/plain",
    )


@app.get(
    "/sitemap.xml"
)
def sitemap_xml(
    request: Request,
):
    base = site_base_url(request)

    return Response(
        content=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f'<url><loc>{esc(base)}/</loc></url>'
            '</urlset>'
        ),
        media_type="application/xml",
    )


# =========================================================
# MANIFEST
# =========================================================

@app.get(
    "/manifest.json"
)
def get_manifest():

    return {
        "name": "Hurda Fiyatları",
        "short_name": "HurdaFiyat",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f1f5f9",
        "theme_color": "#0f172a",
        "icons": [
            {
                "src":
                    "https://cdn-icons-png.flaticon.com/"
                    "512/2954/2954884.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            }
        ],
    }


@app.get(
    "/favicon.ico"
)
def favicon():

    return Response(
        status_code=204
    )


# =========================================================
# SERVICE WORKER
# =========================================================

@app.get(
    "/sw.js"
)
def service_worker():

    javascript = """
self.addEventListener("install", function(event) {
    self.skipWaiting();
});

self.addEventListener("activate", function(event) {
    event.waitUntil(
        self.clients.claim()
    );
});

self.addEventListener("fetch", function(event) {
});

self.addEventListener("push", function(event) {
    let veri = {};

    try {
        veri = event.data ? event.data.json() : {};
    } catch (e) {
        veri = { body: event.data ? event.data.text() : "" };
    }

    event.waitUntil(
        self.registration.showNotification(veri.title || "Hurda fiyat alarmı", {
            body: veri.body || "",
            tag: veri.tag || "hurda-alarm",
            icon: "https://cdn-icons-png.flaticon.com/512/2954/2954884.png",
            data: { url: veri.url || "/" },
        })
    );
});

self.addEventListener("notificationclick", function(event) {
    event.notification.close();
    const hedef = (event.notification.data && event.notification.data.url) || "/";

    event.waitUntil(
        self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function(list) {
            for (const c of list) {
                if ("focus" in c) { return c.focus(); }
            }
            return self.clients.openWindow(hedef);
        })
    );
});
"""

    return Response(
        content=javascript,
        media_type="application/javascript",
    )



ADMIN_TEMA_BASI = """
<script data-tema-enjekte>
try { if (localStorage.getItem("hurdaTema") !== "light") { document.documentElement.classList.add("dark"); } } catch (e) { document.documentElement.classList.add("dark"); }
</script>
<style data-tema-enjekte>
html.dark { color-scheme: dark; }
html.dark body { background: #0a101d !important; color: #e2e8f0; }
html.dark .bg-white, html.dark [class*="bg-white/9"], html.dark [class*="bg-white/8"] { background-color: #111a2e !important; }
html.dark .bg-slate-50, html.dark [class*="bg-slate-50/"] { background-color: #0e1627 !important; }
html.dark .bg-slate-100 { background-color: #162036 !important; }
html.dark .bg-slate-200 { background-color: #1e2b44 !important; }
html.dark .bg-slate-900, html.dark .bg-slate-950 { background-color: #16213a !important; }
html.dark [class*="hover:bg-slate-50"]:hover, html.dark [class*="hover:bg-slate-100"]:hover { background-color: #162036 !important; }
html.dark [class*="hover:bg-slate-200"]:hover { background-color: #1e2b44 !important; }
html.dark .border-slate-100, html.dark .border-slate-200, html.dark .border-slate-300 { border-color: #24324d !important; }
html.dark .text-slate-950, html.dark .text-slate-900, html.dark .text-slate-800 { color: #f1f5f9 !important; }
html.dark .text-slate-700 { color: #cbd5e1 !important; }
html.dark .text-slate-600, html.dark .text-slate-500 { color: #9aa8bf !important; }
html.dark .text-slate-400 { color: #7c8aa3 !important; }
html.dark .bg-emerald-50, html.dark .bg-emerald-100 { background-color: rgba(16,185,129,.15) !important; }
html.dark .text-emerald-700, html.dark .text-emerald-800, html.dark .text-emerald-600 { color: #6ee7b7 !important; }
html.dark .border-emerald-200 { border-color: rgba(16,185,129,.35) !important; }
html.dark .bg-red-50, html.dark .bg-red-100 { background-color: rgba(239,68,68,.15) !important; }
html.dark .text-red-700, html.dark .text-red-800, html.dark .text-red-600 { color: #fca5a5 !important; }
html.dark .border-red-200 { border-color: rgba(239,68,68,.35) !important; }
html.dark .bg-amber-50, html.dark .bg-amber-100 { background-color: rgba(245,158,11,.15) !important; }
html.dark .text-amber-700, html.dark .text-amber-800, html.dark .text-amber-900 { color: #fcd34d !important; }
html.dark .border-amber-200 { border-color: rgba(245,158,11,.35) !important; }
html.dark .bg-blue-50, html.dark .bg-blue-100, html.dark .bg-sky-50 { background-color: rgba(59,130,246,.16) !important; }
html.dark .text-blue-700, html.dark .text-blue-800, html.dark .text-sky-700 { color: #93c5fd !important; }
html.dark .border-blue-200, html.dark .border-sky-200 { border-color: rgba(59,130,246,.35) !important; }
html.dark .bg-indigo-50, html.dark .bg-indigo-100 { background-color: rgba(99,102,241,.18) !important; }
html.dark .text-indigo-700, html.dark .text-indigo-800 { color: #a5b4fc !important; }
html.dark .border-indigo-200 { border-color: rgba(99,102,241,.4) !important; }
html.dark .text-blue-600 { color: #60a5fa !important; }
html.dark .text-indigo-600 { color: #818cf8 !important; }
html.dark .text-emerald-600 { color: #34d399 !important; }
html.dark input, html.dark select, html.dark textarea { background-color: #0e1627 !important; color: #e2e8f0 !important; border-color: #24324d !important; }
html.dark input::placeholder { color: #6b7a93 !important; }
html.dark details { background-color: rgba(239,68,68,.12) !important; }
#temaDugmesi { position: fixed; right: 14px; bottom: 14px; z-index: 60; width: 42px; height: 42px; border-radius: 999px; border: 1px solid #cbd5e1; background: #fff; box-shadow: 0 6px 18px rgba(15,23,42,.2); font-size: 19px; line-height: 1; cursor: pointer; }
html.dark #temaDugmesi { background: #111a2e; border-color: #24324d; }
</style>
"""

ADMIN_TEMA_SONU = """
<button type="button" id="temaDugmesi" aria-label="Karanlık / aydınlık tema" data-tema-enjekte>🌙</button>
<script data-tema-enjekte>
(function() {
    var d = document.getElementById("temaDugmesi");
    function ikon() { d.textContent = document.documentElement.classList.contains("dark") ? "☀️" : "🌙"; }
    ikon();
    d.addEventListener("click", function() {
        var koyu = !document.documentElement.classList.contains("dark");
        document.documentElement.classList.toggle("dark", koyu);
        try { localStorage.setItem("hurdaTema", koyu ? "dark" : "light"); } catch (e) {}
        ikon();
    });
})();
</script>
"""


@app.middleware("http")
async def admin_tema_enjekte(request: Request, call_next):
    """Tüm /admin HTML sayfalarına ortak gece teması ve tema düğmesi ekler."""
    cevap = await call_next(request)

    if (
        request.url.path.startswith("/admin")
        and "text/html" in cevap.headers.get("content-type", "")
    ):
        govde = b"".join([parca async for parca in cevap.body_iterator])
        metin = govde.decode("utf-8", errors="replace")

        if "data-tema-enjekte" not in metin and "</head>" in metin:
            metin = metin.replace("</head>", ADMIN_TEMA_BASI + "</head>", 1)
            if "</body>" in metin:
                metin = metin.replace("</body>", ADMIN_TEMA_SONU + "</body>", 1)

        basliklar = {
            k: v for k, v in cevap.headers.items()
            if k.lower() not in ("content-length", "content-type")
        }
        return Response(
            content=metin,
            status_code=cevap.status_code,
            headers=basliklar,
            media_type="text/html",
        )

    return cevap


@app.middleware("http")
async def reklam_gorsel_onbellegi(request: Request, call_next):
    """Dosya adı içeriğe göre rastgele olduğundan reklam görselleri uzun süre önbelleğe alınır."""
    cevap = await call_next(request)
    if request.url.path.startswith("/static/ads/") and cevap.status_code == 200:
        cevap.headers["Cache-Control"] = "public, max-age=604800, immutable"
    return cevap


# Bant genişliğini düşürmek için yanıtları sıkıştır (HTML ~176 KB → ~36 KB).
# En son eklenen middleware en dışta çalışır; böylece diğer middleware'lerin
# ürettiği yanıtlar da sıkıştırılır.
app.add_middleware(GZipMiddleware, minimum_size=1024)


# =========================================================
# ADMİN GİRİŞ / ÇIKIŞ / ŞİFRE / TOPLU GÜNCELLEME
# =========================================================

def _admin_sayfa(baslik, icerik):
    return HTMLResponse(
        "<!DOCTYPE html><html lang='tr'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='robots' content='noindex,nofollow'>"
        f"<title>{esc(baslik)}</title>"
        "<script src='https://cdn.tailwindcss.com'></script></head>"
        "<body class='min-h-screen bg-slate-100 flex items-center justify-center p-4'>"
        "<div class='w-full max-w-sm bg-white rounded-3xl shadow-lg border border-slate-200 p-6'>"
        f"<h1 class='text-xl font-black text-slate-900 mb-1'>{esc(baslik)}</h1>"
        + icerik +
        "</div></body></html>"
    )


_INPUT = (
    "class='w-full h-11 rounded-xl border border-slate-300 px-3 text-sm font-semibold "
    "outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-100'"
)
_BUTON = (
    "class='w-full h-11 rounded-xl bg-slate-900 hover:bg-slate-800 text-white "
    "text-sm font-black transition'"
)


def _guvenli_hedef(deger):
    deger = str(deger or "")
    if deger.startswith("/admin") and not deger.startswith("//"):
        return deger
    return "/admin"


@app.get("/admin/giris", response_class=HTMLResponse)
def admin_giris_formu(request: Request, next: str = "/admin", hata: str = ""):
    mesaj = ""
    if hata == "1":
        mesaj = "<div class='mb-3 rounded-xl bg-red-50 border border-red-200 text-red-700 text-xs font-bold p-2.5'>Kullanıcı adı veya şifre hatalı.</div>"
    elif hata == "2":
        mesaj = "<div class='mb-3 rounded-xl bg-amber-50 border border-amber-200 text-amber-800 text-xs font-bold p-2.5'>Çok fazla hatalı deneme. 10 dakika sonra tekrar deneyin.</div>"

    return _admin_sayfa(
        "Yönetim Girişi",
        "<p class='text-xs text-slate-500 mb-4'>Hurda Fiyatları yönetim paneli</p>"
        + mesaj
        + "<form method='post' action='/admin/giris' class='space-y-3'>"
        f"<input type='hidden' name='next' value='{esc(_guvenli_hedef(next))}'>"
        f"<input name='username' autocomplete='username' placeholder='Kullanıcı adı' required {_INPUT}>"
        f"<input name='password' type='password' autocomplete='current-password' placeholder='Şifre' required {_INPUT}>"
        f"<button type='submit' {_BUTON}>Giriş yap</button></form>",
    )


@app.post("/admin/giris")
async def admin_giris(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/admin"),
):
    ip = istemci_ip(request)

    if adminauth.engelli_mi(ip):
        return RedirectResponse("/admin/giris?hata=2", status_code=303)

    data = load_data()

    if not adminauth.sifre_dogru_mu(data, username, password, ADMIN_USER, ADMIN_PASS):
        adminauth.hata_kaydet(ip)
        return RedirectResponse("/admin/giris?hata=1", status_code=303)

    adminauth.basari_sifirla(ip)
    token = adminauth.oturum_olustur(data, ADMIN_USER)

    if (data.get("admin_auth") or {}).pop("_yeni", None):
        save_data(data)

    cevap = RedirectResponse(_guvenli_hedef(next), status_code=303)
    cevap.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=adminauth.OTURUM_SURESI,
        httponly=True,
        samesite="strict",
        secure=request.url.hostname not in ("localhost", "127.0.0.1"),
        path="/",
    )
    return cevap


@app.get("/admin/cikis")
def admin_cikis():
    cevap = RedirectResponse("/admin/giris", status_code=303)
    cevap.delete_cookie(ADMIN_COOKIE, path="/")
    return cevap


@app.get("/admin/sifre", response_class=HTMLResponse)
def admin_sifre_formu(username: str = Depends(verify_admin), hata: str = ""):
    mesajlar = {
        "1": "Mevcut şifre hatalı.",
        "2": f"Yeni şifre en az {adminauth.MIN_SIFRE_UZUNLUGU} karakter olmalı.",
        "3": "Yeni şifreler birbiriyle eşleşmiyor.",
    }
    mesaj = (
        f"<div class='mb-3 rounded-xl bg-red-50 border border-red-200 text-red-700 text-xs font-bold p-2.5'>{esc(mesajlar[hata])}</div>"
        if hata in mesajlar else ""
    )

    return _admin_sayfa(
        "Şifre Değiştir",
        "<p class='text-xs text-slate-500 mb-4'>Değişiklikten sonra tüm oturumlar kapanır.</p>"
        + mesaj
        + "<form method='post' action='/admin/sifre' class='space-y-3'>"
        f"<input name='mevcut' type='password' autocomplete='current-password' placeholder='Mevcut şifre' required {_INPUT}>"
        f"<input name='yeni' type='password' autocomplete='new-password' placeholder='Yeni şifre' required {_INPUT}>"
        f"<input name='yeni2' type='password' autocomplete='new-password' placeholder='Yeni şifre (tekrar)' required {_INPUT}>"
        f"<button type='submit' {_BUTON}>Şifreyi değiştir</button></form>"
        "<a href='/admin' class='block text-center text-xs font-bold text-slate-500 mt-4'>← Panele dön</a>",
    )


@app.post("/admin/sifre")
async def admin_sifre_degistir(
    request: Request,
    mevcut: str = Form(""),
    yeni: str = Form(""),
    yeni2: str = Form(""),
    username: str = Depends(verify_admin),
):
    data = load_data()

    if not adminauth.sifre_dogru_mu(data, ADMIN_USER, mevcut, ADMIN_USER, ADMIN_PASS):
        return RedirectResponse("/admin/sifre?hata=1", status_code=303)

    if len(yeni) < adminauth.MIN_SIFRE_UZUNLUGU:
        return RedirectResponse("/admin/sifre?hata=2", status_code=303)

    if yeni != yeni2:
        return RedirectResponse("/admin/sifre?hata=3", status_code=303)

    adminauth.sifre_degistir(data, yeni)
    save_data(data)

    # Yeni oturum anahtarıyla bu tarayıcıyı açık tut.
    cevap = RedirectResponse("/admin?m=sifre", status_code=303)
    cevap.set_cookie(
        ADMIN_COOKIE,
        adminauth.oturum_olustur(data, ADMIN_USER),
        max_age=adminauth.OTURUM_SURESI,
        httponly=True,
        samesite="strict",
        secure=request.url.hostname not in ("localhost", "127.0.0.1"),
        path="/",
    )
    return cevap


@app.get("/admin/degisim-tani", response_class=HTMLResponse)
def admin_degisim_tani(
    username: str = Depends(verify_admin),
    q: str = "",
):
    """Her kalem için gösterilen fiyat, kaynağı ve değişim hesabının dayandığı geçmiş kayıtları."""
    data = load_data()
    indeks = gecmis_indeksi(data)
    arama = str(q or "").strip().casefold()
    satirlar = ""
    sayi = 0

    for firma in fiyat_verilerini_olustur():
        for kalem in firma.get("kalemler", []):
            if arama and arama not in (
                str(firma.get("baslik", "")) + " " + str(kalem.get("cins", ""))
            ).casefold():
                continue

            anahtar = (
                str(firma["firma_id"]).strip().casefold(),
                kalem_kanonik_adi(kalem["cins"]).casefold(),
            )
            kayitlar = indeks.get(anahtar, [])
            son = "".join(
                f'<div>{esc(z.strftime("%d.%m %H:%M"))} → <b>{esc(int(d))}</b></div>'
                for z, d in kayitlar[-4:]
            ) or '<span class="text-red-600 font-bold">GEÇMİŞ YOK</span>'

            kaynak = "manuel" if kalem.get("manuel_fiyat") is not None else "otomatik"
            degisim = kalem.get("degisim") or "—"
            sayi += 1

            satirlar += (
                "<tr class='border-b border-slate-200 align-top'>"
                f"<td class='px-3 py-2 font-bold'>{esc(firma.get('baslik'))}</td>"
                f"<td class='px-3 py-2'>{esc(kalem.get('cins'))}</td>"
                f"<td class='px-3 py-2 whitespace-nowrap'>{esc(kalem.get('fiyat'))}</td>"
                f"<td class='px-3 py-2'>{kaynak}</td>"
                f"<td class='px-3 py-2 font-black'>{esc(degisim)}</td>"
                f"<td class='px-3 py-2 text-xs'>{esc(kalem.get('degisim_tarihi') or '—')}</td>"
                f"<td class='px-3 py-2 text-xs'>{son}</td>"
                f"<td class='px-3 py-2 text-xs'>{len(kayitlar)}</td>"
                "</tr>"
            )

    return HTMLResponse(
        "<!DOCTYPE html><html lang='tr'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta name='robots' content='noindex,nofollow'>"
        "<title>Değişim Tanılama</title>"
        "<script src='https://cdn.tailwindcss.com'></script></head>"
        "<body class='bg-slate-100 p-3 sm:p-5 text-slate-900'>"
        "<div class='max-w-7xl mx-auto'>"
        "<div class='flex flex-wrap items-center justify-between gap-3 mb-4'>"
        "<div><h1 class='text-xl font-black'>Değişim Tanılama</h1>"
        "<p class='text-xs text-slate-500'>Yükselen/düşen, kalemin son farklı fiyata göre değişimidir. "
        "'GEÇMİŞ YOK' görünen kalemde değişim hesaplanamaz.</p></div>"
        "<a href='/admin#genel' class='px-4 py-2 rounded-xl bg-slate-900 text-white text-sm font-bold'>← Panele dön</a></div>"
        f"<form class='mb-3'><input name='q' value='{esc(q)}' placeholder='Firma veya kalem ara...' "
        "class='w-full sm:w-72 h-10 rounded-xl border border-slate-300 px-3 text-sm'></form>"
        "<div class='overflow-x-auto bg-white rounded-2xl border border-slate-200'>"
        "<table class='w-full text-sm text-left'><thead class='bg-slate-50 text-xs uppercase text-slate-500'><tr>"
        "<th class='px-3 py-2'>Firma</th><th class='px-3 py-2'>Kalem</th><th class='px-3 py-2'>Gösterilen fiyat</th>"
        "<th class='px-3 py-2'>Kaynak</th><th class='px-3 py-2'>Değişim</th><th class='px-3 py-2'>Değişim zamanı</th>"
        "<th class='px-3 py-2'>Son geçmiş kayıtları</th><th class='px-3 py-2'>Kayıt</th></tr></thead>"
        f"<tbody>{satirlar}</tbody></table></div>"
        f"<p class='text-xs text-slate-500 mt-3'>{sayi} kalem</p>"
        "</div></body></html>"
    )


@app.post("/admin/lme-grafik")
async def admin_lme_grafik(
    mod: str = Form(""),
    username: str = Depends(verify_admin),
):
    if mod not in LME_GRAFIK_MODLARI:
        return RedirectResponse("/admin", status_code=303)
    data = load_data()
    ayarlar = data.get("ayarlar")
    if not isinstance(ayarlar, dict):
        ayarlar = data["ayarlar"] = {}
    ayarlar["lme_grafik_mod"] = mod
    ayarlar["lme_grafik_aktif"] = mod != "pasif"
    save_data(data)
    return RedirectResponse(f"/admin?m=lme_grafik_{mod}", status_code=303)


@app.post("/admin/yedek-al")
async def admin_yedek_al(username: str = Depends(verify_admin)):
    storage_module.manuel_yedek_al({"ads": ADS_FILE})
    return RedirectResponse("/admin?m=yedek", status_code=303)


@app.get("/admin/yedek-indir")
def admin_yedek_indir(username: str = Depends(verify_admin)):
    damga = now_istanbul().strftime("%Y%m%d-%H%M%S")
    paket = io.BytesIO()

    with zipfile.ZipFile(paket, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(storage_module.DATA_FILE):
            zf.write(storage_module.DATA_FILE, "data.json")
        if os.path.exists(ADS_FILE):
            zf.write(ADS_FILE, "ads.json")

    return Response(
        content=paket.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="hurda-yedek-{damga}.zip"'
        },
    )


@app.post("/admin/update-all")
async def admin_tumunu_guncelle(username: str = Depends(verify_admin)):
    if _GUNCELLEME_KILIDI.locked():
        return RedirectResponse("/admin?m=calisiyor", status_code=303)

    threading.Thread(target=verileri_guncelle, daemon=True).start()

    return RedirectResponse("/admin?m=baslatildi", status_code=303)


# =========================================================
# ADMİN - YENİ KAYNAK
# =========================================================

@app.get(
    "/admin/source/new",
    response_class=HTMLResponse,
)
def admin_new_source(
    username: str = Depends(
        verify_admin
    ),
):

    return """
<!DOCTYPE html>
<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width, initial-scale=1.0"
>

<title>Yeni Kaynak</title>

<script src="https://cdn.tailwindcss.com"></script>

</head>

<body class="bg-slate-100 min-h-screen p-3 sm:p-4">

<div class="max-w-3xl mx-auto">

<div class="bg-white rounded-3xl shadow-xl border border-slate-200 overflow-hidden">

<div class="bg-slate-900 text-white p-5 sm:p-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">

<h1 class="text-2xl sm:text-3xl font-black text-white">
Yeni Firma / Kaynak
</h1>

<a
href="/admin"
class="bg-white/10 border border-white/20 hover:bg-white/15 text-white px-4 py-2 rounded-xl text-sm font-bold text-center transition"
>
← Geri
</a>

</div>

<form
method="post"
action="/admin/source/new"
class="space-y-5 p-5 sm:p-7"
>

<div>

<label class="block text-sm font-bold mb-2">
Firma ID
</label>

<input
type="text"
name="firma_id"
required
placeholder="ornekfirma"
pattern="[a-zA-Z0-9_-]+"
class="w-full border border-slate-200 bg-slate-50 focus:bg-white focus:border-slate-400 outline-none rounded-xl px-4 py-3 transition"
>

<p class="text-xs text-slate-500 mt-1">
Sadece harf, rakam, alt çizgi ve tire kullanın.
</p>

</div>

<div>

<label class="block text-sm font-bold mb-2">
Firma Adı
</label>

<input
type="text"
name="baslik"
required
placeholder="Örnek Demir Çelik"
class="w-full border border-slate-200 bg-slate-50 focus:bg-white focus:border-slate-400 outline-none rounded-xl px-4 py-3 transition"
>

</div>

<div>

<label class="block text-sm font-bold mb-2">
Ana Sayfa Sıra Numarası
</label>

<input
type="number"
name="sira"
value="1"
min="1"
step="1"
required
class="w-full border border-slate-200 bg-slate-50 focus:bg-white focus:border-slate-400 outline-none rounded-xl px-4 py-3 transition"
>

<p class="text-xs text-slate-500 mt-1">
1 = ilk firma. İstediğiniz numarayı yazarak ana sayfadaki yeri belirleyin.
</p>

</div>

<div>

<label class="block text-sm font-bold mb-2">
Kaynak URL
</label>

<input
type="url"
name="url"
placeholder="İsteğe bağlı: https://..."
class="w-full border border-slate-200 bg-slate-50 focus:bg-white focus:border-slate-400 outline-none rounded-xl px-4 py-3 transition"
>

<div class="text-xs text-slate-500 mt-2">
Otomatik fiyat çek seçilirse, girilen URL'den genel HTML tablo/API okuyucusu ile fiyatlar otomatik çekilmeyi denenir. JavaScript/API ile özel çalışan sayfalarda firmaya özel scraper gerekebilir.
</div>

</div>

<div class="flex items-center gap-3">

<input
type="checkbox"
name="aktif"
value="1"
checked
class="w-5 h-5"
>

<label class="font-semibold">
Firma aktif
</label>

</div>

<div class="flex items-center gap-3">

<input
type="checkbox"
name="otomatik"
value="1"
class="w-5 h-5"
>

<label class="font-semibold">
Otomatik fiyat çek
</label>

</div>

<button
type="submit"
class="w-full bg-slate-900 hover:bg-slate-800 text-white py-3.5 rounded-xl font-black transition shadow-sm"
>
Firmayı Kaydet
</button>

</form>

</div>

</div>

</body>

</html>
"""


@app.post(
    "/admin/source/new"
)
async def admin_new_source_save(
    firma_id: str = Form(...),
    baslik: str = Form(...),
    url: str = Form(""),
    aktif: str = Form(None),
    otomatik: str = Form(None),
    sira: int = Form(1),
    username: str = Depends(
        verify_admin
    ),
):

    firma_id = firma_id.strip().lower()
    baslik = baslik.strip()
    url = (
        url or ""
    ).strip()

    if not firma_id:

        raise HTTPException(
            status_code=400,
            detail="Firma ID boş olamaz.",
        )

    if not baslik:

        raise HTTPException(
            status_code=400,
            detail="Firma adı boş olamaz.",
        )

    data = load_data()

    if "firms" not in data:
        data["firms"] = {}

    if firma_id in data[
        "firms"
    ]:

        raise HTTPException(
            status_code=400,
            detail="Bu firma ID zaten mevcut.",
        )

    kayitli_scraper_var = (
        any(
            kayit_id == firma_id
            for kayit_id, _ in TUMU
        )
    )

    otomatik_aktif = (
        otomatik == "1"
        and (
            kayitli_scraper_var
            or bool(url)
        )
    )

    try:
        yeni_sira = max(
            1,
            int(sira),
        )
    except (TypeError, ValueError):
        yeni_sira = 1

    mevcut_firma_sayisi = len(
        data[
            "firms"
        ]
    )

    data[
        "firms"
    ][
        firma_id
    ] = {
        "firma_id": firma_id,
        "baslik": baslik,
        "url": url,
        "otomatik": otomatik_aktif,
        "aktif": aktif == "1",
        "son_basarili_cekme": None,
        "kaynak_fiyat_tarihi": None,
        "durum": "bekliyor",
        "sira": yeni_sira - 1,
    }

    firma_sirasini_uygula(
        data,
        firma_id,
        yeni_sira,
    )

    save_data(
        data
    )

    if otomatik_aktif:

        try:

            fonksiyon = firma_scraperini_bul(
                firma_id,
                data,
            )

            if fonksiyon is None:
                raise RuntimeError(
                    "Otomatik kaynak oluşturulamadı."
                )

            sonuc = firma_verisini_cek(
                fonksiyon
            )

            bildirim_ekle(
                firma_id,
                "basarili_guncelleme",
                (
                    "Firma kaydedildi ve ilk otomatik çekim "
                    "başarılı oldu. "
                    f"{len(sonuc['kalemler'])} fiyat kalemi okundu."
                ),
            )

        except Exception as e:

            data = load_data()

            if firma_id in data.get(
                "firms",
                {},
            ):
                firma_hata_isle(data, firma_id, e)
                save_data(data)

            bildirim_ekle(
                firma_id,
                "kaynak_testi_hatasi",
                (
                    "Firma kaydedildi fakat ilk otomatik çekim "
                    f"başarısız oldu: {e}"
                ),
            )

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


# =========================================================
# ADMİN - KAYNAK DÜZENLE
# =========================================================

@app.get(
    "/admin/source/{firma_id}",
    response_class=HTMLResponse,
)
def admin_source_edit(
    firma_id: str,
    username: str = Depends(
        verify_admin
    ),
):

    data = load_data()

    firma = data.get(
        "firms",
        {},
    ).get(
        firma_id
    )

    if not firma:

        raise HTTPException(
            status_code=404,
            detail="Firma bulunamadı.",
        )

    prices = data.get(
        "prices",
        {},
    ).get(
        firma_id,
        {},
    )

    fiyat_rows = ""
    for index, (kalem, bilgi) in enumerate(prices.items()):

        otomatik = bilgi.get(
            "otomatik_fiyat"
        )

        manuel = bilgi.get(
            "manuel_fiyat"
        )

        fiyat_rows += f"""
<div class="border border-slate-200 rounded-xl p-4">

<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-3">

<div>

<div class="font-bold text-slate-900 break-words">
{esc(kalem)}
</div>

<div class="text-xs text-slate-500">
Otomatik:
{esc(fiyat_format(otomatik))}
</div>

</div>

</div>

<input
type="number"
name="manuel_{esc(kalem)}"
value="{esc(manuel if manuel is not None else '')}"
min="0"
step="1"
class="w-full border border-slate-200 bg-slate-50 focus:bg-white focus:border-slate-400 outline-none rounded-xl px-3 py-2 text-sm transition"
placeholder="Boş = otomatik"
>

<div class="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">

<button
type="submit"
name="guncelle_kalem"
value="{esc(kalem)}"
formaction="/admin/source/{esc(firma_id)}/manual-update"
formmethod="post"
formnovalidate
class="w-full border border-blue-200 bg-blue-50 hover:bg-blue-100 text-blue-700 rounded-xl px-3 py-2 text-sm font-bold"
>
✏️ Güncelle
</button>

<button
type="submit"
name="manuel_sil"
value="{esc(kalem)}"
formaction="/admin/source/{esc(firma_id)}/manual-delete"
formmethod="post"
formnovalidate
class="w-full border border-red-200 bg-red-50 hover:bg-red-100 text-red-700 rounded-xl px-3 py-2 text-sm font-bold"
>
🗑️ Sil
</button>

</div>

</div>
"""

    if not fiyat_rows:
        fiyat_rows = """
<div class="bg-amber-50 border border-amber-200 text-amber-800 rounded-xl p-4 text-sm">
Bu firma için henüz fiyat kaydı bulunmuyor.
</div>
"""

    yeni_manuel_fiyat = """
<div class="mt-5 border-2 border-dashed border-indigo-200 bg-indigo-50 rounded-2xl p-4 sm:p-5">

<h3 class="font-bold text-indigo-900 mb-3">
Yeni Manuel Fiyat Ekle
</h3>

<div class="grid grid-cols-1 md:grid-cols-2 gap-3">

<div>

<label class="block text-sm font-bold mb-2 text-slate-700">
Kalem Adı
</label>

<input
type="text"
name="new_kalem"
placeholder="Örn: DKP, TALAŞ, EKSTRA"
class="w-full border border-slate-300 rounded-xl px-3 py-3"
>

</div>

<div>

<label class="block text-sm font-bold mb-2 text-slate-700">
Fiyat
</label>

<input
type="number"
name="new_fiyat"
min="1"
step="1"
placeholder="Örn: 18500"
class="w-full border border-slate-300 rounded-xl px-3 py-3"
>

</div>

</div>

<p class="text-xs text-slate-500 mt-3">
Yeni bir fiyat kalemi oluşturmak için kalem adını ve fiyatı girin.
</p>

</div>
"""

    durum = firma.get(
        "durum",
        "bekliyor",
    )

    durum_text = {
        "basarili": "Başarılı",
        "hata": "Hata / STALE",
        "bekliyor": "Bekliyor",
    }.get(
        durum,
        durum,
    )

    scraper_var = (
        firma_scraperini_bul(
            firma_id,
            data,
        )
        is not None
    )

    stale = firma_stale_mi(
        firma
    )

    if stale:

        durum_text = (
            "STALE / Güncelleme eski"
        )

    return f"""
<!DOCTYPE html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width, initial-scale=1.0"
>

<title>Kaynak Düzenle</title>

<script src="https://cdn.tailwindcss.com"></script>

</head>

<body class="bg-slate-100 min-h-screen p-3 sm:p-4">

<div class="admin-compact max-w-5xl mx-auto space-y-4 sm:space-y-5">

<div class="bg-slate-900 text-white rounded-3xl shadow-xl p-5 sm:p-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">

<h1 class="text-2xl sm:text-3xl font-black text-white break-words">
{esc(firma.get("baslik", firma_id))}
</h1>

<a
href="/admin"
class="bg-white/10 border border-white/20 hover:bg-white/15 text-white px-4 py-2 rounded-xl text-sm font-bold text-center transition"
>
← Geri
</a>

</div>

<div class="bg-white rounded-3xl shadow-sm border border-slate-200 p-4 sm:p-6 lg:p-7">

<div class="mb-6">

<div class="text-sm text-slate-500">
Durum
</div>

<div class="font-bold text-slate-900">
{esc(durum_text)}
</div>

<div class="text-xs text-slate-500 mt-1 break-words">
Son başarılı çekim:
{esc(firma.get("son_basarili_cekme") or "-")}
</div>

</div>

<form
method="post"
action="/admin/source/{esc(firma_id)}/save"
class="space-y-5"
>

<div>

<label class="block text-sm font-bold mb-2">
Firma Adı
</label>

<input
type="text"
name="baslik"
value="{esc(firma.get("baslik", ""))}"
required
class="w-full border border-slate-200 bg-slate-50 focus:bg-white focus:border-slate-400 outline-none rounded-xl px-4 py-3 transition"
>

</div>

<div>

<label class="block text-sm font-bold mb-2">
Ana Sayfa Sıra Numarası
</label>

<input
type="number"
name="sira"
value="{esc(int(firma.get("sira", 0)) + 1)}"
min="1"
step="1"
required
class="w-full border border-slate-200 bg-slate-50 focus:bg-white focus:border-slate-400 outline-none rounded-xl px-4 py-3 transition"
>

<p class="text-xs text-slate-500 mt-1">
1 = ilk firma. İstediğiniz numarayı yazarak ana sayfadaki yeri belirleyin.
</p>

</div>

<div>

<label class="block text-sm font-bold mb-2">
Kaynak URL
</label>

<input
type="url"
name="url"
value="{esc(firma.get("url", ""))}"
placeholder="İsteğe bağlı: https://..."
class="w-full border border-slate-200 bg-slate-50 focus:bg-white focus:border-slate-400 outline-none rounded-xl px-4 py-3 transition"
>

<div class="text-xs text-slate-500 mt-2">
Kaynak URL zorunlu değildir. Boş bırakabilirsiniz.
</div>

</div>

<div class="flex items-center gap-3">

<input
type="checkbox"
name="aktif"
value="1"
{"checked" if firma.get("aktif", True) else ""}
class="w-5 h-5"
>

<label class="font-semibold">
Firma aktif
</label>

</div>

<div class="flex items-center gap-3">

<input
type="checkbox"
name="otomatik"
value="1"
{"checked" if firma.get("otomatik", True) else ""}
{"disabled" if not scraper_var and not firma.get("url", "").strip() else ""}
class="w-5 h-5"
>

<label class="font-semibold">
Otomatik fiyat çek
</label>

</div>

<div class="text-xs text-slate-500">
Kaynak okuyucu:
{"Özel scraper mevcut" if scraper_var else ("Genel URL okuyucu" if firma.get("url", "").strip() else "URL gerekli")}
</div>

<button
type="submit"
class="w-full bg-slate-900 hover:bg-slate-800 text-white py-3.5 rounded-xl font-black transition shadow-sm"
>
Kaynak Bilgilerini Kaydet
</button>

</form>

<form
method="post"
action="/admin/source/{esc(firma_id)}/test"
class="mt-3"
>

<button
type="submit"
class="w-full border border-blue-200 bg-blue-50 hover:bg-blue-100 text-blue-800 py-3.5 rounded-xl font-black transition"
>
🔎 Kaynağı Test Et
</button>

</form>

</div>

<div class="bg-white rounded-3xl shadow-sm border border-slate-200 p-4 sm:p-6 lg:p-7">

<h2 class="text-xl font-bold mb-5">
Manuel Fiyatlar
</h2>

<form
method="post"
action="/admin/source/{esc(firma_id)}/manual-save-real"
class="space-y-4"
>

{fiyat_rows}

{yeni_manuel_fiyat}

<button
type="submit"
class="w-full bg-blue-600 hover:bg-blue-700 text-white py-3.5 rounded-xl font-black transition shadow-sm"
>
Manuel Fiyatları Kaydet
</button>

</form>

</div>

<div class="bg-white rounded-3xl shadow-sm border border-slate-200 p-4 sm:p-6 lg:p-7">

<h2 class="text-lg font-bold text-red-700 mb-3">
Tehlikeli Bölge
</h2>

<form
method="post"
action="/admin/source/{esc(firma_id)}/delete"
onsubmit="return confirm('Bu firmayı silmek istediğinizden emin misiniz?');"
>

<button
type="submit"
class="w-full bg-red-50 text-red-700 border border-red-200 py-3 rounded-xl font-bold"
>
Firmayı Sil
</button>

</form>

</div>

</div>

</body>

</html>
"""


@app.post(
    "/admin/source/{firma_id}/save"
)
async def admin_source_save(
    firma_id: str,
    baslik: str = Form(...),
    url: str = Form(""),
    aktif: str = Form(None),
    otomatik: str = Form(None),
    sira: int = Form(1),
    username: str = Depends(
        verify_admin
    ),
):

    data = load_data()

    if firma_id not in data.get(
        "firms",
        {},
    ):

        raise HTTPException(
            status_code=404,
            detail="Firma bulunamadı.",
        )

    kayitli_scraper_var = (
        any(
            kayit_id == firma_id
            for kayit_id, _ in TUMU
        )
    )

    baslik = (
        baslik or ""
    ).strip()

    url = (
        url or ""
    ).strip()

    if not baslik:

        raise HTTPException(
            status_code=400,
            detail="Firma adı boş olamaz.",
        )

    # Daha önce silinmiş bir firma aynı ID ile yeniden ekleniyorsa
    # silinmişler listesinden çıkar.
    silinen_firmalar = data.setdefault(
        "silinen_firmalar",
        [],
    )
    data[
        "silinen_firmalar"
    ] = [
        x for x in silinen_firmalar
        if str(x).strip().casefold()
        != str(firma_id).strip().casefold()
    ]

    data[
        "firms"
    ][
        firma_id
    ][
        "baslik"
    ] = baslik

    data[
        "firms"
    ][
        firma_id
    ][
        "url"
    ] = url

    data[
        "firms"
    ][
        firma_id
    ][
        "aktif"
    ] = (
        aktif == "1"
    )

    data[
        "firms"
    ][
        firma_id
    ][
        "otomatik"
    ] = (
        otomatik == "1"
        and (
            kayitli_scraper_var
            or bool(url)
        )
    )

    try:
        yeni_sira = max(
            1,
            int(sira),
        )
    except (TypeError, ValueError):
        yeni_sira = 1

    firma_sirasini_uygula(
        data,
        firma_id,
        yeni_sira,
    )

    save_data(
        data
    )

    # Kaynak ayarlarını kaydetmek ile fiyat çekmeyi ayır.
    # Böylece scraper kaynaklı bir hata, ayar kayıt isteğini 500'e düşürmez.
    if (
        otomatik == "1"
        and not kayitli_scraper_var
        and not url
    ):

        bildirim_ekle(
            firma_id,
            "scraper_bulunamadi",
            (
                "Otomatik çalışma seçildi fakat kaynak URL "
                "girilmedi. Otomatik çekim yapılamaz."
            ),
        )

    elif data["firms"][firma_id].get(
        "otomatik",
        False,
    ):

        bildirim_ekle(
            firma_id,
            "otomatik_ayar",
            (
                "Otomatik fiyat çekme ayarı kaydedildi. "
                "Fiyat çekmek için Kaynağı Test Et / Şimdi Çek "
                "butonunu kullanabilirsiniz."
            ),
        )

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


# =========================================================
# FİRMA SIRALAMA - DOĞRUDAN NUMARA
# =========================================================

@app.post(
    "/admin/source/{firma_id}/order"
)
async def admin_source_order(
    firma_id: str,
    sira: int = Form(...),
    username: str = Depends(
        verify_admin
    ),
):
    data = load_data()

    if firma_id not in data.get("firms", {}):
        raise HTTPException(
            status_code=404,
            detail="Firma bulunamadı.",
        )

    try:
        hedef_sira = max(1, int(sira))
    except (TypeError, ValueError):
        hedef_sira = 1

    firma_sirasini_uygula(
        data,
        firma_id,
        hedef_sira,
    )

    save_data(data)

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


# =========================================================
# FİRMA SIRALAMA
# =========================================================

@app.post(
    "/admin/source/{firma_id}/move"
)
async def admin_source_move(
    firma_id: str,
    direction: str = Form(...),
    username: str = Depends(
        verify_admin
    ),
):

    data = load_data()

    firmalar = firmalari_sirala(
        data
    )

    mevcut_index = None

    for index, firma in enumerate(
        firmalar
    ):

        if firma.get(
            "firma_id"
        ) == firma_id:

            mevcut_index = index
            break

    if mevcut_index is None:

        raise HTTPException(
            status_code=404,
            detail="Firma bulunamadı.",
        )

    yeni_index = mevcut_index

    if direction == "up":

        yeni_index = max(
            0,
            mevcut_index - 1,
        )

    elif direction == "down":

        yeni_index = min(
            len(firmalar) - 1,
            mevcut_index + 1,
        )

    else:

        raise HTTPException(
            status_code=400,
            detail="Geçersiz sıralama yönü.",
        )

    if yeni_index != mevcut_index:
        firma_sirasini_uygula(
            data,
            firma_id,
            yeni_index + 1,
        )

    save_data(
        data
    )

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


# =========================================================
# MANUEL FİYAT KAYDET
# =========================================================

@app.post(
    "/admin/source/{firma_id}/manual-save"
)
async def admin_manual_save(
    firma_id: str,
    username: str = Depends(
        verify_admin
    ),
):

    data = load_data()

    if firma_id not in data.get(
        "firms",
        {},
    ):

        raise HTTPException(
            status_code=404,
            detail="Firma bulunamadı.",
        )

    return RedirectResponse(
        url=f"/admin/source/{firma_id}",
        status_code=303,
    )


# =========================================================
# MANUEL FİYAT KAYDET - GERÇEK
# =========================================================

@app.post(
    "/admin/source/{firma_id}/manual-save-real"
)
async def admin_manual_save_real(
    request: Request,
    firma_id: str,
    username: str = Depends(
        verify_admin
    ),
):

    data = load_data()

    if firma_id not in data.get(
        "firms",
        {},
    ):

        raise HTTPException(
            status_code=404,
            detail="Firma bulunamadı.",
        )

    form = await request.form()

    prices = data.get(
        "prices",
        {},
    ).get(
        firma_id,
        {},
    )

    kaydedilen = 0

    existing_keys = list(
        prices.keys()
    )

    for kalem in existing_keys:

        field_name = (
            "manuel_"
            + kalem
        )

        value = form.get(
            field_name
        )

        if value is None:
            continue

        value = str(
            value
        ).strip()

        if not value:
            continue

        try:

            fiyat = int(
                float(value)
            )

            if fiyat <= 0:
                continue

            manuel_fiyat_kaydet(
                firma_id=firma_id,
                kalem=kalem,
                fiyat=fiyat,
            )

            kaydedilen += 1

        except Exception:

            bildirim_ekle(
                firma_id,
                "manuel_fiyat_hatasi",
                (
                    f"{kalem} için geçersiz "
                    "manuel fiyat girildi."
                ),
            )

    new_kalem = str(
        form.get(
            "new_kalem"
        )
        or ""
    ).strip()

    new_fiyat_raw = str(
        form.get(
            "new_fiyat"
        )
        or ""
    ).strip()

    if (
        new_kalem
        or new_fiyat_raw
    ):

        if not new_kalem:

            bildirim_ekle(
                firma_id,
                "manuel_fiyat_hatasi",
                "Yeni manuel fiyat için kalem adı girilmedi.",
            )

        elif not new_fiyat_raw:

            bildirim_ekle(
                firma_id,
                "manuel_fiyat_hatasi",
                f"{new_kalem} için fiyat girilmedi.",
            )

        else:

            try:

                new_fiyat = int(
                    float(
                        new_fiyat_raw
                    )
                )

                if new_fiyat <= 0:
                    raise ValueError

                data = load_data()

                if "prices" not in data:
                    data["prices"] = {}

                if firma_id not in data[
                    "prices"
                ]:

                    data[
                        "prices"
                    ][
                        firma_id
                    ] = {}

                gizlenen = data.get(
                    "gizlenen_kalemler",
                    {}
                ).get(
                    firma_id,
                    []
                )

                if new_kalem in gizlenen:
                    data["gizlenen_kalemler"][firma_id] = [
                        x for x in gizlenen
                        if x != new_kalem
                    ]

                mevcut = data[
                    "prices"
                ][
                    firma_id
                ].get(
                    new_kalem,
                    {},
                )

                onceki_efektif = (
                    mevcut.get("manuel_fiyat")
                    if mevcut.get("manuel_fiyat") is not None
                    else mevcut.get("otomatik_fiyat")
                )

                gecmise_fiyat_yaz(
                    data,
                    firma_id,
                    new_kalem,
                    onceki_efektif,
                    new_fiyat,
                    now_istanbul().strftime("%Y-%m-%d"),
                )

                data[
                    "prices"
                ][
                    firma_id
                ][
                    new_kalem
                ] = {
                    "otomatik_fiyat":
                        mevcut.get(
                            "otomatik_fiyat"
                        ),

                    "manuel_fiyat":
                        new_fiyat,

                    "fiyat_tarihi":
                        (
                            mevcut.get(
                                "fiyat_tarihi"
                            )
                            or
                            now_istanbul().strftime(
                                "%Y-%m-%d"
                            )
                        ),

                    "guncelleme":
                        now_istanbul_string(),
                }

                save_data(
                    data
                )

                gecmis_ekle(
                    firma_id=firma_id,
                    kalem=new_kalem,
                    fiyat=new_fiyat,
                    fiyat_tarihi=(
                        now_istanbul().strftime(
                            "%Y-%m-%d"
                        )
                    ),
                )

                kaydedilen += 1

            except Exception:

                bildirim_ekle(
                    firma_id,
                    "manuel_fiyat_hatasi",
                    (
                        f"{new_kalem} için geçersiz "
                        "manuel fiyat girildi."
                    ),
                )

    if kaydedilen:

        bildirim_ekle(
            firma_id,
            "manuel_fiyat",
            (
                f"{kaydedilen} manuel fiyat kaydedildi."
            ),
        )

    return RedirectResponse(
        url=f"/admin/source/{firma_id}",
        status_code=303,
    )


# =========================================================
# MANUEL FİYAT GÜNCELLE
# =========================================================

@app.post(
    "/admin/source/{firma_id}/manual-update"
)
async def admin_manual_update(
    request: Request,
    firma_id: str,
    guncelle_kalem: str = Form(...),
    username: str = Depends(
        verify_admin
    ),
):
    data = load_data()

    if firma_id not in data.get(
        "firms",
        {},
    ):
        raise HTTPException(
            status_code=404,
            detail="Firma bulunamadı.",
        )

    kalem = str(        guncelle_kalem
        or ""
    ).strip()
    if not kalem:
        raise HTTPException(
            status_code=400,
            detail="Güncellenecek kalem belirtilmedi.",
        )

    form = await request.form()
    value = form.get(
        "manuel_" + kalem
    )

    value = str(
        value or ""
    ).strip()

    try:
        fiyat = int(
            float(value)
        )

        if fiyat <= 0:
            raise ValueError

    except Exception:
        bildirim_ekle(
            firma_id,
            "manuel_fiyat_hatasi",
            f"{kalem} için geçerli bir manuel fiyat girilmedi.",
        )
        return RedirectResponse(
            url=f"/admin/source/{firma_id}",
            status_code=303,
        )

    prices = data.get(
        "prices",
        {},
    ).get(
        firma_id,
        {},
    )

    if kalem not in prices:
        raise HTTPException(
            status_code=404,
            detail="Güncellenecek fiyat kalemi bulunamadı.",
        )

    manuel_fiyat_kaydet(
        firma_id=firma_id,
        kalem=kalem,
        fiyat=fiyat,
    )

    gecmis_ekle(
        firma_id=firma_id,
        kalem=kalem,
        fiyat=fiyat,
        fiyat_tarihi=now_istanbul().strftime(
            "%Y-%m-%d"
        ),
    )

    bildirim_ekle(
        firma_id,
        "manuel_fiyat",
        f"{kalem} manuel fiyatı {fiyat_format(fiyat)} olarak güncellendi.",
    )

    return RedirectResponse(
        url=f"/admin/source/{firma_id}",
        status_code=303,
    )


# =========================================================
# MANUEL FİYAT SİL
# =========================================================

@app.post(
    "/admin/source/{firma_id}/manual-delete"
)
async def admin_manual_delete(
    firma_id: str,
    manuel_sil: str = Form(...),
    username: str = Depends(
        verify_admin
    ),
):

    data = load_data()

    if firma_id not in data.get(
        "firms",
        {},
    ):

        raise HTTPException(
            status_code=404,
            detail="Firma bulunamadı.",
        )

    kalem = str(
        manuel_sil
        or ""
    ).strip()

    if not kalem:

        raise HTTPException(
            status_code=400,
            detail="Silinecek kalem belirtilmedi.",
        )

    # Storage katmanı gerçek kaydı siler ve
    # otomatik scraper'ın tekrar eklemesini engeller.
    manuel_fiyat_sil(
        firma_id,
        kalem,
    )

    bildirim_ekle(
        firma_id,
        "manuel_fiyat",
        (
            f"{kalem} fiyat listesinden kaldırıldı."
        ),
    )

    return RedirectResponse(
        url=f"/admin/source/{firma_id}",
        status_code=303,
    )

# =========================================================
# FİRMA SİL
# =========================================================

@app.post(
    "/admin/source/{firma_id}/delete"
)
async def admin_source_delete(
    firma_id: str,
    username: str = Depends(
        verify_admin
    ),
):

    data = load_data()

    firms = data.get(
        "firms",
        {}
    )

    gercek_firma_id = firma_id

    if gercek_firma_id not in firms:

        for mevcut_id in list(
            firms.keys()
        ):
            if (
                str(mevcut_id).casefold()
                == str(firma_id).casefold()
            ):
                gercek_firma_id = mevcut_id
                break

    if gercek_firma_id not in firms:

        raise HTTPException(
            status_code=404,
            detail="Firma bulunamadı.",
        )

    # =====================================================
    # FİRMAYI ADMİN LİSTESİNDEN SİL
    # =====================================================

    firms.pop(
        gercek_firma_id,
        None,
    )

    # Otomatik kaynaklar her dakika tekrar tarandığı için,
    # silinen firmayı scraper'ın yeniden oluşturmasını engelle.
    silinen_firmalar = data.setdefault(
        "silinen_firmalar",
        [],
    )

    silinen_id = str(
        gercek_firma_id
    ).strip().casefold()

    if silinen_id not in {
        str(x).strip().casefold()
        for x in silinen_firmalar
    }:
        silinen_firmalar.append(
            gercek_firma_id
        )

    # =====================================================
    # FİRMAYA AİT FİYAT KAYITLARINI SİL
    # =====================================================

    data.get(
        "prices",
        {},
    ).pop(
        gercek_firma_id,
        None,
    )

    data.get(
        "gizlenen_kalemler",
        {},
    ).pop(
        gercek_firma_id,
        None,
    )

    # =====================================================
    # FİRMAYA AİT GEÇMİŞ FİYATLARI SİL
    # =====================================================

    data["history"] = [
        item
        for item in data.get(
            "history",
            [],
        )
        if item.get(
            "firma_id"
        ) != gercek_firma_id
    ]

    # =====================================================
    # KALAN FİRMALARIN SIRASINI DÜZELT
    # =====================================================

    firma_siralarini_duzelt(
        data
    )

    # =====================================================
    # VERİYİ KAYDET
    # =====================================================

    save_data(
        data
    )

    # =====================================================
    # ADMİN PANELİNE GERİ DÖN
    # =====================================================

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


# =========================================================
# KAYNAK TEST
# =========================================================

@app.post(
    "/admin/source/{firma_id}/test"
)
async def admin_source_test(
    firma_id: str,
    username: str = Depends(
        verify_admin
    ),
):

    data = load_data()

    firma = data.get(
        "firms",
        {},
    ).get(
        firma_id
    )

    if not firma:

        raise HTTPException(
            status_code=404,
            detail="Firma bulunamadı.",
        )

    fonksiyon = firma_scraperini_bul(
        firma_id,
        data,
    )

    if fonksiyon is None:

        bildirim_ekle(
            firma_id,
            "kaynak_testi",
            (
                "Bu firma için otomatik scraper "
                "bulunamadı. Kaynak manuel olarak kullanılabilir."
            ),
        )

        return RedirectResponse(
            url=f"/admin/source/{firma_id}",
            status_code=303,
        )

    try:

        sonuc = firma_verisini_cek(
            fonksiyon
        )

        bildirim_ekle(
            firma_id,
            "basarili_guncelleme",
            (
                "Kaynak testi başarılı. "
                f"{len(sonuc['kalemler'])} "
                "fiyat kalemi okundu."
            ),
        )

    except Exception as e:

        data = load_data()

        if firma_id in data.get(
            "firms",
            {},
        ):

            firma_hata_isle(data, firma_id, e)

            save_data(
                data
            )

        bildirim_ekle(
            firma_id,
            "kaynak_testi_hatasi",
            str(e),
        )

    return RedirectResponse(
        url=f"/admin/source/{firma_id}",
        status_code=303,
    )


# =========================================================
# BİLDİRİMLERİ OKUNDU
# =========================================================

@app.post(
    "/admin/notifications/read"
)
async def notifications_read(
    username: str = Depends(
        verify_admin
    ),
):

    bildirimleri_okundu_yap()

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


# =========================================================
# TEK BİLDİRİMİ OKUNDU YAP
# =========================================================

@app.post(
    "/admin/notifications/read-one"
)
async def notification_read_one(
    notification_id: int = Form(...),
    username: str = Depends(
        verify_admin
    ),
):

    bildirim_okundu(
        notification_id
    )

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


# =========================================================
# BİLDİRİM SİL
# =========================================================

@app.post(
    "/admin/notifications/delete"
)
async def notifications_delete(
    notification_id: int = Form(...),
    username: str = Depends(
        verify_admin
    ),
):

    bildirim_sil(
        notification_id
    )

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


# =========================================================
# TÜM BİLDİRİMLERİ SİL
# =========================================================

@app.post(
    "/admin/notifications/delete-all"
)
async def notifications_delete_all(
    username: str = Depends(
        verify_admin
    ),
):

    bildirimleri_sil()

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


# =========================================================
# ADMİN PANELİ
# =========================================================

@app.get(
    "/admin",
    response_class=HTMLResponse,
)
def admin_panel(
    username: str = Depends(
        verify_admin
    ),
    m: str = "",
):

    data = load_data()

    banner_metinleri = {
        "baslatildi": "Tüm kaynaklar için güncelleme başlatıldı. Birkaç dakika içinde sonuçlar işlenecek.",
        "calisiyor": "Güncelleme zaten çalışıyor, bitmesini bekleyin.",
        "sifre": "Şifre değiştirildi.",
        "yedek": "Yeni yedek alındı (yerel + Supabase).",
        "lme_grafik_acik": "LME grafiği: açık gelecek şekilde ayarlandı.",
        "lme_grafik_kapali": "LME grafiği: kapalı gelecek, düğmeyle açılacak.",
        "lme_grafik_pasif": "LME grafiği pasif edildi; ana sayfada gizlenir.",
    }
    banner_html = (
        '<div class="mb-4 rounded-2xl bg-emerald-50 border border-emerald-200 text-emerald-800 text-sm font-bold p-3">'
        + esc(banner_metinleri[m])
        + "</div>"
        if m in banner_metinleri
        else ""
    )

    push_abone_sayisi = len(
        ((data.get("push") or {}).get("abonelikler") or {})
    )
    push_alarm_sayisi = sum(
        len(k.get("alarmlar", []))
        for k in ((data.get("push") or {}).get("abonelikler") or {}).values()
    )
    sifre_kaynagi = (
        "Panelden belirlendi"
        if (data.get("admin_auth") or {}).get("hash")
        else ("VARSAYILAN (değiştirin!)" if ADMIN_PASS == "hurda123" else "Ortam değişkeni")
    )
    sifre_renk = "text-red-600" if sifre_kaynagi.startswith("VARSAYILAN") else "text-slate-900"
    guncelleme_durumu = "ÇALIŞIYOR" if _GUNCELLEME_KILIDI.locked() else "BEKLİYOR"
    supabase_durumu = (
        ("SENKRON" if storage_module._SUPABASE_DATA_SYNCED else "BEKLİYOR (yeniden denenecek)")
        if storage_module._supabase_enabled()
        else "KAPALI"
    )

    # Admin panelini açmak mevcut fiyat/veri dosyasını değiştirmemelidir.
    # Sıralama yalnızca ekranda uygulanır.
    firmalar = firmalari_sirala(
        data
    )

    notifications = data.get(
        "notifications",
        [],
    )

    okunmamis = len(
        [
            x
            for x in notifications
            if not x.get(
                "okundu",
                False,
            )
        ]
    )

    basarili = len(
        [
            x
            for x in firmalar
            if x.get(
                "durum"
            ) == "basarili"
            and not firma_stale_mi(
                x
            )
        ]
    )

    hatali = len(
        [
            x
            for x in firmalar
            if x.get(
                "durum"
            ) == "hata"
            or firma_stale_mi(
                x
            )
        ]
    )

    manuel = len(
        [
            x
            for x in firmalar
            if not x.get(
                "otomatik",
                True,
            )
        ]
    )

    aktif = len(
        [
            x
            for x in firmalar
            if x.get(
                "aktif",
                True,
            )
        ]
    )

    guncellenen_24_saat = [
        x for x in firmalar
        if son_24_saatte_mi(
            x.get("son_basarili_cekme")
        )
    ]

    firma_guncelleme_siralama = sorted(
        guncellenen_24_saat,
        key=lambda x: str(
            x.get("son_basarili_cekme") or ""
        ),
        reverse=True,
    )

    guncelleme_rows = "".join(
        (
            '<div class="rounded-2xl border border-slate-200 bg-slate-50 p-3">'
            '<div class="font-black text-sm text-slate-900 break-words">'
            + esc(firma.get("baslik", firma.get("firma_id", "-")))
            + '</div>'
            '<div class="text-[10px] text-slate-500 mt-1">Son başarılı çekim</div>'
            '<div class="text-xs font-black text-emerald-700 mt-0.5">'
            + esc(firma.get("son_basarili_cekme") or "-")
            + '</div>'
            '</div>'
        )
        for firma in firma_guncelleme_siralama
    )

    if not guncelleme_rows:
        guncelleme_rows = '''
<div class="sm:col-span-2 lg:col-span-3 rounded-xl bg-slate-50 border border-slate-200 p-4 text-sm text-slate-500">
Son 24 saatte başarılı fabrika güncellemesi bulunmuyor.
</div>
'''

    firma_rows = ""

    for index, firma in enumerate(
        firmalar
    ):

        firma_id = firma.get(
            "firma_id",
            "",
        )

        baslik = firma.get(
            "baslik",
            firma_id,
        )

        durum = firma.get(
            "durum",
            "bekliyor",
        )

        stale = firma_stale_mi(
            firma
        )

        if stale:

            durum_html = """
<span class="px-2 py-1 rounded-lg bg-amber-100 text-amber-800 text-xs font-bold">
STALE
</span>
"""

        elif durum == "basarili":

            durum_html = """
<span class="px-2 py-1 rounded-lg bg-emerald-100 text-emerald-800 text-xs font-bold">
BAŞARILI
</span>
"""

        elif durum == "hata":

            durum_html = """
<span class="px-2 py-1 rounded-lg bg-red-100 text-red-800 text-xs font-bold">
HATA
</span>
"""

        else:

            durum_html = """
<span class="px-2 py-1 rounded-lg bg-slate-100 text-slate-700 text-xs font-bold">
BEKLİYOR
</span>
"""

        firma_aktif = firma.get(
            "aktif",
            True,
        )

        otomatik = firma.get(
            "otomatik",
            True,
        )

        son_cekim = firma.get(
            "son_basarili_cekme"
        ) or "-"

        hata_gecmisi = firma.get("hata_gecmisi") or []
        hata_html = ""

        if hata_gecmisi:
            son = hata_gecmisi[-1]
            satirlar = "".join(
                '<li class="mt-1"><span class="font-bold">'
                + esc(h.get("zaman", ""))
                + "</span>"
                + (
                    f' <span class="text-slate-400">×{h.get("adet")}</span>'
                    if h.get("adet", 1) > 1
                    else ""
                )
                + " — "
                + esc(h.get("mesaj", ""))
                + "</li>"
                for h in reversed(hata_gecmisi)
            )
            hata_html = (
                '<details class="mt-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-2 py-1.5">'
                '<summary class="cursor-pointer font-bold">'
                + ("Son hata: " if durum == "hata" else "Geçmiş hata: ")
                + esc(son.get("zaman", ""))
                + "</summary><ul class=\"mt-1 break-words\">"
                + satirlar
                + "</ul></details>"
            )

        yukari_disabled = (
            index == 0
        )

        asagi_disabled = (
            index == len(
                firmalar
            ) - 1
        )

        son24_html = (
            '<span class="inline-flex items-center rounded-md bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 text-[10px] font-black text-emerald-700">24 SAATTE GÜNCELLENDİ</span>'
            if son_24_saatte_mi(son_cekim)
            else ""
        )

        firma_rows += f"""
<div data-firma-row="{esc(str(baslik) + " " + str(firma_id))}" class="firma-kart">

<div class="flex items-start justify-between gap-2">

<div class="min-w-0">

<div class="font-bold text-slate-900 truncate">{esc(baslik)}</div>

<div class="text-[11px] text-slate-500 truncate">ID: {esc(firma_id)} · Son çekim: {esc(son_cekim)}</div>

</div>

<div class="flex flex-wrap justify-end gap-1 shrink-0 text-[11px]">

{durum_html}

<span class="px-2 py-1 rounded-lg bg-slate-100 text-slate-700 text-xs font-bold">{"AKTİF" if firma_aktif else "PASİF"}</span>

<span class="px-2 py-1 rounded-lg bg-indigo-100 text-indigo-800 text-xs font-bold">{"OTOMATİK" if otomatik else "MANUEL"}</span>

</div>

</div>

{son24_html}

{hata_html}

<div class="flex flex-wrap items-center gap-1.5">

<form method="post" action="/admin/source/{esc(firma_id)}/order" class="inline-flex items-center gap-1.5">
<label class="text-[11px] text-indigo-700 font-black" title="Ana sayfadaki sıra">Sıra</label>
<input type="number" name="sira" value="{index + 1}" min="1" step="1" required class="w-14 h-8 rounded-lg border border-indigo-200 bg-white px-1 text-sm font-black text-indigo-800 text-center outline-none focus:border-indigo-500">
<button type="submit" class="eylem bg-indigo-600 hover:bg-indigo-700 text-white" title="Sırayı kaydet">✓</button>
</form>

<form method="post" action="/admin/source/{esc(firma_id)}/move" class="inline">
<input type="hidden" name="direction" value="up">
<button type="submit" title="Yukarı taşı" {"disabled" if yukari_disabled else ""} class="eylem ok-btn {"opacity-40 cursor-not-allowed" if yukari_disabled else "hover:bg-slate-200"}">↑</button>
</form>

<form method="post" action="/admin/source/{esc(firma_id)}/move" class="inline">
<input type="hidden" name="direction" value="down">
<button type="submit" title="Aşağı taşı" {"disabled" if asagi_disabled else ""} class="eylem ok-btn {"opacity-40 cursor-not-allowed" if asagi_disabled else "hover:bg-slate-200"}">↓</button>
</form>

<a href="/admin/source/{esc(firma_id)}" class="eylem bg-slate-900 hover:bg-slate-800 text-white">Düzenle</a>

<form method="post" action="/admin/source/{esc(firma_id)}/test" class="inline">
<button type="submit" class="eylem bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200">Test</button>
</form>

<form method="post" action="/admin/source/{esc(firma_id)}/delete" class="inline" onsubmit="return confirm('Bu firmayı silmek istediğinizden emin misiniz?');">
<button type="submit" class="eylem bg-red-50 text-red-700">Sil</button>
</form>

</div>

</div>
"""

    if not firma_rows:

        firma_rows = """
<div class="bg-slate-50 border border-slate-200 rounded-xl p-5 text-slate-500">
Henüz firma bulunmuyor.
</div>
"""

    notification_rows = ""

    for item in reversed(
        notifications[
            -30:
        ]
    ):

        notification_read_action = ""
        if not item.get("okundu", False):
            notification_read_action = f"""
<form
method="post"
action="/admin/notifications/read-one"
class="inline"
>
<input
type="hidden"
name="notification_id"
value="{esc(item.get("id", ""))}"
>
<button
type="submit"
class="px-3 py-1.5 rounded-lg bg-emerald-50 hover:bg-emerald-100 text-emerald-700 text-xs font-bold"
>
Okundu
</button>
</form>
"""

        notification_rows += f"""
<div class="border border-slate-200 bg-slate-50/50 rounded-xl p-4 hover:bg-white transition">

<div class="flex flex-col sm:flex-row sm:justify-between gap-3">

<div class="min-w-0">

<div class="font-bold text-sm text-slate-900 break-words">
{esc(item.get("tur", "-"))}
</div>

<div class="text-sm text-slate-600 mt-1 break-words">
{esc(item.get("mesaj", ""))}
</div>

</div>

<div class="flex flex-row sm:flex-col items-center sm:items-end gap-2 shrink-0">

<div class="text-xs text-slate-400 whitespace-nowrap">
{esc(item.get("tarih", ""))}
</div>

{notification_read_action}
<form
method="post"
action="/admin/notifications/delete"
onsubmit="return confirm('Bu bildirimi silmek istediğinizden emin misiniz?');"
>
<input
type="hidden"
name="notification_id"
value="{esc(item.get("id", ""))}"
>
<button
type="submit"
class="px-3 py-1.5 rounded-lg bg-red-50 hover:bg-red-100 text-red-700 text-xs font-bold"
>
Sil
</button>
</form>

</div>

</div>

</div>
"""

    if not notification_rows:

        notification_rows = """
<div class="text-sm text-slate-500">
Bildirim bulunmuyor.
</div>
"""

    ads = load_ads()

    ad_form_fields = ""

    for key, label in [
        (
            "left_top",
            "Hesaplama Altı 1",
        ),
        (
            "left_middle",
            "Hesaplama Altı 2",
        ),
        (
            "left_bottom",
            "Hesaplama Altı 3",
        ),
        (
            "right_top",
            "Fiyat Geçmişi Altı 1",
        ),
        (
            "right_middle",
            "Fiyat Geçmişi Altı 2",
        ),
        (
            "right_bottom",
            "Fiyat Geçmişi Altı 3",
        ),
    ]:

        ad = ads[
            key
        ]

        mevcut_gorsel = ""

        if ad.get(
            "image_url"
        ):

            mevcut_gorsel = f"""
<div class="mt-3">

<div class="text-xs font-semibold text-slate-500 mb-2">
Mevcut Görsel
</div>
<div class="bg-slate-50 border border-slate-200 rounded-xl p-2">
<img
src="{esc(ad.get("image_url", ""))}"
alt="{esc(ad.get("title", label))}"class="w-full max-h-32 object-contain rounded-lg"
>
</div>

</div>
"""

        ad_form_fields += f"""
<div class="admin-banner-card border border-slate-200 rounded-2xl p-4 sm:p-5 space-y-3 bg-slate-50/60 hover:bg-white hover:shadow-md transition">

<form
method="post"
action="/admin/update-ads"
enctype="multipart/form-data"
class="space-y-3"
>

<input
type="hidden"
name="banner_key"
value="{key}"
>

<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">

<div class="font-bold text-slate-900 text-lg">
{label}
</div>

<div class="text-xs bg-white border border-slate-200 text-slate-500 px-2.5 py-1.5 rounded-lg font-semibold">
{key}
</div>

</div>

<div class="text-xs text-slate-500">
Bilgisayarınızdan banner seçin. Görsel otomatik olarak siteye yüklenecektir.
</div>

<input
type="text"
name="{key}_title"
value="{esc(ad.get("title", ""))}"
placeholder="Banner başlığı"
class="w-full border border-slate-300 rounded-xl px-3 py-3 text-sm"
>

<label class="block text-sm font-bold text-slate-700">
Banner Görseli
</label>

<input
type="file"
name="{key}_file"
accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
class="w-full border border-slate-300 rounded-xl px-3 py-3 text-sm bg-white"
>

<div class="text-xs text-slate-500">
JPG, JPEG, PNG veya WEBP seçebilirsiniz.
</div>

{mevcut_gorsel}

<input
type="url"
name="{key}_target_url"
value="{esc(ad.get("target_url", ""))}"
placeholder="Resme tıklanınca açılacak bağlantı (isteğe bağlı)"
class="w-full border border-slate-300 rounded-xl px-3 py-3 text-sm"
>

<div class="text-xs text-slate-500">
Bu alan sadece resme tıklandığında gidilecek adres içindir.
</div>

<label class="flex items-center gap-2 text-sm font-semibold">

<input
type="checkbox"
name="{key}_active"
value="1"
{"checked" if ad.get("active") else ""}
class="w-5 h-5"
>

Banner aktif

</label>

<div class="flex flex-col sm:flex-row gap-2">
<button
type="submit"
class="w-full sm:w-auto bg-slate-900 hover:bg-slate-800 text-white px-5 py-3 rounded-xl font-bold text-sm transition"
>
Bannerı Kaydet
</button>
<button
type="submit"
name="banner_delete"
value="1"
formnovalidate
onclick="return confirm('Bu banner görselini kaldırmak istediğinizden emin misiniz?');"
class="w-full sm:w-auto border border-red-200 bg-red-50 hover:bg-red-100 text-red-700 px-5 py-3 rounded-xl font-bold text-sm transition"
>
Bannerı Kaldır
</button>
</div>

</form>

</div>
"""

    html = f"""
<!DOCTYPE html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width, initial-scale=1.0"
>

<title>Hurda Fiyatları - Admin</title>

<script src="https://cdn.tailwindcss.com"></script>

<style>

.admin-tabs {{
    position: sticky;
    top: 0;
    z-index: 30;
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding: 8px;
    border-radius: 18px;
    background: rgba(255, 255, 255, .92);
    border: 1px solid #e2e8f0;
    box-shadow: 0 6px 18px rgba(15, 23, 42, .08);
    backdrop-filter: blur(8px);
    -webkit-overflow-scrolling: touch;
}}

html.dark .admin-tabs {{
    background: rgba(17, 26, 46, .92);
    border-color: #24324d;
}}

.admin-tab {{
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 40px;
    padding: 0 16px;
    border: 0;
    border-radius: 12px;
    background: transparent;
    color: #475569;
    font-size: 13px;
    font-weight: 800;
    white-space: nowrap;
    cursor: pointer;
    transition: background .15s ease, color .15s ease;
}}

.admin-tab:hover {{ background: #f1f5f9; }}

.admin-tab.aktif {{
    background: #0f172a;
    color: #fff;
}}

html.dark .admin-tab {{ color: #9aa8bf; }}
html.dark .admin-tab:hover {{ background: #162036; }}
html.dark .admin-tab.aktif {{ background: #2563eb; color: #fff; }}

.admin-tab .rozet {{
    min-width: 20px;
    height: 20px;
    padding: 0 6px;
    border-radius: 999px;
    background: #ef4444;
    color: #fff;
    font-size: 11px;
    line-height: 20px;
    text-align: center;
}}

.admin-tab .sayi {{
    min-width: 20px;
    height: 20px;
    padding: 0 6px;
    border-radius: 999px;
    background: rgba(100, 116, 139, .18);
    font-size: 11px;
    line-height: 20px;
    text-align: center;
}}

.firma-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 430px), 1fr));
    gap: 12px;
}}

.firma-kart {{
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 14px;
    border-radius: 16px;
    border: 1px solid #e2e8f0;
    background: rgba(248, 250, 252, .6);
    transition: box-shadow .15s ease;
}}

.firma-kart:hover {{ box-shadow: 0 6px 18px rgba(15, 23, 42, .08); }}
html.dark .firma-kart {{ background: #0e1627; border-color: #24324d; }}

.firma-kart .eylem {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 32px;
    padding: 0 11px;
    border-radius: 9px;
    font-size: 12px;
    font-weight: 800;
    white-space: nowrap;
}}

.firma-kart .ok-btn {{
    width: 32px;
    padding: 0;
    border: 1px solid #e2e8f0;
    background: #f8fafc;
    color: #334155;
    font-size: 15px;
}}

html.dark .firma-kart .ok-btn {{ background: #162036; border-color: #24324d; color: #cbd5e1; }}

@media (max-width: 560px) {{
    .firma-kart > div:first-child {{
        flex-direction: column;
        align-items: stretch;
    }}

    .firma-kart > div:first-child > div:last-child {{
        justify-content: flex-start;
    }}
}}

.admin-bolum[hidden] {{ display: none !important; }}


html,
body {{
    width: 100%;
    max-width: 100%;
    overflow-x: hidden;
}}

* {{
    box-sizing: border-box;
}}

.admin-compact .max-w-7xl {{
    gap: 12px;
}}

.admin-compact .max-w-7xl > .bg-slate-900 {{
    padding: 14px 18px !important;
    border-radius: 18px !important;
}}

.admin-compact .max-w-7xl > .bg-white {{
    padding: 14px 16px !important;
    border-radius: 18px !important;
}}

.admin-compact h1 {{
    font-size: 1.35rem !important;
}}

.admin-compact h2 {{
    font-size: 1rem !important;
}}

.admin-compact .space-y-3 > * + * {{
    margin-top: 8px !important;
}}

.admin-compact .space-y-5 > * + * {{
    margin-top: 12px !important;
}}

.admin-compact .space-y-6 > * + * {{
    margin-top: 14px !important;
}}

.admin-compact [data-firma-row] {{
    padding: 10px 12px !important;
}}

.admin-compact [data-firma-row] .text-lg {{
    font-size: .95rem !important;
}}

.admin-compact .admin-banner-grid {{
    display: grid;
    grid-template-columns: repeat(1, minmax(0, 1fr));
    gap: 10px;
}}

@media (min-width: 1024px) {{
    .admin-compact .admin-banner-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
}}

.admin-compact .admin-banner-card {{
    padding: 11px !important;
    border-radius: 14px !important;
}}

.admin-compact .admin-banner-card input[type="text"],
.admin-compact .admin-banner-card input[type="url"],
.admin-compact .admin-banner-card input[type="file"] {{
    padding-top: 8px !important;
    padding-bottom: 8px !important;
}}

.admin-compact .admin-banner-card img {{
    max-height: 120px !important;
}}

.break-anywhere {{
    overflow-wrap: anywhere;
    word-break: break-word;
}}

/* =====================================================
   PİYASA ÖZETİ — MEVCUT VERİDEN GÖRSEL ÖZET
   ===================================================== */

.market-summary {{
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 10px;
    margin-bottom: 14px;
}}

.market-summary-card {{
    min-width: 0;
    background: rgba(255,255,255,.98);
    border: 1px solid #e2e8f0;
    border-radius: 18px;
    padding: 13px 14px;
    box-shadow: 0 8px 22px rgba(15,23,42,.055);
}}

.market-summary-label {{
    color: #64748b;
    font-size: 9px;
    line-height: 1.2;
    font-weight: 900;
    letter-spacing: .10em;
    text-transform: uppercase;
}}

.market-summary-value {{
    color: #0f172a;
    font-size: 21px;
    line-height: 1.15;
    font-weight: 950;
    letter-spacing: -.025em;
    margin-top: 6px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}}

.market-summary-sub {{
    color: #94a3b8;
    font-size: 9px;
    font-weight: 700;
    margin-top: 4px;
}}

.market-summary-card.up .market-summary-value {{
    color: #059669;
}}

.market-summary-card.down .market-summary-value {{
    color: #dc2626;
}}

.market-summary-card.update .market-summary-value {{
    font-size: 13px;
    letter-spacing: -.01em;
}}

.market-design #todayChanges {{
    background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
    border: 1px solid #e2e8f0;
    border-radius: 18px;
    padding: 12px;
    box-shadow: 0 8px 22px rgba(15,23,42,.045);
    max-height: 320px;
    overflow-y: auto;
    scrollbar-width: thin;
}}

.market-design #todayChanges > div:first-child {{
    color: #0f172a;
    font-size: 10px;
    letter-spacing: .10em;
    margin-bottom: 9px;
}}

.market-design #todayChanges .today-change-card {{
    min-height: 74px;
    transition: transform .16s ease, box-shadow .16s ease;
}}

.market-design #todayChanges .today-change-card:hover {{
    transform: translateY(-2px);
    box-shadow: 0 8px 18px rgba(15,23,42,.07);
}}

@media (max-width: 900px) {{
    .market-summary {{
        grid-template-columns: repeat(3, minmax(0, 1fr));
    }}
}}

@media (max-width: 639px) {{
    .market-summary {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
        margin-bottom: 10px;
    }}

    .market-summary-card {{
        border-radius: 15px;
        padding: 11px 12px;
    }}

    .market-summary-value {{
        font-size: 18px;
    }}

    .market-summary-card.update {{
        grid-column: span 2;
    }}
}}

/* =========================================================
   FABRİKA KARTLARI — OKUNAKLI VE SABİT LAYOUT
   ========================================================= */
.market-design .factory-price-grid > .price-card {{
    display: block !important;
    width: 100% !important;
    min-width: 0 !important;
    max-width: none !important;
}}

.market-design .factory-price-grid .firma-toggle {{
    display: block !important;
    width: 100% !important;
    min-width: 0 !important;
    height: auto !important;
    min-height: 124px !important;
    padding: 14px !important;
    text-align: left !important;
    background: #ffffff !important;
    border: 0 !important;
}}

.market-design .factory-price-grid .firma-toggle:hover {{
    background: #f8fafc !important;
}}

.market-design .factory-price-grid .factory-card-header {{
    display: flex !important;
    flex-direction: column !important;
    width: 100% !important;
    min-width: 0 !important;
    gap: 12px !important;
    text-align: left !important;
}}

.market-design .factory-price-grid .factory-card-main {{
    display: flex !important;
    width: 100% !important;
    min-width: 0 !important;
    align-items: center !important;
    justify-content: flex-start !important;
}}

.market-design .factory-price-grid .factory-card-info {{
    display: block !important;
    width: 100% !important;
    min-width: 0 !important;
}}

.market-design .factory-price-grid .factory-card-title-line {{
    display: flex !important;
    width: 100% !important;
    min-width: 0 !important;
    align-items: center !important;
    justify-content: flex-start !important;
}}

.market-design .factory-price-grid .factory-card-title {{
    display: block !important;
    width: 100% !important;
    min-width: 0 !important;
    max-width: none !important;
    color: #0f172a !important;
    font-size: 16px !important;
    font-weight: 900 !important;
    line-height: 1.3 !important;
    white-space: normal !important;
    word-break: normal !important;
    overflow-wrap: anywhere !important;
}}

.market-design .factory-price-grid .factory-card-change {{
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    min-height: 28px !important;
    padding: 0 8px !important;
    border-radius: 10px !important;
    font-size: 9px !important;
    font-weight: 900 !important;
    white-space: nowrap !important;
}}

.market-design .factory-price-grid .factory-card-change-up {{
    color: #047857 !important;
    background: #ecfdf5 !important;
    border: 1px solid #a7f3d0 !important;
}}

.market-design .factory-price-grid .factory-card-change-down {{
    color: #b91c1c !important;
    background: #fef2f2 !important;
    border: 1px solid #fecaca !important;
}}

.market-design .factory-price-grid .factory-card-actions {{
    display: flex !important;
    width: 100% !important;
    min-width: 0 !important;
    max-width: none !important;
    align-items: center !important;
    justify-content: flex-start !important;
    gap: 8px !important;
    margin-top: 0 !important;
    padding-top: 10px !important;
    border-top: 1px solid #e2e8f0 !important;
}}

.market-design .factory-price-grid .factory-card-actions > * {{
    flex: 0 0 auto !important;
}}

.market-design .factory-price-grid .factory-card-price-badge {{
    display: inline-flex !important;
    align-items: center !important;
    gap: 8px !important;
    min-width: 0 !important;
    padding: 7px 11px !important;
    border-radius: 10px !important;
    background: #f1f5f9 !important;
    border: 1px solid #cbd5e1 !important;
    box-shadow: 0 2px 6px rgba(15,23,42,.05) !important;
}}

.market-design .factory-price-grid .factory-card-price-label {{
    font-size: 9px !important;
    font-weight: 900 !important;
    text-transform: uppercase !important;
    letter-spacing: .07em !important;
    color: #475569 !important;
}}

.market-design .factory-price-grid .factory-card-price {{
    font-size: 13px !important;
    font-weight: 900 !important;
    color: #0f172a !important;
    white-space: nowrap !important;
}}

.market-design .factory-price-grid .factory-card-updated {{
    display: inline-flex !important;
    align-items: center !important;
    padding: 7px 10px !important;
    border-radius: 10px !important;
    background: #ecfdf5 !important;
    border: 1px solid #86efac !important;
    color: #047857 !important;
    font-size: 9px !important;
    font-weight: 900 !important;
    white-space: nowrap !important;
}}

.market-design .factory-price-grid .firma-ok-icon {{
    width: 36px !important;
    height: 36px !important;
    margin-left: auto !important;
    border: 1px solid #cbd5e1 !important;
    background: #f8fafc !important;
    color: #334155 !important;
    border-radius: 10px !important;
    box-shadow: 0 2px 6px rgba(15,23,42,.06) !important;
    font-weight: 900 !important;
}}

.market-design .factory-price-grid .firma-toggle:hover .firma-ok-icon {{
    background: #e2e8f0 !important;
    border-color: #94a3b8 !important;
    color: #0f172a !important;
}}

@media (max-width: 639px) {{
    .market-design .factory-price-grid .firma-toggle {{
        min-height: 116px !important;
        padding: 12px !important;
    }}

    .market-design .factory-price-grid .factory-card-header {{
        gap: 10px !important;
    }}

    .market-design .factory-price-grid .factory-card-actions {{
        gap: 6px !important;
        padding-top: 8px !important;
    }}

    .market-design .factory-price-grid .factory-card-price-badge {{
        padding: 6px 8px !important;
        gap: 5px !important;
    }}

    .market-design .factory-price-grid .factory-card-price-label {{
        display: none !important;
    }}

    .market-design .factory-price-grid .factory-card-price {{
        font-size: 11px !important;
    }}

    .market-design .factory-price-grid .factory-card-updated {{
        padding: 6px 8px !important;
        font-size: 8px !important;
    }}

    .market-design .factory-price-grid .firma-ok-icon {{
        width: 34px !important;
        height: 34px !important;
    }}
}}

.market-design .factory-price-grid .factory-price-panel:not(.hidden) {{
    display: block !important;
    width: 100% !important;
    min-width: 0 !important;
    max-width: none !important;
}}

.market-design .factory-price-grid .factory-price-panel:not(.hidden) > .factory-price-panel {{
    display: block !important;
    width: 100% !important;
    min-width: 0 !important;
    max-width: none !important;
}}

.market-design .factory-price-grid .factory-price-list {{
    display: block !important;
    width: 100% !important;
    min-width: 0 !important;
    max-width: none !important;
}}

.market-design .factory-price-grid .factory-price-row {{
    display: grid !important;
    grid-template-columns: minmax(0, 1fr) auto !important;
    width: 100% !important;
    min-width: 0 !important;
    align-items: center !important;
    column-gap: 10px !important;
}}

.market-design .factory-price-grid .factory-price-name {{
    display: block !important;
    width: 100% !important;
    min-width: 0 !important;
}}

.market-design .factory-price-grid .factory-price-value {{
    display: block !important;
    width: auto !important;
    min-width: 105px !important;
    max-width: none !important;
    justify-self: end !important;
    text-align: right !important;
}}

@media (max-width: 639px) {{
    .market-design .factory-price-grid .firma-toggle {{
        min-height: 0 !important;
    }}

    .market-design .factory-price-grid .factory-card-actions {{
        justify-content: space-between !important;
    }}

    .market-design .factory-price-grid .factory-price-row {{
        grid-template-columns: minmax(0, 1fr) auto !important;
    }}

    .market-design .factory-price-grid .factory-price-value {{
        min-width: 88px !important;
    }}
}}


</style>

</head>

<body class="admin-compact bg-slate-100 min-h-screen p-3 sm:p-4 text-slate-900">

<div class="max-w-7xl mx-auto space-y-5 sm:space-y-6">

<div class="bg-slate-900 rounded-3xl shadow-xl p-5 sm:p-6 flex flex-col md:flex-row md:items-center md:justify-between gap-4">

<div class="min-w-0">

<h1 class="text-2xl sm:text-3xl font-black text-white tracking-tight">
Hurda Fiyatları
</h1>

<p class="text-sm text-slate-300 mt-1">
Yönetim Paneli
</p>

</div>

<div class="flex flex-wrap gap-2">

<a
href="/"
class="bg-white/10 border border-white/20 text-white hover:bg-white/15 px-4 py-2.5 rounded-xl text-sm font-bold transition"
>
← Ana Sayfa
</a>

<a
href="/admin/sifre"
class="bg-white/10 border border-white/20 text-white hover:bg-white/15 px-4 py-2.5 rounded-xl text-sm font-bold transition"
>
🔑 Şifre
</a>

<a
href="/admin/cikis"
class="bg-white/10 border border-white/20 text-white hover:bg-white/15 px-4 py-2.5 rounded-xl text-sm font-bold transition"
>
Çıkış
</a>

<a
href="/admin/source/new"
class="bg-white text-slate-900 hover:bg-slate-100 px-4 py-2.5 rounded-xl text-sm font-black transition shadow-sm"
>
+ Yeni Kaynak
</a>

<a
href="/admin/data-backups"
class="bg-amber-400 text-slate-950 hover:bg-amber-300 px-4 py-2.5 rounded-xl text-sm font-black transition shadow-sm"
>
↩ Veri Yedekleri
</a>

</div>

</div>

<nav class="admin-tabs" id="adminTabs" aria-label="Yönetim bölümleri">
<button type="button" class="admin-tab" data-tab="genel">📊 Genel Bakış</button>
<button type="button" class="admin-tab" data-tab="firmalar">🏭 Firmalar <span class="sayi">{len(firmalar)}</span></button>
<button type="button" class="admin-tab" data-tab="bildirimler">🔔 Bildirimler {('<span class="rozet">' + str(okunmamis) + '</span>') if okunmamis else ''}</button>
<button type="button" class="admin-tab" data-tab="bannerlar">🖼 Bannerlar</button>
</nav>

<div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3 admin-bolum" data-bolum="genel">

<div class="bg-white rounded-2xl p-4 sm:p-5 shadow-sm border border-slate-200 hover:shadow-md transition">

<div class="text-xs text-slate-500">
Toplam Firma
</div>

<div class="text-2xl font-bold">
{len(firmalar)}
</div>

</div>

<div class="bg-white rounded-2xl p-4 sm:p-5 shadow-sm border border-slate-200 hover:shadow-md transition">

<div class="text-xs text-slate-500">
Aktif
</div>

<div class="text-2xl font-bold text-emerald-600">
{aktif}
</div>

</div>

<div class="bg-white rounded-2xl p-4 sm:p-5 shadow-sm border border-slate-200 hover:shadow-md transition">

<div class="text-xs text-slate-500">
Başarılı
</div>

<div class="text-2xl font-bold text-blue-600">
{basarili}
</div>

</div>

<div class="bg-white rounded-2xl p-4 sm:p-5 shadow-sm border border-slate-200 hover:shadow-md transition">

<div class="text-xs text-slate-500">
Hata / Stale
</div>

<div class="text-2xl font-bold text-red-600">
{hatali}
</div>

</div>

<div class="bg-white rounded-2xl p-4 sm:p-5 shadow-sm border border-slate-200 hover:shadow-md transition">

<div class="text-xs text-slate-500">
Manuel
</div>

<div class="text-2xl font-bold text-indigo-600">
{manuel}
</div>

</div>

</div>

<div class="bg-white rounded-3xl shadow-sm border border-slate-200 p-4 sm:p-6 lg:p-7 admin-bolum" data-bolum="genel">
<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
<div>
<h2 class="text-xl font-bold">Sistem Sağlığı</h2>
<p class="text-sm text-slate-500 mt-1">Otomatik güncelleme ve veri geçmişinin hızlı özeti.</p>
</div>
<div class="flex flex-wrap items-center gap-2">
<form method="post" action="/admin/yedek-al">
<button type="submit" class="text-xs bg-white border border-slate-300 hover:bg-slate-50 text-slate-800 px-3 py-2 rounded-xl font-black transition">
💾 Yedek al
</button>
</form>
<a href="/admin/yedek-indir" class="text-xs bg-white border border-slate-300 hover:bg-slate-50 text-slate-800 px-3 py-2 rounded-xl font-black transition">
⬇ Yedeği indir
</a>
<form method="post" action="/admin/lme-grafik" class="flex items-center gap-1.5">
<label for="lmeGrafikMod" class="text-xs font-black text-slate-700">📈 LME grafiği</label>
<select id="lmeGrafikMod" name="mod" class="text-xs border border-slate-300 rounded-xl px-2 py-2 font-bold bg-white">
{''.join(f'<option value="{k}"' + (' selected' if k == lme_grafik_modu() else '') + f'>{v}</option>' for k, v in LME_GRAFIK_MODLARI.items())}
</select>
<button type="submit" class="text-xs bg-slate-900 hover:bg-slate-700 text-white px-3 py-2 rounded-xl font-black transition">
Kaydet
</button>
</form>
<a href="/admin/degisim-tani" class="text-xs bg-white border border-slate-300 hover:bg-slate-50 text-slate-800 px-3 py-2 rounded-xl font-black transition">
🔍 Değişim tanılama
</a>
<form method="post" action="/admin/update-all" onsubmit="return confirm('Tüm kaynaklar şimdi güncellensin mi?');">
<button type="submit" class="text-xs bg-slate-900 hover:bg-slate-700 text-white px-3 py-2 rounded-xl font-black transition">
⟳ Tüm kaynakları şimdi güncelle
</button>
</form>
<div class="text-xs bg-emerald-50 text-emerald-700 px-3 py-2 rounded-xl font-black">
Yedekleme: AKTİF
</div>
</div>
</div>
{banner_html}
<div class="grid grid-cols-2 md:grid-cols-4 gap-3">
<div class="rounded-2xl bg-slate-50 border border-slate-200 p-3"><div class="text-[10px] text-slate-500 font-bold">Fiyat kalemi</div><div class="text-xl font-black mt-1">{sum(len(x) for x in data.get("prices", {}).values() if isinstance(x, dict))}</div></div>
<div class="rounded-2xl bg-slate-50 border border-slate-200 p-3"><div class="text-[10px] text-slate-500 font-bold">Geçmiş kaydı</div><div class="text-xl font-black mt-1">{len(data.get("history", []))}</div></div>
<div class="rounded-2xl bg-slate-50 border border-slate-200 p-3"><div class="text-[10px] text-slate-500 font-bold">Bildirim</div><div class="text-xl font-black mt-1">{len(notifications)}</div></div>
<div class="rounded-2xl bg-slate-50 border border-slate-200 p-3"><div class="text-[10px] text-slate-500 font-bold">Otomatik takip</div><div class="text-xl font-black mt-1">{"AÇIK" if AUTO_UPDATE_ENABLED else "KAPALI"}</div></div>
<div class="rounded-2xl bg-slate-50 border border-slate-200 p-3"><div class="text-[10px] text-slate-500 font-bold">Güncelleme turu</div><div class="text-base font-black mt-1">{guncelleme_durumu}</div><div class="text-[10px] text-slate-500 mt-0.5">Son tur: {esc(SON_GUNCELLEME)}</div></div>
<div class="rounded-2xl bg-slate-50 border border-slate-200 p-3"><div class="text-[10px] text-slate-500 font-bold">Supabase kalıcı depo</div><div class="text-base font-black mt-1">{supabase_durumu}</div></div>
<div class="rounded-2xl bg-slate-50 border border-slate-200 p-3"><div class="text-[10px] text-slate-500 font-bold">Bildirim aboneleri</div><div class="text-xl font-black mt-1">{push_abone_sayisi}</div><div class="text-[10px] text-slate-500 mt-0.5">{push_alarm_sayisi} alarm</div></div>
<div class="rounded-2xl bg-slate-50 border border-slate-200 p-3"><div class="text-[10px] text-slate-500 font-bold">Admin şifresi</div><div class="text-sm font-black mt-1 {sifre_renk}">{esc(sifre_kaynagi)}</div></div>
</div>
</div>

<div class="bg-white rounded-3xl shadow-sm border border-slate-200 p-4 sm:p-6 lg:p-7 admin-bolum" data-bolum="genel">

<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
<div>
<h2 class="text-xl font-bold">Son 24 Saat Güncellemeleri</h2>
<p class="text-sm text-slate-500 mt-1">Bugün başarılı veri alan fabrikaların son çekim zamanı.</p>
</div>
<div class="text-xs bg-sky-50 text-sky-700 px-3 py-2 rounded-xl font-black">
{len(guncellenen_24_saat)} firma güncellendi
</div>
</div>

<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
{guncelleme_rows}
</div>

</div>

<div class="bg-white rounded-3xl shadow-sm border border-slate-200 p-4 sm:p-6 lg:p-7 admin-bolum" data-bolum="firmalar">

<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-5">

<h2 class="text-xl font-bold">
Firmalar
</h2>

<div class="flex flex-col sm:flex-row sm:items-center gap-2">
<div class="text-sm text-slate-500">
Toplam: {len(firmalar)}
</div>
<input
id="firmaAra"
type="search"
placeholder="Firma ara..."
class="w-full sm:w-64 border border-slate-200 bg-slate-50 focus:bg-white focus:border-slate-400 outline-none rounded-xl px-3 py-2 text-sm font-semibold transition"
>
</div>

</div>

<div class="mb-4 bg-blue-50 border border-blue-200 text-blue-800 rounded-xl p-3 text-sm">
💡 Ana sayfadaki firma sırasını artık her firmanın düzenleme ekranındaki
<strong>1, 2, 3...</strong> sıra numarasıyla doğrudan belirleyebilirsiniz.
<strong>↑</strong> ve <strong>↓</strong> butonları da çalışmaya devam eder.
</div>

<div class="firma-grid" id="firmaGrid">

{firma_rows}

</div>

</div>

<div class="bg-white rounded-3xl shadow-sm border border-slate-200 p-4 sm:p-6 lg:p-7 admin-bolum" data-bolum="bildirimler">

<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">

<h2 class="text-xl font-bold">
Bildirimler
</h2>

<div class="flex flex-wrap items-center gap-3">

<span class="text-sm text-slate-500">
Okunmamış: {okunmamis}
</span>

<form
method="post"
action="/admin/notifications/read"
>

<button
type="submit"
class="bg-slate-900 text-white px-3 py-2 rounded-xl text-xs font-bold"
>
Tümünü Okundu Yap
</button>

</form>

<form
method="post"
action="/admin/notifications/delete-all"
onsubmit="return confirm('Tüm bildirimleri silmek istediğinizden emin misiniz? Bu işlem geri alınamaz.');"
>

<button
type="submit"
class="bg-red-50 hover:bg-red-100 border border-red-200 text-red-700 px-3 py-2 rounded-xl text-xs font-bold"
>
Tümünü Sil
</button>

</form>

</div>

</div>

<div class="space-y-3">

{notification_rows}

</div>

</div>

<div class="bg-white rounded-3xl shadow-sm border border-slate-200 p-4 sm:p-6 lg:p-7 admin-bolum" data-bolum="bannerlar">

<div class="flex flex-col md:flex-row md:items-center md:justify-between gap-2 mb-5">

<div>

<h2 class="text-xl font-bold">
Banner Yönetimi
</h2>

<p class="text-sm text-slate-500 mt-1">
Ana sayfada masaüstünde hesaplama aracının altında 3, fiyat geçmişinin altında 3 olmak üzere gösterilen 6 bannerı buradan yönetebilirsiniz. Mobilde bu bannerlar gösterilmez.
</p>

</div>

<div class="text-xs bg-indigo-50 text-indigo-700 px-3 py-2 rounded-xl font-semibold">
6 Banner Alanı
</div>

</div>

<div class="admin-banner-grid">

{ad_form_fields}

</div>

</div>

</div>

<script>

(function() {{
    var sekmeler = document.querySelectorAll("#adminTabs [data-tab]");
    var bolumler = document.querySelectorAll("[data-bolum]");

    function goster(ad) {{
        var var_mi = Array.prototype.some.call(sekmeler, function(s) {{ return s.dataset.tab === ad; }});
        if (!var_mi) ad = "genel";

        bolumler.forEach(function(b) {{ b.hidden = b.dataset.bolum !== ad; }});
        sekmeler.forEach(function(s) {{ s.classList.toggle("aktif", s.dataset.tab === ad); }});

        try {{ localStorage.setItem("adminSekme", ad); }} catch (e) {{}}
        if (location.hash !== "#" + ad) {{ history.replaceState(null, "", "#" + ad); }}
    }}

    sekmeler.forEach(function(s) {{
        s.addEventListener("click", function() {{ goster(s.dataset.tab); window.scrollTo({{ top: 0, behavior: "smooth" }}); }});
    }});

    window.addEventListener("hashchange", function() {{
        goster((location.hash || "").replace("#", "") || "genel");
    }});

    var ilk = (location.hash || "").replace("#", "");
    if (!ilk) {{ try {{ ilk = localStorage.getItem("adminSekme") || ""; }} catch (e) {{ ilk = ""; }} }}
    goster(ilk || "genel");
}})();

</script>

<script>

function historyPanelAlign() {{
    const panel = document.getElementById("historySidePanel");
    const factories = document.getElementById("firmaListesi");
    const rightColumn = panel?.parentElement;
    if (!panel || !factories || !rightColumn) return;
    if (window.innerWidth < 1024) {{
        panel.style.marginTop = "0px";
        return;
    }}
    const factoryTop = factories.getBoundingClientRect().top;
    const columnTop = rightColumn.getBoundingClientRect().top;
    const offset = Math.max(0, Math.round(factoryTop - columnTop));
    panel.style.marginTop = offset + "px";
}}


window.addEventListener("load", historyPanelAlign);
window.addEventListener("resize", historyPanelAlign);
setTimeout(historyPanelAlign, 250);

(function () {{
    const input = document.getElementById("firmaAra");
    if (!input) return;

    const rows = Array.from(
        document.querySelectorAll("[data-firma-row]")
    );

    input.addEventListener("input", function () {{
        const query = String(input.value || "").trim().toLocaleLowerCase("tr-TR");

        rows.forEach(function (row) {{
            const haystack = String(
                row.getAttribute("data-firma-row") || ""
            ).toLocaleLowerCase("tr-TR");

            row.style.display =
                !query || haystack.includes(query)
                    ? ""
                    : "none";
        }});
    }});
}})();
</script>

</body>

</html>
"""

    return html


# =========================================================
# BANNER / REKLAM KAYDET
# =========================================================

@app.post(
    "/admin/update-ads"
)
async def update_ads(
    request: Request,
    username: str = Depends(
        verify_admin
    ),
):

    form = await request.form()

    mevcut_ads = load_ads()

    banner_key = str(
        form.get(
            "banner_key",
            "",
        )
    ).strip()

    if banner_key and banner_key not in DEFAULT_ADS:
        raise HTTPException(
            status_code=400,
            detail="Geçersiz banner alanı.",
        )

    # Tek banner kaydediliyorsa diğer bannerlara dokunma.
    keys_to_save = (
        [banner_key]
        if banner_key
        else list(DEFAULT_ADS.keys())
    )

    data = dict(mevcut_ads)

    # Tek banneri tamamen kaldır.
    if str(form.get("banner_delete", "")).strip() == "1":
        eski = mevcut_ads.get(
            banner_key,
            DEFAULT_ADS.get(banner_key, {}),
        )

        eski_url = str(
            eski.get("image_url", "")
        ).strip()

        if eski_url.startswith("/static/ads/"):
            eski_dosya = os.path.basename(eski_url)
            eski_yol = os.path.join(
                ADS_UPLOAD_DIR,
                eski_dosya,
            )

            if os.path.exists(eski_yol):
                try:
                    os.remove(eski_yol)
                except Exception:
                    pass

        data[banner_key] = dict(
            DEFAULT_ADS[banner_key]
        )

        save_ads(data)

        return RedirectResponse(
            url="/admin",
            status_code=303,
        )

    allowed_extensions = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }

    for key in keys_to_save:

        eski = mevcut_ads.get(
            key,
            DEFAULT_ADS[key],
        )

        title = str(
            form.get(
                f"{key}_title",
                "",
            )
        ).strip()

        target_url = str(
            form.get(
                f"{key}_target_url",
                "#",
            )
        ).strip()

        active = (
            form.get(
                f"{key}_active"
            )
            == "1"
        )

        image_url = str(
            eski.get(
                "image_url",
                "",
            )
        ).strip()

        upload = form.get(
            f"{key}_file"
        )

        upload_filename = getattr(
            upload,
            "filename",
            "",
        )

        upload_file = getattr(
            upload,
            "file",
            None,
        )

        if (
            upload_filename
            and upload_file is not None
        ):

            extension = os.path.splitext(
                upload_filename
            )[1].lower()

            if extension not in allowed_extensions:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{key} için sadece "
                        "JPG, JPEG, PNG veya WEBP "
                        "dosyaları yüklenebilir."
                    ),
                )

            unique_name = (
                f"{key}_"
                f"{uuid.uuid4().hex}"
                f"{extension}"
            )

            file_path = os.path.join(
                ADS_UPLOAD_DIR,
                unique_name,
            )

            with open(
                file_path,
                "wb",
            ) as buffer:

                shutil.copyfileobj(
                    upload_file,
                    buffer,
                )

            image_url = (
                f"/static/ads/{unique_name}"
            )

            eski_url = str(
                eski.get(
                    "image_url",
                    "",
                )
            )

            if eski_url.startswith(
                "/static/ads/"
            ):

                eski_dosya = os.path.basename(
                    eski_url
                )

                eski_yol = os.path.join(
                    ADS_UPLOAD_DIR,
                    eski_dosya,
                )

                if (
                    os.path.exists(
                        eski_yol
                    )
                    and os.path.abspath(
                        eski_yol
                    )
                    != os.path.abspath(
                        file_path
                    )
                ):

                    try:

                        os.remove(
                            eski_yol
                        )

                    except Exception:

                        pass

        data[
            key
        ] = {
            "title": title,
            "image_url": image_url,
            "target_url": target_url or "#",
            "active": active,
        }

    save_ads(
        data
    )

    return RedirectResponse(
        url="/admin",
        status_code=303,
    )


# =========================================================
# LME ENDPOINT
# =========================================================

@app.get(
    "/lme"
)
def lme_fiyatlari():

    try:

        sonuc = lme_verilerini_cek()

        return {
            "status": "success",
            "kaynak": sonuc.get(
                "kaynak",
                "LME Official Prices",
            ),
            "gecikme": "Gün gecikmeli",
            "usd_tl": sonuc.get(
                "usd_tl"
            ),
            "tarih": sonuc["tarih"],
            "cekilme": sonuc["cekilme"],
            "veriler": sonuc["veriler"],
            "gecmis": lme_gecmisi_oku(),
            "grafik_aktif": lme_grafik_aktif_mi(),
            "grafik_mod": lme_grafik_modu(),
        }

    except Exception as exc:

        return {
            "status": "error",
            "kaynak": "LME Official Prices + yedek kaynak",
            "gecikme": "Gün gecikmeli",
            "message": str(exc),
            "veriler": [],
        }


# =========================================================
# ANA SAYFA
# =========================================================

@app.get(
    "/",
    response_class=HTMLResponse,
)
def read_root(request: Request):

    ads = load_ads()

    page = """
<!DOCTYPE html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width, initial-scale=1.0"
>

<meta
name="theme-color"
content="#0a101d"
>

<meta
name="description"
content="Güncel hurda ve demir çelik fiyatları."
>

<title>Hurda Fiyatları - Güncel Piyasa Takip</title>
<meta name="description" content="Güncel hurda fiyatları, fabrika fiyatları, LME, döviz ve piyasa takip ekranı.">
<meta name="robots" content="index,follow">
<meta property="og:title" content="Hurda Fiyatları - Güncel Piyasa Takip">
<meta property="og:description" content="Güncel hurda fiyatları, LME ve döviz verileri.">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Hurda Fiyatları">
<meta property="og:locale" content="tr_TR">
<meta property="og:url" content="__BASE_URL__/">
<meta property="og:image" content="https://cdn-icons-png.flaticon.com/512/2954/2954884.png">
<meta property="og:image:alt" content="Hurda Fiyatları">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="Hurda Fiyatları - Güncel Piyasa Takip">
<meta name="twitter:description" content="Fabrika hurda alım fiyatları, LME ve döviz verileri tek ekranda.">
<meta name="twitter:image" content="https://cdn-icons-png.flaticon.com/512/2954/2954884.png">
<script>
try { if (localStorage.getItem("hurdaTema") !== "light") { document.documentElement.classList.add("dark"); } } catch (e) { document.documentElement.classList.add("dark"); }
</script>
<link rel="canonical" href="__BASE_URL__/">
<link rel="apple-touch-icon" href="https://cdn-icons-png.flaticon.com/512/2954/2954884.png">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"WebSite","name":"Hurda Fiyatları","url":"__BASE_URL__/","inLanguage":"tr-TR","description":"Fabrika hurda alım fiyatları, LME ve döviz verileri."}
</script>

<script src="https://cdn.tailwindcss.com"></script>

<style>

html,
body {
    width: 100%;
    max-width: 100%;
    overflow-x: hidden;
}

* {
    box-sizing: border-box;
}

body {
    background:
        radial-gradient(
            circle at 12% 0%,
            rgba(255, 255, 255, .28),
            transparent 34%
        ),
        radial-gradient(
            circle at 88% 8%,
            rgba(135, 206, 250, .20),
            transparent 32%
        ),
        linear-gradient(
            135deg,
            #87CEFA 0%,
            #87CEFA 50%,
            #80DAEB 100%
        );
    background-attachment: fixed;
}

.factory-price-grid {
    align-items: start;
}

.factory-price-grid .price-card {
    height: auto;
    align-self: start;
}

.price-card {
    width: 100%;
    min-width: 0;
    height: auto;
    display: flex;
    flex-direction: column;
    transition:
        transform .2s ease,
        box-shadow .2s ease,
        border-color .2s ease;
}

.firma-toggle {
    height: 132px;
    min-height: 132px !important;
    display: flex;
    align-items: center;
}

.firma-toggle > div {
    width: 100%;
}

@media (max-width: 639px) {
    .firma-toggle {
        height: 124px;
        min-height: 124px !important;
    }
}

.price-card * {
    min-width: 0;
}

.price-card:hover {
    transform: translateY(-2px);
}

.price-card .price-value {
    letter-spacing: -0.01em;
}

.price-card .price-row {
    background: #ffffff;
}

.price-card .price-row:last-child {
    border-bottom: 0;
}

.ad-box {
    width: 100%;
    min-width: 0;
}

.ad-box:empty {
    display: none;
}

.desktop-ad-column {
    min-width: 0;
    height: 100%;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    gap: 16px;
    align-self: stretch;
}

.desktop-ad-column .ad-box {
    flex: 0 0 auto;
    width: 100%;
    max-width: 250px;
    aspect-ratio: 4 / 3;
    min-height: 0;
    height: auto;
    overflow: hidden;
}

/* Yan reklam alanları kaldırıldı; bu sütunlar yeni piyasa araçları için ayrıldı. */
.desktop-feature-column {
    min-width: 0;
    width: 100%;
}

.desktop-feature-column > div {
    min-width: 0;
    width: 100%;
}

.feature-ad-stack {
    width: 100%;
    max-width: 250px;
    margin-left: auto;
    margin-right: auto;
}

.feature-ad-stack .ad-box {
    width: 100%;
    aspect-ratio: 4 / 3;
    overflow: hidden;
}

.feature-ad-stack .ad-box img {
    width: 100%;
    height: 100%;
    display: block;
    object-fit: cover;
    object-position: center;
}

#bottomAds {
    width: 100%;
    max-width: 1120px;
    margin-left: auto;
    margin-right: auto;
}

#bottomAds .ad-box {
    aspect-ratio: 16 / 5;
    height: auto;
    min-height: 0;
    overflow: hidden;
}
#bottomAds .ad-box a {
    height: 100%;
}

#bottomAds .ad-box img {
    width: 100%;    height: 100%;
    object-fit: cover;
    object-position: center;
}

.desktop-ad-column .ad-box img,
#bottomAds .ad-box img {
    width: 100%;
    height: 100%;
    min-height: 0;
    max-height: none;
    display: block;
    object-fit: cover;
    object-position: center;
}

.ad-box a {
    display: block;
    width: 100%;
}

.ad-box img {
    width: 100%;
    height: 100%;
    min-height: 0;
    max-width: 100%;
    display: block;
    object-fit: cover;
    object-position: center;
}

.price-header {
    min-width: 0;
}

.price-body {
    min-width: 0;
}

.price-row {
    width: 100%;
    min-width: 0;
}

.price-name {
    min-width: 0;
    flex: 1 1 auto;
    overflow-wrap: anywhere;
    word-break: break-word;
}

.price-value {
    flex: 0 0 auto;
    min-width: 0;
    text-align: right;
}

.mobile-safe-text {
    overflow-wrap: anywhere;
    word-break: break-word;
}

.mobile-ad-grid {
    width: 100%;
    min-width: 0;
}

.mobile-ad-grid .ad-box {
    min-width: 0;
    width: 100%;
    overflow: hidden;
}

/* Mobilde reklamlar tamamen gizli; masaüstü reklam sistemi aynen korunur. */
@media (max-width: 1023px) {
    #mobileAds {
        display: none !important;
    }
}

.mobile-ad-slot {
    min-height: 120px;
}

.mobile-ad-slot img {
    width: 100%;
    height: 100%;
    min-height: 120px;
    object-fit: cover;
}

/* LME, döviz bandının hemen altında kayan kompakt bant. */
.lme-ticker-shell {
    min-height: 42px;
    display: flex;
    align-items: center;
    overflow: hidden;
}

.lme-ticker-label {
    flex: 0 0 auto;
    padding: 7px 10px;
    background: #020617;
    border-right: 1px solid rgba(148,163,184,.18);
}

.lme-ticker-track {
    min-width: 0;
    flex: 1 1 auto;
    overflow: hidden;
    white-space: nowrap;
}

.lme-ticker-content {
    display: inline-flex;
    align-items: center;
    gap: 16px;
    min-width: max-content;
    padding: 5px 14px;
    animation: lmeTicker 28s linear infinite;
}

.lme-ticker-shell:hover .lme-ticker-content {
    animation-play-state: paused;
}

.lme-ticker-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 10px;
    font-weight: 800;
}

.lme-ticker-metal {
    color: #e2e8f0;
}

.lme-ticker-value {
    color: #fcd34d;
    font-weight: 900;
}

.lme-ticker-separator {
    color: #475569;
    font-size: 8px;
}

@keyframes lmeTicker {
    from { transform: translateX(0); }
    to { transform: translateX(-42%); }
}

/* Kompakt kayan döviz bandı */
.currency-ticker-shell {
    min-height: 48px;
    display: flex;
    align-items: center;
}
.currency-ticker-label {
    position: relative;
    z-index: 2;
    padding: 7px 8px 7px 10px;
    background: #020617;
    box-shadow: 8px 0 18px rgba(2, 6, 23, .55);
}
.currency-ticker-track {
    min-width: 0;
    flex: 1 1 auto;
    overflow: hidden;
    white-space: nowrap;
}
.currency-ticker-content {
    display: inline-flex;
    align-items: center;
    gap: 18px;
    min-width: max-content;
    padding: 6px 18px 6px 14px;
    animation: currencyTicker 22s linear infinite;
}
.currency-ticker-shell:hover .currency-ticker-content {
    animation-play-state: paused;
}
.currency-ticker-item {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 11px;
    font-weight: 800;
}
.currency-code {
    padding: 3px 7px;
    border-radius: 8px;
    font-size: 9px;
    font-weight: 900;
    letter-spacing: .08em;
}
.currency-label {
    color: #cbd5e1;
}
.currency-value {
    font-size: 12px;
    font-weight: 900;
}
.currency-sale,
.currency-sub {
    color: #94a3b8;
}
.currency-sale {
    font-size: 11px;
    font-weight: 800;
}
.currency-divider {
    color: #475569;
}
.currency-ticker-item.usd .currency-code {
    color: #86efac;
    background: rgba(34,197,94,.12);
    border: 1px solid rgba(74,222,128,.2);
}
.currency-ticker-item.usd .currency-value {
    color: #86efac;
}
.currency-ticker-item.eur .currency-code {
    color: #93c5fd;
    background: rgba(59,130,246,.12);
    border: 1px solid rgba(96,165,250,.2);
}
.currency-ticker-item.eur .currency-value {
    color: #93c5fd;
}
.currency-ticker-item.gold .currency-code {
    color: #fcd34d;
    background: rgba(245,158,11,.12);
    border: 1px solid rgba(251,191,36,.22);
}
.currency-ticker-item.gold .currency-value {
    color: #fcd34d;
}
.currency-ticker-separator {
    color: #475569;
    font-size: 8px;
}
@keyframes currencyTicker {
    from { transform: translateX(0); }
    to { transform: translateX(-38%); }
}

/* Fiyat kartlarına hafif renk vurgusu */
.price-card:nth-child(4n+1) { border-top: 3px solid #10b981; }
.price-card:nth-child(4n+2) { border-top: 3px solid #3b82f6; }
.price-card:nth-child(4n+3) { border-top: 3px solid #f59e0b; }
.price-card:nth-child(4n+4) { border-top: 3px solid #8b5cf6; }

@media (max-width: 640px) {

    .price-card {
        border-radius: 1rem;
    }

    .price-card .price-header {
        padding: 0.9rem;
    }

    .price-card .price-body {
        padding: 0.9rem;
    }

    .price-card .price-row {
        align-items: flex-start;
        gap: 0.75rem;
        padding-top: 0.8rem;
        padding-bottom: 0.8rem;
    }

    .price-card .price-name {
        font-size: 0.9rem;
        line-height: 1.25rem;
    }

    .price-card .price-value {
        max-width: 52%;
        font-size: 0.9rem;
        line-height: 1.2rem;
    }

    .mobile-ad-grid {
        gap: 0.75rem;
    }

    .mobile-ad-grid .ad-box {
        border-radius: 1rem;
        overflow: hidden;
    }

    .mobile-ad-grid .ad-box img {
        border-radius: 1rem;
    }

}

/* =====================================================
   YENİ PİYASA TASARIMI — SADECE GÖRSEL KATMAN
   Veri, scraper, API, Supabase ve iş mantığına dokunmaz.
   ===================================================== */

.market-design {
    background:
        radial-gradient(circle at 8% 0%, rgba(255,255,255,.95), transparent 28%),
        radial-gradient(circle at 92% 8%, rgba(226,232,240,.70), transparent 30%),
        #f3f5f7 !important;
    color: #0f172a;
}

.market-design > .w-full.max-w-7xl {
    padding-top: 14px;
    padding-bottom: 28px;
}

.market-design header {
    background:
        linear-gradient(135deg, #0f172a 0%, #172033 55%, #1e293b 100%) !important;
    border-color: rgba(148,163,184,.20) !important;
    box-shadow: 0 18px 45px rgba(15,23,42,.16) !important;
}

.market-design header h1 {
    letter-spacing: -.035em;
}

.market-design #currencySection,
.market-design #lmeSection {
    box-shadow: 0 8px 24px rgba(15,23,42,.08);
}

.market-design #marketTools {
    background: rgba(255,255,255,.98) !important;
    border-color: #e2e8f0 !important;
    box-shadow: 0 12px 30px rgba(15,23,42,.07) !important;
}

.market-design #marketTools input,
.market-design #marketTools select {
    background: #f8fafc;
}

.market-design #marketTools input:focus,
.market-design #marketTools select:focus {
    border-color: #64748b !important;
    box-shadow: 0 0 0 3px rgba(100,116,139,.12) !important;
}

.market-design .factory-price-grid {
    gap: 16px;
}

/* FABRİKA KARTLARI — ayrı sınıflar, iç içe flex/grid selector yok */
.market-design .factory-card-header {
    width: 100% !important;
    min-width: 0 !important;
    display: grid !important;
    grid-template-columns: minmax(0, 1fr) auto !important;
    align-items: center !important;
    gap: 12px !important;
}

.market-design .factory-card-main {
    width: 100% !important;
    min-width: 0 !important;
    display: grid !important;
    grid-template-columns: 40px minmax(0, 1fr) !important;
    align-items: center !important;
    gap: 10px !important;
}

.market-design .factory-card-info {
    min-width: 0 !important;
    width: 100% !important;
}

.market-design .factory-card-title-line {
    min-width: 0 !important;
    width: 100% !important;
    display: flex !important;
    align-items: center !important;
    gap: 8px !important;
}

.market-design .factory-card-title {
    min-width: 0 !important;
    width: auto !important;
    max-width: 100% !important;
    display: block !important;
    overflow-wrap: normal !important;
    word-break: normal !important;
    white-space: normal !important;
}

.market-design .factory-card-meta {
    min-width: 0 !important;
    width: 100% !important;
    display: flex !important;
    align-items: center !important;
    gap: 6px !important;
    white-space: normal !important;
}

.market-design .factory-card-actions {
    flex: 0 0 auto !important;
    min-width: 0 !important;
    max-width: 100% !important;
    display: flex !important;
    align-items: center !important;
    justify-content: flex-end !important;
    gap: 6px !important;
    flex-wrap: wrap !important;
}

.market-design .factory-card-actions > * {
    flex: 0 0 auto !important;
    white-space: nowrap !important;
    line-height: 1.1 !important;
}

.market-design .factory-price-panel {
    width: 100% !important;
    min-width: 0 !important;
}

.market-design .factory-price-list {
    width: 100% !important;
    min-width: 0 !important;
}

.market-design .factory-price-row {
    width: 100% !important;
    min-width: 0 !important;
    display: grid !important;
    grid-template-columns: minmax(0, 1fr) minmax(105px, auto) !important;
    align-items: center !important;
    gap: 12px !important;
}

.market-design .factory-price-name {
    min-width: 0 !important;
    width: 100% !important;
}

.market-design .factory-price-value {
    min-width: 105px !important;
    width: auto !important;
    justify-self: end !important;
}

@media (max-width: 639px) {
    .market-design .factory-card-header {
        grid-template-columns: 1fr !important;
        gap: 8px !important;
    }

    .market-design .factory-card-actions {
        justify-content: flex-end !important;
    }

    .market-design .factory-price-row {
        grid-template-columns: minmax(0, 1fr) auto !important;
    }
}

/* Fabrika bölümü kendi 760px sınırında kalmasın; 5+5 düzende tam alanı kullansın. */
@media (min-width: 1024px) {
    .market-design .factory-layout > main {
        width: 100% !important;
        max-width: none !important;
        min-width: 0 !important;
        margin-left: 0 !important;
        margin-right: 0 !important;
        grid-column: 1 / -1 !important;
    }

    .market-design .factory-layout .factory-price-grid {
        width: 100% !important;
        max-width: none !important;
        min-width: 0 !important;
        grid-template-columns: repeat(5, minmax(0, 1fr)) !important;
    }
}

.market-design .price-card {
    border-color: #e2e8f0 !important;
    border-radius: 22px !important;
    background: #fff !important;
    box-shadow: 0 8px 24px rgba(15,23,42,.065) !important;
    overflow: hidden;
}

.market-design .price-card:hover {
    transform: translateY(-3px);
    border-color: #cbd5e1 !important;
    box-shadow: 0 16px 34px rgba(15,23,42,.10) !important;
}

.market-design .price-card .firma-toggle {
    background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
    border-bottom: 1px solid transparent;
}

.market-design .price-card .firma-toggle:hover {
    background: #f8fafc !important;
}

/* Fabrika kartları: başlık alanı daralıp harf harf alt alta düşmesin. */
.market-design .factory-price-grid .price-card {
    min-width: 0 !important;
    width: 100% !important;
    align-self: start !important;
}

.market-design .factory-price-grid .firma-toggle {
    width: 100% !important;
    min-width: 0 !important;
}

.market-design .factory-price-grid .firma-toggle > div {
    min-width: 0 !important;
    width: 100% !important;
}

.market-design .factory-price-grid .firma-toggle > div > div:first-child {
    min-width: 0 !important;
    flex: 1 1 auto !important;
}

.market-design .factory-price-grid .firma-toggle > div > div:first-child > div:last-child {
    min-width: 0 !important;
    flex: 1 1 auto !important;
}

.market-design .factory-price-grid .firma-toggle h2 {
    min-width: 0 !important;
    max-width: 100% !important;
    overflow-wrap: normal !important;
    word-break: normal !important;
    white-space: normal !important;
}

.market-design .factory-price-grid .firma-toggle h2 + * {
    flex: 0 0 auto !important;
}

.market-design .factory-price-grid .price-row {
    min-width: 0 !important;
}

.market-design .factory-price-grid .price-name {
    min-width: 0 !important;
    flex: 1 1 auto !important;
    overflow-wrap: normal !important;
    word-break: normal !important;
}

.market-design .factory-price-grid .price-name > div:first-child {
    overflow-wrap: normal !important;
    word-break: normal !important;
    white-space: normal !important;
}

/* Fabrika kartları: içeriği gerçek genişlikte tut, dar kolonlara sıkıştırma. */
@media (min-width: 1024px) {
    .market-design .factory-price-grid > .price-card {
        width: 100% !important;
        min-width: 0 !important;
        max-width: none !important;
    }

    .market-design .factory-price-grid .firma-toggle {
        display: flex !important;
        align-items: center !important;
        width: 100% !important;
        min-width: 0 !important;
    }

    .market-design .factory-price-grid .firma-toggle > div {
        display: flex !important;
        align-items: center !important;
        justify-content: space-between !important;
        gap: 12px !important;
        width: 100% !important;
        min-width: 0 !important;
    }

    .market-design .factory-price-grid .firma-toggle > div > div:first-child {
        display: flex !important;
        align-items: center !important;
        gap: 10px !important;
        flex: 1 1 auto !important;
        width: auto !important;
        min-width: 0 !important;
    }

    .market-design .factory-price-grid .firma-toggle > div > div:first-child > div:first-child {
        display: flex !important;
        align-items: center !important;
        gap: 10px !important;
        flex: 1 1 auto !important;
        width: 100% !important;
        min-width: 0 !important;
    }

    .market-design .factory-price-grid .firma-toggle > div > div:first-child > div:first-child > div:nth-child(2) {
        flex: 1 1 auto !important;
        width: auto !important;
        min-width: 0 !important;
    }

    .market-design .factory-price-grid .price-card > div:not(.firma-toggle),
    .market-design .factory-price-grid .price-card > div:not(.firma-toggle) > div {
        width: 100% !important;
        min-width: 0 !important;
        max-width: none !important;
    }

    .market-design .factory-price-grid .price-row {
        display: flex !important;
        align-items: center !important;
        justify-content: space-between !important;
        gap: 12px !important;
        width: 100% !important;
        min-width: 0 !important;
    }

    .market-design .factory-price-grid .price-row .price-name {
        flex: 1 1 auto !important;
        width: auto !important;
        min-width: 0 !important;
    }

    .market-design .factory-price-grid .price-row .price-value {
        flex: 0 0 auto !important;
        width: auto !important;
        min-width: 105px !important;
    }
}

.market-design .price-card .firma-toggle > div > div > div:first-child > div:first-child {
    background: #0f172a !important;
    box-shadow: 0 5px 12px rgba(15,23,42,.12);
}

.market-design .price-card .price-row {
    background: #fff !important;
    padding-left: 4px;
    padding-right: 4px;
}

.market-design .price-card .price-row:hover {
    background: #f8fafc !important;
}

.market-design .price-card .price-value {
    color: #0f172a;
}

.market-design .price-card .price-value > div:first-child {
    letter-spacing: -.025em;
}

.market-design #loading {
    border: 1px solid #e2e8f0;
    box-shadow: 0 8px 24px rgba(15,23,42,.05);
}

.market-design #todayChanges > div,
.market-design #comparePanel,
.market-design #historyPanel,
.market-design #alarmPanel {
    border-color: #e2e8f0 !important;
}

.market-design .text-cyan-300 {
    color: #475569 !important;
}

.market-design main > .text-center .text-white {
    color: #0f172a !important;
}

.market-design main > .text-center .text-slate-200 {
    color: #64748b !important;
}

.market-design main > .text-center .text-slate-500 {
    color: #94a3b8 !important;
}

.market-design #bottomAds {
    margin-top: 18px;
}

/* FABRİKA LAYOUT — MASAÜSTÜ: 3 REKLAM | 10 FABRİKA | 3 REKLAM */
@media (min-width: 1024px) {
    .factory-layout {
        display: grid !important;
        grid-template-columns: 250px minmax(0, 1fr) 250px !important;
        gap: 18px !important;
        align-items: start !important;
    }

    .factory-layout > main {
        grid-column: 2 !important;
        grid-row: 1 !important;
        width: 100% !important;
        max-width: none !important;
        min-width: 0 !important;
    }

    .factory-layout > .desktop-ad-column:first-child {
        grid-column: 1 !important;
        grid-row: 1 !important;
        display: grid !important;
        grid-template-columns: 1fr !important;
        grid-template-rows: repeat(3, minmax(0, auto)) !important;
        gap: 14px !important;
        width: 100% !important;
        height: auto !important;
        align-items: start !important;
    }

    .factory-layout > .desktop-ad-column:last-child {
        grid-column: 3 !important;
        grid-row: 1 !important;
        display: grid !important;
        grid-template-columns: 1fr !important;
        grid-template-rows: repeat(3, minmax(0, auto)) !important;
        gap: 14px !important;
        width: 100% !important;
        height: auto !important;
        align-items: start !important;
    }

    .factory-layout > .desktop-ad-column .ad-box {
        width: 100% !important;
        max-width: none !important;
        aspect-ratio: 4 / 3 !important;
    }

    /* Ortadaki 10 fabrika: 2 sütun x 5 satır. */
    .factory-price-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 16px !important;
        width: 100% !important;
        min-width: 0 !important;
    }
}

@media (min-width: 640px) and (max-width: 1023px) {
    .factory-price-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    }
}

@media (max-width: 639px) {
    .market-design > .w-full.max-w-7xl {
        padding-top: 8px;
        padding-left: 10px;
        padding-right: 10px;
    }

    .market-design header {
        border-radius: 20px !important;
        padding: 16px !important;
    }

    .market-design .factory-price-grid {
        gap: 14px;
    }

    .market-design .price-card {
        border-radius: 18px !important;
    }
}

/* Piyasa özeti — ana sayfa */
.market-design .market-summary {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 12px;
    margin-bottom: 14px;
}

.market-design .market-summary-card {
    min-width: 0;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 18px;
    padding: 13px 14px;
    box-shadow: 0 8px 22px rgba(15,23,42,.055);
}

.market-design .market-summary-label {
    color: #64748b;
    font-size: 9px;
    line-height: 1.2;
    font-weight: 900;
    letter-spacing: .10em;
    text-transform: uppercase;
}

.market-design .market-summary-value {
    color: #0f172a;
    font-size: 21px;
    line-height: 1.15;
    font-weight: 900;
    letter-spacing: -.025em;
    margin-top: 6px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.market-design .market-summary-sub {
    color: #94a3b8;
    font-size: 9px;
    font-weight: 700;
    margin-top: 4px;
}

.market-design .market-summary-card.up .market-summary-value {
    color: #059669;
}

.market-design .market-summary-card.down .market-summary-value {
    color: #dc2626;
}

.market-design .market-summary-card.update .market-summary-value {
    font-size: 13px;
    letter-spacing: -.01em;
}

/* 10 fabrika kartısında başlık alanının sıkışmasını engelle */
.market-design .firma-toggle > div > div:first-child {
    min-width: 0;
    flex: 1 1 auto;
}

.market-design .firma-toggle h2 {
    min-width: 0;
    overflow-wrap: normal !important;
    word-break: normal !important;
    white-space: normal;
}

.market-design .firma-toggle h2 + * {
    flex: 0 0 auto;
}

@media (max-width: 1100px) {
    .market-design .market-summary {
        grid-template-columns: repeat(3, minmax(0, 1fr));
    }
}

@media (max-width: 639px) {
    .market-design .market-summary {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
        margin-bottom: 10px;
    }

    .market-design .market-summary-card {
        border-radius: 15px;
        padding: 11px 12px;
    }

    .market-design .market-summary-value {
        font-size: 18px;
    }

    .market-design .market-summary-card.update {
        grid-column: span 2;
    }
}

/* =====================================================
   SON MASAÜSTÜ LAYOUT OVERRIDE
   3 REKLAM | 10 FABRİKA | 3 REKLAM
   HTML yapısı korunur; JS veri/render mantığına dokunulmaz.
   ===================================================== */
.market-design #calculatorSidePanel > #calculatorPanel {
    margin-top: 0 !important;
    border-top: 0 !important;
    padding-top: 0 !important;
}

/* Sol hesaplama paneli: dış kalıp sabit, içerik dar ekrana göre kompakt. */
.market-design #calculatorSidePanel #calculatorPanel > div {
    padding: 10px !important;
}

.market-design #calculatorSidePanel #calculatorPanel .calc-title {
    font-size: 13px !important;
    line-height: 1.2 !important;
}

.market-design #calculatorSidePanel #calculatorPanel .calc-description {
    font-size: 9px !important;
    line-height: 1.3 !important;
}

.market-design #calculatorSidePanel #calculatorPanel .calc-form-grid {
    display: grid !important;
    grid-template-columns: 1fr !important;
    gap: 6px !important;
}

.market-design #calculatorSidePanel #calculatorPanel .calc-form-grid select,
.market-design #calculatorSidePanel #calculatorPanel .calc-form-grid input,
.market-design #calculatorSidePanel #calculatorPanel .calc-form-grid button {
    width: 100% !important;
    height: 34px !important;
    min-height: 34px !important;
    padding: 0 9px !important;
    border-radius: 9px !important;
    font-size: 10px !important;
}

.market-design #calculatorSidePanel #calculatorPanel #calcResult {
    margin-top: 7px !important;
}

.market-design #calculatorSidePanel #calculatorPanel #calcResult > div {
    padding: 8px !important;
}

.market-design #calculatorSidePanel #calculatorPanel #calcResult .text-lg {
    font-size: 12px !important;
    line-height: 1.2 !important;
}

.market-design #calculatorSidePanel #calculatorPanel #calcResult .text-xs {
    font-size: 9px !important;
}

@media (min-width: 1024px) {
    .market-design .factory-layout {
        display: grid !important;
        grid-template-columns: 250px minmax(0, 1fr) 250px !important;
        gap: 18px !important;
        align-items: start !important;
        width: 100% !important;
    }

    .market-design .factory-layout > .desktop-ad-column:first-child {
        display: grid !important;
        grid-column: 1 !important;
        grid-row: 1 !important;
        grid-template-columns: 1fr !important;
        gap: 14px !important;
        width: 100% !important;
        height: auto !important;
    }

    .market-design .factory-layout > main {
        display: block !important;
        grid-column: 2 !important;
        grid-row: 1 !important;
        width: 100% !important;
        max-width: none !important;
        min-width: 0 !important;
        margin: 0 !important;
    }

    .market-design .factory-layout > .desktop-ad-column:last-child {
        display: grid !important;
        grid-column: 3 !important;
        grid-row: 1 !important;
        grid-template-columns: 1fr !important;
        gap: 14px !important;
        width: 100% !important;
        height: auto !important;
    }

    .market-design .factory-layout > .desktop-ad-column .ad-box {
        width: 100% !important;
        max-width: none !important;
        aspect-ratio: 4 / 3 !important;
        grid-column: auto !important;
        grid-row: auto !important;
    }

    .market-design .factory-layout .factory-price-grid {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 16px !important;
        width: 100% !important;
        max-width: none !important;
        min-width: 0 !important;
    }
}

/* =====================================================
   FABRİKA KARTI SON GENİŞLİK AYARI
   Yan reklamlar biraz daraltılır, orta piyasa alanı eşit
   şekilde genişletilir. Sadece CSS.
   ===================================================== */
@media (min-width: 1024px) {
    .market-design .factory-layout {
        grid-template-columns: 220px minmax(0, 1fr) 220px !important;
        gap: 14px !important;
    }

    .market-design .factory-layout > aside.desktop-ad-column {
        min-width: 0 !important;
        width: 100% !important;
    }

    .market-design .factory-layout > main {
        min-width: 0 !important;
        width: 100% !important;
        max-width: none !important;
    }

    .market-design .factory-price-grid {
        min-width: 0 !important;
        width: 100% !important;
    }

    .market-design .factory-price-grid .price-card {
        min-width: 0 !important;
        width: 100% !important;
    }

    .market-design .factory-price-grid .factory-card-header {
        min-width: 0 !important;
        width: 100% !important;
        grid-template-columns: minmax(0, 1fr) auto !important;
    }

    .market-design .factory-price-grid .factory-card-main,
    .market-design .factory-price-grid .factory-card-info,
    .market-design .factory-price-grid .factory-card-title-line,
    .market-design .factory-price-grid .factory-card-title {
        min-width: 0 !important;
        max-width: 100% !important;
    }

    .market-design .factory-price-grid .factory-card-title {
        white-space: normal !important;
        overflow-wrap: normal !important;
        word-break: normal !important;
    }

    .market-design .factory-price-grid .factory-card-actions {
        min-width: 0 !important;
        flex: 0 0 auto !important;
    }
}

@media (min-width: 1024px) and (max-width: 1199px) {
    .market-design .factory-layout {
        grid-template-columns: 205px minmax(0, 1fr) 205px !important;
        gap: 12px !important;
    }
}

@media (min-width: 1200px) and (max-width: 1399px) {
    .market-design .factory-layout {
        grid-template-columns: 215px minmax(0, 1fr) 215px !important;
        gap: 14px !important;
    }
}

/* Fabrika butonları — yeni kart tasarımı */
.market-design .fc-btn {
    height: auto !important;
    min-height: 0 !important;
    display: flex !important;
    flex-direction: column;
    gap: 12px;
    width: 100%;
    padding: 16px;
    text-align: left;
    background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
    border: 0;
    border-left: 4px solid #0ea5e9;
    align-items: stretch;
    justify-content: flex-start;
    box-sizing: border-box;
    cursor: pointer;
    transition: background .2s ease;
}

.market-design .fc-btn > * {
    width: 100%;
    box-sizing: border-box;
}

.market-design .fc-btn:hover {
    background: #f1f5f9;
}

.market-design .fc-btn:focus-visible {
    outline: 3px solid #38bdf8;
    outline-offset: -3px;
}

.market-design .fc-top {
    display: flex;
    text-align: left;
    align-items: center;
    gap: 12px;
    min-width: 0;
}

.market-design .fc-avatar {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 44px;
    height: 44px;
    border-radius: 14px;
    background: linear-gradient(135deg, #0f172a, #1e3a8a);
    color: #fff;
    font-size: 17px;
    font-weight: 900;
    box-shadow: 0 6px 14px rgba(15, 23, 42, .18);
}

.market-design .fc-info {
    display: flex;
    flex-direction: column;
    gap: 3px;
    flex: 1 1 auto;
    min-width: 0;
}

.market-design .fc-title {
    display: block;
    color: #0f172a;
    font-size: 15px;
    font-weight: 900;
    line-height: 1.25;
    overflow-wrap: anywhere;
}

.market-design .fc-meta {
    display: block;
    color: #64748b;
    font-size: 10px;
    font-weight: 700;
}

.market-design .fc-badges {
    display: flex;
    flex: 0 0 auto;
    flex-direction: column;
    gap: 4px;
    align-items: flex-end;
}

.market-design .fc-chip {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 10px;
    font-weight: 900;
    white-space: nowrap;
}

.market-design .fc-chip-up {
    color: #047857;
    background: #ecfdf5;
    border: 1px solid #a7f3d0;
}

.market-design .fc-chip-down {
    color: #b91c1c;
    background: #fef2f2;
    border: 1px solid #fecaca;
}

.market-design .fc-fresh {
    display: block;
    color: #0369a1;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: .02em;
}

.market-design .fc-stale {
    display: block;
    color: #b45309;
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-radius: 8px;
    padding: 3px 8px;
    font-size: 10px;
    font-weight: 800;
}

.market-design .fc-cta {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 9px 12px;
    border-radius: 12px;
    background: #0f172a;
    color: #fff;
    font-size: 11px;
    font-weight: 900;
    letter-spacing: .03em;
    transition: background .2s ease;
}

.market-design .fc-btn:hover .fc-cta {
    background: #1d4ed8;
}

.market-design .fc-cta-icon {
    display: inline-block;
    font-size: 10px;
    transition: transform .2s ease;
}

.market-design .fc-btn[aria-expanded="true"] {
    border-left-color: #22c55e;
}

/* ---- Yeni rozetler ---- */
.market-design .fc-best {
    display: block;
    color: #92400e;
    background: #fef9c3;
    border: 1px solid #fde68a;
    border-radius: 8px;
    padding: 3px 8px;
    font-size: 10px;
    font-weight: 800;
}

.market-design .trend-line {
    display: block;
    margin-left: auto;
    margin-top: 6px;
}

/* ---- Mobil: yan sütunlardaki araçlar görünür, reklam görselleri gizli ---- */
@media (max-width: 1023px) {
    .market-design .feature-ad-stack {
        display: none !important;
    }

    .market-design .desktop-feature-column > div {
        max-width: none !important;
        width: 100% !important;
        margin: 0 !important;
    }

    .market-design {
        padding-bottom: 88px;
    }
}

.market-design .side-widget {
    font-variant-numeric: tabular-nums;
}

@media (max-width: 1023px) {
    .market-design .desktop-feature-column > .side-widget {
        max-width: none !important;
        width: 100% !important;
        margin: 0 !important;
    }
}


/* ---- Kart içeriği: tüm kartlar aynı yükseklikte ---- */
.market-design .fc-top {
    min-height: 52px;
}

.market-design .fc-fresh {
    display: block;
    padding: 3px 8px;
    border: 1px solid #bae6fd;
    border-radius: 8px;
    background: #f0f9ff;
    font-size: 10px;
    font-weight: 800;
}

.market-design .fc-fresh.fc-fresh-ok {
    color: #047857;
    background: #ecfdf5;
    border-color: #a7f3d0;
}

.market-design .fc-fresh.fc-fresh-muted {
    color: #475569;
    background: #f1f5f9;
    border-color: #e2e8f0;
}

html.dark .market-design .fc-fresh {
    background: rgba(14, 165, 233, .14);
    border-color: rgba(14, 165, 233, .35);
}

html.dark .market-design .fc-fresh.fc-fresh-ok {
    color: #6ee7b7;
    background: rgba(16, 185, 129, .15);
    border-color: rgba(16, 185, 129, .35);
}

html.dark .market-design .fc-fresh.fc-fresh-muted {
    color: #cbd5e1;
    background: rgba(148, 163, 184, .14);
    border-color: rgba(148, 163, 184, .3);
}

/* ---- LME: altın tema ---- */
#lmeSection {
    background: linear-gradient(180deg, #fffbeb 0%, #ffffff 55%) !important;
    border-color: #f2d58a !important;
    box-shadow: 0 8px 24px rgba(180, 120, 10, .12) !important;
}

#lmeSection .lme-ticker-label {
    background: linear-gradient(135deg, #f6d365 0%, #c9971c 100%) !important;
    border-right: 0 !important;
}

#lmeSection .lme-ticker-label span {
    color: #3b2a00 !important;
}

#lmeSection .lme-ticker-metal {
    color: #fde68a !important;
}

#lmeSection .lme-ticker-value {
    color: #fbbf24 !important;
}

#lmeToggle .bg-slate-900 {
    background: linear-gradient(135deg, #f6d365 0%, #d4a017 55%, #9a6b0c 100%) !important;
    color: #3b2a00 !important;
    box-shadow: 0 4px 12px rgba(180, 120, 10, .35) !important;
}

#lmeToggle .text-slate-400 {
    color: #b8860b !important;
}

#lmeToggle .bg-slate-100 {
    background-color: #fef3c7 !important;
    border-color: #f6d365 !important;
}

#lmeToggle .bg-slate-100.text-slate-500,
#lmeToggle .text-slate-500 {
    color: #92400e !important;
}

#lmeToggle .text-slate-900 {
    color: #7c4a03 !important;
}

#lmeGrid {
    border-color: #f2d58a !important;
}

#lmeGrid thead {
    background-color: #fef3c7 !important;
    border-color: #f2d58a !important;
}

#lmeGrid thead th {
    color: #92400e !important;
}

#lmeTableBody tr {
    border-color: #fbe9b7 !important;
}

#lmeTableBody tr:hover {
    background-color: #fffbeb !important;
}

#lmeTableBody td {
    color: #7c4a03 !important;
}

#lmeTableBody td:first-child {
    color: #5b3a00 !important;
}

#lmeTableBody td:last-child {
    color: #b45309 !important;
    font-weight: 900;
}

#lmeTableBody .bg-slate-900 {
    background: linear-gradient(135deg, #f6d365 0%, #d4a017 100%) !important;
    color: #3b2a00 !important;
    box-shadow: 0 2px 8px rgba(180, 120, 10, .25);
}

html.dark #lmeSection {
    background: linear-gradient(180deg, #1d1707 0%, #111a2e 55%) !important;
    border-color: #5b4510 !important;
    box-shadow: 0 8px 24px rgba(0, 0, 0, .35) !important;
}

html.dark #lmeToggle .text-slate-900 {
    color: #fbbf24 !important;
}

html.dark #lmeToggle .bg-slate-100 {
    background-color: rgba(212, 160, 23, .18) !important;
    border-color: rgba(212, 160, 23, .45) !important;
}

html.dark #lmeToggle .text-slate-500 {
    color: #fcd34d !important;
}

html.dark #lmeGrid {
    border-color: #5b4510 !important;
}

html.dark #lmeGrid thead {
    background-color: #2a210c !important;
}

html.dark #lmeGrid thead th {
    color: #fbbf24 !important;
}

html.dark #lmeTableBody tr {
    border-color: #3a2d0e !important;
}

html.dark #lmeTableBody tr:hover {
    background-color: #241c0a !important;
}

html.dark #lmeTableBody td {
    color: #fde68a !important;
}

html.dark #lmeTableBody td:first-child {
    color: #fef3c7 !important;
}

html.dark #lmeTableBody td:last-child {
    color: #fbbf24 !important;
}


/* ---- LME grafik ---- */
.lme-chart-box {
    background: #fffbeb;
    border-color: #f2d58a;
}

/* Grafik her açılışta kapalı başlar; admin pasif ederse hiç görünmez. */
.lme-chart-box.lme-kapali,
.lme-chart-box.lme-pasif,
#lmeGrafikToggle.lme-pasif {
    display: none !important;
}

#lmeGrafikToggle[aria-pressed="true"] {
    background: linear-gradient(135deg, #f6d365 0%, #c9971c 100%);
    color: #3b2a00;
    border-color: #d4a017;
}

/* Geniş ekranda grafik tablonun hemen sağında açılır; dar ekran ve mobilde altında. */
@media (min-width: 1280px) {
    #lmeSplit.lme-acik {
        display: grid;
        grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
        gap: 1rem;
        align-items: start;
    }

    #lmeSplit.lme-acik .lme-chart-box {
        margin-top: 0;
    }
}

.lme-chart-label { color: #b8860b; }
.lme-chart-title { color: #7c4a03; }
.lme-chart-note { color: #a16207; }

.lme-chip {
    height: 30px;
    padding: 0 11px;
    border-radius: 999px;
    border: 1px solid #f2d58a;
    background: #fff;
    color: #92400e;
    font-size: 11px;
    font-weight: 800;
    cursor: pointer;
    transition: background .15s ease;
}

.lme-chip:hover { background: #fef3c7; }

.lme-chip-on {
    background: linear-gradient(135deg, #f6d365 0%, #d4a017 100%);
    border-color: #d4a017;
    color: #3b2a00;
}

.lme-svg text { fill: #a16207; font-size: 10px; font-weight: 700; }
.lme-grid-line { stroke: #f2d58a; stroke-width: 1; stroke-dasharray: 3 4; }
.lme-line-cash { fill: none; stroke: #d4a017; stroke-width: 2.4; stroke-linejoin: round; stroke-linecap: round; }
.lme-line-3m { fill: none; stroke: #92400e; stroke-width: 1.8; stroke-dasharray: 5 4; stroke-linejoin: round; stroke-linecap: round; }
.lme-area { fill: rgba(212, 160, 23, .14); }
.lme-dot-cash { fill: #d4a017; stroke: #fff; stroke-width: 1.5; }

.lme-tooltip {
    position: absolute;
    pointer-events: none;
    z-index: 5;
    padding: 6px 9px;
    border-radius: 10px;
    background: #3b2a00;
    color: #fde68a;
    font-size: 11px;
    font-weight: 700;
    line-height: 1.35;
    white-space: nowrap;
    box-shadow: 0 6px 18px rgba(0, 0, 0, .3);
}

.lme-bar-track { height: 7px; border-radius: 999px; background: #fde68a55; overflow: hidden; }
.lme-bar-up { height: 100%; border-radius: 999px; background: #10b981; }
.lme-bar-down { height: 100%; border-radius: 999px; background: #ef4444; }

html.dark .lme-chart-box { background: #1a1405; border-color: #5b4510; }
html.dark .lme-chart-label { color: #fbbf24; }
html.dark .lme-chart-title { color: #fde68a; }
html.dark .lme-chart-note { color: #b8963a; }
html.dark .lme-chip { background: #241c0a; border-color: #5b4510; color: #fcd34d; }
html.dark .lme-chip:hover { background: #2f2410; }
html.dark .lme-chip-on { background: linear-gradient(135deg, #f6d365 0%, #c9971c 100%); color: #3b2a00; border-color: #d4a017; }
html.dark .lme-svg text { fill: #d6b25e; }
html.dark .lme-grid-line { stroke: #4a3a12; }
html.dark .lme-line-cash { stroke: #fbbf24; }
html.dark .lme-line-3m { stroke: #fde68a; }
html.dark .lme-area { fill: rgba(251, 191, 36, .12); }
html.dark .lme-dot-cash { fill: #fbbf24; stroke: #1a1405; }
html.dark .lme-bar-track { background: #3a2d0e; }


/* ---- Kompakt, animasyonlu fiyat paneli ---- */
.market-design .fp-wrap {
    display: grid;
    grid-template-rows: 0fr;
    opacity: 0;
    border-top: 1px solid transparent;
    transition: grid-template-rows .32s ease, opacity .25s ease, border-color .25s ease;
}

.market-design .fp-wrap.open {
    grid-template-rows: 1fr;
    opacity: 1;
    border-top-color: #e2e8f0;
}

.market-design .fp-inner {
    min-height: 0;
    overflow: hidden;
}

.market-design .fp-body {
    padding: 10px 12px 12px;
    background: rgba(248, 250, 252, .7);
}

.market-design .fp-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 7px;
}

.market-design .fp-title {
    font-size: 10px;
    font-weight: 900;
    letter-spacing: .12em;
    text-transform: uppercase;
    color: #64748b;
}

.market-design .fp-actions {
    display: flex;
    align-items: center;
    gap: 6px;
}

.market-design .fp-btn {
    display: inline-flex;
    align-items: center;
    height: 26px;
    padding: 0 9px;
    border-radius: 8px;
    border: 1px solid #e2e8f0;
    background: #fff;
    color: #475569;
    font-size: 10px;
    font-weight: 800;
    white-space: nowrap;
    cursor: pointer;
    transition: background .15s ease;
}

.market-design .fp-btn:hover { background: #f1f5f9; }
.market-design .fp-btn.wa { background: #059669; border-color: #059669; color: #fff; }
.market-design .fp-btn.wa:hover { background: #047857; }
.market-design .fp-btn-muted { color: #94a3b8; cursor: default; }

.market-design .fp-list {
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    background: #fff;
    overflow: hidden;
}

.market-design .fp-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    align-items: center;
    gap: 10px;
    padding: 7px 11px;
    border-bottom: 1px solid #f1f5f9;
}

.market-design .fp-row:last-child { border-bottom: 0; }

.market-design .fp-cins {
    display: block;
    font-size: 13px;
    font-weight: 800;
    color: #0f172a;
    line-height: 1.2;
    overflow-wrap: anywhere;
}

.market-design .fp-meta {
    display: block;
    margin-top: 1px;
    font-size: 10px;
    font-weight: 600;
    color: #94a3b8;
}

.market-design .fp-spark { line-height: 0; }
.market-design .trend-inline { display: block; }

.market-design .fp-price {
    text-align: right;
    white-space: nowrap;
}

.market-design .fp-fiyat {
    font-size: 15px;
    font-weight: 900;
    color: #0f172a;
}

.market-design .fp-unit {
    margin-left: 3px;
    font-size: 9px;
    font-weight: 700;
    color: #94a3b8;
}

.market-design .fp-chg {
    display: block;
    margin-top: 1px;
    font-size: 10px;
    font-weight: 900;
}

.market-design .fp-chg.up { color: #059669; }
.market-design .fp-chg.down { color: #dc2626; }

@media (max-width: 380px) {
    .market-design .fp-spark { display: none; }
    .market-design .fp-row { grid-template-columns: minmax(0, 1fr) auto; }
}

html.dark .market-design .fp-wrap.open { border-top-color: #24324d; }
html.dark .market-design .fp-body { background: rgba(14, 22, 39, .7); }
html.dark .market-design .fp-title { color: #9aa8bf; }
html.dark .market-design .fp-btn { background: #162036; border-color: #24324d; color: #cbd5e1; }
html.dark .market-design .fp-btn:hover { background: #1e2b44; }
html.dark .market-design .fp-btn.wa { background: #059669; border-color: #059669; color: #fff; }
html.dark .market-design .fp-list { background: #111a2e; border-color: #24324d; }
html.dark .market-design .fp-row { border-bottom-color: #1e2b44; }
html.dark .market-design .fp-cins,
html.dark .market-design .fp-fiyat { color: #f1f5f9; }
html.dark .market-design .fp-meta,
html.dark .market-design .fp-unit { color: #7c8aa3; }
html.dark .market-design .fp-chg.up { color: #34d399; }
html.dark .market-design .fp-chg.down { color: #f87171; }

/* ---- Mobil alt araç çubuğu ---- */
#mobileBar {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 50;
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 4px;
    padding: 6px 8px calc(6px + env(safe-area-inset-bottom));
    background: rgba(255, 255, 255, .96);
    border-top: 1px solid #e2e8f0;
    box-shadow: 0 -6px 20px rgba(15, 23, 42, .10);
    backdrop-filter: blur(8px);
}

#mobileBar button {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    height: 52px;
    border: 0;
    border-radius: 14px;
    background: transparent;
    color: #334155;
    font-size: 10px;
    font-weight: 800;
}

#mobileBar button:active {
    background: #e2e8f0;
}

#mobileBar button span:first-child {
    font-size: 19px;
    line-height: 1;
}

@media (min-width: 1024px) {
    #mobileBar {
        display: none;
    }
}

#themeToggleDesktop {
    position: fixed;
    right: 16px;
    bottom: 16px;
    z-index: 50;
    width: 44px;
    height: 44px;
    border-radius: 999px;
    border: 1px solid #cbd5e1;
    background: #fff;
    box-shadow: 0 6px 18px rgba(15, 23, 42, .18);
    font-size: 20px;
    line-height: 1;
    cursor: pointer;
}

@media (max-width: 1023px) {
    #themeToggleDesktop {
        display: none;
    }
}

/* ---- Karanlık mod ---- */
html.dark {
    color-scheme: dark;
}

html.dark .market-design {
    background:
        radial-gradient(circle at 8% 0%, rgba(30, 41, 59, .55), transparent 30%),
        radial-gradient(circle at 92% 8%, rgba(30, 58, 138, .20), transparent 32%),
        #0a101d !important;
    color: #e2e8f0;
}

html.dark .bg-white,
html.dark [class*="bg-white/9"],
html.dark [class*="bg-white/8"],
html.dark [class*="bg-white/7"],
html.dark [class*="bg-white/6"] {
    background-color: #111a2e !important;
}

html.dark .bg-slate-50,
html.dark [class*="bg-slate-50/"] {
    background-color: #0e1627 !important;
}

html.dark .bg-slate-100 {
    background-color: #162036 !important;
}

html.dark [class*="hover:bg-slate-50"]:hover {
    background-color: #162036 !important;
}

html.dark .border-slate-100,
html.dark .border-slate-200,
html.dark .border-slate-300,
html.dark [class*="border-slate-200/"],
html.dark [class*="border-white/7"] {
    border-color: #24324d !important;
}

html.dark .divide-slate-100 > * + * {
    border-color: #1e2b44 !important;
}

html.dark .text-slate-950,
html.dark .text-slate-900,
html.dark .text-slate-800 {
    color: #f1f5f9 !important;
}

html.dark .text-slate-700 {
    color: #cbd5e1 !important;
}

html.dark .text-slate-600,
html.dark .text-slate-500 {
    color: #9aa8bf !important;
}

html.dark .text-slate-400 {
    color: #7c8aa3 !important;
}

html.dark .bg-emerald-50,
html.dark .bg-emerald-100 {
    background-color: rgba(16, 185, 129, .15) !important;
}

html.dark .text-emerald-700,
html.dark .text-emerald-600 {
    color: #6ee7b7 !important;
}

html.dark .border-emerald-200 {
    border-color: rgba(16, 185, 129, .35) !important;
}

html.dark .bg-red-50 {
    background-color: rgba(239, 68, 68, .15) !important;
}

html.dark .text-red-700,
html.dark .text-red-600 {
    color: #fca5a5 !important;
}

html.dark .border-red-200 {
    border-color: rgba(239, 68, 68, .35) !important;
}

html.dark .bg-amber-50,
html.dark .bg-amber-100 {
    background-color: rgba(245, 158, 11, .15) !important;
}

html.dark .text-amber-700,
html.dark .text-amber-800,
html.dark .text-amber-900 {
    color: #fcd34d !important;
}

html.dark .border-amber-200 {
    border-color: rgba(245, 158, 11, .35) !important;
}

html.dark .bg-sky-50,
html.dark [class*="bg-sky-50/"] {
    background-color: rgba(14, 165, 233, .12) !important;
}

html.dark .text-sky-700,
html.dark .text-sky-600 {
    color: #7dd3fc !important;
}

html.dark .border-sky-200 {
    border-color: rgba(14, 165, 233, .35) !important;
}

html.dark .bg-indigo-100 {
    background-color: rgba(99, 102, 241, .2) !important;
}

html.dark .text-indigo-700 {
    color: #a5b4fc !important;
}

html.dark .bg-yellow-100 {
    background-color: rgba(234, 179, 8, .18) !important;
}

html.dark .text-yellow-800 {
    color: #fde047 !important;
}

html.dark input,
html.dark select,
html.dark textarea {
    background-color: #0e1627 !important;
    color: #e2e8f0 !important;
    border-color: #24324d !important;
}

html.dark input::placeholder {
    color: #6b7a93 !important;
}

html.dark .fc-btn {
    background: linear-gradient(180deg, #111a2e 0%, #0e1627 100%) !important;
}

html.dark .fc-btn:hover {
    background: #162036 !important;
}

html.dark .fc-title {
    color: #f1f5f9;
}

html.dark .fc-meta {
    color: #9aa8bf;
}

html.dark .fc-cta {
    background: #1e40af;
}

html.dark .fc-stale {
    color: #fcd34d;
    background: rgba(245, 158, 11, .15);
    border-color: rgba(245, 158, 11, .35);
}

html.dark .fc-best {
    color: #fde047;
    background: rgba(234, 179, 8, .16);
    border-color: rgba(234, 179, 8, .35);
}

html.dark .fc-chip-up {
    color: #6ee7b7;
    background: rgba(16, 185, 129, .15);
    border-color: rgba(16, 185, 129, .35);
}

html.dark .fc-chip-down {
    color: #fca5a5;
    background: rgba(239, 68, 68, .15);
    border-color: rgba(239, 68, 68, .35);
}

html.dark .market-summary-card {
    background: #111a2e !important;
    border-color: #24324d !important;
}

html.dark .market-summary-value {
    color: #f1f5f9;
}

html.dark .market-summary-card.up .market-summary-value {
    color: #34d399;
}

html.dark .market-summary-card.down .market-summary-value {
    color: #f87171;
}

html.dark #mobileBar {
    background: rgba(10, 16, 29, .96);
    border-top-color: #24324d;
}

html.dark #mobileBar button {
    color: #cbd5e1;
}

html.dark #mobileBar button:active {
    background: #162036;
}

html.dark #themeToggleDesktop {
    background: #111a2e;
    border-color: #24324d;
}

html.dark .ad-box {
    filter: brightness(.9);
}

/* Eski kimlik/sınıf tabanlı beyaz arka planları karanlık modda ez */
html.dark .market-design #marketTools {
    background-color: #111a2e !important;
    border-color: #24324d !important;
}

html.dark .market-design #marketTools input,
html.dark .market-design #marketTools select {
    background-color: #0e1627 !important;
}

html.dark .market-design .price-card .firma-toggle,
html.dark .market-design .price-card .fc-btn {
    background: linear-gradient(180deg, #111a2e 0%, #0e1627 100%) !important;
}

html.dark .market-design .price-card .firma-toggle:hover,
html.dark .market-design .price-card .fc-btn:hover {
    background: #162036 !important;
}

html.dark .market-design .price-card .price-row,
html.dark .price-card .price-row {
    background: #111a2e !important;
}

html.dark .market-design .price-card .price-row:hover {
    background: #162036 !important;
}

html.dark .market-design .bg-slate-900:not(header *) {
    background-color: #1e293b !important;
    border: 1px solid #334155;
}

html.dark [class*="bg-emerald-50/"] {
    background-color: rgba(16, 185, 129, .13) !important;
}
</style>

</head>

<body class="market-design min-h-screen">

<nav id="mobileBar" aria-label="Hızlı işlemler">
<button type="button" data-bar="ara"><span>🔍</span><span>Ara</span></button>
<button type="button" data-bar="alarm"><span>🔔</span><span>Alarm</span></button>
<button type="button" data-bar="paylas"><span>💬</span><span>Paylaş</span></button>
<button type="button" data-bar="tema"><span data-tema-ikon>🌙</span><span>Tema</span></button>
</nav>
<button type="button" id="themeToggleDesktop" aria-label="Karanlık / aydınlık tema" data-bar="tema"><span data-tema-ikon>🌙</span></button>


<div class="w-full max-w-7xl mx-auto px-3 sm:px-4 py-3 sm:py-4">

<header class="relative overflow-hidden bg-slate-950 text-white rounded-2xl sm:rounded-3xl p-4 sm:p-5 mb-4 sm:mb-5 shadow-xl border border-slate-800">

<div class="absolute -right-16 -top-20 w-44 h-44 rounded-full bg-slate-800/40 blur-2xl"></div>
<div class="absolute -left-10 -bottom-20 w-36 h-36 rounded-full bg-slate-800/30 blur-2xl"></div>

<div class="relative flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">

<div class="min-w-0">

<div class="inline-flex items-center gap-2 rounded-full bg-white/10 border border-white/10 px-3 py-1 text-[10px] font-black uppercase tracking-[0.15em] text-slate-300">
<span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
Piyasa Takip Merkezi
</div>

<h1 class="text-2xl sm:text-3xl md:text-4xl font-black tracking-tight mt-2 break-words">
Cevhersan Metal
</h1>

<p class="text-slate-300 text-sm sm:text-base mt-1">
Güncel hurda fiyatları, döviz kurları ve LME verileri
</p>

<div class="flex flex-wrap items-center gap-2 mt-3">
<span class="inline-flex items-center gap-2 rounded-xl bg-white/10 border border-white/10 px-3 py-1.5 text-[10px] sm:text-[11px] font-bold text-slate-200">
<span class="text-slate-400">Veri</span>
Otomatik güncelleniyor
</span>

<span class="inline-flex items-center gap-2 rounded-xl bg-emerald-400/10 border border-emerald-400/20 px-3 py-1.5 text-[10px] sm:text-[11px] font-bold text-emerald-300">
<span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
Sistem aktif
</span>
</div>

</div>

<div class="w-full lg:w-auto lg:min-w-[215px]">

<div class="rounded-2xl bg-white/10 border border-white/10 backdrop-blur-sm p-3 sm:p-3.5">

<div class="flex items-center justify-between gap-3">
<div>
<div class="text-[10px] uppercase tracking-wide text-slate-400 font-black">
Son Güncelleme
</div>

<div
id="sonGuncelleme"
class="text-sm sm:text-base font-black text-white mt-1 break-words"
>
Yükleniyor...
</div>
</div>

<div class="w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_0_4px_rgba(52,211,153,0.10)] shrink-0"></div>
</div>

<div class="h-px bg-white/10 my-2.5"></div>

<div class="flex items-center justify-between gap-3 text-[10px]">
<span class="text-slate-400">Piyasa ekranı</span>
<span class="font-bold text-emerald-300">Aktif</span>
</div>

</div>

</div>

</div>

</header>

<!-- =====================================================
     DÖVİZ KURLARI
     ===================================================== -->

<section
id="currencySection"
class="mb-3 sm:mb-4"
>

<div class="currency-ticker-shell relative overflow-hidden rounded-2xl border border-slate-800 bg-slate-950 shadow-md">

<div class="currency-ticker-label shrink-0">
<span class="inline-flex items-center gap-1.5 rounded-xl bg-white/10 border border-white/10 px-2.5 py-1.5 text-[9px] sm:text-[10px] font-black uppercase tracking-[0.12em] text-slate-200">
<span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
DÖVİZ
</span>
</div>

<div class="currency-ticker-track">
<div class="currency-ticker-content">

<div class="currency-ticker-item usd">
<span class="currency-code">USD</span>
<span class="currency-label">Dolar</span>
<span id="usdAlis" class="currency-value">Yükleniyor...</span>
<span class="currency-divider">•</span>
<span class="currency-sub">Satış</span>
<span id="usdSatis" class="currency-sale">Yükleniyor...</span>
</div>

<div class="currency-ticker-separator">◆</div>

<div class="currency-ticker-item eur">
<span class="currency-code">EUR</span>
<span class="currency-label">Euro</span>
<span id="eurAlis" class="currency-value">Yükleniyor...</span>
<span class="currency-divider">•</span>
<span class="currency-sub">Satış</span>
<span id="eurSatis" class="currency-sale">Yükleniyor...</span>
</div>

<div class="currency-ticker-separator">◆</div>

<div class="currency-ticker-item gold">
<span class="currency-code">ALTIN</span>
<span class="currency-label">Gram</span>
<span id="altinAlis" class="currency-value">Yükleniyor...</span>
<span class="currency-divider">•</span>
<span class="currency-sub">Satış</span>
<span id="altinSatis" class="currency-sale">Yükleniyor...</span>
</div>

<div class="currency-ticker-separator">◆</div>

<div class="currency-ticker-item">
<span class="currency-label">Kaynak</span>
<span id="currencyKaynak" class="currency-sub">Güncelleniyor...</span>
</div>

</div>
</div>

</div>

<div
id="currencyInfo"
class="mt-1 px-1 text-[9px] sm:text-[10px] text-slate-400"
>
Kur kaynağı: TCMB · Güncelleniyor...
</div>

</section>


<!-- =====================================================
     LME METAL FİYATLARI
     ===================================================== -->

<section
id="lmeSection"
class="bg-white rounded-2xl shadow-sm border border-slate-200 p-3 sm:p-4 mb-4 sm:mb-5"
>

<div
id="lmeTicker"
class="lme-ticker-shell mb-3 rounded-xl border border-slate-200 bg-slate-950"
>
<div class="lme-ticker-label">
<span class="text-[9px] font-black uppercase tracking-[0.12em] text-slate-300">
LME
</span>
</div>
<div class="lme-ticker-track">
<div id="lmeTickerContent" class="lme-ticker-content">
<span class="text-[10px] font-bold text-slate-400">
LME verileri alınıyor...
</span>
</div>
</div>
</div>

<button
type="button"
id="lmeToggle"
class="w-full text-left flex items-center justify-between gap-3 mb-0 hover:bg-slate-50 rounded-2xl p-2 -m-2 transition"
aria-expanded="true"
aria-controls="lmeContent"
>

<div class="flex items-center gap-3 min-w-0">

<div class="w-10 h-10 sm:w-11 sm:h-11 rounded-2xl bg-slate-900 text-white flex items-center justify-center text-sm font-black shadow-sm shrink-0">
LME
</div>

<div class="min-w-0">

<div class="flex items-center gap-2 flex-wrap">
<span class="text-[10px] font-black uppercase tracking-[0.14em] text-slate-400">
Londra Metal Borsası
</span>

<span class="inline-flex items-center gap-1 rounded-lg bg-slate-100 border border-slate-200 px-2 py-1 text-[9px] font-black text-slate-500">
7 METAL
</span>
</div>

<div class="flex items-center gap-2 mt-0.5">
<div class="text-lg sm:text-xl font-black text-slate-900">
LME Metal Fiyatları
</div>

<span
id="lmeToggleIcon"
class="text-slate-400 text-sm transition-transform"
>
▼
</span>
</div>

<div class="text-[10px] sm:text-[11px] text-slate-400 mt-0.5">
Gün gecikmeli resmi piyasa verileri · USD / metrik ton
</div>

</div>

</div>

<div class="shrink-0 hidden sm:flex items-center gap-2">
<span class="text-[10px] font-black text-slate-400">
AÇ / KAPAT
</span>
<span
class="w-8 h-8 rounded-xl border border-slate-200 bg-white flex items-center justify-center shadow-sm"
>
⌄
</span>
</div>

<div
id="lmeInfo"
class="text-[9px] text-slate-500 text-right max-w-[140px] sm:max-w-[190px]"
>
LME verisi yükleniyor...
</div>

</button>

<div
id="lmeContent"
class="mt-4"
aria-hidden="false"
>

<div class="flex justify-end mb-2">
<button
type="button"
id="lmeGrafikToggle"
aria-pressed="false"
class="lme-chip"
>
📈 Grafik
</button>
</div>

<div id="lmeSplit">

<div
id="lmeGrid"
class="w-full overflow-x-auto rounded-xl border border-slate-200"
>

<table class="w-full min-w-[720px] border-collapse text-sm">
<thead class="bg-slate-50 border-b border-slate-200">
<tr>
<th class="px-3 sm:px-4 py-3 text-left text-[10px] sm:text-[11px] uppercase tracking-wide font-black text-slate-500">
Metal
</th>
<th class="px-3 sm:px-4 py-3 text-right text-[10px] sm:text-[11px] uppercase tracking-wide font-black text-slate-500">
Alış (Nakit) $
</th>
<th class="px-3 sm:px-4 py-3 text-right text-[10px] sm:text-[11px] uppercase tracking-wide font-black text-slate-500">
Satış (Nakit) $
</th>
<th class="px-3 sm:px-4 py-3 text-right text-[10px] sm:text-[11px] uppercase tracking-wide font-black text-slate-500">
3 Ay Alış $
</th>
<th class="px-3 sm:px-4 py-3 text-right text-[10px] sm:text-[11px] uppercase tracking-wide font-black text-slate-500">
3 Ay Satış $
</th>
<th class="px-3 sm:px-4 py-3 text-right text-[10px] sm:text-[11px] uppercase tracking-wide font-black text-slate-500">
3 Ay TL / Ton
</th>
</tr>
</thead>

<tbody id="lmeTableBody">
<tr>
<td
colspan="6"
class="px-4 py-8 text-center text-sm text-slate-500"
>
LME verileri alınıyor...
</td>
</tr>
</tbody>
</table>

</div>

<div id="lmeChartBox" class="lme-chart-box lme-kapali mt-4 rounded-xl border p-3 sm:p-4">

<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-3">
<div>
<div class="text-[9px] uppercase tracking-[0.14em] font-black lme-chart-label">Grafik</div>
<div class="text-base font-black lme-chart-title">📈 LME Fiyat Grafiği</div>
</div>
<div class="flex items-center gap-1.5" id="lmeRangeBtns">
<button type="button" data-days="7" class="lme-chip">7G</button>
<button type="button" data-days="30" class="lme-chip lme-chip-on">30G</button>
<button type="button" data-days="90" class="lme-chip">90G</button>
</div>
</div>

<div id="lmeMetalTabs" class="flex flex-wrap gap-1.5 mb-3"></div>

<div id="lmeChartArea" class="relative"></div>

<div class="mt-4">
<div class="text-[10px] uppercase tracking-wide font-black lme-chart-label mb-1.5">Nakit → 3 Ay farkı (%)</div>
<div id="lmeSpreadBars" class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5"></div>
<div class="text-[10px] mt-2 lme-chart-note">Yeşil: 3 ay vadeli fiyat nakitten yüksek (contango). Kırmızı: düşük (backwardation).</div>
</div>

</div>

</div>

</div>

<div class="mt-3 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 text-[10px] text-slate-400">
<div>
3 Ay TL değeri: 3 aylık alış/satış ortalaması × USD/TRY alış kuru
</div>

<div class="shrink-0">
Kaynak: LME Official Prices
</div>
</div>

</section>


<!-- =====================================================
     MOBİL REKLAMLAR
     ===================================================== -->


<div class="factory-layout grid grid-cols-1 lg:grid-cols-[250px_minmax(0,1fr)_250px] gap-4 lg:gap-5 items-start">

<aside class="desktop-feature-column grid gap-4 order-2 lg:order-none" aria-label="Sol piyasa araçları">
<div id="calculatorSidePanel" class="w-full max-w-[250px] mr-auto" aria-label="Hurda değeri hesaplama">
<div
id="calculatorPanel"
class="mt-0 border-t border-slate-200 pt-0"
>
<div class="rounded-2xl border border-emerald-200 bg-emerald-50/70 p-3 sm:p-4">
<div class="flex items-start justify-between gap-2 mb-2">
<div>
<div class="text-[9px] uppercase tracking-[0.10em] font-black text-emerald-700">Hesaplama Aracı</div>
<div class="calc-title text-[13px] font-black text-slate-900 mt-0.5">Hurda Değeri Hesapla</div>
<div class="calc-description text-[9px] text-slate-500 mt-1">Güncel listedeki fiyatı ton miktarıyla çarpar.</div>
</div>
<span class="inline-flex items-center rounded-xl bg-white border border-emerald-200 px-2 py-1 text-[9px] font-black text-emerald-700 whitespace-nowrap">TL / TON</span>
</div>
<div class="calc-form-grid grid grid-cols-1 gap-1.5">
<select id="calcFirm" class="h-10 rounded-xl border border-slate-200 bg-white px-3 text-xs font-bold text-slate-700"><option value="">Firma seçin</option></select>
<select id="calcItem" class="h-10 rounded-xl border border-slate-200 bg-white px-3 text-xs font-bold text-slate-700"><option value="">Kalem seçin</option></select>
<input id="calcQuantity" type="number" min="0" step="0.01" placeholder="Miktar (ton)" class="h-10 rounded-xl border border-slate-200 bg-white px-3 text-xs font-bold text-slate-700">
<button type="button" id="calcButton" class="h-10 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-black transition">Hesapla</button>
</div>
<div id="calcResult" class="mt-3">
<div class="rounded-xl border border-dashed border-emerald-200 bg-white/80 p-3 text-center text-xs text-slate-500">Firma, kalem ve ton miktarı seçip hesaplayın.</div>
</div>
</div>
</div>
</div>
<div class="feature-ad-stack grid gap-3" aria-label="Piyasa görseli">
<div class="ad-box rounded-2xl overflow-hidden" id="bottomAd1"></div>
</div>
<section id="widgetBest" class="side-widget w-full max-w-[250px] mx-auto bg-white rounded-2xl border border-slate-200 shadow-sm p-3" aria-label="🏆 En Yüksek Fiyatlar">
<div class="text-[9px] uppercase tracking-[0.12em] font-black text-sky-600">Cins bazında</div>
<div class="text-[13px] font-black text-slate-900 mt-0.5 mb-2">🏆 En Yüksek Fiyatlar</div>
<div class="side-widget-body text-xs text-slate-500">Yükleniyor...</div>
</section>
<div class="feature-ad-stack grid gap-3" aria-label="Piyasa görseli">
<div class="ad-box rounded-2xl overflow-hidden" id="bottomAd2"></div>
</div>
<section id="widgetAlarms" class="side-widget w-full max-w-[250px] mx-auto bg-white rounded-2xl border border-slate-200 shadow-sm p-3" aria-label="🔔 Alarmlarım">
<div class="text-[9px] uppercase tracking-[0.12em] font-black text-sky-600">Takip</div>
<div class="text-[13px] font-black text-slate-900 mt-0.5 mb-2">🔔 Alarmlarım</div>
<div class="side-widget-body text-xs text-slate-500">Yükleniyor...</div>
</section>
<div class="feature-ad-stack grid gap-3" aria-label="Piyasa görseli">
<div class="ad-box rounded-2xl overflow-hidden" id="bottomAd3"></div>
</div>
<section id="widgetSources" class="side-widget w-full max-w-[250px] mx-auto bg-white rounded-2xl border border-slate-200 shadow-sm p-3" aria-label="Kaynaklar ve veri durumu">
<div class="text-[9px] uppercase tracking-[0.12em] font-black text-sky-600">Şeffaflık</div>
<div class="text-[13px] font-black text-slate-900 mt-0.5 mb-2">🔗 Kaynaklar ve Veri Durumu</div>
<div class="side-widget-body text-xs text-slate-500">Yükleniyor...</div>
</section>

</aside>

<main class="min-w-0 w-full mx-auto order-1 lg:order-none">

<div class="text-center mb-3 sm:mb-4 px-1">
<div class="text-[10px] sm:text-[11px] font-black uppercase tracking-[0.14em] text-cyan-300">
Güncel Hurda Fiyatları
</div>
<div class="text-lg sm:text-xl font-black text-white mt-1">
Fabrika Fiyatları
</div>
<div class="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 mt-1.5 text-[10px] font-bold text-slate-200">
<span class="inline-flex items-center gap-1.5">
<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,.7)]"></span>
<span class="text-emerald-300 font-black">Canlı takip</span>
</span>
<span class="text-slate-500">•</span>
<span class="text-slate-200">Firmaların son yayınladığı fiyatlar</span>
</div>
</div>

<section id="marketSummary" class="market-summary" aria-label="Piyasa özeti">
<div class="market-summary-card">
<div class="market-summary-label">Firma</div>
<div id="summaryFirmCount" class="market-summary-value">-</div>
<div class="market-summary-sub">aktif fiyat kaynağı</div>
</div>
<div class="market-summary-card">
<div class="market-summary-label">Fiyat Kalemi</div>
<div id="summaryItemCount" class="market-summary-value">-</div>
<div class="market-summary-sub">yayındaki fiyat</div>
</div>
<div class="market-summary-card up">
<div class="market-summary-label">Yükselen</div>
<div id="summaryUpCount" class="market-summary-value">-</div>
<div class="market-summary-sub">son değişime göre</div>
</div>
<div class="market-summary-card down">
<div class="market-summary-label">Düşen</div>
<div id="summaryDownCount" class="market-summary-value">-</div>
<div class="market-summary-sub">son değişime göre</div>
</div>
<div class="market-summary-card update">
<div class="market-summary-label">Son Güncelleme</div>
<div id="summaryLastUpdate" class="market-summary-value">Yükleniyor...</div>
<div class="market-summary-sub">son kontrol · TSİ</div>
</div>
</section>

<div
id="todayUpdates"
class="mt-3"
aria-live="polite"
></div>

<section
id="marketTools"
class="bg-white/95 rounded-2xl sm:rounded-3xl border border-white/70 shadow-lg p-3 sm:p-4 mb-4"
>

<div class="flex flex-col gap-3">

<div>
<label for="fiyatArama" class="block text-[11px] uppercase tracking-wide font-black text-slate-500 mb-1.5">
Fiyat / Firma Ara
</label>
<div class="relative">
<span class="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-slate-400 text-base" aria-hidden="true">🔍</span>
<input
id="fiyatArama"
type="search"
placeholder="DKP, Çolakoğlu, Erdemir..."
autocomplete="off"
class="w-full h-12 rounded-2xl border border-slate-200 bg-slate-50 pl-11 pr-4 text-base font-bold text-slate-800 placeholder:font-semibold placeholder:text-slate-400 outline-none transition focus:bg-white focus:border-sky-400 focus:ring-4 focus:ring-sky-100"
>
</div>
</div>

<div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">

<div class="grid grid-cols-2 sm:flex gap-2">
<button
type="button"
id="compareToggle"
class="h-11 px-5 rounded-xl bg-sky-600 text-white text-sm font-black hover:bg-sky-700 transition whitespace-nowrap"
>
🔎 Firma Karşılaştır
</button>
<button
type="button"
id="alarmButton"
class="h-11 px-5 rounded-xl bg-slate-900 text-white text-sm font-black hover:bg-slate-800 transition whitespace-nowrap"
>
🔔 Fiyat Alarmı
</button>
</div>

<div class="flex flex-col sm:flex-row sm:items-center gap-2">
<span class="text-[10px] uppercase tracking-wide font-black text-slate-400">Paylaş / İndir</span>
<div class="grid grid-cols-3 sm:flex gap-2">
<button
type="button"
id="shareWhatsapp"
class="h-10 px-3.5 rounded-xl bg-emerald-600 text-white text-xs font-black hover:bg-emerald-700 transition whitespace-nowrap"
>
💬 WhatsApp
</button>
<button
type="button"
id="exportCsv"
class="h-10 px-3.5 rounded-xl bg-white border border-slate-300 text-slate-800 text-xs font-black hover:bg-slate-50 transition whitespace-nowrap"
>
⬇ Excel
</button>
<button
type="button"
id="exportPdf"
class="h-10 px-3.5 rounded-xl bg-white border border-slate-300 text-slate-800 text-xs font-black hover:bg-slate-50 transition whitespace-nowrap"
>
🖨 PDF
</button>
</div>
</div>

</div>

</div>

<div
id="todayChanges"
class="mt-3 hidden"
></div>

<div
id="comparePanel"
class="mt-3 hidden border-t border-slate-200 pt-3"
>
<div class="flex flex-col sm:flex-row sm:items-end gap-2">
<div class="flex-1">
<label class="block text-[10px] uppercase tracking-wide font-black text-slate-500 mb-1.5">
Firma Karşılaştırma
</label>
<select
id="compareSelect"
class="w-full h-10 rounded-xl border border-slate-200 bg-white px-3 text-sm font-bold text-slate-700"
>
<option value="">Kalem seçin</option>
</select>
</div>
<button
type="button"
id="compareButton"
class="h-10 px-4 rounded-xl bg-sky-600 hover:bg-sky-700 text-white text-xs font-black transition"
>
Karşılaştır
</button>
</div>
<div id="compareResult" class="mt-3"></div>
</div>



<div
id="alarmPanel"
class="mt-3 hidden border-t border-slate-200 pt-3"
>
<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2">
<select id="alarmFirm" class="h-10 rounded-xl border border-slate-200 px-3 text-xs font-bold"></select>
<select id="alarmItem" class="h-10 rounded-xl border border-slate-200 px-3 text-xs font-bold"></select>
<select id="alarmDirection" class="h-10 rounded-xl border border-slate-200 px-3 text-xs font-bold">
<option value="above">Şu fiyata çıkınca</option>
<option value="below">Şu fiyatın altına inince</option>
</select>
<input id="alarmValue" type="number" step="0.01" min="0" placeholder="Hedef TL" class="h-10 rounded-xl border border-slate-200 px-3 text-xs font-bold">
<button type="button" id="alarmSaveButton" class="h-10 rounded-xl bg-amber-500 hover:bg-amber-600 text-white text-xs font-black">
Alarmı Kaydet
</button>
</div>
<div id="alarmList" class="mt-3"></div>
<div class="mt-3 flex flex-wrap items-center gap-2">
<button type="button" id="pushToggle" class="h-9 px-3 rounded-xl bg-sky-600 hover:bg-sky-700 text-white text-xs font-black transition">
🔔 Bildirimleri aç
</button>
<span id="pushStatus" class="text-[11px] font-semibold text-slate-500"></span>
</div>
<label id="dailyDigestRow" class="hidden mt-2 flex items-center gap-2 text-xs font-semibold text-slate-600 cursor-pointer">
<input type="checkbox" id="dailyDigest" class="h-4 w-4 rounded border-slate-300">
Her sabah 08:30'da günlük fiyat özeti bildirimi al
</label>
</div>

</section>

<div
id="loading"
class="bg-white rounded-2xl p-6 sm:p-8 text-center text-slate-500"
>
Firmalar yükleniyor...
</div>

<div
id="firmaListesi"
class="factory-price-grid grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 items-stretch gap-3 sm:gap-4 w-full min-w-0"
>
</div>



</main>

<aside class="desktop-feature-column grid gap-4 order-3 lg:order-none" aria-label="Sağ piyasa araçları">
<div id="historySidePanel" class="w-full max-w-[250px] ml-auto" aria-label="Fiyat geçmişi ve grafik">
<div
id="historyPanel"
class="border border-slate-200 bg-white rounded-2xl shadow-sm p-2.5"
>
<div class="flex items-start justify-between gap-3 mb-3">
<div>
<div class="text-[10px] uppercase tracking-[0.14em] font-black text-sky-600">Fiyat Geçmişi</div>
<h2 class="text-base sm:text-lg font-black text-slate-950 mt-0.5">Fiyat Geçmişi & Grafik</h2>
<p class="text-[10px] sm:text-[11px] text-slate-500 mt-1">Seçtiğin firma ve kalemin geçmiş fiyat hareketini burada gör.</p>
</div>
<span class="inline-flex items-center rounded-xl bg-sky-50 border border-sky-200 px-2 py-1 text-[9px] font-black text-sky-700 whitespace-nowrap">GEÇMİŞ</span>
</div>

<div class="flex flex-col gap-2">
<select
id="historyFirmSelect"
class="w-full h-10 rounded-xl border border-slate-200 bg-white px-3 text-xs font-bold text-slate-700"
>
<option value="">Firma seçin</option>
</select>
<select
id="historyItemSelect"
class="w-full h-10 rounded-xl border border-slate-200 bg-white px-3 text-xs font-bold text-slate-700"
>
<option value="">Kalem seçin</option>
</select>
<button
type="button"
id="historyLoadButton"
class="h-10 px-4 rounded-xl bg-slate-900 hover:bg-slate-800 text-white text-xs font-black transition"
>
Grafiği Göster
</button>
</div>

<div id="historyResult" class="mt-3">
<div class="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-4 text-center text-xs text-slate-500">
Firma ve kalem seçip <strong>Grafiği Göster</strong> butonuna bas.
</div>
</div>
</div>

</div>
<div class="feature-ad-stack grid gap-3" aria-label="Piyasa görseli">
<div class="ad-box rounded-2xl overflow-hidden" id="bottomAd4"></div>
</div>
<section id="widgetChanges" class="side-widget w-full max-w-[250px] mx-auto bg-white rounded-2xl border border-slate-200 shadow-sm p-3" aria-label="📈 Son Değişimler">
<div class="text-[9px] uppercase tracking-[0.12em] font-black text-sky-600">Akış</div>
<div class="text-[13px] font-black text-slate-900 mt-0.5 mb-2">📈 Son Değişimler</div>
<div class="side-widget-body text-xs text-slate-500">Yükleniyor...</div>
</section>
<div class="feature-ad-stack grid gap-3" aria-label="Piyasa görseli">
<div class="ad-box rounded-2xl overflow-hidden" id="bottomAd5"></div>
</div>
<section id="widgetRanking" class="side-widget w-full max-w-[250px] mx-auto bg-white rounded-2xl border border-slate-200 shadow-sm p-3" aria-label="📊 Fiyat Sıralaması">
<div class="text-[9px] uppercase tracking-[0.12em] font-black text-sky-600">Karşılaştırma</div>
<div class="text-[13px] font-black text-slate-900 mt-0.5 mb-2">📊 Fiyat Sıralaması</div>
<div class="side-widget-body text-xs text-slate-500">Yükleniyor...</div>
</section>
<div class="feature-ad-stack grid gap-3" aria-label="Piyasa görseli">
<div class="ad-box rounded-2xl overflow-hidden" id="bottomAd6"></div>
</div>

</aside>

</div>



</div>

<script>

function escapeHtml(value) {

    if (
        value === null ||
        value === undefined
    ) {
        return "";
    }

    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


function trendCizgisi(trend, w0, h0, sinif) {
    if (!Array.isArray(trend) || trend.length < 2) return "";

    const w = w0 || 72, h = h0 || 24, pad = 2;
    const ts = trend.map(function(p) { return p[0]; });
    const vs = trend.map(function(p) { return p[1]; });
    const t0 = Math.min.apply(null, ts), t1 = Math.max.apply(null, ts);
    const vmin = Math.min.apply(null, vs), vmax = Math.max.apply(null, vs);

    const x = function(t) { return pad + (t1 === t0 ? 0 : (t - t0) / (t1 - t0)) * (w - 2 * pad); };
    const y = function(v) { return h - pad - (vmax === vmin ? 0.5 : (v - vmin) / (vmax - vmin)) * (h - 2 * pad); };

    // Fiyat değişim anlarında sıçrayan basamaklı çizgi.
    let yol = "M" + x(ts[0]).toFixed(1) + " " + y(vs[0]).toFixed(1);
    for (let i = 1; i < trend.length; i++) {
        yol += " H" + x(ts[i]).toFixed(1) + " V" + y(vs[i]).toFixed(1);
    }

    const renk = vs[vs.length - 1] > vs[0] ? "#10b981" : (vs[vs.length - 1] < vs[0] ? "#ef4444" : "#94a3b8");
    const fark = vs[vs.length - 1] - vs[0];

    return '<svg class="' + (sinif || "trend-line") + '" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h +
        '" role="img" aria-label="Son 30 gün: ' + (fark > 0 ? '+' : '') + fark.toLocaleString("tr-TR") + ' TL">' +
        '<title>Son 30 gün: ' + (fark > 0 ? '+' : '') + fark.toLocaleString("tr-TR") + ' TL</title>' +
        '<path d="' + yol + '" fill="none" stroke="' + renk + '" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>' +
        '<circle cx="' + x(ts[ts.length - 1]).toFixed(1) + '" cy="' + y(vs[vs.length - 1]).toFixed(1) + '" r="2.2" fill="' + renk + '"/>' +
        '</svg>';
}


function durumEtiketi(durum) {

    if (durum === "current") {

        return `
        <span class="text-[10px] bg-emerald-100 text-emerald-700 px-2 py-1 rounded-lg font-bold whitespace-nowrap">
            GÜNCEL
        </span>
        `;

    }

    if (durum === "stale") {

        return `
        <span class="text-[10px] bg-amber-100 text-amber-700 px-2 py-1 rounded-lg font-bold whitespace-nowrap">
            ESKİ FİYAT
        </span>
        `;

    }

    if (durum === "manuel") {

        return `
        <span class="text-[10px] bg-indigo-100 text-indigo-700 px-2 py-1 rounded-lg font-bold whitespace-nowrap">
            MANUEL
        </span>
        `;

    }

    return "";
}


function dovizGoster(deger) {

    return Number(
        deger
    ).toLocaleString(
        "tr-TR",
        {
            minimumFractionDigits: 4,
            maximumFractionDigits: 4,
        }
    ) + " TL";

}


function lmeAcKapat() {

    const toggle =
        document.getElementById(
            "lmeToggle"
        );

    const content =
        document.getElementById(
            "lmeContent"
        );

    const icon =
        document.getElementById(
            "lmeToggleIcon"
        );

    if (!toggle || !content) {
        return;
    }

    const acik =
        !content.classList.contains(
            "hidden"
        );

    content.classList.toggle(
        "hidden",
        acik
    );

    content.setAttribute(
        "aria-hidden",
        String(acik)
    );

    toggle.setAttribute(
        "aria-expanded",
        String(!acik)
    );

    if (icon) {
        icon.style.transform =
            acik
                ? "rotate(0deg)"
                : "rotate(180deg)";
    }

}


// ---------------------------------------------------------
// LME grafik
// ---------------------------------------------------------
window.__lmeAralik = 30;
window.__lmeSecili = null;

function lmeGunYaz(gun) {
    const p = String(gun || "").split("-");
    return p.length === 3 ? p[2] + "." + p[1] : String(gun || "");
}

function lmeSayi(n) {
    return Number(n).toLocaleString("tr-TR", { maximumFractionDigits: 2 });
}

function lmeGrafikToggleBagla() {
    const dugme = document.getElementById("lmeGrafikToggle");
    if (!dugme || dugme.dataset.bound === "1") return;
    dugme.dataset.bound = "1";
    dugme.addEventListener("click", function() {
        const kutu = document.getElementById("lmeChartBox");
        if (!kutu) return;
        lmeGrafikAc(kutu.classList.contains("lme-kapali"));
    });
}

function lmeGrafikAc(ac) {
    const kutu = document.getElementById("lmeChartBox");
    const bolum = document.getElementById("lmeSplit");
    const dugme = document.getElementById("lmeGrafikToggle");
    if (!kutu || !bolum) return;
    kutu.classList.toggle("lme-kapali", !ac);
    bolum.classList.toggle("lme-acik", ac);
    if (dugme) dugme.setAttribute("aria-pressed", String(ac));
    // Açılışta gerçek genişliğe göre yeniden çiz.
    if (ac && window.__lmeSon) {
        try { lmeGrafikCiz(window.__lmeSon); } catch (hata) { console.error("LME grafik:", hata); }
    }
}

function lmeGrafikCiz(result) {
    window.__lmeSon = result;
    const pasif = result.grafik_aktif === false;
    const kutu = document.getElementById("lmeChartBox");
    const dugme = document.getElementById("lmeGrafikToggle");
    if (kutu) kutu.classList.toggle("lme-pasif", pasif);
    if (dugme) dugme.classList.toggle("lme-pasif", pasif);
    lmeGrafikToggleBagla();
    if (pasif) return;

    // Admin "açık gelsin" dediyse sayfa ilk yüklendiğinde bir kez açılır;
    // sonraki yenilemelerde ziyaretçinin kapatması bozulmaz.
    if (!window.__lmeIlkAcilisYapildi) {
        window.__lmeIlkAcilisYapildi = true;
        if (result.grafik_mod === "acik") {
            lmeGrafikAc(true);
            return;
        }
    }
    const veriler = Array.isArray(result.veriler) ? result.veriler : [];
    const gecmis = result.gecmis || {};
    const sekmeler = document.getElementById("lmeMetalTabs");
    const alan = document.getElementById("lmeChartArea");
    const barlar = document.getElementById("lmeSpreadBars");

    if (!sekmeler || !alan || !barlar || !veriler.length) return;

    const adlar = veriler.map(function(v) { return v.ad; });
    if (!window.__lmeSecili || adlar.indexOf(window.__lmeSecili) === -1) {
        window.__lmeSecili = adlar.indexOf("Bakır") !== -1 ? "Bakır" : adlar[0];
    }

    // Nakit -> 3 ay farkı çubukları (veri geçmişi olmadan da anında görünür)
    const farklar = veriler.map(function(v) {
        const f = (v.cash_ask && v.three_month_ask) ? (v.three_month_ask - v.cash_ask) / v.cash_ask * 100 : 0;
        return { ad: v.ad, f: f };
    });
    const enBuyuk = Math.max.apply(null, farklar.map(function(x) { return Math.abs(x.f); }).concat([0.01]));

    barlar.innerHTML = farklar.map(function(x) {
        const yuzde = Math.max(3, Math.round(Math.abs(x.f) / enBuyuk * 100));
        const yukari = x.f >= 0;
        return '<div><div class="flex items-center justify-between gap-2 text-[11px] font-bold lme-chart-title">' +
            "<span>" + escapeHtml(x.ad) + "</span>" +
            '<span class="' + (yukari ? "text-emerald-600" : "text-red-600") + '">' + (yukari ? "+" : "") + x.f.toFixed(2).replace(".", ",") + "%</span></div>" +
            '<div class="lme-bar-track"><div class="' + (yukari ? "lme-bar-up" : "lme-bar-down") + '" style="width:' + yuzde + '%"></div></div></div>';
    }).join("");

    // Metal sekmeleri
    sekmeler.innerHTML = adlar.map(function(ad) {
        return '<button type="button" data-metal="' + escapeHtml(ad) + '" class="lme-chip' + (ad === window.__lmeSecili ? " lme-chip-on" : "") + '">' + escapeHtml(ad) + "</button>";
    }).join("");

    sekmeler.querySelectorAll("[data-metal]").forEach(function(d) {
        d.addEventListener("click", function() {
            window.__lmeSecili = d.dataset.metal;
            lmeGrafikCiz(result);
        });
    });

    const aralikDugmeleri = document.querySelectorAll("#lmeRangeBtns [data-days]");
    aralikDugmeleri.forEach(function(d) {
        d.classList.toggle("lme-chip-on", Number(d.dataset.days) === window.__lmeAralik);
        if (d.dataset.bound !== "1") {
            d.addEventListener("click", function() {
                window.__lmeAralik = Number(d.dataset.days);
                lmeGrafikCiz(result);
            });
            d.dataset.bound = "1";
        }
    });

    // Çizgi grafik
    const seri = (gecmis[window.__lmeSecili] || []).slice(-window.__lmeAralik);

    if (seri.length < 2) {
        alan.innerHTML = '<div class="rounded-xl border border-dashed p-4 text-center text-xs font-semibold lme-chart-note" style="border-color:#f2d58a">' +
            "Fiyat çizgisi, günlük LME verisi biriktikçe oluşur (şu an " + seri.length + " gün kayıtlı). Aşağıdaki fark çubukları şimdiden geçerli." + "</div>";
        return;
    }

    const kap = alan.clientWidth || 640;
    const W = Math.max(300, Math.min(640, Math.round(kap)));
    const H = W < 450 ? 215 : 230;
    const L = W < 450 ? 46 : 52, R = 12, T = 14, B = 26;
    const nakit = seri.map(function(p) { return Number(p[1]); });
    const ucay = seri.map(function(p) { return p[2] === null || p[2] === undefined ? null : Number(p[2]); });
    const tum = nakit.concat(ucay.filter(function(v) { return v !== null; }));
    let vmin = Math.min.apply(null, tum), vmax = Math.max.apply(null, tum);
    const bosluk = (vmax - vmin) * 0.12 || vmax * 0.01;
    vmin -= bosluk; vmax += bosluk;

    const x = function(i) { return L + (seri.length === 1 ? 0 : i / (seri.length - 1)) * (W - L - R); };
    const y = function(v) { return T + (1 - (v - vmin) / (vmax - vmin)) * (H - T - B); };

    let izgara = "";
    for (let k = 0; k <= 4; k++) {
        const v = vmin + (vmax - vmin) * k / 4;
        izgara += '<line class="lme-grid-line" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(v).toFixed(1) + '" y2="' + y(v).toFixed(1) + '"/>' +
            '<text x="' + (L - 6) + '" y="' + (y(v) + 3).toFixed(1) + '" text-anchor="end">' + lmeSayi(Math.round(v)) + "</text>";
    }

    const yol = function(deg) {
        let d = "", acik = false;
        deg.forEach(function(v, i) {
            if (v === null) { acik = false; return; }
            d += (acik ? " L" : " M") + x(i).toFixed(1) + " " + y(v).toFixed(1);
            acik = true;
        });
        return d.trim();
    };

    const alanYolu = yol(nakit) + " L" + x(nakit.length - 1).toFixed(1) + " " + (H - B) + " L" + x(0).toFixed(1) + " " + (H - B) + " Z";
    const son = nakit.length - 1;

    alan.innerHTML =
        '<svg class="lme-svg w-full" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + escapeHtml(window.__lmeSecili) + ' fiyat grafiği" style="touch-action:pan-y">' +
            izgara +
            '<path class="lme-area" d="' + alanYolu + '"/>' +
            '<path class="lme-line-3m" d="' + yol(ucay) + '"/>' +
            '<path class="lme-line-cash" d="' + yol(nakit) + '"/>' +
            '<circle class="lme-dot-cash" cx="' + x(son).toFixed(1) + '" cy="' + y(nakit[son]).toFixed(1) + '" r="4"/>' +
            '<text x="' + L + '" y="' + (H - 6) + '">' + lmeGunYaz(seri[0][0]) + "</text>" +
            '<text x="' + (W - R) + '" y="' + (H - 6) + '" text-anchor="end">' + lmeGunYaz(seri[son][0]) + "</text>" +
            '<line id="lmeCursor" x1="0" x2="0" y1="' + T + '" y2="' + (H - B) + '" stroke="#d4a017" stroke-width="1" opacity="0"/>' +
        "</svg>" +
        '<div class="flex flex-wrap items-center gap-4 mt-1 text-[10px] font-bold lme-chart-note">' +
            '<span><span style="display:inline-block;width:16px;height:3px;border-radius:2px;background:#d4a017;vertical-align:middle"></span> Nakit satış</span>' +
            '<span><span style="display:inline-block;width:16px;height:0;border-top:2px dashed #92400e;vertical-align:middle"></span> 3 ay satış</span>' +
            "<span>USD / metrik ton</span></div>" +
        '<div id="lmeTooltip" class="lme-tooltip" style="display:none"></div>';

    const svg = alan.querySelector("svg");
    const ipucu = document.getElementById("lmeTooltip");
    const imlec = document.getElementById("lmeCursor");

    function goster(olay) {
        const kutu = svg.getBoundingClientRect();
        const oran = (olay.clientX - kutu.left) / kutu.width;
        const px = oran * W;
        let i = Math.round((px - L) / (W - L - R) * (seri.length - 1));
        i = Math.max(0, Math.min(seri.length - 1, i));

        imlec.setAttribute("x1", x(i)); imlec.setAttribute("x2", x(i)); imlec.setAttribute("opacity", "0.6");
        ipucu.style.display = "block";
        ipucu.innerHTML = "<div>" + lmeGunYaz(seri[i][0]) + "</div><div>Nakit: " + lmeSayi(nakit[i]) + "</div>" +
            (ucay[i] !== null ? "<div>3 Ay: " + lmeSayi(ucay[i]) + "</div>" : "");
        const sol = Math.min(Math.max(x(i) / W * kutu.width - 40, 0), kutu.width - 110);
        ipucu.style.left = sol + "px";
        ipucu.style.top = "0px";
    }

    function gizle() {
        ipucu.style.display = "none";
        imlec.setAttribute("opacity", "0");
    }

    svg.addEventListener("pointermove", goster);
    svg.addEventListener("pointerdown", goster);
    svg.addEventListener("pointerleave", gizle);
}


async function lmeFiyatlariniGetir() {

    const lmeToggle =
        document.getElementById(
            "lmeToggle"
        );

    const lmeContent =
        document.getElementById(
            "lmeContent"
        );

    if (lmeContent && lmeToggle) {
        lmeContent.classList.remove(
            "hidden"
        );
        lmeContent.setAttribute(
            "aria-hidden",
            "false"
        );
        lmeToggle.setAttribute(
            "aria-expanded",
            "true"
        );
    }

    if (
        lmeToggle
        && lmeToggle.dataset.bound !== "1"
    ) {
        lmeToggle.addEventListener(
            "click",
            lmeAcKapat
        );
        lmeToggle.dataset.bound = "1";
    }

    const tableBody =
        document.getElementById(
            "lmeTableBody"
        );

    const info =
        document.getElementById(
            "lmeInfo"
        );

    try {

        const response =
            await fetch(
                "/lme",
                {
                    cache: "no-store"
                }
            );

        if (!response.ok) {

            throw new Error(
                "LME servisi çalışmadı."
            );
        }

        const result =
            await response.json();

        if (
            result.status !== "success"
            || !result.veriler
            || !result.veriler.length
        ) {

            throw new Error(
                result.message
                || "LME verisi alınamadı."
            );
        }

        const usdYaz =
            function(value) {
                if (
                    value === null
                    || value === undefined
                ) {
                    return "-";
                }

                return "$" + Number(
                    value
                ).toLocaleString(
                    "tr-TR",
                    {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2
                    }
                );
            };

        const formatFiyat =
            function(value) {

                if (
                    value === null
                    || value === undefined
                ) {
                    return "-";
                }

                return Number(
                    value
                ).toLocaleString(
                    "tr-TR",
                    {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2
                    }
                );
            };

        try { lmeGrafikCiz(result); } catch (hata) { console.error("LME grafik:", hata); }

        tableBody.innerHTML = "";

        const ticker =
            document.getElementById(
                "lmeTickerContent"
            );

        if (ticker) {
            const tickerItems =
                result.veriler.map(
                    function(item) {
                        const bid = item.three_month_bid;
                        const ask = item.three_month_ask;
                        const usdOrta =
                            bid !== null && bid !== undefined
                            && ask !== null && ask !== undefined
                                ? (Number(bid) + Number(ask)) / 2
                                : (ask !== null && ask !== undefined
                                    ? ask
                                    : bid);
                        const deger = usdYaz(usdOrta);

                        return (
                            '<span class="lme-ticker-item">' +
                                '<span class="lme-ticker-metal">' +
                                    escapeHtml(
                                        item.ad || "-"
                                    ) +
                                "</span>" +
                                '<span class="lme-ticker-value">' +
                                    escapeHtml(deger) +
                                "</span>" +
                            "</span>"
                        );
                    }
                ).join(
                    '<span class="lme-ticker-separator">◆</span>'
                );

            ticker.innerHTML =
                tickerItems +
                '<span class="lme-ticker-separator">◆</span>' +
                tickerItems;
        }

        result.veriler.forEach(
            function(item, index) {

                const row =
                    document.createElement(
                        "tr"
                    );

                row.className =
                    "border-b border-slate-100 last:border-0 hover:bg-slate-50 transition";

                row.innerHTML =
                    '<td class="px-3 sm:px-4 py-3 font-black text-slate-900 whitespace-nowrap">' +
                        escapeHtml(
                            item.ad
                            || "-"
                        ) +
                    "</td>" +

                    '<td class="px-3 sm:px-4 py-3 text-right font-semibold text-slate-700 whitespace-nowrap">' +
                        usdYaz(
                            item.cash_bid
                        ) +
                    "</td>" +

                    '<td class="px-3 sm:px-4 py-3 text-right font-semibold text-slate-700 whitespace-nowrap">' +
                        usdYaz(
                            item.cash_ask
                        ) +
                    "</td>" +

                    '<td class="px-3 sm:px-4 py-3 text-right font-semibold text-slate-700 whitespace-nowrap">' +
                        usdYaz(
                            item.three_month_bid
                        ) +
                    "</td>" +

                    '<td class="px-3 sm:px-4 py-3 text-right font-semibold text-slate-700 whitespace-nowrap">' +
                        usdYaz(
                            item.three_month_ask
                        ) +
                    "</td>" +

                    '<td class="px-3 sm:px-4 py-3 text-right whitespace-nowrap">' +
                        '<span class="inline-flex items-center justify-end rounded-lg bg-slate-900 text-white px-2.5 py-1.5 font-black">' +
                            (
                                item.three_month_tl !== null
                                && item.three_month_tl !== undefined
                                    ? formatFiyat(
                                        item.three_month_tl
                                      ) + " TL"
                                    : "-"
                            ) +
                        "</span>" +
                    "</td>";

                tableBody.appendChild(
                    row
                );
            }
        );
        info.textContent =
            "Kaynak: "
            + (
                result.kaynak
                || "LME Official Prices"
            )
            + " · Veri tarihi: "
            + (
                result.tarih
                || "-"
            )
            + " · Gün gecikmeli"
            + (
                result.usd_tl
                    ? " · USD/TRY alış: "
                    + Number(
                        result.usd_tl                      ).toLocaleString(
                        "tr-TR",
                        {
                            minimumFractionDigits: 4,
                            maximumFractionDigits: 4
                        }
                      )
                    : ""
            );

    }
    catch (error) {

        tableBody.innerHTML =
            '<tr>' +
                '<td colspan="6" class="px-4 py-6 text-center bg-amber-50">' +
                    '<div class="text-sm font-black text-amber-900">LME verisi şu anda alınamıyor</div>' +
                    '<div class="text-xs font-semibold text-amber-700 mt-1">Kaynak geçici olarak yanıt vermiyor; kısa süre içinde otomatik yeniden denenecek.</div>' +
                "</td>" +
            "</tr>";

        info.textContent =
            "LME verisi alınamadı.";

        console.error(
            "LME:",
            error
        );

        // Hata durumunda 15 dakika beklemeden 1 dakika sonra yeniden dene.
        if (!window.__lmeYenidenDeneme) {
            window.__lmeYenidenDeneme = setTimeout(function() {
                window.__lmeYenidenDeneme = null;
                lmeFiyatlariniGetir();
            }, 60000);
        }
    }
}


async function dovizleriGetir() {

    try {

        const response =
            await fetch(
                "/currency",
                {
                    cache: "no-store"
                }
            );

        if (!response.ok) {
            throw new Error(
                "Döviz servisi çalışmadı."
            );
        }

        const result =
            await response.json();

        const usd =
            result.veriler &&
            result.veriler.USD;

        const eur =
            result.veriler &&
            result.veriler.EUR;

        const altin =
            result.veriler &&
            result.veriler.ALTIN;

        if (
            result.status !== "success"
            || !usd
            || !eur
            || !altin
        ) {
            throw new Error(
                "Döviz verisi alınamadı."
            );
        }

        document.getElementById("usdAlis").textContent =
            dovizGoster(usd.alis);

        document.getElementById("usdSatis").textContent =
            dovizGoster(usd.satis);

        document.getElementById("eurAlis").textContent =
            dovizGoster(eur.alis);

        document.getElementById("eurSatis").textContent =
            dovizGoster(eur.satis);

        document.getElementById("altinAlis").textContent =
            dovizGoster(altin.alis);

        document.getElementById("altinSatis").textContent =
            dovizGoster(altin.satis);

        document.getElementById("currencyInfo").textContent =
            "Kur kaynağı: " + (result.kaynak || "Frankfurter") + " · "
            + (result.tarih || "-");

        const kaynakEtiketi = document.getElementById("currencyKaynak");
        if (kaynakEtiketi) {
            kaynakEtiketi.textContent = result.kaynak || "Frankfurter";
        }

    }
    catch (error) {

        document.getElementById("usdAlis").textContent = "-";
        document.getElementById("usdSatis").textContent = "-";
        document.getElementById("eurAlis").textContent = "-";
        document.getElementById("eurSatis").textContent = "-";
        document.getElementById("altinAlis").textContent = "-";
        document.getElementById("altinSatis").textContent = "-";

        document.getElementById("currencyInfo").textContent =
            "Kur bilgisi şu anda alınamıyor.";

        console.error(
            "Döviz kurları:",
            error
        );

        if (!window.__dovizYenidenDeneme) {
            window.__dovizYenidenDeneme = setTimeout(function() {
                window.__dovizYenidenDeneme = null;
                dovizleriGetir();
            }, 60000);
        }

    }

}


async function manuelFiyatSil(button) {

    const url =
        button.dataset.deleteUrl;

    const kalem =
        button.dataset.kalem;

    if (!url || !kalem) {
        return;
    }

    if (
        !confirm(
            kalem
            + " için manuel fiyat kaldırılacak. Devam edilsin mi?"
        )
    ) {
        return;
    }

    const eskiMetin =
        button.textContent;

    button.disabled = true;
    button.textContent =
        "Kaldırılıyor...";

    try {

        const formData =
            new FormData();

        formData.append(
            "manuel_sil",
            kalem
        );

        const response =
            await fetch(
                url,
                {
                    method: "POST",
                    body: formData,
                    credentials: "same-origin",
                    cache: "no-store",
                }
            );

        if (!response.ok) {
            throw new Error(
                "Manuel fiyat silme isteği başarısız."
            );
        }

        window.location.reload();

    }
    catch (error) {

        button.disabled = false;
        button.textContent =
            eskiMetin;

        alert(
            "Manuel fiyat kaldırılamadı. Lütfen tekrar deneyin."
        );

        console.error(
            "Manuel fiyat silme:",
            error
        );

    }

}


// ---------------------------------------------------------
// Paylaş / indir araçları
// ---------------------------------------------------------
window.__hurdaVeri = { data: [], son_guncelleme: "" };

function hurdaFiyatMetni(firma) {
    const satirlar = (firma.kalemler || []).map(function(k) {
        const fark = k.degisim ? " (" + k.degisim + ")" : "";
        return "• " + k.cins + ": " + k.fiyat + fark;
    });
    return "*" + firma.baslik + "*" +
        (firma.tarih && firma.tarih !== "-" ? " (" + firma.tarih + ")" : "") +
        "\\n" + satirlar.join("\\n");
}

function hurdaPaylas(metin) {
    const adres = window.location.origin;
    const tam = metin + "\\n\\n" + adres;

    if (navigator.share && /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent)) {
        navigator.share({ title: "Güncel Hurda Fiyatları", text: tam }).catch(function() {});
        return;
    }

    window.open("https://wa.me/?text=" + encodeURIComponent(tam), "_blank", "noopener");
}

function hurdaTumMetin() {
    const v = window.__hurdaVeri;
    return "*Güncel Hurda Fiyatları*" +
        (v.son_guncelleme ? " — " + v.son_guncelleme : "") + "\\n\\n" +
        (v.data || []).map(hurdaFiyatMetni).join("\\n\\n");
}

function hurdaCsvIndir() {
    const v = window.__hurdaVeri;
    const satir = function(a) {
        return a.map(function(x) {
            return '"' + String(x === null || x === undefined ? "" : x).replace(/"/g, '""') + '"';
        }).join(";");
    };
    const rows = [satir(["Firma", "Kalem", "Fiyat (TL/ton)", "Değişim", "Dünkü fiyat", "Fiyat tarihi"])];

    (v.data || []).forEach(function(f) {
        (f.kalemler || []).forEach(function(k) {
            const sayi = k.manuel_fiyat !== null && k.manuel_fiyat !== undefined
                ? k.manuel_fiyat : k.otomatik_fiyat;
            rows.push(satir([f.baslik, k.cins, sayi, k.degisim || "", k.dun_fiyat, k.fiyat_tarihi || f.tarih || ""]));
        });
    });

    const blob = new Blob(["\\ufeff" + rows.join("\\r\\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "hurda-fiyatlari-" + new Date().toISOString().slice(0, 10) + ".csv";
    document.body.appendChild(a);
    a.click();
    setTimeout(function() { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

function hurdaPdfYazdir() {
    const v = window.__hurdaVeri;
    const esc = function(x) {
        return String(x === null || x === undefined ? "" : x)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    };

    const bolumler = (v.data || []).map(function(f) {
        const rows = (f.kalemler || []).map(function(k) {
            return "<tr><td>" + esc(k.cins) + "</td><td class='r'>" + esc(k.fiyat) +
                "</td><td class='r'>" + esc(k.degisim || "") + "</td></tr>";
        }).join("");
        return "<h2>" + esc(f.baslik) + " <small>" + esc(f.tarih || "") + "</small></h2>" +
            "<table><tbody>" + rows + "</tbody></table>";
    }).join("");

    const iframe = document.createElement("iframe");
    iframe.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
    document.body.appendChild(iframe);
    const doc = iframe.contentWindow.document;
    doc.open();
    doc.write("<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>Güncel Hurda Fiyatları</title>" +
        "<style>body{font-family:Arial,sans-serif;margin:24px;color:#0f172a}h1{font-size:20px;margin:0 0 4px}" +
        "p{margin:0 0 16px;color:#475569;font-size:12px}h2{font-size:14px;margin:16px 0 6px;border-bottom:2px solid #0f172a;padding-bottom:3px}" +
        "h2 small{color:#64748b;font-weight:400;font-size:11px}table{width:100%;border-collapse:collapse;font-size:12px}" +
        "td{padding:4px 6px;border-bottom:1px solid #e2e8f0}.r{text-align:right;white-space:nowrap}" +
        "h2,table{break-inside:avoid}</style></head><body><h1>Güncel Hurda Fiyatları</h1><p>" +
        esc(v.son_guncelleme || "") + " · " + esc(window.location.origin) + "</p>" + bolumler + "</body></html>");
    doc.close();

    setTimeout(function() {
        iframe.contentWindow.focus();
        iframe.contentWindow.print();
        setTimeout(function() { iframe.remove(); }, 2000);
    }, 300);
}

// ---------------------------------------------------------
// Arka plan fiyat alarmı bildirimleri (Web Push)
// ---------------------------------------------------------
function hurdaPushDesteklenir() {
    return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

function hurdaAnahtarDizisi(b64) {
    const pad = "=".repeat((4 - b64.length % 4) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, function(c) { return c.charCodeAt(0); });
}

async function hurdaPushKayit() {
    if (!hurdaPushDesteklenir()) return null;
    try {
        const reg = await Promise.race([
            navigator.serviceWorker.ready,
            new Promise(function(_, red) { setTimeout(function() { red(new Error("sw")); }, 4000); }),
        ]);
        return reg;
    } catch (e) {
        return null;
    }
}

async function hurdaPushSenkron() {
    try {
        const reg = await hurdaPushKayit();
        if (!reg) return;
        const sub = await reg.pushManager.getSubscription();
        if (!sub) return;
        const alarms = JSON.parse(localStorage.getItem("hurdaPriceAlarms") || "[]");
        await fetch("/push/subscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                subscription: sub.toJSON(),
                alarms: alarms,
                daily: localStorage.getItem("hurdaGunlukOzet") === "1",
            }),
        });
    } catch (e) {
        console.error("Push senkron hatası", e);
    }
}

async function hurdaPushDurumuYaz() {
    const dugme = document.getElementById("pushToggle");
    const durum = document.getElementById("pushStatus");
    if (!dugme || !durum) return;

    if (!hurdaPushDesteklenir()) {
        dugme.classList.add("hidden");
        durum.textContent = "Bu tarayıcı arka plan bildirimini desteklemiyor (iPhone'da siteyi ana ekrana ekleyin).";
        return;
    }

    if (Notification.permission === "denied") {
        dugme.classList.add("hidden");
        durum.textContent = "Bildirim izni engellenmiş. Tarayıcı site ayarlarından izin verin.";
        return;
    }

    const reg = await hurdaPushKayit();
    const sub = reg ? await reg.pushManager.getSubscription() : null;

    localStorage.setItem("hurdaPushAktif", sub ? "1" : "0");

    const ozetSatiri = document.getElementById("dailyDigestRow");
    const ozetKutusu = document.getElementById("dailyDigest");
    if (ozetSatiri && ozetKutusu) {
        ozetSatiri.classList.toggle("hidden", !sub);
        ozetKutusu.checked = localStorage.getItem("hurdaGunlukOzet") === "1";
    }

    dugme.textContent = sub ? "🔕 Bildirimleri kapat" : "🔔 Bildirimleri aç";
    durum.textContent = sub
        ? "Açık: site kapalıyken de alarm bildirimi gelir."
        : "Alarmların site kapalıyken de bildirim göndermesi için açın.";
}

async function hurdaPushDegistir() {
    const durum = document.getElementById("pushStatus");
    try {
        const reg = await hurdaPushKayit();
        if (!reg) throw new Error("Servis çalışanı hazır değil.");
        const mevcut = await reg.pushManager.getSubscription();

        if (mevcut) {
            await fetch("/push/unsubscribe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ endpoint: mevcut.endpoint }),
            });
            await mevcut.unsubscribe();
        } else {
            const izin = await Notification.requestPermission();
            if (izin !== "granted") throw new Error("Bildirim izni verilmedi.");
            const anahtar = (await (await fetch("/push/public-key")).json()).key;
            await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: hurdaAnahtarDizisi(anahtar),
            });
            await hurdaPushSenkron();
        }
    } catch (e) {
        if (durum) durum.textContent = "Bildirim açılamadı: " + (e && e.message ? e.message : e);
        return;
    }
    hurdaPushDurumuYaz();
}

// ---------------------------------------------------------
// Yan sütun kutucukları
// ---------------------------------------------------------
function hurdaKatla(m) {
    return String(m || "")
        .replace(/İ/g, "i").replace(/I/g, "ı").toLowerCase()
        .replace(/ş/g, "s").replace(/ç/g, "c").replace(/ğ/g, "g")
        .replace(/ö/g, "o").replace(/ü/g, "u").replace(/ı/g, "i")
        .replace(/[^a-z0-9]/g, "");
}

function hurdaKalemFiyati(k) {
    const v = (k.manuel_fiyat !== null && k.manuel_fiyat !== undefined) ? k.manuel_fiyat : k.otomatik_fiyat;
    return Number(v);
}

function hurdaTl(n) {
    return Number(n).toLocaleString("tr-TR") + " TL";
}

function hurdaTarihSirasi(metin) {
    const m = String(metin || "").match(/(\\d{2})\\.(\\d{2})\\.(\\d{4})(?:\\s+(\\d{2}):(\\d{2}))?/);
    if (!m) return 0;
    return new Date(+m[3], +m[2] - 1, +m[1], +(m[4] || 0), +(m[5] || 0)).getTime();
}

function hurdaGruplari(firmalar) {
    const gruplar = {};

    firmalar.forEach(function(f) {
        (f.kalemler || []).forEach(function(k) {
            const fiyat = hurdaKalemFiyati(k);
            const anahtar = hurdaKatla(k.cins);
            if (!anahtar || !Number.isFinite(fiyat)) return;

            if (!gruplar[anahtar]) gruplar[anahtar] = { ad: k.cins, uyeler: [] };
            gruplar[anahtar].uyeler.push({ firma: f.baslik, fiyat: fiyat, yas: f.fiyat_yasi_gun });
        });
    });

    return Object.keys(gruplar)
        .map(function(a) { return Object.assign({ anahtar: a }, gruplar[a]); })
        .filter(function(g) { return new Set(g.uyeler.map(function(u) { return u.firma; })).size >= 2; });
}

function hurdaKutuYaz(id, html) {
    const el = document.querySelector("#" + id + " .side-widget-body");
    if (el) el.innerHTML = html;
}

function hurdaBos(metin) {
    return '<div class="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-3 text-center text-[11px] text-slate-500">' + escapeHtml(metin) + "</div>";
}

function hurdaYasMetni(gun) {
    if (gun === null || gun === undefined) return "";
    if (gun <= 0) return "bugün";
    if (gun === 1) return "dün";
    return gun + " gün önce";
}

function yanKutulariCiz(firmalar) {
    // 1) Cins bazında en yüksek fiyatlar
    const gruplar = hurdaGruplari(firmalar)
        .sort(function(a, b) { return b.uyeler.length - a.uyeler.length; })
        .slice(0, 5);

    hurdaKutuYaz("widgetBest", gruplar.length
        ? gruplar.map(function(g) {
            const en = g.uyeler.slice().sort(function(a, b) { return b.fiyat - a.fiyat; })[0];
            const eski = en.yas !== null && en.yas !== undefined && en.yas > 3;
            return '<div class="flex items-center justify-between gap-2 py-1.5 border-b border-slate-100 last:border-0">' +
                '<div class="min-w-0"><div class="text-[12px] font-black text-slate-800 truncate">' + escapeHtml(g.ad) + "</div>" +
                '<div class="text-[10px] font-semibold text-slate-500 truncate">' + escapeHtml(en.firma) + (eski ? " · ⚠ eski" : "") + "</div></div>" +
                '<div class="text-[12px] font-black text-slate-900 whitespace-nowrap">' + hurdaTl(en.fiyat) + "</div></div>";
        }).join("")
        : hurdaBos("Karşılaştırılacak ortak cins bulunamadı."));

    // 2) Alarmlarım
    let alarmlar = [];
    try { alarmlar = JSON.parse(localStorage.getItem("hurdaPriceAlarms") || "[]"); } catch (e) { alarmlar = []; }
    const pushAcik = localStorage.getItem("hurdaPushAktif") === "1";

    hurdaKutuYaz("widgetAlarms",
        (alarmlar.length
            ? alarmlar.slice(0, 4).map(function(a) {
                const f = firmalar.find(function(x) { return x.firma_id === a.firma_id; });
                return '<div class="py-1.5 border-b border-slate-100 last:border-0">' +
                    '<div class="text-[12px] font-black text-slate-800 truncate">' + escapeHtml(f ? f.baslik : a.firma_id) + " · " + escapeHtml(a.kalem) + "</div>" +
                    '<div class="text-[10px] font-semibold ' + (a.fired ? "text-emerald-600" : "text-slate-500") + '">' +
                    (a.direction === "above" ? "≥ " : "≤ ") + hurdaTl(a.value) + (a.fired ? " · tetiklendi" : " · aktif") + "</div></div>";
            }).join("") + (alarmlar.length > 4 ? '<div class="text-[10px] text-slate-400 pt-1">+' + (alarmlar.length - 4) + " alarm daha</div>" : "")
            : hurdaBos("Henüz alarm yok. Hedef fiyata ulaşınca haber verelim.")) +
        '<div class="flex items-center justify-between gap-2 mt-2">' +
            '<button type="button" id="widgetAlarmAdd" class="h-8 px-3 rounded-lg bg-amber-500 hover:bg-amber-600 text-white text-[11px] font-black">＋ Alarm ekle</button>' +
            '<span class="text-[10px] font-semibold ' + (pushAcik ? "text-emerald-600" : "text-slate-400") + '">' + (pushAcik ? "🔔 Bildirim açık" : "🔕 Bildirim kapalı") + "</span></div>");

    const ekle = document.getElementById("widgetAlarmAdd");
    if (ekle) ekle.addEventListener("click", function() {
        const bolum = document.getElementById("marketTools");
        if (bolum) bolum.scrollIntoView({ behavior: "smooth", block: "start" });
        const panel = document.getElementById("alarmPanel");
        const dugme = document.getElementById("alarmButton");
        if (panel && dugme && panel.classList.contains("hidden")) dugme.click();
    });

    // 3) Kaynaklar ve veri durumu (bağlantı + fiyatın yaşı)
    hurdaKutuYaz("widgetSources", firmalar.map(function(f) {
        const yas = f.fiyat_yasi_gun;
        const renk = yas === null || yas === undefined ? "bg-slate-300" : (yas <= 1 ? "bg-emerald-500" : (yas <= 3 ? "bg-amber-400" : "bg-red-500"));
        const ad = escapeHtml(f.baslik);
        const sol = '<span class="flex items-center gap-2 min-w-0"><span class="inline-block w-2 h-2 rounded-full shrink-0 ' + renk + '"></span>' +
            '<span class="text-[12px] font-bold text-slate-700 truncate">' + ad + (f.url ? ' <span class="text-slate-400">↗</span>' : "") + "</span></span>";
        const sag = '<span class="text-[10px] font-semibold text-slate-500 whitespace-nowrap">' + escapeHtml(hurdaYasMetni(yas) || "-") + "</span>";
        const kok = "flex items-center justify-between gap-2 py-1.5 border-b border-slate-100 last:border-0";

        return f.url
            ? '<a href="' + escapeHtml(f.url) + '" target="_blank" rel="noopener noreferrer" class="' + kok + ' hover:opacity-80">' + sol + sag + "</a>"
            : '<div class="' + kok + '">' + sol + sag + "</div>";
    }).join("") + '<div class="text-[10px] text-slate-400 mt-2">Tarih, fiyatın kaynakta yayınlandığı güne göredir. Kaynağa gitmek için satıra tıklayın.</div>');

    // 4) Son değişimler
    const degisimler = [];
    firmalar.forEach(function(f) {
        (f.kalemler || []).forEach(function(k) {
            const fark = String(k.degisim || k.onceki_degisim || "").trim();
            if (fark.startsWith("+") || fark.startsWith("-")) {
                degisimler.push({ firma: f.baslik, kalem: k.cins, fark: fark, tarih: k.degisim_tarihi || "", sira: hurdaTarihSirasi(k.degisim_tarihi) });
            }
        });
    });
    degisimler.sort(function(a, b) { return b.sira - a.sira; });

    hurdaKutuYaz("widgetChanges", degisimler.length
        ? degisimler.slice(0, 6).map(function(d) {
            const yuk = d.fark.startsWith("+");
            return '<div class="py-1.5 border-b border-slate-100 last:border-0">' +
                '<div class="flex items-center justify-between gap-2"><span class="text-[12px] font-black text-slate-800 truncate">' + escapeHtml(d.kalem) + "</span>" +
                '<span class="text-[11px] font-black whitespace-nowrap ' + (yuk ? "text-emerald-600" : "text-red-600") + '">' + (yuk ? "▲ " : "▼ ") + escapeHtml(d.fark) + "</span></div>" +
                '<div class="text-[10px] font-semibold text-slate-500 truncate">' + escapeHtml(d.firma) + (d.tarih ? " · " + escapeHtml(d.tarih) : "") + "</div></div>";
        }).join("")
        : hurdaBos("Fiyatlar değiştikçe burada listelenir."));

    // 5) Fiyat sıralaması (seçilen cins)
    const secenekler = hurdaGruplari(firmalar).sort(function(a, b) { return b.uyeler.length - a.uyeler.length; });
    if (secenekler.length) {
        let secili = secenekler.find(function(g) { return g.anahtar === window.__siralamaSecim; })
            || secenekler.find(function(g) { return g.anahtar === "dkp"; })
            || secenekler[0];

        const sirali = secili.uyeler.slice().sort(function(a, b) { return b.fiyat - a.fiyat; });
        const enYuksek = sirali[0].fiyat, enDusuk = sirali[sirali.length - 1].fiyat;
        const alt = enDusuk - Math.max(enYuksek - enDusuk, enYuksek * 0.01);

        hurdaKutuYaz("widgetRanking",
            '<select id="rankingSelect" class="w-full h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs font-bold text-slate-700 mb-2">' +
                secenekler.map(function(g) {
                    return '<option value="' + escapeHtml(g.anahtar) + '"' + (g.anahtar === secili.anahtar ? " selected" : "") + ">" + escapeHtml(g.ad) + "</option>";
                }).join("") + "</select>" +
            sirali.map(function(u, i) {
                const yuzde = Math.max(8, Math.round((u.fiyat - alt) / (enYuksek - alt) * 100));
                return '<div class="mb-1.5"><div class="flex items-center justify-between gap-2 text-[10px] font-bold">' +
                    '<span class="truncate text-slate-600">' + escapeHtml(u.firma) + "</span>" +
                    '<span class="whitespace-nowrap ' + (i === 0 ? "text-emerald-600" : "text-slate-700") + '">' + hurdaTl(u.fiyat) + "</span></div>" +
                    '<div class="h-1.5 rounded-full bg-slate-100 overflow-hidden"><div class="h-full rounded-full ' + (i === 0 ? "bg-emerald-500" : "bg-sky-400") + '" style="width:' + yuzde + '%"></div></div></div>';
            }).join(""));

        const secim = document.getElementById("rankingSelect");
        if (secim) secim.addEventListener("change", function() {
            window.__siralamaSecim = secim.value;
            yanKutulariCiz(firmalar);
        });
    } else {
        hurdaKutuYaz("widgetRanking", hurdaBos("Karşılaştırılacak ortak cins bulunamadı."));
    }

}


function marketToolsInit(result) {

    const firmalar = Array.isArray(result.data)
        ? result.data
        : [];

    window.__hurdaVeri = {
        data: firmalar,
        son_guncelleme: result.son_guncelleme || "",
    };

    try { yanKutulariCiz(firmalar); } catch (hata) { console.error("Yan kutular:", hata); }

    const ozetKutusu = document.getElementById("dailyDigest");
    if (ozetKutusu && ozetKutusu.dataset.bound !== "1") {
        ozetKutusu.addEventListener("change", function() {
            localStorage.setItem("hurdaGunlukOzet", ozetKutusu.checked ? "1" : "0");
            hurdaPushSenkron();
        });
        ozetKutusu.dataset.bound = "1";
    }

    const pushBtn = document.getElementById("pushToggle");
    if (pushBtn && pushBtn.dataset.bound !== "1") {
        pushBtn.addEventListener("click", hurdaPushDegistir);
        pushBtn.dataset.bound = "1";
        hurdaPushDurumuYaz();
    }

    const waBtn = document.getElementById("shareWhatsapp");
    if (waBtn && waBtn.dataset.bound !== "1") {
        waBtn.addEventListener("click", function() { hurdaPaylas(hurdaTumMetin()); });
        waBtn.dataset.bound = "1";
    }

    const csvBtn = document.getElementById("exportCsv");
    if (csvBtn && csvBtn.dataset.bound !== "1") {
        csvBtn.addEventListener("click", hurdaCsvIndir);
        csvBtn.dataset.bound = "1";
    }

    const pdfBtn = document.getElementById("exportPdf");
    if (pdfBtn && pdfBtn.dataset.bound !== "1") {
        pdfBtn.addEventListener("click", hurdaPdfYazdir);
        pdfBtn.dataset.bound = "1";
    }

    // Piyasa özeti mevcut /prices verisinden hesaplanır.
    // Yeni veri kaynağı veya backend değişikliği gerektirmez.
    const summaryFirmCount = document.getElementById("summaryFirmCount");
    const summaryItemCount = document.getElementById("summaryItemCount");
    const summaryUpCount = document.getElementById("summaryUpCount");
    const summaryDownCount = document.getElementById("summaryDownCount");
    const summaryLastUpdate = document.getElementById("summaryLastUpdate");

    let itemCount = 0;
    let upCount = 0;
    let downCount = 0;
    let updated24Count = 0;
    const factoryChangesByFirm = {};
    const allChangesByFirm = {};
    let allUpCount = 0;
    let allDownCount = 0;

    firmalar.forEach(function(firma) {
        if (firma.son_24_saatte_guncellendi) {
            updated24Count++;
        }

        const kalemler = Array.isArray(firma.kalemler)
            ? firma.kalemler
            : [];

        itemCount += kalemler.length;

        const firmaDegisimleri = [];

        kalemler.forEach(function(kalem) {
            const degisim = String(
                kalem.degisim || ""
            ).trim();

            if (degisim.startsWith("+")) {
                upCount++;
                firmaDegisimleri.push({
                    firma_id: firma.firma_id,
                    firma: firma.baslik,
                    kalem: kalem.cins,
                    fark: degisim,
                });
            }
            else if (degisim.startsWith("-")) {
                downCount++;
                firmaDegisimleri.push({
                    firma_id: firma.firma_id,
                    firma: firma.baslik,
                    kalem: kalem.cins,
                    fark: degisim,
                });
            }
        });

        if (firmaDegisimleri.length) {
            factoryChangesByFirm[
                String(firma.firma_id || "").trim().toLowerCase()
            ] = firmaDegisimleri;
        }

        // Kayıtlı eski fiyatlarla karşılaştırma (süreden bağımsız).
        const tumDegisimler = [];
        kalemler.forEach(function(kalem) {
            const fark = String(
                kalem.degisim || kalem.onceki_degisim || ""
            ).trim();

            if (!fark.startsWith("+") && !fark.startsWith("-")) return;

            tumDegisimler.push({
                kalem: kalem.cins,
                fark: fark,
                yukselis: fark.startsWith("+"),
                son24: Boolean(kalem.degisim),
                tarih: kalem.degisim_tarihi || "",
            });
        });

        allChangesByFirm[
            String(firma.firma_id || "").trim().toLowerCase()
        ] = tumDegisimler;
        allUpCount += tumDegisimler.filter(function(c) { return c.yukselis; }).length;
        allDownCount += tumDegisimler.filter(function(c) { return !c.yukselis; }).length;
    });

    if (summaryFirmCount) summaryFirmCount.textContent = firmalar.length;
    if (summaryItemCount) summaryItemCount.textContent = itemCount;
    if (summaryUpCount) summaryUpCount.textContent = upCount;
    if (summaryDownCount) summaryDownCount.textContent = downCount;
    if (summaryLastUpdate) summaryLastUpdate.textContent = result.son_guncelleme || "-";

    const todayUpdates = document.getElementById("todayUpdates");
    if (todayUpdates) {
        const sirali = firmalar.slice().sort(function(a, b) {
            const sayi = function(f) {
                return (allChangesByFirm[String(f.firma_id || "").trim().toLowerCase()] || []).length;
            };
            return sayi(b) - sayi(a);
        });

        const degisenFirmalar = sirali.filter(function(f) {
            return (allChangesByFirm[String(f.firma_id || "").trim().toLowerCase()] || []).length > 0;
        });
        const degismeyenFirmalar = sirali.filter(function(f) {
            return degisenFirmalar.indexOf(f) === -1;
        });

        const kartlar = degisenFirmalar.map(function(firma, index) {
            const firmaId = String(firma.firma_id || "").trim().toLowerCase();
            const degisimler = allChangesByFirm[firmaId] || [];
            const yukselen = degisimler.filter(function(c) { return c.yukselis; }).length;
            const dusen = degisimler.length - yukselen;
            const panelId = "today-update-" + firmaId.replace(/[^a-zA-Z0-9_-]/g, "") + "-" + index;

            const rozetler = degisimler.length
                ? (yukselen ? '<span class="fc-chip fc-chip-up">▲ ' + yukselen + '</span>' : "") +
                  (dusen ? '<span class="fc-chip fc-chip-down">▼ ' + dusen + '</span>' : "")
                : '<span class="text-[10px] font-bold text-slate-400">Değişim yok</span>';

            const liste = degisimler.length
                ? degisimler.map(function(c) {
                    return '<span class="inline-flex items-center gap-1 rounded-lg ' +
                        (c.yukselis
                            ? 'bg-emerald-50 border border-emerald-200 text-emerald-700'
                            : 'bg-red-50 border border-red-200 text-red-700') +
                        ' px-2 py-1 text-[9px] sm:text-[10px] font-black whitespace-nowrap">' +
                        (c.yukselis ? '▲ ' : '▼ ') +
                        escapeHtml(c.kalem || "") + ' ' + escapeHtml(c.fark) +
                        (c.son24 ? ' · 24s' : (c.tarih ? ' · ' + escapeHtml(c.tarih) : '')) +
                        '</span>';
                  }).join("")
                : '<span class="text-[11px] font-semibold text-slate-500">Kayıtlı eski fiyata göre değişen kalem yok.</span>';

            return '<div class="today-update-card rounded-xl border shadow-sm overflow-hidden ' +
                (degisimler.length ? 'bg-white border-slate-200' : 'bg-white/60 border-slate-200/70 opacity-80') + '">' +
                '<button type="button" class="today-update-toggle w-full min-h-11 px-3 py-2.5 hover:bg-slate-50 transition flex items-center justify-between gap-2 text-left" aria-expanded="false">' +
                    '<span class="text-[11px] sm:text-[12px] font-black text-slate-800 leading-5">' +
                        escapeHtml(firma.baslik || firma.firma_id || "-") +
                    '</span>' +
                    '<span class="flex items-center gap-1.5 shrink-0">' + rozetler + '</span>' +
                '</button>' +
                '<div id="' + panelId + '" class="today-update-panel hidden border-t border-slate-100 px-3 py-2.5">' +
                    '<div class="flex flex-wrap items-center gap-1.5">' + liste + '</div>' +
                '</div>' +
            '</div>';
        }).join("");

        todayUpdates.innerHTML =
            '<div class="rounded-2xl border border-sky-200 bg-sky-50/80 p-3 sm:p-4">' +
                '<div class="flex items-center justify-between gap-2">' +
                    '<div>' +
                        '<div class="text-[10px] uppercase tracking-[0.14em] font-black text-sky-700">Eski fiyatlarla karşılaştırma</div>' +
                        '<div class="text-sm sm:text-base font-black text-slate-900 mt-0.5">Fiyatı değişen fabrikalar</div>' +
                        '<div class="text-[10px] font-bold text-slate-500 mt-0.5">Kayıtlı / kaynağın yayınladığı önceki fiyata göre</div>' +
                    '</div>' +
                    '<div class="inline-flex items-center rounded-xl bg-white border border-sky-200 px-2.5 py-1.5 text-[10px] font-black text-sky-700">' +
                        allUpCount + ' ↑ · ' + allDownCount + ' ↓' +
                    '</div>' +
                '</div>' +
                (degisenFirmalar.length
                    ? '<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 mt-3 items-start">' + kartlar + '</div>'
                    : '<div class="mt-3 rounded-xl bg-white/70 border border-slate-200 px-3 py-2.5 text-xs font-semibold text-slate-500">Kayıtlı bir fiyat değişimi henüz yok. Fiyatlar değiştikçe burada görünecek.</div>') +
                (degisenFirmalar.length && degismeyenFirmalar.length
                    ? '<div class="mt-2.5 text-[11px] font-semibold text-slate-500">Değişim yok: ' +
                        degismeyenFirmalar.map(function(f) { return escapeHtml(f.baslik || f.firma_id); }).join(" · ") + '</div>'
                    : '') +
            '</div>';

        todayUpdates
            .querySelectorAll(".today-update-toggle")
            .forEach(function(toggle) {
                toggle.addEventListener("click", function() {
                    const card = toggle.closest(".today-update-card");
                    if (!card) return;
                    const panel = card.querySelector(".today-update-panel");
                    if (!panel) return;
                    const acik = !panel.classList.contains("hidden");
                    panel.classList.toggle("hidden", acik);
                    toggle.setAttribute("aria-expanded", String(!acik));
                });
            });
    }

    const search = document.getElementById("fiyatArama");
    if (search && search.dataset.bound !== "1") {
        search.addEventListener("input", function() {
            const needle = String(search.value || "").trim().toLocaleLowerCase("tr-TR");

            document.querySelectorAll("#firmaListesi .price-card").forEach(function(card) {
                const text = String(card.textContent || "").toLocaleLowerCase("tr-TR");
                card.style.display = !needle || text.includes(needle) ? "" : "none";
            });
        });
        search.dataset.bound = "1";
    }

    const firmSelect = document.getElementById("historyFirmSelect");
    const alarmFirm = document.getElementById("alarmFirm");
    const compareSelect = document.getElementById("compareSelect");
    const calcFirm = document.getElementById("calcFirm");
    const calcItem = document.getElementById("calcItem");
    const calcButton = document.getElementById("calcButton");
    const calcResult = document.getElementById("calcResult");

    const firmsForSelect = firmalar.map(function(f) {
        return '<option value="' + escapeHtml(f.firma_id) + '">' + escapeHtml(f.baslik) + '</option>';
    }).join("");

    if (firmSelect) firmSelect.innerHTML = '<option value="">Firma seçin</option>' + firmsForSelect;
    if (alarmFirm) alarmFirm.innerHTML = '<option value="">Firma seçin</option>' + firmsForSelect;
    if (calcFirm) calcFirm.innerHTML = '<option value="">Firma seçin</option>' + firmsForSelect;

    const itemMap = {};
    firmalar.forEach(function(f) {
        (f.kalemler || []).forEach(function(k) {
            itemMap[k.cins] = true;
        });
    });

    const itemOptions = Object.keys(itemMap)
        .sort(function(a,b) { return a.localeCompare(b, "tr"); })
        .map(function(k) {
            return '<option value="' + escapeHtml(k) + '">' + escapeHtml(k) + '</option>';
        }).join("");

    const alarmItem = document.getElementById("alarmItem");
    if (alarmItem) alarmItem.innerHTML = '<option value="">Kalem seçin</option>' + itemOptions;

    if (compareSelect) compareSelect.innerHTML = '<option value="">Kalem seçin</option>' + itemOptions;

    function firmaKalemleriniDoldur(selectId, firmaId) {
        const select = document.getElementById(selectId);
        if (!select) return;
        const firma = firmalar.find(function(f) { return f.firma_id === firmaId; });
        const options = firma && Array.isArray(firma.kalemler)
            ? firma.kalemler.map(function(k) {
                return '<option value="' + escapeHtml(k.cins) + '">' + escapeHtml(k.cins) + '</option>';
            }).join("")
            : "";
        select.innerHTML = '<option value="">Kalem seçin</option>' + options;
    }

    if (calcFirm && calcFirm.dataset.bound !== "1") {
        calcFirm.addEventListener("change", function() {
            firmaKalemleriniDoldur("calcItem", calcFirm.value);
        });
        calcFirm.dataset.bound = "1";
    }

    if (calcButton && calcButton.dataset.bound !== "1") {
        calcButton.addEventListener("click", function() {
            const firma = firmalar.find(function(f) { return f.firma_id === (calcFirm ? calcFirm.value : ""); });
            const kalem = firma && Array.isArray(firma.kalemler)
                ? firma.kalemler.find(function(k) { return k.cins === (calcItem ? calcItem.value : ""); })
                : null;
            const miktar = Number(document.getElementById("calcQuantity")?.value || 0);

            if (!firma || !kalem || !Number.isFinite(miktar) || miktar <= 0) {
                if (calcResult) calcResult.innerHTML = '<div class="rounded-xl border border-amber-200 bg-amber-50 p-3 text-center text-xs text-amber-700 font-bold">Firma, kalem ve 0’dan büyük bir ton miktarı seçin.</div>';
                return;
            }

            const rawPrice = kalem.manuel_fiyat !== null && kalem.manuel_fiyat !== undefined
                ? Number(kalem.manuel_fiyat)
                : Number(kalem.otomatik_fiyat);

            if (!Number.isFinite(rawPrice) || rawPrice <= 0) {
                if (calcResult) calcResult.innerHTML = '<div class="rounded-xl border border-red-200 bg-red-50 p-3 text-center text-xs text-red-700 font-bold">Seçilen kalemin geçerli bir fiyatı bulunamadı.</div>';
                return;
            }

            const toplam = rawPrice * miktar;
            const fiyatText = rawPrice.toLocaleString("tr-TR", {minimumFractionDigits: 0, maximumFractionDigits: 2});
            const toplamText = toplam.toLocaleString("tr-TR", {minimumFractionDigits: 0, maximumFractionDigits: 0});

            if (calcResult) {
                calcResult.innerHTML =
                    '<div class="grid grid-cols-1 sm:grid-cols-3 gap-2">' +
                        '<div class="rounded-xl bg-white border border-slate-200 p-3"><div class="text-[9px] uppercase tracking-wide font-black text-slate-400">Seçilen fiyat</div><div class="text-lg font-black text-slate-900 mt-1">' + fiyatText + ' TL / ton</div></div>' +
                        '<div class="rounded-xl bg-white border border-slate-200 p-3"><div class="text-[9px] uppercase tracking-wide font-black text-slate-400">Miktar</div><div class="text-lg font-black text-slate-900 mt-1">' + miktar.toLocaleString("tr-TR", {maximumFractionDigits: 2}) + ' ton</div></div>' +
                        '<div class="min-w-0 rounded-xl bg-emerald-600 p-2.5 text-white"><div class="text-[8px] uppercase tracking-[0.04em] font-black text-emerald-100 leading-tight whitespace-nowrap">Tahmini toplam</div><div class="text-base sm:text-lg font-black mt-1 leading-tight break-words">' + toplamText + ' TL</div></div>' +
                    '</div>' +
                    '<div class="text-[9px] text-slate-500 mt-2">Hesaplama: seçilen fiyat × ton miktarı. Taşıma, fire, kesinti ve diğer ticari şartlar dahil değildir.</div>';
            }
        });
        calcButton.dataset.bound = "1";
    }

    if (firmSelect && firmSelect.dataset.bound !== "1") {
        firmSelect.addEventListener("change", function() {
            firmaKalemleriniDoldur("historyItemSelect", firmSelect.value);
        });
        firmSelect.dataset.bound = "1";
    }

    // Özet sayaçları doğrudan alttaki 2x5 fabrika kartlarının
    // /prices verisindeki kalem.degisim değerlerinden gelir.
    // Ayrı bir /today-changes çağrısı bu değerleri ezmez.

    const comparePanel = document.getElementById("comparePanel");
    const historyPanel = document.getElementById("historyPanel");
    const alarmPanel = document.getElementById("alarmPanel");

    const compareToggle = document.getElementById("compareToggle");
    if (compareToggle && compareToggle.dataset.bound !== "1") {
        compareToggle.addEventListener("click", function(){
            comparePanel?.classList.toggle("hidden");
            historyPanel?.classList.add("hidden");
            alarmPanel?.classList.add("hidden");
        });
        compareToggle.dataset.bound = "1";
    }

    const compareButton = document.getElementById("compareButton");
    if (compareButton && compareButton.dataset.bound !== "1") {
        compareButton.addEventListener("click", function() {
            const kalem = document.getElementById("compareSelect")?.value;
            if (!kalem) return;

            compareButton.disabled = true;
            compareButton.textContent = "Karşılaştırılıyor...";

            fetch("/compare?kalem=" + encodeURIComponent(kalem), {cache:"no-store"})
                .then(function(r){ return r.json(); })
                .then(function(payload){
                    const box = document.getElementById("compareResult");
                    const rows = (payload.data || []).filter(function(x){
                        return Number.isFinite(Number(x.fiyat));
                    }).sort(function(a, b){
                        return Number(b.fiyat) - Number(a.fiyat);
                    });

                    if (!rows.length) {
                        box.innerHTML = '<div class="rounded-xl bg-slate-50 border border-slate-200 p-3 text-xs text-slate-500 font-semibold">Bu kalem için karşılaştırılabilir fiyat bulunamadı.</div>';
                        return;
                    }

                    const enYuksek = Number(rows[0].fiyat);
                    const enDusuk = Number(rows[rows.length - 1].fiyat);
                    const fark = enYuksek - enDusuk;

                    box.innerHTML =
                        '<div class="mb-3 flex items-center justify-between gap-2">' +
                            '<div><div class="text-[9px] uppercase tracking-wide font-black text-slate-400">Firma karşılaştırması</div>' +
                            '<div class="text-sm font-black text-slate-900 mt-0.5">' + escapeHtml(kalem) + '</div></div>' +
                            '<div class="text-[10px] font-bold text-slate-400">' + rows.length + ' firma</div>' +
                        '</div>' +
                        '<div class="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-3">' +
                            '<div class="rounded-xl border border-emerald-200 bg-emerald-50 p-3">' +
                                '<div class="text-[9px] uppercase tracking-wide font-black text-emerald-600">En yüksek fiyat</div>' +
                                '<div class="text-lg font-black text-emerald-700 mt-1">' + enYuksek.toLocaleString("tr-TR") + ' TL</div>' +
                            '</div>' +
                            '<div class="rounded-xl border border-slate-200 bg-white p-3">' +
                                '<div class="text-[9px] uppercase tracking-wide font-black text-slate-400">En düşük fiyat</div>' +
                                '<div class="text-lg font-black text-slate-900 mt-1">' + enDusuk.toLocaleString("tr-TR") + ' TL</div>' +
                            '</div>' +
                            '<div class="rounded-xl border border-slate-200 bg-white p-3">' +
                                '<div class="text-[9px] uppercase tracking-wide font-black text-slate-400">Fiyat aralığı</div>' +
                                '<div class="text-lg font-black text-slate-900 mt-1">' + fark.toLocaleString("tr-TR") + ' TL</div>' +
                            '</div>' +
                        '</div>' +
                        '<div class="overflow-x-auto rounded-xl border border-slate-200">' +
                            '<table class="w-full text-xs">' +
                                '<thead><tr class="bg-slate-50 border-b border-slate-200">' +
                                    '<th class="text-left px-3 py-2.5 font-black text-slate-500">Firma</th>' +
                                    '<th class="text-right px-3 py-2.5 font-black text-slate-500">Fiyat</th>' +
                                    '<th class="text-right px-3 py-2.5 font-black text-slate-500">En yüksekten fark</th>' +
                                '</tr></thead>' +
                                '<tbody>' +
                                    rows.map(function(x, index){
                                        const fiyat = Number(x.fiyat);
                                        const farktan = enYuksek - fiyat;
                                        const vurgu = index === 0 ? ' bg-emerald-50' : '';
                                        return '<tr class="border-b border-slate-100 last:border-0' + vurgu + '">' +
                                            '<td class="px-3 py-2.5 font-bold text-slate-800">' +
                                                '<span class="inline-flex items-center gap-2">' +
                                                    '<span class="w-6 h-6 rounded-lg bg-slate-900 text-white flex items-center justify-center text-[9px] font-black">' + String(index + 1).padStart(2, "0") + '</span>' +
                                                    escapeHtml(x.firma) +
                                                '</span>' +
                                            '</td>' +
                                            '<td class="px-3 py-2.5 text-right font-black text-slate-950 whitespace-nowrap">' + fiyat.toLocaleString("tr-TR") + ' TL</td>' +
                                            '<td class="px-3 py-2.5 text-right font-bold ' + (farktan === 0 ? 'text-emerald-700' : 'text-slate-500') + ' whitespace-nowrap">' +
                                                (farktan === 0 ? 'EN YÜKSEK' : '-' + farktan.toLocaleString("tr-TR") + ' TL') +
                                            '</td>' +
                                        '</tr>';
                                    }).join("") +
                                '</tbody>' +
                            '</table>' +
                        '</div>';
                })
                .catch(function(){
                    const box = document.getElementById("compareResult");
                    if (box) box.innerHTML = '<div class="rounded-xl bg-red-50 border border-red-200 p-3 text-xs text-red-700 font-semibold">Karşılaştırma verisi alınamadı.</div>';
                })
                .finally(function(){
                    compareButton.disabled = false;
                    compareButton.textContent = "Karşılaştır";
                });
        });
        compareButton.dataset.bound = "1";
    }

    const historyLoad = document.getElementById("historyLoadButton");
    if (historyLoad && historyLoad.dataset.bound !== "1") {
        historyLoad.addEventListener("click", function(){
            const firma = firmSelect?.value;
            const kalem = document.getElementById("historyItemSelect")?.value;
            const box = document.getElementById("historyResult");

            if (!firma || !kalem) {
                if (box) box.innerHTML = '<div class="rounded-xl bg-amber-50 border border-amber-200 p-3 text-xs text-amber-700 font-bold">Önce firma ve kalem seçin.</div>';
                return;
            }

            historyLoad.disabled = true;
            historyLoad.textContent = "Yükleniyor...";

            fetch("/history?firma_id=" + encodeURIComponent(firma) + "&kalem=" + encodeURIComponent(kalem) + "&limit=60", {cache:"no-store"})
                .then(function(r){
                    if (!r.ok) throw new Error("Geçmiş verisi alınamadı.");
                    return r.json();
                })
                .then(function(payload){
                    const rows = (payload.data || []).filter(function(x){
                        return Number.isFinite(Number(x.fiyat));
                    });

                    if (!rows.length) {
                        if (box) box.innerHTML = '<div class="rounded-xl bg-slate-50 border border-slate-200 p-3 text-xs text-slate-500 font-semibold">Bu firma ve kalem için geçmiş fiyat kaydı bulunamadı.</div>';
                        return;
                    }

                    const vals = rows.map(function(x){ return Number(x.fiyat); });
                    const min = Math.min.apply(null, vals);
                    const max = Math.max.apply(null, vals);
                    const latest = vals[vals.length - 1];
                    const first = vals[0];
                    const change = latest - first;
                    const range = Math.max(1, max - min);

                    const width = 620;
                    const height = 210;
                    const padX = 28;
                    const padY = 22;
                    const usableW = width - padX * 2;
                    const usableH = height - padY * 2;

                    const points = rows.map(function(x, index){
                        const px = rows.length === 1
                            ? width / 2
                            : padX + (index / (rows.length - 1)) * usableW;
                        const py = padY + (1 - ((Number(x.fiyat) - min) / range)) * usableH;
                        return {x:px, y:py, value:Number(x.fiyat), date:x.tarih || x.fiyat_tarihi || ""};
                    });

                    const polyline = points.map(function(p){
                        return p.x.toFixed(1) + "," + p.y.toFixed(1);
                    }).join(" ");

                    const dots = points.map(function(p, index){
                        if (index !== points.length - 1 && index !== 0) return "";
                        return '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="4" fill="currentColor"><title>' +
                            escapeHtml(p.date) + ' · ' + p.value.toLocaleString("tr-TR") + ' TL</title></circle>';
                    }).join("");

                    const up = change > 0;
                    const changeClass = up ? "text-emerald-700 bg-emerald-50 border-emerald-200" : (change < 0 ? "text-red-700 bg-red-50 border-red-200" : "text-slate-600 bg-slate-50 border-slate-200");
                    const changeText = (change > 0 ? "+" : "") + change.toLocaleString("tr-TR") + " TL";

                    const lastDate = rows[rows.length - 1].tarih || rows[rows.length - 1].fiyat_tarihi || "-";

                    box.innerHTML =
                        '<div class="rounded-2xl border border-slate-200 bg-slate-50/70 overflow-hidden">' +
                            '<div class="grid grid-cols-3 gap-2 p-3 border-b border-slate-200 bg-white">' +
                                '<div><div class="text-[9px] uppercase tracking-wide font-black text-slate-400">Son fiyat</div><div class="text-base font-black text-slate-950 mt-1">' + latest.toLocaleString("tr-TR") + ' TL</div></div>' +
                                '<div><div class="text-[9px] uppercase tracking-wide font-black text-slate-400">Min / Max</div><div class="text-xs font-black text-slate-700 mt-1">' + min.toLocaleString("tr-TR") + ' / ' + max.toLocaleString("tr-TR") + '</div></div>' +
                                '<div><div class="text-[9px] uppercase tracking-wide font-black text-slate-400">Değişim</div><div class="inline-flex mt-1 rounded-lg border px-2 py-1 text-[10px] font-black ' + changeClass + '">' + changeText + '</div></div>' +
                            '</div>' +
                            '<div class="p-2 sm:p-3">' +
                                '<div class="text-[9px] text-slate-400 font-bold mb-1">Kayıt sayısı: ' + rows.length + ' · Son kayıt: ' + escapeHtml(lastDate) + '</div>' +
                                '<div class="w-full overflow-hidden rounded-xl bg-white border border-slate-200">' +
                                    '<svg viewBox="0 0 ' + width + ' ' + height + '" class="block w-full h-auto text-sky-600" role="img" aria-label="Fiyat geçmişi grafiği">' +
                                        '<line x1="' + padX + '" y1="' + padY + '" x2="' + padX + '" y2="' + (height-padY) + '" stroke="currentColor" stroke-opacity=".12" />' +
                                        '<line x1="' + padX + '" y1="' + (height-padY) + '" x2="' + (width-padX) + '" y2="' + (height-padY) + '" stroke="currentColor" stroke-opacity=".12" />' +
                                        '<polyline points="' + polyline + '" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />' +
                                        dots +
                                    '</svg>' +
                                '</div>' +
                            '</div>' +
                        '</div>';
                })
                .catch(function(){
                    if (box) box.innerHTML = '<div class="rounded-xl bg-red-50 border border-red-200 p-3 text-xs text-red-700 font-semibold">Geçmiş fiyat verisi alınamadı.</div>';
                })
                .finally(function(){
                    historyLoad.disabled = false;
                    historyLoad.textContent = "Grafiği Göster";
                });
        });
        historyLoad.dataset.bound = "1";
    }

    const alarmButton = document.getElementById("alarmButton");
    if (alarmButton && alarmButton.dataset.bound !== "1") {
        alarmButton.addEventListener("click", function(){
            if ("Notification" in window && Notification.permission === "default") {
                Notification.requestPermission().catch(function(){});
            }
            alarmPanel?.classList.toggle("hidden");
            comparePanel?.classList.add("hidden");
            historyPanel?.classList.add("hidden");
        });
        alarmButton.dataset.bound = "1";
    }

    const alarmSave = document.getElementById("alarmSaveButton");
    if (alarmSave && alarmSave.dataset.bound !== "1") {
        alarmSave.addEventListener("click", function(){
            const firm = document.getElementById("alarmFirm")?.value;
            const item = document.getElementById("alarmItem")?.value;
            const direction = document.getElementById("alarmDirection")?.value;
            const value = Number(document.getElementById("alarmValue")?.value);

            if (!firm || !item || !Number.isFinite(value) || value <= 0) return;

            const alarms = JSON.parse(localStorage.getItem("hurdaPriceAlarms") || "[]");
            alarms.push({
                id: Date.now(),
                firma_id: firm,
                kalem: item,
                direction: direction,
                value: value,
                fired: false
            });
            localStorage.setItem("hurdaPriceAlarms", JSON.stringify(alarms));
            renderAlarms();
            hurdaPushSenkron();
        });
        alarmSave.dataset.bound = "1";
    }

    function renderAlarms() {
        const box = document.getElementById("alarmList");
        if (!box) return;
        const alarms = JSON.parse(localStorage.getItem("hurdaPriceAlarms") || "[]");
        box.innerHTML = alarms.length
            ? alarms.map(function(a, index){
                const firma = firmalar.find(function(f){ return f.firma_id === a.firma_id; });
                const firmaAdi = firma ? firma.baslik : a.firma_id;
                const yon = a.direction === "above" ? "Yükselince" : "Altına inince";
                const durum = a.fired ? "Tetiklendi" : "Aktif";
                return '<div class="flex items-center justify-between gap-2 rounded-xl bg-slate-50 border border-slate-200 p-2.5 text-xs">' +
                    '<div class="min-w-0">' +
                        '<div class="font-bold text-slate-800 truncate">' + escapeHtml(firmaAdi) + ' · ' + escapeHtml(a.kalem) + '</div>' +
                        '<div class="mt-0.5 text-[10px] font-semibold text-slate-500">' + escapeHtml(yon) + ' ' + Number(a.value).toLocaleString("tr-TR") + ' TL · ' + escapeHtml(durum) + '</div>' +
                    '</div>' +
                    '<button type="button" data-alarm-delete="' + index + '" class="text-red-600 font-black shrink-0">Sil</button></div>';
            }).join("")
            : '<div class="text-xs text-slate-500">Kayıtlı fiyat alarmı yok.</div>';

        box.querySelectorAll("[data-alarm-delete]").forEach(function(btn){
            btn.addEventListener("click", function(){
                const list = JSON.parse(localStorage.getItem("hurdaPriceAlarms") || "[]");
                list.splice(Number(btn.dataset.alarmDelete), 1);
                localStorage.setItem("hurdaPriceAlarms", JSON.stringify(list));
                renderAlarms();
                hurdaPushSenkron();
            });
        });
    }

    renderAlarms();

    // Fiyat alarmı tarayıcı açıkken çalışır; izin verilirse sistem bildirimi de verir.
    const alarms = JSON.parse(localStorage.getItem("hurdaPriceAlarms") || "[]");
    alarms.forEach(function(a){
        const firma = firmalar.find(function(f){ return f.firma_id === a.firma_id; });
        const kalem = firma && (firma.kalemler || []).find(function(k){ return k.cins === a.kalem; });
        if (!kalem) return;

        const fiyat = kalem.manuel_fiyat !== null && kalem.manuel_fiyat !== undefined
            ? Number(kalem.manuel_fiyat)
            : Number(kalem.otomatik_fiyat);
        if (!Number.isFinite(fiyat)) return;
        const oldu = a.direction === "above" ? fiyat >= a.value : fiyat <= a.value;

        if (oldu && !a.fired) {
            a.fired = true;

            // Arka plan push açıksa bildirimi sunucu gönderir; çift bildirim olmasın.
            if (
                localStorage.getItem("hurdaPushAktif") !== "1"
                && "Notification" in window
                && Notification.permission === "granted"
            ) {
                const baslik = "Hurda fiyat alarmı";
                const govde = (firma ? firma.baslik + " · " : "") + a.kalem + " · " + Number(fiyat).toLocaleString("tr-TR") + " TL";

                if (navigator.serviceWorker && navigator.serviceWorker.getRegistration) {
                    navigator.serviceWorker.getRegistration().then(function(reg) {
                        if (reg && reg.showNotification) {
                            reg.showNotification(baslik, { body: govde, tag: "alarm-" + a.id });
                        } else {
                            new Notification(baslik, { body: govde });
                        }
                    });
                } else {
                    new Notification(baslik, { body: govde });
                }
            }
        }
        else if (!oldu && a.fired) {
            // Fiyat koşuldan çıktı: alarm yeniden kurulur.
            a.fired = false;
        }
    });
    localStorage.setItem("hurdaPriceAlarms", JSON.stringify(alarms));

}


async function fiyatlariGetir() {

    try {

        const response =
            await fetch(
                "/prices",
                {
                    cache: "no-store"
                }
            );

        if (!response.ok) {

            throw new Error(
                "Fiyat servisi çalışmadı."
            );

        }

        const result =
            await response.json();

        document.getElementById(
            "sonGuncelleme"
        ).textContent =
            result.son_guncelleme || "-";

        const summaryLastUpdate =
            document.getElementById("summaryLastUpdate");

        if (summaryLastUpdate) {
            summaryLastUpdate.textContent =
                result.son_guncelleme || "-";
        }

        const firmaListesi =
            document.getElementById(
                "firmaListesi"
            );

        const loading =
            document.getElementById(
                "loading"
            );

        firmaListesi.innerHTML = "";

        if (
            !result.data ||
            !result.data.length
        ) {

            loading.textContent =
                "Henüz firma fiyatı bulunmuyor.";

            loading.classList.remove(
                "hidden"
            );

            return;
        }

        loading.classList.add(
            "hidden"
        );

        const firmalar = result.data;

        marketToolsInit(result);

        firmalar.forEach(function(item, index) {

            const wrapper = document.createElement("div");
            wrapper.className =
                "price-card bg-white rounded-2xl sm:rounded-3xl shadow-sm border border-slate-200 overflow-hidden hover:shadow-md transition-all duration-200";

            const panelId = "firma_" + index;
            let rows = "";

            const kalemSayisi =
                Array.isArray(item.kalemler)
                    ? item.kalemler.length
                    : 0;

            const artisSayisi =
                Array.isArray(item.kalemler)
                    ? item.kalemler.filter(function(kalem) {
                        return String(
                            kalem.degisim || ""
                        ).startsWith("+");
                    }).length
                    : 0;

            const dususSayisi =
                Array.isArray(item.kalemler)
                    ? item.kalemler.filter(function(kalem) {
                        return String(
                            kalem.degisim || ""
                        ).startsWith("-");
                    }).length
                    : 0;

            item.kalemler.forEach(function(kalem) {

                const degisim = kalem.degisim || "";
                let degisimHtml = "";

                if (degisim) {

                    let cls =
                        "text-slate-600 bg-slate-100 border-slate-200";

                    let icon = "•";

                    if (degisim.startsWith("+")) {
                        cls =
                            "text-emerald-700 bg-emerald-50 border-emerald-200";
                        icon = "▲";
                    }
                    else if (degisim.startsWith("-")) {
                        cls =
                            "text-red-700 bg-red-50 border-red-200";
                        icon = "▼";
                    }

                    degisimHtml =
                        '<span class="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[10px] ' +
                        cls +
                        ' font-black whitespace-nowrap">' +
                            icon +
                            " " +
                            escapeHtml(degisim) +
                        "</span>";
                }

                const sayiMatch = String(kalem.fiyat || "").match(/^([\\d.,]+)\\s*(.*)$/);
                const fiyatSayi = sayiMatch ? sayiMatch[1] : String(kalem.fiyat || "-");
                const fiyatBirim = sayiMatch ? sayiMatch[2] : "";

                const kisaTarih = String(kalem.fiyat_tarihi || "").replace(/^(\\d{4})-(\\d{2})-(\\d{2})$/, "$3.$2");

                const metaParcalari = [];
                if (kisaTarih) metaParcalari.push(escapeHtml(kisaTarih));
                if (kalem.durum === "manuel") metaParcalari.push("manuel");
                if (kalem.dun_fiyat !== null && kalem.dun_fiyat !== undefined && kalem.dun_fark) {
                    metaParcalari.push("dün " + (kalem.dun_fark > 0 ? "+" : "") + Number(kalem.dun_fark).toLocaleString("tr-TR"));
                }
                if (kalem.en_yuksek) metaParcalari.push("🏆");

                const yon = degisim.startsWith("+") ? "up" : (degisim.startsWith("-") ? "down" : "");

                rows +=
                    '<div class="fp-row">' +
                        '<div class="min-w-0">' +
                            '<span class="fp-cins">' + escapeHtml(kalem.cins) + "</span>" +
                            '<span class="fp-meta">' + metaParcalari.join(" · ") + "</span>" +
                        "</div>" +
                        '<div class="fp-spark">' + trendCizgisi(kalem.trend, 54, 20, "trend-inline") + "</div>" +
                        '<div class="fp-price">' +
                            '<span class="fp-fiyat">' + escapeHtml(fiyatSayi) + "</span>" +
                            (fiyatBirim ? '<span class="fp-unit">' + escapeHtml(fiyatBirim) + "</span>" : "") +
                            (degisim
                                ? '<span class="fp-chg ' + yon + '">' + (yon === "up" ? "▲ " : (yon === "down" ? "▼ " : "")) + escapeHtml(degisim) + "</span>"
                                : "") +
                        "</div>" +
                    "</div>";
            });

            const kaynakLink = item.url
                ? '<a href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener noreferrer" class="fp-btn" title="Resmi kaynağa git">Kaynak ↗</a>'
                : '<span class="fp-btn fp-btn-muted">Manuel</span>';

            wrapper.innerHTML =
                '<button type="button" class="firma-toggle fc-btn" data-panel="' +
                    panelId +
                    '" aria-expanded="false">' +

                    '<span class="fc-top">' +

                        '<span class="fc-avatar" aria-hidden="true">' +
                            escapeHtml(
                                String(item.baslik || "?")
                                    .trim()
                                    .charAt(0)
                                    .toLocaleUpperCase("tr-TR")
                            ) +
                        "</span>" +

                        '<span class="fc-info">' +
                            '<span class="fc-title">' +
                                escapeHtml(item.baslik) +
                            "</span>" +
                            '<span class="fc-meta">' +
                                escapeHtml(
                                    (item.kalemler ? item.kalemler.length : 0) +
                                    " kalem" +
                                    (item.tarih && item.tarih !== "-"
                                        ? " · " + item.tarih
                                        : "") +
                                    (item.en_yuksek_sayisi > 0
                                        ? " · 🏆 " + item.en_yuksek_sayisi
                                        : "")
                                ) +
                            "</span>" +
                        "</span>" +

                        '<span class="fc-badges">' +
                            (
                                artisSayisi > 0
                                    ? '<span class="fc-chip fc-chip-up">▲ ' + artisSayisi + "</span>"
                                    : ""
                            ) +
                            (
                                dususSayisi > 0
                                    ? '<span class="fc-chip fc-chip-down">▼ ' + dususSayisi + "</span>"
                                    : ""
                            ) +
                        "</span>" +

                    "</span>" +

                    (
                        item.fiyat_yasi_gun !== null && item.fiyat_yasi_gun !== undefined && item.fiyat_yasi_gun >= 2
                            ? '<span class="fc-stale">⚠ ' + item.fiyat_yasi_gun + ' gün önceki fiyat</span>'
                            : (
                                item.son_24_saatte_guncellendi
                                    ? '<span class="fc-fresh">● 24 saat içinde güncellendi</span>'
                                    : (
                                        item.fiyat_yasi_gun === null || item.fiyat_yasi_gun === undefined
                                            ? '<span class="fc-fresh fc-fresh-muted">✎ Manuel fiyat</span>'
                                            : '<span class="fc-fresh fc-fresh-ok">● Güncel fiyat</span>'
                                    )
                            )
                    ) +

                    '<span class="fc-cta">' +
                        '<span class="fc-cta-text">Fiyat Gör</span>' +
                        '<span class="firma-ok-icon fc-cta-icon">▼</span>' +
                    "</span>" +

                "</button>" +

                '<div id="' + panelId + '" class="fp-wrap">' +
                    '<div class="fp-inner">' +
                        '<div class="fp-body">' +

                            '<div class="fp-head">' +
                                '<span class="fp-title">' + kalemSayisi + " fiyat kalemi</span>" +
                                '<span class="fp-actions">' +
                                    '<button type="button" class="fp-btn wa firma-wa" data-firma="' + escapeHtml(item.firma_id) + '" title="WhatsApp\\'ta paylaş">💬 Paylaş</button>' +
                                    kaynakLink +
                                "</span>" +
                            "</div>" +

                            '<div class="fp-list">' + rows + "</div>" +

                        "</div>" +
                    "</div>" +
                "</div>";

            firmaListesi.appendChild(
                wrapper
            );

            const waFirma = wrapper.querySelector(".firma-wa");
            if (waFirma) {
                waFirma.addEventListener("click", function(e) {
                    e.stopPropagation();
                    hurdaPaylas(hurdaFiyatMetni(item));
                });
            }

            const toggle =
                wrapper.querySelector(
                    ".firma-toggle"
                );

            toggle.addEventListener("click", function() {
                const panel = document.getElementById(panelId);
                const acik = panel.classList.contains("open");

                // Akordeon: yeni kart açılırken diğer açık olanlar kapanır.
                if (!acik) {
                    document.querySelectorAll("#firmaListesi .fp-wrap.open").forEach(function(diger) {
                        diger.classList.remove("open");
                        const dugme = diger.parentElement.querySelector(".firma-toggle");
                        if (dugme) {
                            dugme.setAttribute("aria-expanded", "false");
                            const ik = dugme.querySelector(".firma-ok-icon");
                            if (ik) ik.style.transform = "rotate(0deg)";
                        }
                    });
                }

                panel.classList.toggle("open", !acik);
                toggle.setAttribute("aria-expanded", String(!acik));

                const icon = toggle.querySelector(".firma-ok-icon");
                if (icon) icon.style.transform = acik ? "rotate(0deg)" : "rotate(180deg)";
            });

        });
    }

    catch (error) {

        document.getElementById(
            "loading"
        ).textContent =
            "Fiyatlar yüklenirken hata oluştu.";

        console.error(
            error
        );

    }

}


dovizleriGetir();

lmeFiyatlariniGetir();

fiyatlariGetir();


// Bant genişliği için: arka plandaki (gizli) sekmeler veri çekmez,
// sekme tekrar görününce hemen yenilenir.
function yalnizcaGorunurken(fn) {
    return function() {
        if (!document.hidden) {
            fn();
        }
    };
}

setInterval(
    yalnizcaGorunurken(dovizleriGetir),
    600000
);


setInterval(
    yalnizcaGorunurken(lmeFiyatlariniGetir),
    900000
);


setInterval(
    yalnizcaGorunurken(fiyatlariGetir),
    180000
);

(function() {
    let gizlenme = 0;
    document.addEventListener("visibilitychange", function() {
        if (document.hidden) {
            gizlenme = Date.now();
        } else if (gizlenme && Date.now() - gizlenme > 180000) {
            // 3 dakikadan uzun süre gizli kaldıysa fiyatları hemen yenile.
            fiyatlariGetir();
        }
    });
})();

</script>

<script>

function bosReklamAlanlariniGizle() {

    const bottom =
        document.getElementById(
            "bottomAds"
        );

    if (bottom) {

        const kutular =
            bottom.querySelectorAll(
                ".ad-box"
            );

        const dolu =
            Array.from(
                kutular
            ).some(
                function(kutu) {
                    return (
                        kutu.innerHTML
                        || ""
                    ).trim() !== "";
                }
            );

        if (!dolu) {
            bottom.style.display = "none";
        }

    }

}

bosReklamAlanlariniGizle();

</script>

<script>

if (
    "serviceWorker"
    in navigator
) {

    window.addEventListener(
        "load",
        function() {

            navigator.serviceWorker
                .register(
                    "/sw.js"
                )
                .catch(
                    function(error) {

                        console.log(
                            "Service Worker:",
                            error
                        );

                    }
                );

        }
    );

}

</script>

<script>
(function() {
    function temaUygula(koyu) {
        document.documentElement.classList.toggle("dark", koyu);
        try { localStorage.setItem("hurdaTema", koyu ? "dark" : "light"); } catch (e) {}
        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.setAttribute("content", koyu ? "#0a101d" : "#0f172a");
        document.querySelectorAll("[data-tema-ikon]").forEach(function(el) {
            el.textContent = koyu ? "☀️" : "🌙";
        });
    }

    temaUygula(document.documentElement.classList.contains("dark"));

    function araca(hedefId) {
        const el = document.getElementById(hedefId);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    document.querySelectorAll("[data-bar]").forEach(function(dugme) {
        dugme.addEventListener("click", function() {
            const islem = dugme.dataset.bar;

            if (islem === "tema") {
                temaUygula(!document.documentElement.classList.contains("dark"));
            }
            else if (islem === "ara") {
                araca("marketTools");
                setTimeout(function() {
                    const kutu = document.getElementById("fiyatArama");
                    if (kutu) kutu.focus({ preventScroll: true });
                }, 350);
            }
            else if (islem === "alarm") {
                araca("marketTools");
                const panel = document.getElementById("alarmPanel");
                const buton = document.getElementById("alarmButton");
                if (panel && buton && panel.classList.contains("hidden")) buton.click();
            }
            else if (islem === "paylas") {
                if (typeof hurdaPaylas === "function") hurdaPaylas(hurdaTumMetin());
            }
        });
    });
})();
</script>

</body>

</html>
"""

    # =====================================================
    # BANNERLARI ANA SAYFAYA YERLEŞTİR
    # =====================================================

    reklamlar = {
        "bottomAd1": ads[
            "left_top"
        ],
        "bottomAd2": ads[
            "left_middle"
        ],
        "bottomAd3": ads[
            "left_bottom"
        ],
        "bottomAd4": ads[
            "right_top"
        ],
        "bottomAd5": ads[
            "right_middle"
        ],
        "bottomAd6": ads[
            "right_bottom"
        ],
    }

    page = page.replace(
        "__BASE_URL__",
        esc(site_base_url(request)),
    )

    for reklam_id, reklam in reklamlar.items():

        desen = (
            r'<div\s+'
            r'class="ad-box rounded-2xl overflow-hidden"\s+'
            r'id="' + re.escape(
                reklam_id
            ) + r'"\s*>\s*'
            r'</div>'
        )

        icerik = (
            '<div class="ad-box rounded-2xl overflow-hidden" '
            'id="' + reklam_id + '">'
            + ad_html(
                reklam
            )
            + '</div>'
        )

        page, degisim_sayisi = re.subn(
            desen,
            icerik,
            page,
            count=1,
        )

        if degisim_sayisi == 0:

            print(
                f"UYARI: {reklam_id} reklam alanı "
                "anasayfa HTML'inde bulunamadı."
            )

    return page


# =========================================================
# ÇALIŞTIR
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000,
        )
    )

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
    )


# DEPLOY SYNTAX CHECK MARKER
