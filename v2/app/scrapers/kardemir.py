import re
from datetime import datetime
from html import unescape

import requests

from app.models import FirmaSonuc, Kalem
from app.scrapers.base import ScraperHatasi, ZAMAN_ASIMI


ID = "kardemir"
BASLIK = "Kardemir"
URL = "https://www.kardemir.com/hurda_alim_fiyatlari"

SESSION_URL = "https://www.kardemir.com/session/getallsession"

DATE_LIST_URL = (
    "https://www.kardemir.com/api/TedarikciIliskileriapi/"
    "getscrappricemarkdatelist"
)

PRICE_URL = (
    "https://www.kardemir.com/api/TedarikciIliskileriapi/"
    "getscrapprice"
)


def _fiyat_bul(metin: str):
    """
    Örnekler:

    20.120 TL/Ton -> 20120
    20 .120 TL/Ton -> 20120
    19.850 TL/Ton -> 19850
    19000 TL/Ton -> 19000
    """

    eslesme = re.search(
        r"(\d+(?:\s*\.\s*\d{3})*)\s*TL\s*/?\s*Ton",
        metin,
        re.IGNORECASE,
    )

    if not eslesme:
        return None

    rakam = eslesme.group(1)

    rakam = re.sub(
        r"\s+",
        "",
        rakam,
    )

    return int(
        rakam.replace(".", "")
    )


def _kalem_fiyati(metin: str, isim: str):
    """
    Verilen ürün adının arkasındaki fiyatı bulur.
    """

    if isim == "Ekstra":
        # "Özel Ekstra" içindeki Ekstra'yı kesinlikle alma.
        desen = (
            r"(?<!Özel\s)"
            r"\bEkstra\b"
            r"\s*:\s*"
            r"([^<]{0,50}?\d+(?:\s*\.\s*\d{3})*\s*TL\s*/?\s*Ton)"
        )
    else:
        desen = (
            r"\b"
            + re.escape(isim)
            + r"\b"
            r"\s*:\s*"
            r"([^<]{0,50}?\d+(?:\s*\.\s*\d{3})*\s*TL\s*/?\s*Ton)"
        )

    eslesme = re.search(
        desen,
        metin,
        re.IGNORECASE,
    )

    if not eslesme:
        return None

    return _fiyat_bul(
        eslesme.group(1)
    )


def cek() -> FirmaSonuc:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }

    session = requests.Session()

    try:
        # Kardemir API'sinin istediği Dil bilgisini al.
        cevap = session.get(
            SESSION_URL,
            headers=headers,
            timeout=ZAMAN_ASIMI,
        )
        cevap.raise_for_status()

        dil = cevap.text.strip()

        if not dil:
            raise ScraperHatasi(
                "Kardemir: Dil bilgisi alınamadı"
            )

        headers["Dil"] = dil

        # Güncel fiyat tarihlerini al.
        cevap = session.get(
            DATE_LIST_URL,
            headers=headers,
            timeout=ZAMAN_ASIMI,
        )
        cevap.raise_for_status()

        tarihler = cevap.json()

        if not isinstance(tarihler, list) or not tarihler:
            raise ScraperHatasi(
                "Kardemir: fiyat tarihleri alınamadı"
            )

        # İlk tarih en güncel tarihtir.
        son_tarih = tarihler[0]

        # Güncel fiyat içeriğini al.
        cevap = session.get(
            PRICE_URL,
            headers=headers,
            params={
                "date": son_tarih
            },
            timeout=ZAMAN_ASIMI,
        )
        cevap.raise_for_status()

        veri = cevap.json()

    except requests.RequestException as e:
        raise ScraperHatasi(
            f"Kardemir: API bağlantı hatası: {e}"
        ) from e

    except ValueError as e:
        raise ScraperHatasi(
            f"Kardemir: API geçerli JSON döndürmedi: {e}"
        ) from e

    content = veri.get("content")

    if not content:
        raise ScraperHatasi(
            "Kardemir: fiyat içeriği boş"
        )

    # HTML entity'lerini çöz.
    content = unescape(content)

    # HTML etiketlerini kaldır.
    temiz = re.sub(
        r"<[^>]+>",
        " ",
        content,
    )

    # Gereksiz boşlukları temizle.
    temiz = temiz.replace(
        "\xa0",
        " ",
    )

    temiz = re.sub(
        r"\s+",
        " ",
        temiz,
    ).strip()

    kalemler = []

    urunler = [
        "Özel Ekstra",
        "DKP Hurda",
        "Ekstra",
        "1. Sınıf",
        "2. Sınıf",
    ]

    for urun in urunler:
        fiyat = _kalem_fiyati(
            temiz,
            urun,
        )

        if fiyat is not None:
            kalemler.append(
                Kalem(
                    cins=urun,
                    fiyat=fiyat,
                )
            )

    if not kalemler:
        raise ScraperHatasi(
            "Kardemir: resmi sayfadan fiyat kalemi bulunamadı"
        )

    # API tarihini al.
    try:
        fiyat_tarihi = datetime.fromisoformat(
            veri["date"]
        ).date()

    except (KeyError, ValueError, TypeError):
        try:
            fiyat_tarihi = datetime.strptime(
                son_tarih,
                "%d/%m/%Y",
            ).date()

        except (ValueError, TypeError):
            fiyat_tarihi = None

    return FirmaSonuc(
        ID,
        BASLIK,
        URL,
        fiyat_tarihi,
        kalemler,
    )