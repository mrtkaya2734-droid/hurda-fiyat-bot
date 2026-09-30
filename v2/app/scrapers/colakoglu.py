from datetime import datetime

import requests

from app.models import FirmaSonuc, Kalem
from app.scrapers.base import ScraperHatasi


ID = "colakoglu"
BASLIK = "Çolakoğlu Metalurji"
URL = "https://www.colakoglu.com.tr/hurda"
API = "https://client.colakoglu.com.tr/webservice/scrap-price"


def cek() -> FirmaSonuc:
    # Çolakoğlu'nun canlı fiyatları doğrudan kendi API'sinden alınır.
    # API erişilemezse mevcut son kayıt korunur; resmi HTML sayfasına
    # ayrıca gitmeye çalışılmaz. Bu sayfa Render tarafında robots
    # nedeniyle engellendiği için her dakika gereksiz bekleme oluşturuyordu.
    try:
        cevap = requests.get(
            API,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
            verify=False,
            timeout=4,
        )
        cevap.raise_for_status()
        veri = cevap.json()

    except requests.RequestException as e:
        raise ScraperHatasi(
            f"Çolakoğlu: API bağlantı hatası: {e}"
        ) from e

    except ValueError as e:
        raise ScraperHatasi(
            "Çolakoğlu: API geçerli JSON döndürmedi."
        ) from e

    fiyatlar = veri.get("prices")

    if not fiyatlar:
        raise ScraperHatasi(
            "Çolakoğlu: 'prices' alanı boş ya da yok."
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
        raise ScraperHatasi(
            "Çolakoğlu: geçerli kalem bulunamadı."
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
