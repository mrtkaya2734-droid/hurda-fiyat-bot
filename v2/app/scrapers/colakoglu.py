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


def _resmi_api_proxy_istegi():
    """
    Render -> client.colakoglu.com.tr doğrudan bağlantısı timeout olursa,
    resmi Çolakoğlu API URL'sini bir HTTP fetch proxy üzerinden ister.
    Proxy yalnızca resmi API cevabını taşır; fiyat kaynağı yine Çolakoğlu API'sidir.
    """
    proxy_url = (
        "https://r.jina.ai/http://"
        "client.colakoglu.com.tr/webservice/scrap-price"
    )

    response = requests.get(
        proxy_url,
        headers={
            "User-Agent": "HurdaFiyatBot/2.0",
            "Accept": "application/json,text/plain,*/*",
        },
        timeout=20,
    )
    response.raise_for_status()

    text = response.text.strip()

    try:
        return response, response.json()
    except ValueError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start < 0 or end <= start:
        raise ValueError(
            "Resmi API proxy cevabında JSON bulunamadı."
        )

    try:
        veri = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Resmi API proxy cevabı geçerli JSON değil."
        ) from exc

    return response, veri


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
        # Resmi API alan adı Render'da doğrudan bağlantıda zaman aşımına
        # uğrayabiliyor. Önce resmi DNS kaydını dış DNS servislerinden al,
        # sonra isteği yine alan adıyla ve doğru SNI ile yap.
        dns_urls = (
            "https://dns.google/resolve",
            "https://cloudflare-dns.com/dns-query",
        )

        dns_hatalari = []

        for dns_url in dns_urls:
            try:
                if "cloudflare" in dns_url:
                    dns = requests.get(
                        dns_url,
                        params={"name": API_HOST, "type": "A"},
                        headers={
                            "Accept": "application/dns-json",
                            **headers,
                        },
                        verify=False,
                        timeout=6,
                    )
                else:
                    dns = requests.get(
                        dns_url,
                        params={"name": API_HOST, "type": "A"},
                        headers={"Accept": "application/dns-json"},
                        verify=False,
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
                    dns_hatalari.append(
                        f"{dns_url}: A kaydı yok"
                    )
                    continue

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
                    except requests.RequestException as ip_error:
                        dns_hatalari.append(
                            f"{ip}: {ip_error}"
                        )
                    finally:
                        socket.getaddrinfo = original_getaddrinfo

            except Exception as dns_error:
                dns_hatalari.append(
                    f"{dns_url}: {dns_error}"
                )

        raise requests.RequestException(
            "Resmi API bağlantısı başarısız. "
            f"İlk hata: {normal_error}; "
            f"DNS/bağlantı denemeleri: {' | '.join(dns_hatalari[-6:])}"
        ) from normal_error


def cek() -> FirmaSonuc:
    # Çolakoğlu'nun canlı fiyatları doğrudan kendi API'sinden alınır.
    # API erişilemezse mevcut son kayıt korunur; resmi HTML sayfasına
    # ayrıca gitmeye çalışılmaz. Bu sayfa Render tarafında robots
    # nedeniyle engellendiği için her dakika gereksiz bekleme oluşturuyordu.
    try:
        try:
            cevap = _resmi_api_istegi()
            cevap.raise_for_status()
            veri = cevap.json()
        except requests.RequestException as direct_error:
            try:
                _, veri = _resmi_api_proxy_istegi()
            except Exception as proxy_error:
                raise requests.RequestException(
                    "Resmi API doğrudan ve proxy üzerinden alınamadı. "
                    f"Doğrudan hata: {direct_error}; "
                    f"proxy hatası: {proxy_error}"
                ) from proxy_error

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
