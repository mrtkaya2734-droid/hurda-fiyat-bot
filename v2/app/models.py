from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Kalem:
    cins: str
    fiyat: int                         # TL/ton
    eski_fiyat: Optional[int] = None   # kaynak veriyorsa (Erdemir, İsdemir)


@dataclass
class FirmaSonuc:
    firma_id: str
    baslik: str
    url: str
    fiyat_tarihi: Optional[date]
    kalemler: list = field(default_factory=list)