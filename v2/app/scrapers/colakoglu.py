from datetime import datetime

import requests

from app.models import FirmaSonuc, Kalem
from app.scrapers.base import ScraperHatasi
from app.scrapers.generic import cek_url as generic_url_cek


ID = "colakoglu"
BASLIK = "Çolakoğlu Metalurji"
URL = "https://www.colakoglu.com.tr/hurda"
API = "https://client.colakoglu.com.tr/webservice/scrap-price"


def _resmi_sayfadan_cek() -> FirmaSonuc:
    return generic_url_cek(
        ID,
        BASLIK,
        URL,
    )


def cek() -> FirmaSonuc:
    try:
        cevap = requests.get(
            API,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
            verify=False,
            timeout=8,
        )
        cevap.raise_for_status()
        veri = cevap.json()

    except requests.RequestException as e:
        try:
            return _resmi_sayfadan_cek()
        except Exception as fallback_error:
            raise ScraperHatasi(
                f"Çolakoğlu: API bağlantı hatası: {e}; "
                f"resmi sayfa yedeği de başarısız: {fallback_error}"
            ) from e

    except ValueError as e:
        try:
            return _resmi_sayfadan_cek()
        except Exception as fallback_error:
            raise ScraperHatasi(
                "Çolakoğlu: API geçerli JSON döndürmedi; "
                f"resmi sayfa yedeği de başarısız: {fallback_error}"
            ) from e

    fiyatlar = veri.get("prices")

    if not fiyatlar:
        try:
            return _resmi_sayfadan_cek()
        except Exception as fallback_error:
            raise ScraperHatasi(
                "Çolakoğlu: 'prices' alanı boş ya da yok; "
                f"resmi sayfa yedeği de başarısız: {fallback_error}"
            )

    kalemler = []

    for f in fiyatlar:
        if (
            isinstance(f, dict)
            and isinstance(f.get("price"), (int, float))
            and f.get("name")
        ):
            kalemler.append(
                Kalem(
                    cins=str(f["name"]).strip(),
                    fiyat=int(f["price"]),
                )
            )

    if not kalemler:
        try:
            return _resmi_sayfadan_cek()
        except Exception as fallback_error:
            raise ScraperHatasi(
                "Çolakoğlu: geçerli kalem bulunamadı; "
                f"resmi sayfa yedeği de başarısız: {fallback_error}"
            )

    try:
        tarih = datetime.fromisoformat(
            veri["date"]
        ).date()
    except (KeyError, ValueError, TypeError):
        tarih = None

    return FirmaSonuc(
        ID,
        BASLIK,
        URL,
        tarih,
        kalemler,
    )
