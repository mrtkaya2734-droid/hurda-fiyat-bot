"""Admin girişi: çerez oturumu + değiştirilebilir (PBKDF2) şifre + deneme sınırı.

Şifre ilk kurulumda ADMIN_PASS ortam değişkenidir. Admin panelinden
değiştirildiğinde tuzlu PBKDF2 özeti data.json içinde ("admin_auth") saklanır
ve ortam değişkeni artık kullanılmaz.
"""
import hashlib
import hmac
import os
import secrets
import time

OTURUM_SURESI = 12 * 3600
PBKDF2_TUR = 200_000
MIN_SIFRE_UZUNLUGU = 10

_HATALAR = {}  # anahtar -> [zaman damgaları]
_PENCERE = 600
_IP_LIMIT = 5
_GENEL_LIMIT = 40


def _ozet(sifre: str, tuz: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", sifre.encode("utf-8"), tuz, PBKDF2_TUR).hex()


def sifre_dogru_mu(data: dict, kullanici: str, sifre: str, env_kullanici: str, env_sifre: str) -> bool:
    kayit = (data.get("admin_auth") or {})

    kullanici_ok = hmac.compare_digest(
        str(kullanici).encode("utf-8"), env_kullanici.encode("utf-8")
    )

    if kayit.get("hash") and kayit.get("salt"):
        hesap = _ozet(str(sifre), bytes.fromhex(kayit["salt"]))
        sifre_ok = hmac.compare_digest(hesap, kayit["hash"])
    else:
        sifre_ok = hmac.compare_digest(
            str(sifre).encode("utf-8"), env_sifre.encode("utf-8")
        )

    return kullanici_ok and sifre_ok


def sifre_degistir(data: dict, yeni_sifre: str) -> None:
    """Yeni şifre özetini yazar ve oturum anahtarını yeniler (eski oturumlar düşer)."""
    tuz = os.urandom(16)
    data["admin_auth"] = {
        "salt": tuz.hex(),
        "hash": _ozet(yeni_sifre, tuz),
        "secret": secrets.token_hex(32),
        "degisti": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _oturum_anahtari(data: dict) -> bytes:
    kayit = data.setdefault("admin_auth", {})
    if not kayit.get("secret"):
        kayit["secret"] = secrets.token_hex(32)
        kayit["_yeni"] = True
    return kayit["secret"].encode("utf-8")


def oturum_olustur(data: dict, kullanici: str) -> str:
    bitis = int(time.time()) + OTURUM_SURESI
    imza = hmac.new(
        _oturum_anahtari(data), f"{kullanici}|{bitis}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{bitis}.{imza}"


def oturum_gecerli_mi(data: dict, token: str, kullanici: str) -> bool:
    try:
        bitis_s, imza = token.split(".", 1)
        bitis = int(bitis_s)
    except (ValueError, AttributeError):
        return False

    if bitis < time.time():
        return False

    kayit = data.get("admin_auth") or {}
    if not kayit.get("secret"):
        return False

    beklenen = hmac.new(
        kayit["secret"].encode("utf-8"), f"{kullanici}|{bitis}".encode("utf-8"), hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(imza, beklenen)


# --- Deneme sınırı -------------------------------------------------

def _temizle(anahtar: str) -> list:
    simdi = time.time()
    liste = [t for t in _HATALAR.get(anahtar, []) if simdi - t < _PENCERE]
    _HATALAR[anahtar] = liste
    return liste


def engelli_mi(ip: str) -> bool:
    return len(_temizle(ip)) >= _IP_LIMIT or len(_temizle("*")) >= _GENEL_LIMIT


def hata_kaydet(ip: str) -> None:
    _temizle(ip).append(time.time())
    _temizle("*").append(time.time())


def basari_sifirla(ip: str) -> None:
    _HATALAR.pop(ip, None)
