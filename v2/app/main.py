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
from datetime import datetime
from zoneinfo import ZoneInfo

import uvicorn
import os
import json
import secrets
import html as html_lib
import uuid
import shutil
import re
import gc
import requests
import xml.etree.ElementTree as ET

from app.scrapers import TUMU
from app.scrapers.generic import cek_url as generic_url_cek

from app.storage import (
    load_data,
    save_data,
    now_string,
    firma_sil,
    fiyat_kaydet,
    manuel_fiyat_kaydet,
    manuel_fiyat_sil,
    bildirim_ekle,
    bildirim_okundu,
    bildirim_sil,
    bildirimleri_okundu_yap,
    gecmis_ekle,
    sistem_ozeti,
)


# =========================================================
# AYARLAR
# =========================================================

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "hurda123")

STALE_MINUTES = 1440

security = HTTPBasic()


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


ISTANBUL = ZoneInfo("Europe/Istanbul")


def now_istanbul():
    return datetime.now(ISTANBUL)


def now_istanbul_string():
    return now_istanbul().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


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
        r"Data valid for\\s+"
        r"(\\d{1,2})\\s+"
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


def lme_verilerini_cek():
    global _LME_CACHE

    simdi = now_istanbul()

    if (
        _LME_CACHE["veriler"]
        and _LME_CACHE["cekilme"]
    ):
        gecen = (
            simdi
            - _LME_CACHE["cekilme"]
        ).total_seconds()

        if gecen < 900:
            return _LME_CACHE

    hatalar = []

    # Birincil kaynak: LME'nin kendi gün gecikmeli JSON
    # veri servisi. HTML sayfasındaki 403 kısıtından bağımsızdır.
    try:
        fiyatlar, tarih = (
            _lme_api_verilerini_cek()
        )

        kaynak = "LME Official Prices · day-delayed API"

    except Exception as exc:
        hatalar.append(
            f"LME API: {exc}"
        )

        # Resmi API erişilemezse mevcut yedek kaynağı dene.
        try:
            fiyatlar, tarih = (
                _westmetall_lme_verilerini_cek()
            )

            kaynak = (
                "Westmetall · Official LME Prices"
            )

        except Exception as yedek_exc:
            hatalar.append(
                f"Westmetall: {yedek_exc}"
            )

            raise RuntimeError(
                "LME verisi alınamadı. "
                + " | ".join(hatalar)
            ) from yedek_exc

    usd_tl = None

    try:
        doviz = doviz_kurlarini_getir()

        usd_tl = (
            doviz.get(
                "veriler",
                {}
            )
            .get(
                "USD",
                {}
            )
            .get(
                "alis"
            )
        )

    except Exception:
        usd_tl = None

    veriler = []

    for key in LME_METALS:
        if key not in fiyatlar:
            continue

        item = dict(
            fiyatlar[key]
        )

        bid = item.get(
            "three_month_bid"
        )

        ask = item.get(
            "three_month_ask"
        )

        if (
            usd_tl is not None
            and bid is not None
            and ask is not None
        ):
            orta = (
                float(bid)
                + float(ask)
            ) / 2

            item["three_month_tl"] = (
                round(
                    orta * float(usd_tl),
                    2,
                )
            )

        else:
            item["three_month_tl"] = None

        veriler.append(
            item
        )

    sonuc = {
        "tarih": tarih or "-",
        "veriler": veriler,
        "cekilme": simdi.strftime(
            "%d.%m.%Y %H:%M:%S"
        ),
        "kaynak": kaynak,
        "usd_tl": usd_tl,
    }

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
    "right_bottom": {
        "title": "Sağ Alt Reklam",
        "image_url": "",
        "target_url": "#",
        "active": True,
    },
    "bottom_left": {
        "title": "Alt Orta Sol Reklam",
        "image_url": "",
        "target_url": "#",
        "active": True,
    },
    "bottom_right": {
        "title": "Alt Orta Sağ Reklam",
        "image_url": "",
        "target_url": "#",
        "active": True,
    },
}


# =========================================================
# YARDIMCI FONKSİYONLAR
# =========================================================

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

    firmalar = firmalari_sirala(
        data
    )

    for index, firma in enumerate(
        firmalar
    ):

        firma_id = firma.get(
            "firma_id"
        )

        if firma_id in data.get(
            "firms",
            {},
        ):

            data["firms"][
                firma_id
            ]["sira"] = index

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


def son_fiyat_degisim(
    data,
    firma_id,
    kalem,
    fiyat,
):

    gecmis = [
        x
        for x in data.get(
            "price_history",
            [],
        )
        if x.get(
            "firma_id"
        ) == firma_id
        and x.get(
            "kalem"
        ) == kalem
    ]

    if len(gecmis) < 2:
        return ""

    onceki = gecmis[
        -2
    ].get(
        "fiyat"
    )

    if onceki is None:
        return ""

    try:

        fark = (
            fiyat - onceki
        )

    except Exception:

        return ""

    if fark > 0:

        return (
            f"+{fark:,}".replace(
                ",",
                ".",
            )
            + " TL"
        )

    if fark < 0:

        return (
            f"{fark:,}".replace(
                ",",
                ".",
            )
            + " TL"
        )

    return "0 TL"


# =========================================================
# ADMİN GİRİŞİ
# =========================================================

def verify_admin(
    credentials: HTTPBasicCredentials = Depends(
        security
    ),
):

    correct_username = secrets.compare_digest(
        credentials.username,
        ADMIN_USER,
    )

    correct_password = secrets.compare_digest(
        credentials.password,
        ADMIN_PASS,
    )

    if not (
        correct_username
        and correct_password
    ):

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yetkisiz erişim!",
            headers={
                "WWW-Authenticate": "Basic"
            },
        )

    return credentials.username


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

        return normalize_ads(
            data
        )

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


