"""Bağımlılıksız Web Push (RFC 8030 + 8291 aes128gcm + 8292 VAPID).

pywebpush yerine yalnızca `cryptography` ve `requests` kullanılır.
"""
import base64
import json
import os
import struct
import time
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def b64u(veri: bytes) -> str:
    return base64.urlsafe_b64encode(veri).rstrip(b"=").decode()


def b64d(metin: str) -> bytes:
    return base64.urlsafe_b64decode(metin + "=" * (-len(metin) % 4))


def _uncompressed(public_key) -> bytes:
    return public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )


def vapid_anahtari_uret() -> dict:
    ozel = ec.generate_private_key(ec.SECP256R1())
    sayi = ozel.private_numbers().private_value.to_bytes(32, "big")
    return {
        "private": b64u(sayi),
        "public": b64u(_uncompressed(ozel.public_key())),
    }


def _ozel_anahtar(private_b64: str):
    return ec.derive_private_key(
        int.from_bytes(b64d(private_b64), "big"), ec.SECP256R1()
    )


def vapid_basligi(endpoint: str, vapid: dict, konu: str) -> str:
    """RFC 8292: 'vapid t=<jwt>, k=<public key>' Authorization değeri."""
    p = urlparse(endpoint)
    aud = f"{p.scheme}://{p.netloc}"

    baslik = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    govde = b64u(
        json.dumps(
            {"aud": aud, "exp": int(time.time()) + 12 * 3600, "sub": konu},
            separators=(",", ":"),
        ).encode()
    )
    imzalanacak = f"{baslik}.{govde}".encode()

    der = _ozel_anahtar(vapid["private"]).sign(imzalanacak, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    imza = r.to_bytes(32, "big") + s.to_bytes(32, "big")

    return f"vapid t={baslik}.{govde}.{b64u(imza)}, k={vapid['public']}"


def sifrele(
    veri: bytes,
    ua_public_b64: str,
    auth_b64: str,
    as_private=None,
    salt: bytes = None,
) -> bytes:
    """RFC 8291 aes128gcm ile tek kayıtlık mesaj gövdesi üretir."""
    ua_public = b64d(ua_public_b64)
    auth_secret = b64d(auth_b64)

    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = _uncompressed(as_private.public_key())
    salt = salt or os.urandom(16)

    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ortak = as_private.exchange(ec.ECDH(), ua_key)

    prk = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=auth_secret,
        info=b"WebPush: info\x00" + ua_public + as_public,
    ).derive(ortak)

    cek = HKDF(
        algorithm=hashes.SHA256(),
        length=16,
        salt=salt,
        info=b"Content-Encoding: aes128gcm\x00",
    ).derive(prk)

    nonce = HKDF(
        algorithm=hashes.SHA256(),
        length=12,
        salt=salt,
        info=b"Content-Encoding: nonce\x00",
    ).derive(prk)

    sifreli = AESGCM(cek).encrypt(nonce, veri + b"\x02", None)

    return salt + struct.pack(">I", 4096) + bytes([len(as_public)]) + as_public + sifreli


def gonder(abonelik: dict, yuk: dict, vapid: dict, konu: str, zaman_asimi: int = 10):
    """Bildirim gönderir. Dönüş: (basarili, abonelik_gecersiz_mi)."""
    endpoint = abonelik["endpoint"]
    anahtarlar = abonelik["keys"]

    govde = sifrele(
        json.dumps(yuk, ensure_ascii=False).encode("utf-8"),
        anahtarlar["p256dh"],
        anahtarlar["auth"],
    )

    cevap = requests.post(
        endpoint,
        data=govde,
        headers={
            "Authorization": vapid_basligi(endpoint, vapid, konu),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": "86400",
            "Urgency": "normal",
        },
        timeout=zaman_asimi,
    )

    if cevap.status_code in (404, 410):
        return False, True

    return 200 <= cevap.status_code < 300, False
