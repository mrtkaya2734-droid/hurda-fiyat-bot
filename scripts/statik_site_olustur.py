"""
Uygulamanın o anki halini GitHub Pages gibi statik bir barındırmada çalışacak
şekilde dosyalara döker (demo / test yayını).

Kullanım:
    python scripts/statik_site_olustur.py [cikti_klasoru]

Ortam değişkenleri:
    DATA_FILE        Çalışılacak veri dosyası (yoksa v2/data.json kopyalanır)
    STATIK_GUNCELLE  "1" ise önce tüm kaynaklardan fiyatlar çekilir
    STATIK_URL       Yayın adresi (paylaşım etiketlerinde kullanılır), örn.
                     https://kullanici.github.io/hurda-fiyat-bot/

Statik sitede yönetici paneli ve anlık bildirimler (Web Push) yoktur; sayfanın
API çağrıları sayfaya eklenen küçük bir yönlendirici ile bu dosyalara gider.
"""

import base64
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
V2 = KOK / "v2"
CIKTI = Path(sys.argv[1] if len(sys.argv) > 1 else KOK / "site").resolve()

if not os.getenv("DATA_FILE"):
    os.environ["DATA_FILE"] = str(Path(tempfile.mkdtemp()) / "data.json")

VERI_DOSYASI = Path(os.environ["DATA_FILE"])
VERI_DOSYASI.parent.mkdir(parents=True, exist_ok=True)
if not VERI_DOSYASI.exists():
    shutil.copy(V2 / "data.json", VERI_DOSYASI)

sys.path.insert(0, str(V2))

from fastapi.testclient import TestClient  # noqa: E402

from app import main as uygulama  # noqa: E402
from app import storage as depolama  # noqa: E402


def supabase_sadece_okuma():
    """
    Supabase bilgileri verildiyse güncel veri oradan okunur (admin'den eklenen
    firmalar, ayarlar, reklamlar). Demo canlı veriyi bozmasın diye yükleme,
    yedekleme ve silme işlemleri kapatılır.
    """
    if not os.getenv("SUPABASE_URL", "").strip():
        return False

    def yazma_yok(*args, **kwargs):
        return False

    for ad in ("supabase_storage_upload", "supabase_upload_json", "supabase_nesne_sil"):
        for modul in (depolama, uygulama):
            if hasattr(modul, ad):
                setattr(modul, ad, yazma_yok)

    print("Supabase salt okunur modda: veri okunacak, geri yazılmayacak.")
    return True


SUPABASE_OKUNUYOR = supabase_sadece_okuma()


