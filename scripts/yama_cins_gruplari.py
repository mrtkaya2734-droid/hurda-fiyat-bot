# Tek seferlik yama: "1. Grup", "1. Kalite", "1. Sinif" kalemlerini
# En Yuksek Fiyatlar ve Fiyat Siralamasi kutularinda tek grupta toplar.
# Iki kez calistirilirsa bir sey yapmaz.
# Kullanim (depo kokunde): python3 scripts/yama_cins_gruplari.py
import sys
from pathlib import Path

YOL = Path("v2/app/main.py")
ESKI = r'''function hurdaGruplari(firmalar) {
    const gruplar = {};

    firmalar.forEach(function(f) {
        (f.kalemler || []).forEach(function(k) {
            const fiyat = hurdaKalemFiyati(k);
            const anahtar = hurdaKatla(k.cins);
            if (!anahtar || !Number.isFinite(fiyat)) return;

            if (!gruplar[anahtar]) gruplar[anahtar] = { ad: k.cins, uyeler: [] };'''
YENI = r'''// Firmalar aynı sınıfı farklı adlarla yayınlıyor: "1. Grup", "1. Kalite",
// "1 SINIF" aslında aynı cins. Bunları tek anahtarda (1grup, 2grup...) topluyoruz.
function hurdaCinsAnahtari(cins) {
    const katli = hurdaKatla(cins);
    const m = katli.match(/^([0-9]+)(grup|kalite|sinif)$/);
    if (m) return { anahtar: m[1] + "grup", ad: m[1] + ". Grup/Kalite/Sınıf" };
    return { anahtar: katli, ad: cins };
}

function hurdaGruplari(firmalar) {
    const gruplar = {};

    firmalar.forEach(function(f) {
        (f.kalemler || []).forEach(function(k) {
            const fiyat = hurdaKalemFiyati(k);
            const cins = hurdaCinsAnahtari(k.cins);
            const anahtar = cins.anahtar;
            if (!anahtar || !Number.isFinite(fiyat)) return;

            if (!gruplar[anahtar]) gruplar[anahtar] = { ad: cins.ad, uyeler: [] };

            // Aynı firma aynı gruba iki kez düşerse (ör. hem "1. Grup" hem "1. Sınıf") yüksek olanı tut
            const mevcut = gruplar[anahtar].uyeler.find(function(u) { return u.firma === f.baslik; });
            if (mevcut) {
                if (fiyat > mevcut.fiyat) mevcut.fiyat = fiyat;
                return;
            }'''

metin = YOL.read_text(encoding="utf-8")
if "function hurdaCinsAnahtari" in metin:
    print("Yama zaten uygulanmis.")
    sys.exit(0)
if metin.count(ESKI) != 1:
    print("HATA: degistirilecek blok bulunamadi veya birden fazla.")
    sys.exit(1)
YOL.write_text(metin.replace(ESKI, YENI), encoding="utf-8", newline="")
print("Yama uygulandi.")
