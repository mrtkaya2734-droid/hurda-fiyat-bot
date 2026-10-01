from datetime import datetime

import json
import requests

from app.models import FirmaSonuc, Kalem
from app.scrapers.base import ScraperHatasi


ID = "colakoglu"
BASLIK = "Çolakoğlu Metalurji"
URL = "https://www.colakoglu.com.tr/hurda"
API = "https://client.colakoglu.com.tr/webservice/scrap-price"
API_HOST = "client.colakoglu.com.tr"


def _resmi_api_istegi():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    }

    try:
        return requests.get(
            API,
            headers=headers,
            verify=False,
            timeout=12,
        )
    except requests.RequestException as normal_error:
        # Render ortamında client.colakoglu.com.tr zaman zaman DNS
        # çözülemiyor. Bu durumda alan adını Google DNS-over-HTTPS
        # üzerinden çözerek yine Çolakoğlu'nun kendi API'sine bağlan.
        try:
            dns = requests.get(
                "https://dns.google/resolve",
                params={"name": API_HOST, "type": "A"},
                headers={"Accept": "application/dns-json"},
                timeout=6,
            )
            dns.raise_for_status()
            cevap = dns.json()
            adresler = [
                item.get("data")
                for item in cevap.get("Answer", [])
                if item.get("type") == 1 and item.get("data")
            ]

            if not adresler:
                raise RuntimeError("DNS A kaydı bulunamadı.")

            son_hata = normal_error
            for ip in adresler:
                try:
                    response = requests.get(
                        f"https://{ip}/webservice/scrap-price",
                        headers={
                            **headers,
                            "Host": API_HOST,
                        },
                        verify=False,
                        timeout=12,
                    )
                    return response
                except requests.RequestException as ip_error:
                    son_hata = ip_error

            raise son_hata

        except Exception as dns_error:
            raise normal_error from dns_error


def cek() -> FirmaSonuc:
    # Çolakoğlu'nun canlı fiyatları doğrudan kendi API'sinden alınır.
    # API erişilemezse mevcut son kayıt korunur; resmi HTML sayfasına
    # ayrıca gitmeye çalışılmaz. Bu sayfa Render tarafında robots
    # nedeniyle engellendiği için her dakika gereksiz bekleme oluşturuyordu.
    try:
        son_hata = None
        veri = None

        for deneme in range(2):
            try:
                cevap = _resmi_api_istegi()
                cevap.raise_for_status()
                veri = cevap.json()
                break
            except requests.RequestException as exc:
                son_hata = exc
                if deneme == 0:
                    continue

        if veri is None:
            raise son_hata or requests.RequestException("Bilinmeyen API hatası")

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