# =========================================================
# FİYAT VERİLERİ
# =========================================================

GUNCEL_VERILER = []

SON_GUNCELLEME = "Henüz yapılmadı"


def firma_verisini_cek(
    fonksiyon
):

    sonuc = fonksiyon()

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

    for kalem in sonuc.kalemler:

        kalem_anahtari = (
            " ".join(
                str(kalem.cins or "").strip().split()
            ).casefold()
        )

        if kalem_anahtari in gizlenen_kalemler:
            continue

        degisim = ""

        if kalem.eski_fiyat is not None:

            fark = (
                kalem.fiyat
                - kalem.eski_fiyat
            )

            if fark > 0:

                degisim = (
                    "+"
                    + f"{fark:,}".replace(
                        ",",
                        ".",
                    )
                    + " TL"
                )

            elif fark < 0:

                degisim = (
                    f"{fark:,}".replace(
                        ",",
                        ".",
                    )
                    + " TL"
                )

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

        fiyat_kaydet(
            firma_id=sonuc.firma_id,
            kalem=kalem.cins,
            otomatik_fiyat=kalem.fiyat,
            fiyat_tarihi=fiyat_tarihi,
        )

        gecmis_ekle(
            firma_id=sonuc.firma_id,
            kalem=kalem.cins,
            fiyat=kalem.fiyat,
            fiyat_tarihi=fiyat_tarihi,
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

    firma_siralarini_duzelt(
        data
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


def verileri_guncelle():

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

    for firma_id, kayitli_fonksiyon in kaynaklar:

        try:

            data = load_data()

            firma = data.get(
                "firms",
                {},
            ).get(
                firma_id
            )

            if (
                firma
                and not firma.get(
                    "aktif",
                    True,
                )
            ):

                print(
                    f"PASİF: {firma_id}"
                )

                continue

            if (
                firma
                and not firma.get(
                    "otomatik",
                    True,
                )
            ):

                print(
                    f"MANUEL: {firma_id}"
                )

                continue

            fonksiyon = firma_scraperini_bul(
                firma_id,
                data,
            )

            if fonksiyon is None:
                print(
                    f"OTOMATİK KAYNAK YOK: {firma_id}"
                )
                continue

            sonuc = firma_verisini_cek(
                fonksiyon
            )

            print(
                f"OK: {sonuc['baslik']} "
                f"({len(sonuc['kalemler'])} kalem)"
            )

            bildirim_ekle(
                firma_id,
                "basarili_guncelleme",
                (
                    f"{sonuc['baslik']} başarıyla "
                    f"güncellendi. "
                    f"{len(sonuc['kalemler'])} "
                    "fiyat kalemi okundu."
                ),
            )

        except Exception as e:

            print(
                f"HATA: {firma_id} -> "
                f"{type(e).__name__}: {e}"
            )

            try:

                data = load_data()

                if firma_id in data.get(
                    "firms",
                    {},
                ):

                    data[
                        "firms"
                    ][
                        firma_id
                    ][
                        "durum"
                    ] = "hata"

                    save_data(
                        data
                    )

                bildirim_ekle(
                    firma_id=firma_id,
                    tur="scraper_hatasi",
                    mesaj=str(e),
                )

            except Exception as bildirim_hatasi:

                print(
                    "BİLDİRİM HATASI: "
                    f"{bildirim_hatasi}"
                )

    SON_GUNCELLEME = (
        now_istanbul().strftime(
            "%d.%m.%Y %H:%M:%S"
        )
    )

    GUNCEL_VERILER = []

    # Tarama sırasında oluşan geçici Python nesnelerini
    # mümkün olduğunca hemen temizle.
    gc.collect()

    print(
        "Güncelleme tamamlandı."
    )


# =========================================================
# ANASAYFA FİYAT VERİLERİ
# =========================================================

def fiyat_verilerini_olustur():

    data = load_data()

    sonuc = []

    for firma in firmalari_sirala(
        data
    ):

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

        firma_kalemleri = []

        sirali_fiyatlar = sorted(
            fiyatlar.items(),
            key=lambda item: str(
                item[1].get(
                    "guncelleme",
                    ""
                )
            ),
            reverse=True,
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
            )

            firma_kalemleri.append(
                {
                    "cins": kalem,
                    "fiyat": fiyat_format(
                        kullanilan
                    ),
                    "degisim": degisim,
                    "durum": kaynak_tipi,
                    "otomatik_fiyat": otomatik,
                    "manuel_fiyat": manuel,
                    "fiyat_tarihi": (
                        now_istanbul().strftime("%Y-%m-%d")
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

        sonuc.append(
            {
                "firma_id": firma_id,
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

    return sonuc


# =========================================================
# FASTAPI
# =========================================================

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app):

    load_ads()

    print(
        "İlk fiyat güncellemesi başlıyor..."
    )

    verileri_guncelle()

    scheduler.add_job(
        verileri_guncelle,
        "interval",
        minutes=1,
        id="fiyat_guncelleme",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )

    scheduler.start()

    yield

    if scheduler.running:

        scheduler.shutdown(
            wait=False
        )


app = FastAPI(
    title="Hurda Fiyatları",
    lifespan=lifespan,
)


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
DOVIZ_CACHE_SANIYE = 600


def doviz_kurlarini_getir(force=False):

    global DOVIZ_CACHE
    global DOVIZ_SON_CEKME

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

    API_URL = (
        "https://api.frankfurter.dev/v2/rate"
    )

    bulunan = {}

    try:

        for kod in (
            "USD",
            "EUR",
        ):

            response = requests.get(
                f"{API_URL}/{kod.lower()}/try",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "HurdaFiyatBot/2.0",
                },
                timeout=10,
            )

            response.raise_for_status()

            veri = response.json()

            rate = veri.get(
                "rate"
            )

            tarih = veri.get(
                "date",
                "",
            )

            if rate is None:

                raise ValueError(
                    f"Frankfurter {kod}/TRY kuru boş döndü."
                )

            bulunan[kod] = {
                "kod": kod,
                "birim": "1",
                "alis": float(rate),
                "satis": float(rate),
                "kur": float(rate),
                "kur_turu": "Referans kur",
                "tarih": tarih,
            }

        tarihler = [
            x.get(
                "tarih",
                "",
            )
            for x in bulunan.values()
            if x.get(
                "tarih",
                "",
            )
        ]

        kaynak_tarihi = (
            min(tarihler)
            if tarihler
            else ""
        )

        DOVIZ_CACHE = {
            "status": "success",
            "kaynak": "Frankfurter",
            "kur_turu": "Günlük referans kuru",
            "tarih": kaynak_tarihi,
            "veriler": bulunan,
        }

        DOVIZ_SON_CEKME = simdi

        return DOVIZ_CACHE

    except Exception as e:

        print(
            "DÖVİZ KUR HATASI: "
            f"{type(e).__name__}: {e}"
        )

        if DOVIZ_CACHE:
            return DOVIZ_CACHE

        return {
            "status": "error",
            "kaynak": "Frankfurter",
            "kur_turu": "Günlük referans kuru",
            "tarih": "",
            "veriler": {},
            "hata": (
                "USD/EUR kurları şu anda alınamadı."
            ),
        }


# =========================================================
# FİYAT API
# =========================================================

@app.get(
    "/prices"
)
def get_prices():

    return {
        "status": "success",
        "son_guncelleme": SON_GUNCELLEME,
        "data": fiyat_verilerini_olustur(),
    }


@app.get(
    "/currency"
)
def get_currency():

    return doviz_kurlarini_getir()


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
"""

    return Response(
        content=javascript,
        media_type="application/javascript",
    )


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

<div class="max-w-2xl mx-auto">

<div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6">

<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">

<h1 class="text-2xl font-bold text-slate-900">
Yeni Firma / Kaynak
</h1>

<a
href="/admin"
class="bg-slate-100 px-4 py-2 rounded-xl text-sm font-bold text-center"
>
← Geri
</a>

</div>

<form
method="post"
action="/admin/source/new"
class="space-y-5"
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
class="w-full border border-slate-300 rounded-xl px-4 py-3"
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
class="w-full border border-slate-300 rounded-xl px-4 py-3"
>

</div>

<div>

<label class="block text-sm font-bold mb-2">
Kaynak URL
</label>

<input
type="url"
name="url"
placeholder="İsteğe bağlı: https://..."
class="w-full border border-slate-300 rounded-xl px-4 py-3"
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
class="w-full bg-slate-900 text-white py-3 rounded-xl font-bold"
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
        "sira": mevcut_firma_sayisi,
    }

    firma_siralarini_duzelt(
        data
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
                data["firms"][firma_id]["durum"] = "hata"
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
class="w-full border border-slate-300 rounded-xl px-3 py-2 text-sm"
placeholder="Boş = otomatik"
>

<button
type="submit"
name="manuel_sil"
value="{esc(kalem)}"
formaction="/admin/source/{esc(firma_id)}/manual-delete"
formmethod="post"
formnovalidate
class="w-full mt-2 border border-red-200 bg-red-50 hover:bg-red-100 text-red-700 rounded-xl px-3 py-2 text-sm font-bold"
>
Kalemi Tamamen Sil
</button>

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

<div class="max-w-3xl mx-auto space-y-5 sm:space-y-6">

<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">

<h1 class="text-2xl font-bold text-slate-900 break-words">
{esc(firma.get("baslik", firma_id))}
</h1>

<a
href="/admin"
class="bg-white border border-slate-200 px-4 py-2 rounded-xl text-sm font-bold text-center"
>
← Geri
</a>

</div>

<div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6">

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
class="w-full border border-slate-300 rounded-xl px-4 py-3"
>

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
class="w-full border border-slate-300 rounded-xl px-4 py-3"
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
{"disabled" if not scraper_var else ""}
class="w-5 h-5"
>

<label class="font-semibold">
Otomatik fiyat çek
</label>

</div>

<div class="text-xs text-slate-500">
Scraper:
{"Mevcut" if scraper_var else "Yok - manuel kullanım"}
</div>

<button
type="submit"
class="w-full bg-slate-900 text-white py-3 rounded-xl font-bold"
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
class="w-full border border-blue-200 bg-blue-50 hover:bg-blue-100 text-blue-800 py-3 rounded-xl font-bold"
>
🔎 Kaynağı Test Et
</button>

</form>

</div>

<div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6">

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
class="w-full bg-blue-600 hover:bg-blue-700 text-white py-3 rounded-xl font-bold"
>
Manuel Fiyatları Kaydet
</button>

</form>

</div>

<div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6">

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

    if "sira" not in data[
        "firms"
    ][
        firma_id
    ]:

        data[
            "firms"
        ][
            firma_id
        ][
            "sira"
        ] = len(
            data[
                "firms"
            ]
        )

    firma_siralarini_duzelt(
        data
    )

    save_data(
        data
    )

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

        try:

            fonksiyon = firma_scraperini_bul(
                firma_id,
                data,
            )

            if fonksiyon is not None:
                sonuc = firma_verisini_cek(
                    fonksiyon
                )

                bildirim_ekle(
                    firma_id,
                    "basarili_guncelleme",
                    (
                        "Kaynak ayarları kaydedildi ve ilk "
                        "otomatik çekim başarılı oldu. "
                        f"{len(sonuc['kalemler'])} fiyat kalemi okundu."
                    ),
                )

        except Exception as e:

            data = load_data()

            if firma_id in data.get(
                "firms",
                {},
            ):

                data["firms"][firma_id]["durum"] = "hata"

                save_data(
                    data
                )

            bildirim_ekle(
                firma_id,
                "kaynak_testi_hatasi",
                (
                    "Kaynak ayarları kaydedildi fakat "
                    f"otomatik çekim başarısız oldu: {e}"
                ),
            )

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

        firmalar[
            mevcut_index
        ], firmalar[
            yeni_index
        ] = (
            firmalar[
                yeni_index
            ],
            firmalar[
                mevcut_index
            ],
        )

    for index, firma in enumerate(
        firmalar
    ):

        id_degeri = firma.get(
            "firma_id"
        )

        if id_degeri in data.get(
            "firms",
            {},
        ):

            data[
                "firms"
            ][
                id_degeri
            ][
                "sira"
            ] = index

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

    data["price_history"] = [
        item
        for item in data.get(
            "price_history",
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

            data[
                "firms"
            ][
                firma_id
            ][
                "durum"
            ] = "hata"

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
):

    data = load_data()

    firma_siralarini_duzelt(
        data
    )

    save_data(
        data
    )

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

        yukari_disabled = (
            index == 0
        )

        asagi_disabled = (
            index == len(
                firmalar
            ) - 1
        )

        firma_rows += f"""
<div class="border border-slate-200 rounded-2xl p-4">

<div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">

<div class="min-w-0 flex-1">

<div class="font-bold text-slate-900 break-words">
{esc(baslik)}
</div>

<div class="text-xs text-slate-500 mt-1 break-all">
ID: {esc(firma_id)}
</div>

<div class="text-xs text-slate-500 mt-1 break-words">
Son başarılı çekim: {esc(son_cekim)}
</div>

<div class="flex flex-wrap gap-2 mt-3">

{durum_html}

<span class="px-2 py-1 rounded-lg bg-slate-100 text-slate-700 text-xs font-bold">
{"AKTİF" if firma_aktif else "PASİF"}
</span>

<span class="px-2 py-1 rounded-lg bg-indigo-100 text-indigo-800 text-xs font-bold">
{"OTOMATİK" if otomatik else "MANUEL"}
</span>

</div>

</div>

<div class="flex flex-wrap gap-2 items-center">

<form
method="post"
action="/admin/source/{esc(firma_id)}/move"
class="inline"
>

<input
type="hidden"
name="direction"
value="up"
>

<button
type="submit"
title="Yukarı taşı"
{"disabled" if yukari_disabled else ""}
class="w-10 h-10 rounded-lg border border-slate-200 bg-slate-50 text-slate-700 font-black text-lg {"opacity-40 cursor-not-allowed" if yukari_disabled else "hover:bg-slate-200"}"
>
↑
</button>

</form>

<form
method="post"
action="/admin/source/{esc(firma_id)}/move"
class="inline"
>

<input
type="hidden"
name="direction"
value="down"
>

<button
type="submit"
title="Aşağı taşı"
{"disabled" if asagi_disabled else ""}
class="w-10 h-10 rounded-lg border border-slate-200 bg-slate-50 text-slate-700 font-black text-lg {"opacity-40 cursor-not-allowed" if asagi_disabled else "hover:bg-slate-200"}"
>
↓
</button>

</form>

<a
href="/admin/source/{esc(firma_id)}"
class="px-3 py-2 rounded-lg bg-slate-900 text-white text-xs font-bold"
>
Düzenle
</a>

<form
method="post"
action="/admin/source/{esc(firma_id)}/delete"
onsubmit="return confirm('Bu firmayı silmek istediğinizden emin misiniz?');"
>

<button
type="submit"
class="px-3 py-2 rounded-lg bg-red-50 text-red-700 text-xs font-bold"
>
Sil
</button>

</form>

</div>

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

        notification_rows += f"""
<div class="border border-slate-200 rounded-xl p-4">

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
            "Sol Üst",
        ),
        (
            "left_bottom",
            "Sol Alt",
        ),
        (
            "right_top",
            "Sağ Üst",
        ),
        (
            "right_bottom",
            "Sağ Alt",
        ),
        (
            "bottom_left",
            "Alt Orta Sol",
        ),
        (
            "bottom_right",
            "Alt Orta Sağ",
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
Mevcut Banner
</div>

<div class="bg-slate-50 border border-slate-200 rounded-xl p-2">
<img
src="{esc(ad.get("image_url", ""))}"
alt="{esc(ad.get("title", label))}"
class="w-full max-h-72 object-contain rounded-lg"
>
</div>

</div>
"""

        ad_form_fields += f"""
<div class="border border-slate-200 rounded-2xl p-4 sm:p-5 space-y-3">

<div class="font-bold text-slate-900 text-lg">
{label}
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

html,
body {{
    width: 100%;
    max-width: 100%;
    overflow-x: hidden;
}}

* {{
    box-sizing: border-box;
}}

.break-anywhere {{
    overflow-wrap: anywhere;
    word-break: break-word;
}}

</style>

</head>

<body class="bg-slate-100 min-h-screen p-3 sm:p-4">

<div class="max-w-6xl mx-auto space-y-5 sm:space-y-6">

<div class="flex flex-col md:flex-row md:items-center md:justify-between gap-3">

<div class="min-w-0">

<h1 class="text-2xl sm:text-3xl font-bold text-slate-900">
Hurda Fiyatları
</h1>

<p class="text-sm text-slate-500 mt-1">
Yönetim Paneli
</p>

</div>

<div class="flex flex-wrap gap-2">

<a
href="/"
class="bg-white border border-slate-200 px-4 py-2.5 rounded-xl text-sm font-bold"
>
← Ana Sayfa
</a>

<a
href="/admin/source/new"
class="bg-slate-900 text-white px-4 py-2.5 rounded-xl text-sm font-bold"
>
+ Yeni Kaynak
</a>

</div>

</div>

<div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">

<div class="bg-white rounded-2xl p-4 shadow-sm">

<div class="text-xs text-slate-500">
Toplam Firma
</div>

<div class="text-2xl font-bold">
{len(firmalar)}
</div>

</div>

<div class="bg-white rounded-2xl p-4 shadow-sm">

<div class="text-xs text-slate-500">
Aktif
</div>

<div class="text-2xl font-bold text-emerald-600">
{aktif}
</div>

</div>

<div class="bg-white rounded-2xl p-4 shadow-sm">

<div class="text-xs text-slate-500">
Başarılı
</div>

<div class="text-2xl font-bold text-blue-600">
{basarili}
</div>

</div>

<div class="bg-white rounded-2xl p-4 shadow-sm">

<div class="text-xs text-slate-500">
Hata / Stale
</div>

<div class="text-2xl font-bold text-red-600">
{hatali}
</div>

</div>

<div class="bg-white rounded-2xl p-4 shadow-sm">

<div class="text-xs text-slate-500">
Manuel
</div>

<div class="text-2xl font-bold text-indigo-600">
{manuel}
</div>

</div>

</div>

<div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6">

<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-5">

<h2 class="text-xl font-bold">
Firmalar
</h2>

<div class="text-sm text-slate-500">
Toplam: {len(firmalar)}
</div>

</div>

<div class="mb-4 bg-blue-50 border border-blue-200 text-blue-800 rounded-xl p-3 text-sm">
💡 Firmaların ana sayfadaki sırasını değiştirmek için
<strong>↑</strong> ve <strong>↓</strong> butonlarını kullanın.
</div>

<div class="space-y-3">

{firma_rows}

</div>

</div>

<div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6">

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

</div>

</div>

<div class="space-y-3">

{notification_rows}

</div>

</div>

<div class="bg-white rounded-2xl shadow-sm p-4 sm:p-6">

<div class="flex flex-col md:flex-row md:items-center md:justify-between gap-2 mb-5">

<div>

<h2 class="text-xl font-bold">
Banner Yönetimi
</h2>

<p class="text-sm text-slate-500 mt-1">
Ana sayfanın sağ, sol ve sayfanın en alt orta bölümündeki bannerları buradan yönetebilirsiniz.
</p>

</div>

<div class="text-xs bg-indigo-50 text-indigo-700 px-3 py-2 rounded-xl font-semibold">
6 Banner Alanı
</div>

</div>

<form
method="post"
action="/admin/update-ads"
enctype="multipart/form-data"
class="space-y-5"
>

{ad_form_fields}

<button
type="submit"
class="w-full bg-slate-900 hover:bg-slate-800 text-white py-4 rounded-xl font-bold"
>
Bannerları Kaydet
</button>

</form>

</div>

</div>

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

    data = {}

    allowed_extensions = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }

    for key in DEFAULT_ADS.keys():

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
def read_root():

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
content="#0f172a"
>

<meta
name="description"
content="Güncel hurda ve demir çelik fiyatları."
>

<title>Hurda Fiyatları</title>

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
    background: #f1f5f9;
}

.price-card {
    width: 100%;
    min-width: 0;
    transition:
        transform .2s ease,
        box-shadow .2s ease,
        border-color .2s ease;
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
}

.desktop-ad-column .ad-box,
#bottomAds .ad-box {
    height: 270px;
}

.desktop-ad-column .ad-box img,
#bottomAds .ad-box img {
    width: 100%;
    height: 270px;
    min-height: 270px;
    max-height: 270px;
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
    height: 270px;
    min-height: 230px;
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

</style>

</head>

<body class="min-h-screen">

<div class="w-full max-w-7xl mx-auto px-3 sm:px-4 py-4 sm:py-5">

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

<div class="flex items-end justify-between gap-3 mb-3 px-1">

<div>
<div class="text-[10px] sm:text-[11px] font-black uppercase tracking-[0.14em] text-slate-500">
Döviz Piyasası
</div>

<div class="text-base sm:text-lg font-black text-slate-900 mt-0.5">
USD / TL · EUR / TL
</div>
</div>

<div class="hidden sm:flex items-center gap-2 rounded-xl bg-white border border-slate-200 px-3 py-1.5 text-[10px] font-bold text-slate-500 shadow-sm">
<span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
Günlük referans
</div>

</div>

<div class="grid grid-cols-1 lg:grid-cols-2 gap-3">

<div class="relative overflow-hidden bg-white rounded-2xl shadow-sm border border-slate-200 p-3.5 sm:p-4">

<div class="absolute right-0 top-0 w-24 h-24 rounded-full bg-slate-50 -translate-y-9 translate-x-9"></div>

<div class="relative flex items-center justify-between gap-3">

<div class="flex items-center gap-3">

<div class="w-9 h-9 sm:w-10 sm:h-10 rounded-xl bg-slate-900 text-white flex items-center justify-center text-lg shadow-sm">
🇺🇸
</div>

<div>
<div class="text-[9px] font-black uppercase tracking-wide text-slate-400">
Amerikan Doları
</div>
<div class="text-sm sm:text-base font-black text-slate-900">
USD / TRY
</div>
</div>

</div>

<div class="rounded-lg bg-slate-50 border border-slate-200 px-2 py-1 text-[10px] font-black text-slate-500">
USD
</div>

</div>

<div class="relative mt-3">
<div class="text-[10px] uppercase tracking-wide text-slate-400 font-black">
Referans Kur
</div>

<div
id="usdAlis"
class="text-xl sm:text-2xl font-black tracking-tight text-slate-950 mt-0.5"
>
Yükleniyor...
</div>

<div class="text-[10px] text-slate-400 mt-1">
1 USD = Türk Lirası
</div>
</div>

<div class="relative mt-2.5 pt-2.5 border-t border-slate-100 flex items-center justify-between gap-3">
<div class="text-[10px] font-bold text-slate-400">
Günlük oran
</div>

<div
id="usdSatis"
class="text-xs sm:text-sm font-black text-slate-700"
>
Yükleniyor...
</div>
</div>

</div>

<div class="relative overflow-hidden bg-white rounded-2xl shadow-sm border border-slate-200 p-3.5 sm:p-4">

<div class="absolute right-0 top-0 w-24 h-24 rounded-full bg-slate-50 -translate-y-9 translate-x-9"></div>

<div class="relative flex items-center justify-between gap-3">

<div class="flex items-center gap-3">

<div class="w-9 h-9 sm:w-10 sm:h-10 rounded-xl bg-slate-900 text-white flex items-center justify-center text-lg shadow-sm">
🇪🇺
</div>

<div>
<div class="text-[9px] font-black uppercase tracking-wide text-slate-400">
Euro
</div>
<div class="text-sm sm:text-base font-black text-slate-900">
EUR / TRY
</div>
</div>

</div>

<div class="rounded-lg bg-slate-50 border border-slate-200 px-2 py-1 text-[10px] font-black text-slate-500">
EUR
</div>

</div>

<div class="relative mt-3">
<div class="text-[10px] uppercase tracking-wide text-slate-400 font-black">
Referans Kur
</div>

<div
id="eurAlis"
class="text-xl sm:text-2xl font-black tracking-tight text-slate-950 mt-0.5"
>
Yükleniyor...
</div>

<div class="text-[10px] text-slate-400 mt-1">
1 EUR = Türk Lirası
</div>
</div>

<div class="relative mt-2.5 pt-2.5 border-t border-slate-100 flex items-center justify-between gap-3">
<div class="text-[10px] font-bold text-slate-400">
Günlük oran
</div>

<div
id="eurSatis"
class="text-xs sm:text-sm font-black text-slate-700"
>
Yükleniyor...
</div>
</div>

</div>

</div>

<div
id="currencyInfo"
class="mt-1.5 px-1 text-[10px] sm:text-[11px] text-slate-400"
>
Kur kaynağı: TCMB · Güncelleniyor...
</div>

</section>


<!-- =====================================================
     LME METAL FİYATLARI
     ===================================================== -->

<section
id="lmeSection"
class="bg-white rounded-2xl shadow-sm border border-slate-200 p-4 sm:p-5 mb-4 sm:mb-5"
>

<button
type="button"
id="lmeToggle"
class="w-full text-left flex items-center justify-between gap-3 mb-0 hover:bg-slate-50 rounded-2xl p-2 -m-2 transition"
aria-expanded="false"
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
class="hidden mt-4"
aria-hidden="true"
>

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
Alış (Nakit)
</th>
<th class="px-3 sm:px-4 py-3 text-right text-[10px] sm:text-[11px] uppercase tracking-wide font-black text-slate-500">
Satış (Nakit)
</th>
<th class="px-3 sm:px-4 py-3 text-right text-[10px] sm:text-[11px] uppercase tracking-wide font-black text-slate-500">
3 Ay Alış
</th>
<th class="px-3 sm:px-4 py-3 text-right text-[10px] sm:text-[11px] uppercase tracking-wide font-black text-slate-500">
3 Ay Satış
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

<div
id="mobileAds"
class="mobile-ad-grid grid grid-cols-2 gap-3 lg:hidden mb-4"
>

<!-- MOBILE_LEFT_TOP_AD -->

<!-- MOBILE_LEFT_BOTTOM_AD -->

<!-- MOBILE_RIGHT_TOP_AD -->

<!-- MOBILE_RIGHT_BOTTOM_AD -->

</div>




<div class="grid grid-cols-1 lg:grid-cols-[270px_minmax(0,1fr)_270px] gap-4 sm:gap-5">

<aside class="desktop-ad-column hidden lg:block space-y-6 pt-16">

<div
class="ad-box rounded-2xl overflow-hidden"
id="leftTopAd"
>
</div>

<div
class="ad-box rounded-2xl overflow-hidden"
id="leftBottomAd"
>
</div>

</aside>

<main class="min-w-0 w-full">

<div class="text-center mb-3 sm:mb-4 px-1">
<div class="text-[10px] sm:text-[11px] font-black uppercase tracking-[0.14em] text-slate-500">
Güncel Hurda Fiyatları
</div>
<div class="text-lg sm:text-xl font-black text-slate-900 mt-1">
Fabrika Fiyatları
</div>
<div class="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 mt-1.5 text-[10px] font-bold text-slate-400">
<span class="inline-flex items-center gap-1.5">
<span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
Canlı takip
</span>
<span class="text-slate-300">•</span>
<span>Firmaların son yayınladığı fiyatlar</span>
</div>
</div>

<div
id="loading"
class="bg-white rounded-2xl p-6 sm:p-8 text-center text-slate-500"
>
Firmalar yükleniyor...
</div>

<div
id="firmaListesi"
class="grid grid-cols-1 sm:grid-cols-2 items-start gap-3 sm:gap-4 w-full min-w-0"
>
</div>

</main>

<aside class="desktop-ad-column hidden lg:block space-y-6 pt-16">

<div
class="ad-box rounded-2xl overflow-hidden"
id="rightTopAd"
>
</div>

<div
class="ad-box rounded-2xl overflow-hidden"
id="rightBottomAd"
>
</div>

</aside>

</div>

<div
id="bottomAds"
class="grid grid-cols-1 sm:grid-cols-2 gap-6 max-w-4xl mx-auto mt-5"
>

<div
class="ad-box rounded-2xl overflow-hidden"
id="bottomLeftAd"
>
</div>

<div
class="ad-box rounded-2xl overflow-hidden"
id="bottomRightAd"
>
</div>

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


async function lmeFiyatlariniGetir() {
    
    const lmeToggle =
        document.getElementById(
            "lmeToggle"
        );

    if (lmeToggle) {
        lmeToggle.addEventListener(
            "click",
            lmeAcKapat
        );
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

        tableBody.innerHTML = "";

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
                        formatFiyat(
                            item.cash_bid
                        ) +
                    "</td>" +

                    '<td class="px-3 sm:px-4 py-3 text-right font-semibold text-slate-700 whitespace-nowrap">' +
                        formatFiyat(
                            item.cash_ask
                        ) +
                    "</td>" +

                    '<td class="px-3 sm:px-4 py-3 text-right font-semibold text-slate-700 whitespace-nowrap">' +
                        formatFiyat(
                            item.three_month_bid
                        ) +
                    "</td>" +

                    '<td class="px-3 sm:px-4 py-3 text-right font-semibold text-slate-700 whitespace-nowrap">' +
                        formatFiyat(
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
                        result.usd_tl
                      ).toLocaleString(
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
                '<td colspan="6" class="px-4 py-6 text-center text-sm text-amber-800 bg-amber-50">' +
                    "LME verisi şu anda alınamıyor: "
                    + escapeHtml(
                        error.message
                        || "Bilinmeyen hata."
                    ) +
                "</td>" +
            "</tr>";

        info.textContent =
            "LME verisi alınamadı.";

        console.error(
            "LME:",
            error
        );
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

        if (
            result.status !== "success"
            || !usd
            || !eur
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

        document.getElementById("currencyInfo").textContent =
            "Kur kaynağı: " + (result.kaynak || "Frankfurter") + " · "
            + (result.tarih || "-");

    }
    catch (error) {

        document.getElementById("usdAlis").textContent = "-";
        document.getElementById("usdSatis").textContent = "-";
        document.getElementById("eurAlis").textContent = "-";
        document.getElementById("eurSatis").textContent = "-";

        document.getElementById("currencyInfo").textContent =
            "Kur bilgisi şu anda alınamıyor.";

        console.error(
            "Döviz kurları:",
            error
        );

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

        firmalar.forEach(function(item, index) {

            const wrapper = document.createElement("div");
            wrapper.className =
                "price-card bg-white rounded-2xl sm:rounded-3xl shadow-sm border border-slate-200 overflow-hidden h-fit hover:shadow-md transition-all duration-200";

            if (
                index === firmalar.length - 1
                && firmalar.length % 2 === 1
            ) {
                wrapper.classList.add(
                    "sm:col-span-2",
                    "sm:w-1/2",
                    "sm:justify-self-center"
                );
            }

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

                rows +=
                    '<div class="price-row flex items-center justify-between gap-3 py-3.5 sm:py-4 border-b border-slate-100 last:border-0">' +

                        '<div class="price-name min-w-0 pr-2">' +

                            '<div class="font-bold text-slate-800 text-sm sm:text-[15px] leading-5 break-words">' +
                                escapeHtml(kalem.cins) +
                            "</div>" +

                            '<div class="flex flex-wrap items-center gap-1.5 mt-1.5">' +

                                '<span class="text-[9px] uppercase tracking-wide text-slate-400 font-bold">Tarih</span>' +

                                '<span class="text-[10px] font-bold text-slate-500">' +
                                    escapeHtml(kalem.fiyat_tarihi || "-") +
                                "</span>" +

                                '<span class="ml-0.5">' +
                                    durumEtiketi(kalem.durum) +
                                "</span>" +

                            "</div>" +

                        "</div>" +

                        '<div class="price-value text-right shrink-0 min-w-[105px] sm:min-w-[125px]">' +

                            '<div class="font-black text-slate-950 text-lg sm:text-xl leading-tight whitespace-nowrap">' +
                                escapeHtml(kalem.fiyat) +
                            "</div>" +

                            (
                                degisimHtml
                                    ? '<div class="mt-1.5 flex justify-end">' +
                                        degisimHtml +
                                      "</div>"
                                    : ""
                            ) +

                        "</div>" +

                    "</div>";
            });

            const kaynakLink = item.url
                ? '<a href="' +
                    escapeHtml(item.url) +
                    '" target="_blank" rel="noopener noreferrer" ' +
                    'class="inline-flex items-center gap-1.5 rounded-xl bg-white border border-slate-200 px-2.5 py-1.5 text-[10px] font-black text-slate-600 hover:bg-slate-100 hover:border-slate-300 transition">' +
                        "Kaynak ↗" +
                  "</a>"
                : '<span class="inline-flex items-center rounded-xl bg-white border border-slate-200 px-2.5 py-1.5 text-[10px] font-bold text-slate-400">' +
                    "Manuel fiyat" +
                  "</span>";

            wrapper.innerHTML =
                '<button type="button" class="firma-toggle w-full min-h-[112px] sm:min-h-[116px] text-left p-4 sm:p-4 hover:bg-slate-50 transition" data-panel="' +
                    panelId +
                    '" aria-expanded="false">' +

                    '<div class="flex items-center justify-between gap-3 sm:gap-4">' +

                        '<div class="min-w-0 flex-1">' +

                            '<div class="flex items-center gap-2.5">' +

                                '<div class="w-9 h-9 sm:w-10 sm:h-10 rounded-xl bg-slate-900 text-white flex items-center justify-center text-[11px] sm:text-xs font-black shadow-sm shrink-0">' +
                                    String(index + 1).padStart(2, "0") +
                                "</div>" +

                                '<div class="min-w-0">' +

                                    '<div class="flex items-center gap-2 flex-wrap">' +

                                        '<h2 class="font-black text-[15px] sm:text-lg text-slate-950 leading-5 break-words">' +
                                            escapeHtml(item.baslik) +
                                        "</h2>" +

                                        durumEtiketi(item.durum) +

                                    "</div>" +

                                    '<div class="flex items-center gap-1.5 mt-1">' +
                                        '<span class="w-1.5 h-1.5 rounded-full bg-slate-300"></span>' +
                                        '<span class="text-[10px] sm:text-[11px] text-slate-500 font-semibold break-words">' +
                                            "Fiyat tarihi: " +
                                            escapeHtml(item.tarih || "-") +
                                        "</span>" +
                                        '<span class="text-slate-300">•</span>' +
                                        '<span class="text-[10px] text-slate-400 font-bold">' +
                                            kalemSayisi +
                                            " kalem" +
                                        "</span>" +
                                    "</div>" +

                                "</div>" +

                            "</div>" +

                        "</div>" +

                        (
                            artisSayisi || dususSayisi
                                ? '<div class="hidden sm:flex items-center gap-2 text-[10px] font-black mr-1">' +
                                    (
                                        artisSayisi
                                            ? '<span class="text-emerald-600">▲ ' + artisSayisi + "</span>"
                                            : ""
                                    ) +
                                    (
                                        artisSayisi && dususSayisi
                                            ? '<span class="text-slate-300">•</span>'
                                            : ""
                                    ) +
                                    (
                                        dususSayisi
                                            ? '<span class="text-red-600">▼ ' + dususSayisi + "</span>"
                                            : ""
                                    ) +
                                  "</div>"
                                : ""
                        ) +

                        '<div class="shrink-0 flex items-center gap-2">' +

                            '<span class="hidden md:inline-flex items-center rounded-xl bg-slate-50 border border-slate-200 px-2.5 py-1.5 text-[10px] font-black text-slate-500">' +
                                "Fiyatları Gör" +
                            "</span>" +

                            '<span class="firma-ok-icon w-9 h-9 rounded-xl border border-slate-200 bg-white text-slate-400 flex items-center justify-center text-sm transition-transform shadow-sm">' +
                                "▼" +
                            "</span>" +

                        "</div>" +

                    "</div>" +

                "</button>" +

                '<div id="' +
                    panelId +
                    '" class="hidden border-t border-slate-200 bg-slate-50/60">' +

                    '<div class="p-3 sm:p-4">' +

                        '<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-2.5">' +

                            '<div>' +
                                '<div class="text-[9px] uppercase tracking-[0.14em] font-black text-slate-400">Fiyatlar</div>' +
                                '<div class="text-sm font-black text-slate-800 mt-0.5">Güncel liste</div>' +
                            "</div>" +

                            '<div class="shrink-0">' +
                                kaynakLink +
                            "</div>" +

                        "</div>" +

                        '<div class="bg-white rounded-2xl border border-slate-200 px-3 sm:px-4 shadow-sm">' +
                            rows +
                        "</div>" +

                    "</div>" +

                "</div>";

            firmaListesi.appendChild(
                wrapper
            );

            const toggle =
                wrapper.querySelector(
                    ".firma-toggle"
                );

            toggle.addEventListener(
                "click",
                function() {

                    const panel =
                        document.getElementById(
                            panelId
                        );

                    const open =
                        !panel.classList.contains(
                            "hidden"
                        );

                    panel.classList.toggle(
                        "hidden",
                        open
                    );

                    toggle.setAttribute(
                        "aria-expanded",
                        String(!open)
                    );

                    const icon =
                        toggle.querySelector(
                            ".firma-ok-icon"
                        );

                    if (icon) {

                        icon.style.transform =
                            open
                                ? "rotate(0deg)"
                                : "rotate(180deg)";

                    }

                }
            );

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


setInterval(
    dovizleriGetir,
    600000
);


setInterval(
    lmeFiyatlariniGetir,
    900000
);


setInterval(
    fiyatlariGetir,
    60000
);

</script>

<script>

function bosReklamAlanlariniGizle() {

    document.querySelectorAll(
        ".desktop-ad-column"
    ).forEach(
        function(aside) {

            const kutular =
                aside.querySelectorAll(
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
                aside.style.display = "none";
            }

        }
    );

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

</body>

</html>
"""

    # =====================================================
    # BANNERLARI ANA SAYFAYA YERLEŞTİR
    # =====================================================

    reklamlar = {
        "leftTopAd": ads[
            "left_top"
        ],
        "leftBottomAd": ads[
            "left_bottom"
        ],
        "rightTopAd": ads[
            "right_top"
        ],
        "rightBottomAd": ads[
            "right_bottom"
        ],
        "bottomLeftAd": ads[
            "bottom_left"
        ],
        "bottomRightAd": ads[
            "bottom_right"
        ],
    }

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

    # =====================================================
    # MOBİL REKLAMLARI YERLEŞTİR
    # =====================================================

    mobile_reklamlar = [
        (
            "<!-- MOBILE_LEFT_TOP_AD -->",
            ads["left_top"],
        ),
        (
            "<!-- MOBILE_LEFT_BOTTOM_AD -->",
            ads["left_bottom"],
        ),
        (
            "<!-- MOBILE_RIGHT_TOP_AD -->",
            ads["right_top"],
        ),
        (
            "<!-- MOBILE_RIGHT_BOTTOM_AD -->",
            ads["right_bottom"],
        ),
    ]

    for placeholder, reklam in mobile_reklamlar:

        page = page.replace(
            placeholder,
            ad_html(
                reklam
            ),
            1,
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
