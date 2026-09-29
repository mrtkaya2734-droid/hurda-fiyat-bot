import json
import re
from datetime import date
from typing import Any

from bs4 import BeautifulSoup

from app.models import FirmaSonuc, Kalem
from app.scrapers.base import ScraperHatasi, http_get


PRICE_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[. ]\d{3})+|\d{4,6})(?:[.,]\d{1,2})?\s*(?:₺|TL)(?:\s*/\s*(?:ton|mt|kg))?",
    re.IGNORECASE,
)

DATE_RE = re.compile(
    r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b"
)


def _date_from_text(text: str):
    for match in DATE_RE.finditer(text or ""):
        try:
            return date(
                int(match.group(3)),
                int(match.group(2)),
                int(match.group(1)),
            )
        except ValueError:
            continue
    return None


def _parse_price(raw: str):
    text = str(raw or "").strip()

    match = PRICE_RE.search(
        text
    )

    if match:
        value = (
            match.group(1)
            .replace(".", "")
            .replace(" ", "")
        )

        try:
            return int(value)
        except ValueError:
            pass

    # Bazı tablolar para birimini ayrı sütunda verir:
    # "DKP | 18605 | TL/ton" gibi.
    for candidate in re.findall(
        r"(?<!\\d)(\\d{1,3}(?:[. ]\\d{3})+|\\d{4,6})(?!\\d)",
        text,
    ):
        value = candidate.replace(
            ".",
            "",
        ).replace(
            " ",
            "",
        )

        if not value.isdigit():
            continue

        number = int(value)

        if 4000 <= number <= 200000:
            return number

    return None


def _looks_like_label(text: str):
    value = " ".join(str(text or "").split()).strip()
    if not value or len(value) > 80:
        return False

    lower = value.casefold()
    if any(
        word == lower
        for word in (
            "telefon",
            "adres",
            "whatsapp",
            "email",
            "e-posta",
            "toplam",
        )
    ):
        return False

    return bool(
        re.search(
            r"[a-zA-ZçğıöşüÇĞİÖŞÜ]",
            value,
        )
    )


def _rows_from_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [
                " ".join(cell.stripped_strings)
                for cell in tr.find_all(["th", "td"])
            ]

            if len(cells) < 2:
                continue

            price = None
            price_index = -1

            for index, cell in enumerate(cells):
                candidate = _parse_price(cell)
                if candidate is not None:
                    price = candidate
                    price_index = index

            if price is None:
                continue

            label = ""
            for cell in cells[:price_index]:
                if _looks_like_label(cell) and _parse_price(cell) is None:
                    label = cell
                    break

            if not label:
                for cell in cells:
                    if _looks_like_label(cell) and _parse_price(cell) is None:
                        label = cell
                        break

            if label:
                rows.append((label, price))

    if rows:
        return rows, soup

    # Bazı siteler tablo yerine kart/div yapısı kullanır.
    fallback = []

    for element in soup.find_all(
        string=re.compile(r"(TL|₺)", re.IGNORECASE)
    ):
        parent = element.parent
        if parent is None:
            continue

        full = " ".join(parent.stripped_strings).strip()
        price = _parse_price(full)
        if price is None:
            continue

        label = PRICE_RE.sub("", full).strip(" -:|")
        if _looks_like_label(label):
            fallback.append((label[:80], price))

    return fallback, soup


def _json_pairs(value: Any):
    pairs = []

    if isinstance(value, dict):
        label = None
        price = None

        for key, item in value.items():
            lower = str(key).casefold()

            if (
                label is None
                and isinstance(item, str)
                and any(
                    token in lower
                    for token in (
                        "name",
                        "product",
                        "kalite",
                        "cins",
                        "quality",
                        "title",
                    )
                )
            ):
                label = item.strip()

            if price is None:
                if (
                    isinstance(item, (int, float))
                    and 4000 <= float(item) <= 200000
                ):
                    price = int(item)
                elif isinstance(item, str):
                    parsed = _parse_price(item)
                    if (
                        parsed is not None
                        and 4000 <= parsed <= 200000
                    ):
                        price = parsed

        if label and price:
            pairs.append((label, price))

        for item in value.values():
            pairs.extend(_json_pairs(item))

    elif isinstance(value, list):
        for item in value:
            pairs.extend(_json_pairs(item))

    return pairs


def cek_url(
    firma_id: str,
    baslik: str,
    url: str,
) -> FirmaSonuc:
    url = str(url or "").strip()

    if not url:
        raise ScraperHatasi(
            "Otomatik çekim için kaynak URL girilmelidir."
        )

    try:
        response = http_get(
            url,
            timeout=25,
        )
    except ScraperHatasi:
        raise
    except Exception as exc:
        raise ScraperHatasi(
            f"{baslik}: kaynak sayfası alınamadı: {exc}"
        ) from exc

    content_type = response.headers.get(
        "content-type",
        "",
    ).casefold()

    pairs = []
    page_text = ""

    if (
        "json" in content_type
        or response.text.lstrip().startswith(("{", "["))
    ):
        try:
            payload = response.json()
            pairs = _json_pairs(payload)
            page_text = json.dumps(
                payload,
                ensure_ascii=False,
            )
        except (ValueError, TypeError):
            pairs = []

    if not pairs:
        pairs, soup = _rows_from_html(response.text)
        page_text = soup.get_text(
            " ",
            strip=True,
        )

    unique = {}
    for label, price in pairs:
        normalized = " ".join(
            str(label or "").split()
        ).strip()

        if not normalized:
            continue

        if 4000 <= price <= 200000:
            unique[normalized] = price

    kalemler = [
        Kalem(
            cins=label,
            fiyat=price,
        )
        for label, price in unique.items()
    ]

    if not kalemler:
        raise ScraperHatasi(
            f"{baslik}: URL'de otomatik fiyat verisi bulunamadı. "
            "Sayfa JavaScript/API ile yükleniyorsa bu firma için "
            "özel scraper gerekebilir."
        )

    return FirmaSonuc(
        firma_id=firma_id,
        baslik=baslik,
        url=url,
        fiyat_tarihi=_date_from_text(page_text),
        kalemler=kalemler[:100],
    )