def anahtar(metin):
    """JS tarafındaki dosya adı dönüşümüyle aynı olmalı."""
    return (
        base64.urlsafe_b64encode(str(metin).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )


def yaz(yol, icerik):
    yol = CIKTI / yol
    yol.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(icerik, (dict, list)):
        icerik = json.dumps(icerik, ensure_ascii=False)
    if isinstance(icerik, str):
        icerik = icerik.encode("utf-8")
    yol.write_bytes(icerik)


YONLENDIRICI = r"""<script>
/* Statik demo: API adreslerini üretilmiş JSON dosyalarına yönlendirir.
   Dolar/euro/altın tarayıcıdan canlı çekilmeyi dener, olmazsa statik dosyaya düşer. */
(function () {
    var gercek = window.fetch.bind(window);

    function anahtar(s) {
        return btoa(unescape(encodeURIComponent(s)))
            .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    }

    function esle(adres) {
        var u = new URL(adres, "http://x");
        var p = u.searchParams;

        if (u.pathname === "/prices") return "data/prices.json";
        if (u.pathname === "/lme") return "data/lme.json";
        if (u.pathname === "/currency") return "data/currency.json";
        if (u.pathname === "/compare") {
            return "data/compare/" + anahtar(p.get("kalem") || "") + ".json";
        }
        if (u.pathname === "/history") {
            return "data/history/" +
                anahtar((p.get("firma_id") || "") + "|" + (p.get("kalem") || "")) + ".json";
        }
        return null;
    }

    function sayi(x) {
        if (typeof x === "number") return x;
        x = String(x == null ? "" : x).trim().replace(/\s/g, "");
        if (/^\d{1,3}(\.\d{3})+$/.test(x)) x = x.replace(/\./g, "");
        else if (x.indexOf(",") > -1 && x.indexOf(".") > -1) x = x.replace(/\./g, "").replace(",", ".");
        else x = x.replace(",", ".");
        return parseFloat(x);
    }

    var ARALIK = { USD: [5, 500], EUR: [5, 600], ALTIN: [500, 100000] };

    function canliKur() {
        return gercek("https://finans.truncgil.com/v4/today.json", { cache: "no-store" })
            .then(function (r) {
                if (!r.ok) throw new Error("kur kaynağı yanıt vermedi");
                return r.json();
            })
            .then(function (v) {
                var govde = (v && v.Rates) || v || {};
                var tarih = String((v && (v.Update_Date || (v.Meta_Data && v.Meta_Data.Update_Date))) || "");
                var veriler = {};
                [["USD", ["USD"]], ["EUR", ["EUR"]], ["ALTIN", ["GRA", "gram-altin"]]].forEach(function (k) {
                    var kayit = null;
                    k[1].forEach(function (ad) { if (!kayit && govde[ad]) kayit = govde[ad]; });
                    if (!kayit) return;
                    var al = sayi(kayit.Buying != null ? kayit.Buying : kayit["Alış"]);
                    var sat = sayi(kayit.Selling != null ? kayit.Selling : kayit["Satış"]);
                    if (!isFinite(al) || !isFinite(sat)) return;
                    var orta = (al + sat) / 2;
                    if (orta < ARALIK[k[0]][0] || orta > ARALIK[k[0]][1]) return;
                    veriler[k[0]] = {
                        kod: k[0], birim: k[0] === "ALTIN" ? "1 gram" : "1",
                        alis: orta, satis: orta, kur: orta, tarih: tarih,
                        kur_turu: "Serbest piyasa kuru"
                    };
                });
                if (!veriler.USD || !veriler.EUR || !veriler.ALTIN) throw new Error("eksik kur");
                return new Response(JSON.stringify({
                    status: "success", kaynak: "Truncgil (canlı)",
                    kur_turu: "Güncel piyasa kuru", tarih: tarih, veriler: veriler
                }), { headers: { "Content-Type": "application/json" } });
            });
    }

    window.fetch = function (girdi, secenekler) {
        var adres = typeof girdi === "string" ? girdi : (girdi && girdi.url) || "";

        if (adres.charAt(0) === "/" && adres.indexOf("//") !== 0) {
            if (adres.split("?")[0] === "/currency") {
                return canliKur().catch(function () {
                    return gercek("data/currency.json", { cache: "no-cache" });
                });
            }
            var dosya = esle(adres);
            if (dosya) {
                return gercek(dosya, { cache: "no-cache" });
            }
            return Promise.resolve(
                new Response("{}", { status: 404, headers: { "Content-Type": "application/json" } })
            );
        }
        return gercek(girdi, secenekler);
    };
})();
</script>
"""

DEMO_SERIDI = """<div style="position:fixed;left:0;right:0;bottom:0;z-index:99999;
background:#111827;color:#fcd34d;font:600 11px/1.4 system-ui,sans-serif;
padding:5px 10px;text-align:center;opacity:.92;pointer-events:none">
Demo yayını &middot; veriler fiyatlar yaklaşık 5 dakikada bir, kurlar dakikada bir güncellenir
&middot; yönetici paneli ve bildirimler bu sürümde yoktur
</div>
"""


def main():
    if os.getenv("STATIK_GUNCELLE") == "1":
        try:
            print("Fiyatlar çekiliyor...")
            uygulama.verileri_guncelle()
        except Exception as exc:
            print(f"UYARI: güncelleme başarısız, mevcut veri kullanılacak: {exc}")

    if CIKTI.exists():
        shutil.rmtree(CIKTI)
    CIKTI.mkdir(parents=True)

    istemci = TestClient(uygulama.app)

    def al(adres):
        cevap = istemci.get(adres)
        if cevap.status_code != 200:
            raise RuntimeError(f"{adres} -> {cevap.status_code}")
        return cevap

    fiyatlar = al("/prices").json()
    yaz("data/prices.json", fiyatlar)

    for ad in ("lme", "currency"):
        try:
            yaz(f"data/{ad}.json", al(f"/{ad}").json())
        except Exception as exc:
            print(f"UYARI: /{ad} alınamadı: {exc}")
            yaz(f"data/{ad}.json", {"status": "error", "veriler": {}})

    kalemler = set()
    for firma in fiyatlar.get("data", []):
        firma_id = firma.get("firma_id", "")
        for kalem in firma.get("kalemler", []):
            ad = kalem.get("cins")
            if not ad:
                continue
            kalemler.add(ad)
            adres = f"/history?firma_id={_q(firma_id)}&kalem={_q(ad)}&limit=60"
            yaz(
                f"data/history/{anahtar(firma_id + '|' + ad)}.json",
                al(adres).json(),
            )

    for ad in sorted(kalemler):
        yaz(f"data/compare/{anahtar(ad)}.json", al(f"/compare?kalem={_q(ad)}").json())

    html = al("/").text
    yayin_adresi = os.getenv("STATIK_URL", "").strip()
    if yayin_adresi and not yayin_adresi.endswith("/"):
        yayin_adresi += "/"

    for sahte in ("http://testserver/", "https://testserver/"):
        html = html.replace(sahte, yayin_adresi or "./")
    html = html.replace('"/static/', '"static/').replace("'/static/", "'static/")
    # Canlı kur tarayıcıdan çekildiği için yenileme 10 dk yerine 1 dk olur.
    html = html.replace(
        "yalnizcaGorunurken(dovizleriGetir),\n    600000",
        "yalnizcaGorunurken(dovizleriGetir),\n    60000",
        1,
    )
    html = html.replace("<head>", "<head>\n" + YONLENDIRICI, 1)
    # Sayfadaki betikler içinde de "</body>" metni geçtiği için sonuncusu seçilir.
    son = html.rfind("</body>")
    if son != -1:
        html = html[:son] + DEMO_SERIDI + html[son:]

    yaz("index.html", html)
    yaz("404.html", html)
    yaz(".nojekyll", "")

    shutil.copytree(V2 / "static", CIKTI / "static")

    adet = sum(1 for _ in CIKTI.rglob("*") if _.is_file())
    print(f"Hazır: {CIKTI} ({adet} dosya, {len(kalemler)} kalem)")


def _q(deger):
    from urllib.parse import quote

    return quote(str(deger), safe="")


if __name__ == "__main__":
    main()
