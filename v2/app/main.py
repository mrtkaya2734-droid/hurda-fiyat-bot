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

    sonuc = fonksiyon()

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

    for firma_id, kayitli_fonksiyon in kaynaklar:

        try:

            data = load_data()

            if firma_id in data.get(
                "silinen_firmalar",
                [],
            ):
                print(
                    f"SİLİNMİŞ: {firma_id}"
                )
                continue

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

            # Başarılı güncelleme bildirimi yalnızca gerçekten
            # fiyat değişikliği olduğunda oluşturulur. Böylece dakika
            # başına aynı "başarılı" bildiriminin birikmesi engellenir.
            fiyat_degisti = any(
                str(kalem.get("degisim", "")).strip()
                not in {"", "0 TL"}
                for kalem in sonuc.get("kalemler", [])
            )

            if fiyat_degisti:
                bildirim_ekle(
                    firma_id,
                    "basarili_guncelleme",
                    (
                        f"{sonuc['baslik']} fiyatları değişti. "
                        f"{len(sonuc['kalemler'])} "
                        "fiyat kalemi güncellendi."
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

        # Ana sayfada kayıtlı fiyatı bulunan firmalar "GÜNCEL" olarak
        # gösterilir. Bu yalnızca ekrandaki etiketi değiştirir;
        # fiyatın kendisi, son başarılı çekim tarihi ve otomatik
        # güncelleme mekanizması değiştirilmez.
        if fiyatlar:
            stale = False

        firma_kalemleri = []

        # Kaynakta gelen / kayıtlı kalem sırasını koru.
        # Güncelleme tarihine göre sıralamak, fiyat kalemlerinin
        # doğal sırasını bozuyordu.
        sirali_fiyatlar = list(
            fiyatlar.items()
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

    if AUTO_UPDATE_ENABLED:
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

        # Gram 24 ayar altın: XAU ons fiyatı USD/ons -> TRY/gram.
        gold_response = requests.get(
            "https://api.gold-api.com/price/XAU",
            headers={
                "Accept": "application/json",
                "User-Agent": "HurdaFiyatBot/2.0",
            },
            timeout=10,
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
    data = load_data()
    now = datetime.now()
    sonuc = []

    # Son 24 saatteki her firma/kalem için son iki farklı fiyatı bul.
    gruplar = {}

    for item in data.get("history", []):
        firma_id = str(item.get("firma_id", "")).strip().lower()
        kalem = str(item.get("kalem", "")).strip()

        if not firma_id or not kalem:
            continue

        anahtar = (firma_id, kalem)
        gruplar.setdefault(anahtar, []).append(item)

    for (firma_id, kalem), kayitlar in gruplar.items():
        sonlar = list(reversed(kayitlar))
        bulunan = []

        for item in sonlar:
            try:
                zaman = datetime.fromisoformat(
                    str(item.get("tarih", "")).replace("Z", "")
                )
            except Exception:
                continue

            if (
                now.replace(tzinfo=None) - zaman
            ).total_seconds() > 24 * 60 * 60:
                break

            fiyat = item.get("fiyat")
            if fiyat is None:
                continue

            if not bulunan or bulunan[-1].get("fiyat") != fiyat:
                bulunan.append(item)

            if len(bulunan) >= 2:
                break

        if len(bulunan) < 2:
            continue

        yeni = bulunan[0].get("fiyat")
        eski = bulunan[1].get("fiyat")

        try:
            fark = float(yeni) - float(eski)
        except (TypeError, ValueError):
            continue

        if fark == 0:
            continue

        firma = data.get("firms", {}).get(firma_id, {})

        sonuc.append({
            "firma_id": firma_id,
            "firma": firma.get("baslik", firma_id),
            "kalem": kalem,
            "eski": eski,
            "yeni": yeni,
            "fark": fark,
            "tarih": bulunan[0].get("tarih"),
        })

    sonuc.sort(
        key=lambda item: str(item.get("tarih", "")),
        reverse=True,
    )

    return {
        "status": "success",
        "data": sonuc[:30],
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
    base = str(
        request.base_url
    ).rstrip("/")

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
        hedef_sira - 1,
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
):

    data = load_data()

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

        firma_rows += f"""\n<div data-firma-row="{esc(str(baslik) + " " + str(firma_id))}" class="border border-slate-200 bg-slate-50/50 hover:bg-white hover:shadow-md rounded-2xl p-4 sm:p-5 transition">

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

<div class="flex items-center gap-2 mt-2">
<form
method="post"
action="/admin/source/{esc(firma_id)}/order"
class="flex items-center gap-2"
>
<label class="text-xs text-indigo-700 font-black whitespace-nowrap">
Ana Sayfa Sırası
</label>
<input
type="number"
name="sira"
value="{index + 1}"
min="1"
step="1"
required
class="w-20 h-9 rounded-lg border border-indigo-200 bg-white px-2 text-sm font-black text-indigo-800 text-center outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
>
<button
type="submit"
class="h-9 px-3 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-black transition"
>
Kaydet
</button>
</form>
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
class="px-3 py-2 rounded-lg bg-slate-900 hover:bg-slate-800 text-white text-xs font-bold transition"
>
Düzenle / Güncelle
</a>

<form
method="post"
action="/admin/source/{esc(firma_id)}/test"
class="inline"
>
<button
type="submit"
class="px-3 py-2 rounded-lg bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 text-xs font-bold transition"
>
Test Et
</button>
</form>

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
            "Sol Üst",
        ),
        (
            "left_middle",
            "Sol Orta",
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
            "right_middle",
            "Sağ Orta",
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

<div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">

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

<div class="bg-white rounded-3xl shadow-sm border border-slate-200 p-4 sm:p-6 lg:p-7">
<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
<div>
<h2 class="text-xl font-bold">Sistem Sağlığı</h2>
<p class="text-sm text-slate-500 mt-1">Otomatik güncelleme ve veri geçmişinin hızlı özeti.</p>
</div>
<div class="text-xs bg-emerald-50 text-emerald-700 px-3 py-2 rounded-xl font-black">
Yedekleme: AKTİF
</div>
</div>
<div class="grid grid-cols-2 md:grid-cols-4 gap-3">
<div class="rounded-2xl bg-slate-50 border border-slate-200 p-3"><div class="text-[10px] text-slate-500 font-bold">Fiyat kalemi</div><div class="text-xl font-black mt-1">{sum(len(x) for x in data.get("prices", {}).values() if isinstance(x, dict))}</div></div>
<div class="rounded-2xl bg-slate-50 border border-slate-200 p-3"><div class="text-[10px] text-slate-500 font-bold">Geçmiş kaydı</div><div class="text-xl font-black mt-1">{len(data.get("history", []))}</div></div>
<div class="rounded-2xl bg-slate-50 border border-slate-200 p-3"><div class="text-[10px] text-slate-500 font-bold">Bildirim</div><div class="text-xl font-black mt-1">{len(notifications)}</div></div>
<div class="rounded-2xl bg-slate-50 border border-slate-200 p-3"><div class="text-[10px] text-slate-500 font-bold">Otomatik takip</div><div class="text-xl font-black mt-1">{"AÇIK" if AUTO_UPDATE_ENABLED else "KAPALI"}</div></div>
</div>
</div>

<div class="bg-white rounded-3xl shadow-sm border border-slate-200 p-4 sm:p-6 lg:p-7">

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

<div class="space-y-3">

{firma_rows}

</div>

</div>

<div class="bg-white rounded-3xl shadow-sm border border-slate-200 p-4 sm:p-6 lg:p-7">

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

<div class="bg-white rounded-3xl shadow-sm border border-slate-200 p-4 sm:p-6 lg:p-7">

<div class="flex flex-col md:flex-row md:items-center md:justify-between gap-2 mb-5">

<div>

<h2 class="text-xl font-bold">
Banner Yönetimi
</h2>

<p class="text-sm text-slate-500 mt-1">
Ana sayfanın sol ve sağ tarafındaki 3'er bannerı ve sayfanın en alt orta bölümündeki 2 bannerı buradan yönetebilirsiniz.
</p>

</div>

<div class="text-xs bg-indigo-50 text-indigo-700 px-3 py-2 rounded-xl font-semibold">
8 Banner Alanı
</div>

</div>

<div class="admin-banner-grid">

{ad_form_fields}

</div>

</div>

</div>

<script>
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

<title>Hurda Fiyatları - Güncel Piyasa Takip</title>
<meta name="description" content="Güncel hurda fiyatları, fabrika fiyatları, LME, döviz ve piyasa takip ekranı.">
<meta name="robots" content="index,follow">
<meta property="og:title" content="Hurda Fiyatları - Güncel Piyasa Takip">
<meta property="og:description" content="Güncel hurda fiyatları, LME ve döviz verileri.">
<meta property="og:type" content="website">

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

/* Fabrika alanı tam genişlikte; önce 10 fabrika 5+5, sonra 6 reklam tek sıra. */
@media (min-width: 1024px) {
    .factory-layout {
        display: grid !important;
        grid-template-columns: repeat(6, minmax(0, 1fr)) !important;
        gap: 18px !important;
        align-items: start;
    }

    .factory-layout > main {
        grid-column: 1 / -1 !important;
        grid-row: 1 !important;
        width: 100% !important;
        max-width: none !important;
    }

    /* İki mevcut reklam kolonu tek satırda 6 ayrı kutuya dönüşür. */
    .factory-layout > .desktop-ad-column {
        display: contents !important;
    }

    .factory-layout > .desktop-ad-column .ad-box {
        width: 100% !important;
        max-width: none !important;
        aspect-ratio: 4 / 3 !important;
        grid-row: 2 !important;
    }

    .factory-layout > .desktop-ad-column:first-child .ad-box:first-child {
        grid-column: 1 !important;
    }

    .factory-layout > .desktop-ad-column:first-child .ad-box:nth-child(2) {
        grid-column: 2 !important;
    }

    .factory-layout > .desktop-ad-column:first-child .ad-box:nth-child(3) {
        grid-column: 3 !important;
    }

    .factory-layout > .desktop-ad-column:last-child .ad-box:first-child {
        grid-column: 4 !important;
    }

    .factory-layout > .desktop-ad-column:last-child .ad-box:nth-child(2) {
        grid-column: 5 !important;
    }

    .factory-layout > .desktop-ad-column:last-child .ad-box:nth-child(3) {
        grid-column: 6 !important;
    }

    /* 10 fabrika: 5 + 5. */
    .factory-price-grid {
        grid-template-columns: repeat(5, minmax(0, 1fr)) !important;
        gap: 16px !important;
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

</style>

</head>

<body class="market-design min-h-screen">

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
<span class="currency-sub">Frankfurter + Gold API</span>
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

<!-- MOBILE_LEFT_MIDDLE_AD -->

<!-- MOBILE_LEFT_BOTTOM_AD -->

<!-- MOBILE_RIGHT_TOP_AD -->

<!-- MOBILE_RIGHT_MIDDLE_AD -->

<!-- MOBILE_RIGHT_BOTTOM_AD -->

</div>




<div class="factory-layout grid grid-cols-1 lg:grid-cols-[250px_minmax(0,1fr)_250px] gap-4 lg:gap-5 items-start">

<aside class="desktop-ad-column hidden lg:grid">

<div
class="ad-box rounded-2xl overflow-hidden"
id="leftTopAd"
>
</div>

<div
class="ad-box rounded-2xl overflow-hidden"
id="leftMiddleAd"
>
</div>

<div
class="ad-box rounded-2xl overflow-hidden"
id="leftBottomAd"
>
</div>

</aside>

<main class="min-w-0 w-full max-w-[760px] mx-auto">

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
<div class="market-summary-sub">son değişim kayıtları</div>
</div>
<div class="market-summary-card down">
<div class="market-summary-label">Düşen</div>
<div id="summaryDownCount" class="market-summary-value">-</div>
<div class="market-summary-sub">son değişim kayıtları</div>
</div>
<div class="market-summary-card update">
<div class="market-summary-label">Son Güncelleme</div>
<div id="summaryLastUpdate" class="market-summary-value">Yükleniyor...</div>
<div class="market-summary-sub">sistem zamanı</div>
</div>
</section>

<section
id="marketTools"
class="bg-white/95 rounded-2xl sm:rounded-3xl border border-white/70 shadow-lg p-3 sm:p-4 mb-4"
>

<div class="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_auto] gap-3 items-end">

<div>
<label class="block text-[10px] uppercase tracking-wide font-black text-slate-500 mb-1.5">
Fiyat / Firma Ara
</label>
<input
id="fiyatArama"
type="search"
placeholder="Örn. DKP, Çolakoğlu..."
class="w-full h-11 rounded-xl border border-slate-200 bg-white px-3 text-sm font-bold text-slate-800 outline-none focus:border-sky-400 focus:ring-2 focus:ring-sky-100"
>
</div>

<div class="flex flex-wrap gap-2">
<button
type="button"
id="alarmButton"
class="h-11 px-4 rounded-xl bg-slate-900 text-white text-xs font-black hover:bg-slate-800 transition"
>
🔔 Fiyat Alarmı
</button>
<button
type="button"
id="historyButton"
class="h-11 px-4 rounded-xl bg-sky-50 border border-sky-200 text-sky-700 text-xs font-black hover:bg-sky-100 transition"
>
📈 Geçmiş
</button>
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
id="historyPanel"
class="mt-3 hidden border-t border-slate-200 pt-3"
>
<div class="flex flex-col sm:flex-row sm:items-end gap-2">
<div class="flex-1">
<label class="block text-[10px] uppercase tracking-wide font-black text-slate-500 mb-1.5">
Geçmiş Fiyat
</label>
<select
id="historyFirmSelect"
class="w-full h-10 rounded-xl border border-slate-200 bg-white px-3 text-sm font-bold text-slate-700"
>
<option value="">Firma seçin</option>
</select>
</div>
<div class="flex-1">
<select
id="historyItemSelect"
class="w-full h-10 rounded-xl border border-slate-200 bg-white px-3 text-sm font-bold text-slate-700"
>
<option value="">Kalem seçin</option>
</select>
</div>
<button
type="button"
id="historyLoadButton"
class="h-10 px-4 rounded-xl bg-slate-900 hover:bg-slate-800 text-white text-xs font-black transition"
>
Göster
</button>
</div>
<div id="historyResult" class="mt-3"></div>
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

<aside class="desktop-ad-column hidden lg:grid">

<div
class="ad-box rounded-2xl overflow-hidden"
id="rightTopAd"
>
</div>

<div
class="ad-box rounded-2xl overflow-hidden"
id="rightMiddleAd"
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
class="hidden lg:grid grid-cols-1 sm:grid-cols-2 items-stretch gap-3 sm:gap-4 w-full mt-4"
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

        const ticker =
            document.getElementById(
                "lmeTickerContent"
            );

        if (ticker) {
            const tickerItems =
                result.veriler.map(
                    function(item) {
                        const deger =
                            item.three_month_tl !== null
                            && item.three_month_tl !== undefined
                                ? formatFiyat(
                                    item.three_month_tl
                                  ) + " TL"
                                : "-";

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


function marketToolsInit(result) {

    const firmalar = Array.isArray(result.data)
        ? result.data
        : [];

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

    firmalar.forEach(function(firma) {
        const kalemler = Array.isArray(firma.kalemler)
            ? firma.kalemler
            : [];

        itemCount += kalemler.length;

        kalemler.forEach(function(kalem) {
            const degisim = String(kalem.degisim || "").trim();
            if (degisim.startsWith("+")) upCount++;
            if (degisim.startsWith("-")) downCount++;
        });
    });

    if (summaryFirmCount) summaryFirmCount.textContent = firmalar.length;
    if (summaryItemCount) summaryItemCount.textContent = itemCount;
    if (summaryUpCount) summaryUpCount.textContent = upCount;
    if (summaryDownCount) summaryDownCount.textContent = downCount;
    if (summaryLastUpdate) summaryLastUpdate.textContent = result.son_guncelleme || "-";

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

    const firmsForSelect = firmalar.map(function(f) {
        return '<option value="' + escapeHtml(f.firma_id) + '">' + escapeHtml(f.baslik) + '</option>';
    }).join("");

    if (firmSelect) firmSelect.innerHTML = '<option value="">Firma seçin</option>' + firmsForSelect;
    if (alarmFirm) alarmFirm.innerHTML = '<option value="">Firma seçin</option>' + firmsForSelect;

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

    if (firmSelect && firmSelect.dataset.bound !== "1") {
        firmSelect.addEventListener("change", function() {
            firmaKalemleriniDoldur("historyItemSelect", firmSelect.value);
        });
        firmSelect.dataset.bound = "1";
    }

    if (document.getElementById("todayChanges")?.dataset.loaded !== "1") {
        fetch("/today-changes", {cache:"no-store"})
            .then(function(r){ return r.json(); })
            .then(function(payload){
                const box = document.getElementById("todayChanges");
                if (!box) return;
                box.dataset.loaded = "1";
                const rows = payload.data || [];
                if (!rows.length) {
                    box.innerHTML = '<div class="rounded-xl bg-slate-50 border border-slate-200 p-3 text-xs text-slate-500 font-semibold">Son 24 saatte kayda değer fiyat değişimi yok.</div>';
                } else {
                    box.innerHTML =
                        '<div class="text-[10px] uppercase tracking-wide font-black text-slate-500 mb-2">Son 24 Saatte Değişenler</div>' +
                        '<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">' +
                        rows.map(function(x){
                            const up = Number(x.fark) > 0;
                            return '<div class="today-change-card rounded-xl border ' + (up ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50') + ' p-3">' +
                                '<div class="text-xs font-black text-slate-800">' + escapeHtml(x.firma) + ' · ' + escapeHtml(x.kalem) + '</div>' +
                                '<div class="mt-1 text-sm font-black ' + (up ? 'text-emerald-700' : 'text-red-700') + '">' +
                                    (up ? '▲ +' : '▼ ') + Number(x.fark).toLocaleString("tr-TR") + ' TL' +
                                    ' <span class="text-slate-500 font-bold">(' + Number(x.yeni).toLocaleString("tr-TR") + ' TL)</span>' +
                                '</div>' +
                                '<div class="mt-1 text-[9px] font-bold ' + (up ? 'text-emerald-600' : 'text-red-600') + '">' +
                                    (up ? 'Fiyat yükseldi' : 'Fiyat düştü') +
                                '</div>' +
                            '</div>';
                        }).join("") +
                        '</div>';
                    box.classList.remove("hidden");
                }
            })
            .catch(function(){});
    }

    const comparePanel = document.getElementById("comparePanel");
    const historyPanel = document.getElementById("historyPanel");
    const alarmPanel = document.getElementById("alarmPanel");

    const compareButton = document.getElementById("compareButton");
    if (compareButton && compareButton.dataset.bound !== "1") {
        compareButton.addEventListener("click", function() {
            const kalem = document.getElementById("compareSelect")?.value;
            if (!kalem) return;
            fetch("/compare?kalem=" + encodeURIComponent(kalem), {cache:"no-store"})
                .then(function(r){ return r.json(); })
                .then(function(payload){
                    const box = document.getElementById("compareResult");
                    const rows = payload.data || [];
                    box.innerHTML = rows.length
                        ? '<div class="overflow-x-auto"><table class="w-full text-xs"><thead><tr class="border-b border-slate-200"><th class="text-left py-2">Firma</th><th class="text-right py-2">Fiyat</th></tr></thead><tbody>' +
                          rows.map(function(x){ return '<tr class="border-b border-slate-100"><td class="py-2 font-bold">' + escapeHtml(x.firma) + '</td><td class="py-2 text-right font-black">' + Number(x.fiyat).toLocaleString("tr-TR") + ' TL</td></tr>'; }).join("") +
                          '</tbody></table></div>'
                        : '<div class="text-xs text-slate-500">Bu kalem için kayıt bulunamadı.</div>';
                });
        });
        compareButton.dataset.bound = "1";
    }

    const historyButton = document.getElementById("historyButton");
    if (historyButton && historyButton.dataset.bound !== "1") {
        historyButton.addEventListener("click", function(){
            historyPanel?.classList.toggle("hidden");
            comparePanel?.classList.add("hidden");
            alarmPanel?.classList.add("hidden");
        });
        historyButton.dataset.bound = "1";
    }

    const historyLoad = document.getElementById("historyLoadButton");
    if (historyLoad && historyLoad.dataset.bound !== "1") {
        historyLoad.addEventListener("click", function(){
            const firma = firmSelect?.value;
            const kalem = document.getElementById("historyItemSelect")?.value;
            if (!firma || !kalem) return;

            fetch("/history?firma_id=" + encodeURIComponent(firma) + "&kalem=" + encodeURIComponent(kalem) + "&limit=60", {cache:"no-store"})
                .then(function(r){ return r.json(); })
                .then(function(payload){
                    const rows = payload.data || [];
                    const box = document.getElementById("historyResult");
                    if (!rows.length) {
                        box.innerHTML = '<div class="text-xs text-slate-500">Geçmiş kayıt bulunamadı.</div>';
                        return;
                    }
                    const vals = rows.map(function(x){ return Number(x.fiyat); }).filter(Number.isFinite);
                    const min = Math.min.apply(null, vals);
                    const max = Math.max.apply(null, vals);
                    const range = Math.max(1, max - min);

                    box.innerHTML =
                        '<div class="rounded-xl border border-slate-200 bg-slate-50 p-3">' +
                        '<div class="flex items-center justify-between text-xs font-black text-slate-600 mb-2"><span>Min: ' + min.toLocaleString("tr-TR") + ' TL</span><span>Max: ' + max.toLocaleString("tr-TR") + ' TL</span></div>' +
                        '<div class="flex items-end gap-1 h-32">' +
                        rows.map(function(x){
                            const h = Math.max(4, ((Number(x.fiyat)-min)/range)*100);
                            return '<div title="' + escapeHtml(x.tarih || "") + ' · ' + Number(x.fiyat).toLocaleString("tr-TR") + ' TL" class="flex-1 min-w-[3px] rounded-t bg-sky-400" style="height:' + h + '%"></div>';
                        }).join("") +
                        '</div></div>';
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
        });
        alarmSave.dataset.bound = "1";
    }

    function renderAlarms() {
        const box = document.getElementById("alarmList");
        if (!box) return;
        const alarms = JSON.parse(localStorage.getItem("hurdaPriceAlarms") || "[]");
        box.innerHTML = alarms.length
            ? alarms.map(function(a, index){
                return '<div class="flex items-center justify-between gap-2 rounded-xl bg-slate-50 border border-slate-200 p-2.5 text-xs">' +
                    '<span class="font-bold">' + escapeHtml(a.kalem) + ' · ' + Number(a.value).toLocaleString("tr-TR") + ' TL</span>' +
                    '<button type="button" data-alarm-delete="' + index + '" class="text-red-600 font-black">Sil</button></div>';
            }).join("")
            : '<div class="text-xs text-slate-500">Kayıtlı fiyat alarmı yok.</div>';

        box.querySelectorAll("[data-alarm-delete]").forEach(function(btn){
            btn.addEventListener("click", function(){
                const list = JSON.parse(localStorage.getItem("hurdaPriceAlarms") || "[]");
                list.splice(Number(btn.dataset.alarmDelete), 1);
                localStorage.setItem("hurdaPriceAlarms", JSON.stringify(list));
                renderAlarms();
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

        const fiyat = Number(String(kalem.fiyat).replace(/[^0-9,.-]/g, "").replace(/\./g, "").replace(",", "."));
        const oldu = a.direction === "above" ? fiyat >= a.value : fiyat <= a.value;

        if (oldu && !a.fired) {
            a.fired = true;
            if ("Notification" in window && Notification.permission === "granted") {
                new Notification("Hurda fiyat alarmı", {
                    body: a.kalem + " · " + Number(fiyat).toLocaleString("tr-TR") + " TL"
                });
            }
        }
    });
    localStorage.setItem("hurdaPriceAlarms", JSON.stringify(alarms));

    const compareToggle = document.getElementById("compareSelect");
    if (compareToggle && compareToggle.dataset.bound !== "1") {
        compareToggle.addEventListener("change", function(){
            comparePanel?.classList.remove("hidden");
        });
        compareToggle.dataset.bound = "1";
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
                '<button type="button" class="firma-toggle w-full text-left p-4 sm:p-4 hover:bg-slate-50 transition" data-panel="' +
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
        "leftMiddleAd": ads[
            "left_middle"
        ],
        "leftBottomAd": ads[
            "left_bottom"
        ],
        "rightTopAd": ads[
            "right_top"
        ],
        "rightMiddleAd": ads[
            "right_middle"
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
            "<!-- MOBILE_LEFT_MIDDLE_AD -->",
            ads["left_middle"],
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
            "<!-- MOBILE_RIGHT_MIDDLE_AD -->",
            ads["right_middle"],
        ),
        (
            "<!-- MOBILE_RIGHT_BOTTOM_AD -->",
            ads["right_bottom"],
        ),
    ]

    for placeholder, reklam in mobile_reklamlar:

        slot_title = "Reklam Alanı"

        if "LEFT_TOP" in placeholder:
            slot_title = "Sol Üst Reklam"
        elif "LEFT_MIDDLE" in placeholder:
            slot_title = "Sol Orta Reklam"
        elif "LEFT_BOTTOM" in placeholder:
            slot_title = "Sol Alt Reklam"
        elif "RIGHT_TOP" in placeholder:
            slot_title = "Sağ Üst Reklam"
        elif "RIGHT_MIDDLE" in placeholder:
            slot_title = "Sağ Orta Reklam"
        elif "RIGHT_BOTTOM" in placeholder:
            slot_title = "Sağ Alt Reklam"

        page = page.replace(
            placeholder,
            mobile_ad_html(
                reklam,
                slot_title,
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


# DEPLOY SYNTAX CHECK MARKER
