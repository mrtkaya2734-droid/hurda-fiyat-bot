from datetime import datetime

import json
import socket

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
        # Render'da client.colakoglu.com.tr zaman zaman DNS çözümlemiyor.
        # Önce resmi DNS kaydını alıyoruz; ardından isteği IP adresine değil,
        # yine alan adına gönderiyoruz. Böylece HTTPS SNI/certificate doğrulaması
        # client.colakoglu.com.tr olarak kalıyor.
        try:
            dns = requests.get(
                "https://dns.google/resolve",
                params={"name": API_HOST, "type": "A"},
                headers={"Accept": "application/dns-json"},
                timeout=6,
            )
            dns.raise_for_status()

            adresler = [
                item.get("data")
                for item in dns.json().get("Answer", [])
                if item.get("type") == 1 and item.get("data")
            ]

            if not adresler:
                raise RuntimeError("Çolakoğlu API için DNS A kaydı bulunamadı.")

            original_getaddrinfo = socket.getaddrinfo

            for ip in adresler:
                def resolved_getaddrinfo(
                    host,
                    port,
                    family=0,
                    type=0,
                    proto=0,
                    flags=0,
                    _ip=ip,
                ):
                    if host == API_HOST:
                        return [
                            (
                                socket.AF_INET,
                                socket.SOCK_STREAM,
                                socket.IPPROTO_TCP,
                                "",
                                (_ip, int(port)),
                            )
                        ]
                    return original_getaddrinfo(
                        host,
                        port,
                        family,
                        type,
                        proto,
                        flags,
                    )

                socket.getaddrinfo = resolved_getaddrinfo

                try:
                    response = requests.get(
                        API,
                        headers=headers,
                        verify=False,
                        timeout=12,
                    )
                    return response
                except requests.RequestException:
                    continue
                finally:
                    socket.getaddrinfo = original_getaddrinfo

            raise RuntimeError(
                "Resmi Çolakoğlu API adresine DNS üzerinden ulaşılamadı."
            )

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
