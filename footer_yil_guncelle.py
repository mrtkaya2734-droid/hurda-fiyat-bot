#!/usr/bin/env python3
"""Hostingdeki sitede footer yilini (c) 2023 -> (c) 2026 yapar (FTP/FTPS).

Kullanim (once onizleme, hicbir sey degismez):
    python footer_yil_guncelle.py --host ftp.siteadiniz.com --user KULLANICI

Gercekten uygulamak icin --uygula ekleyin:
    python footer_yil_guncelle.py --host ftp.siteadiniz.com --user KULLANICI --uygula

Sifre komut satirina yazilmaz; sorulur (veya FTP_SIFRE ortam degiskeni okunur).
Degistirilen her dosyanin yedegi ./yedek/ klasorune kaydedilir.
"""
import argparse
import ftplib
import getpass
import io
import os
import re
import sys

UZANTILAR = (".html", ".htm", ".php", ".tpl", ".twig", ".phtml", ".js", ".inc")
ATLA_KLASORLER = {"cgi-bin", "node_modules", ".git", "vendor", "cache", "logs", "tmp"}
# "© 2023", "&copy; 2023", "&#169; 2023", "(c) 2023" -> sadece yili degistirir
DESEN = re.compile(r"((?:©|&copy;|&#169;|&#xA9;|\(c\))\s*)2023(?=\D)", re.IGNORECASE)


def baglan(host, port, user, sifre, tls):
    if tls:
        ftp = ftplib.FTP_TLS()
        ftp.connect(host, port, timeout=30)
        ftp.login(user, sifre)
        ftp.prot_p()
    else:
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=30)
        ftp.login(user, sifre)
    return ftp


def dosyalari_listele(ftp, yol):
    """(yol, klasor_mu) listesi dondurur."""
    sonuc = []
    try:
        for ad, bilgi in ftp.mlsd(yol):
            if ad in (".", ".."):
                continue
            sonuc.append((ad, bilgi.get("type") == "dir"))
        return sonuc
    except (ftplib.error_perm, AttributeError):
        pass
    # MLSD desteklenmiyorsa: cd ile klasor mu diye dene
    for ad in ftp.nlst(yol):
        ad = ad.rsplit("/", 1)[-1]
        if ad in (".", ".."):
            continue
        tam = f"{yol.rstrip('/')}/{ad}"
        try:
            ftp.cwd(tam)
            ftp.cwd("/")
            sonuc.append((ad, True))
        except ftplib.error_perm:
            sonuc.append((ad, False))
    return sonuc


def gez(ftp, yol):
    for ad, klasor in dosyalari_listele(ftp, yol):
        tam = f"{yol.rstrip('/')}/{ad}"
        if klasor:
            if ad not in ATLA_KLASORLER:
                yield from gez(ftp, tam)
        elif ad.lower().endswith(UZANTILAR):
            yield tam


def indir(ftp, yol):
    tampon = io.BytesIO()
    ftp.retrbinary(f"RETR {yol}", tampon.write)
    return tampon.getvalue()


def coz(veri):
    for kodlama in ("utf-8", "cp1254"):
        try:
            return veri.decode(kodlama), kodlama
        except UnicodeDecodeError:
            continue
    return None, None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--port", type=int, default=21)
    p.add_argument("--klasor", default="/", help="Taranacak kok klasor (orn. /public_html)")
    p.add_argument("--duz-ftp", action="store_true", help="FTPS yerine sifresiz FTP kullan")
    p.add_argument("--uygula", action="store_true", help="Degisiklikleri gercekten yukle")
    a = p.parse_args()

    sifre = os.environ.get("FTP_SIFRE") or getpass.getpass("FTP sifresi: ")
    try:
        ftp = baglan(a.host, a.port, a.user, sifre, tls=not a.duz_ftp)
    except Exception as e:
        if a.duz_ftp:
            sys.exit(f"Baglanti hatasi: {e}")
        print(f"FTPS olmadi ({e}); --duz-ftp ile tekrar deneyin.")
        sys.exit(1)

    degisen = 0
    for yol in gez(ftp, a.klasor):
        try:
            veri = indir(ftp, yol)
        except ftplib.all_errors as e:
            print(f"! okunamadi: {yol} ({e})")
            continue
        metin, kodlama = coz(veri)
        if metin is None:
            continue
        yeni, adet = DESEN.subn(r"\g<1>2026", metin)
        if not adet:
            continue
        degisen += 1
        print(f"{'GUNCELLENDI' if a.uygula else 'DEGISECEK'}: {yol} ({adet} yer)")
        if a.uygula:
            yedek = os.path.join("yedek", yol.lstrip("/"))
            os.makedirs(os.path.dirname(yedek), exist_ok=True)
            with open(yedek, "wb") as f:
                f.write(veri)
            ftp.storbinary(f"STOR {yol}", io.BytesIO(yeni.encode(kodlama)))

    ftp.quit()
    print(f"\nToplam {degisen} dosya {'guncellendi' if a.uygula else 'degisecek'}.")
    if not a.uygula and degisen:
        print("Uygulamak icin komuta --uygula ekleyin.")


if __name__ == "__main__":
    main()
