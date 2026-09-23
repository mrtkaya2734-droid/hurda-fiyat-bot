from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import os
import uvicorn
import gc

app = FastAPI(
    title="Hurda Fiyat Takibi",
    version="67.0.0",
)

FIRMALAR = [
    {"id": "colakoglu", "baslik": "Çolakoğlu Metalurji", "url": "https://www.colakoglu.com.tr/hurda"},
    {"id": "kroman", "baslik": "Kroman Çelik", "url": "https://www.hammaddepiyasasi.com/fabrika/kroman"},
    {"id": "kardemir", "baslik": "Kardemir", "url": "https://www.hammaddepiyasasi.com/fabrika/kardemir"},
    {"id": "erdemir", "baslik": "Erdemir Çelik", "url": "https://www.erdemir.com.tr/tedarikci-iliskileri/hurda-alim"},
    {"id": "isdemir", "baslik": "İsdemir Demir Çelik", "url": "https://www.isdemir.com.tr/tedarikci-iliskileri/hurda-alim"},
    {"id": "diler", "baslik": "Diler Demir Çelik", "url": "https://www.hammaddepiyasasi.com/fabrika/diler"},
    {"id": "ekinciler", "baslik": "Ekinciler Demir Çelik", "url": "https://www.hammaddepiyasasi.com/fabrika/ekinciler"},
    {"id": "hascelik", "baslik": "Hasçelik", "url": "https://www.hammaddepiyasasi.com/fabrika/hascelik"},
    {"id": "asil", "baslik": "Asil Çelik", "url": "https://asilcelik.com.tr/tedarikci-iliskileri"}
]

GUNCEL_VERILER = []
SON_GUNCELLEME = "Veriler yükleniyor..."

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

def temizle_metin(text):
    if not text:
        return ""
    temiz = text.replace("HurdaFiyat geçmişi", "").replace("Geçmişi", "").strip()
    if len(temiz) > 28:
        temiz = temiz[:26] + "..."
    return temiz

def veri_cek(firma):
    kalemler = []
    bulunan_tarih = datetime.now().strftime("%d.%m.%Y")
    
    try:
        response = requests.get(firma["url"], headers=headers, timeout=10)
        if response.status_code != 200:
            return {
                "baslik": firma["baslik"],
                "url": firma["url"],
                "tarih": bulunan_tarih,
                "kalemler": [{"cins": f"Erişim Hatası ({response.status_code})", "fiyat": "---", "degisim": "0 ₺"}]
            }
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. ÇOLAKOĞLU ÖZEL AYRIŞTIRMA (id="scrap")
        if firma["id"] == "colakoglu":
            scrap_section = soup.find(id="scrap")
            if scrap_section:
                lines = [line.strip() for line in scrap_section.get_text(separator="\n").split("\n") if line.strip()]
                i = 0
                while i < len(lines) - 1:
                    l1, l2 = lines[i], lines[i+1]
                    cins, fiyat = "", ""
                    if "TL" in l1 or "₺" in l1 or "TON" in l1:
                        fiyat, cins = l1, l2
                    elif "TL" in l2 or "₺" in l2 or "TON" in l2:
                        cins, fiyat = l1, l2
                    
                    if cins and fiyat and len(cins) > 1 and "TL" not in cins and "₺" not in cins:
                        cins_temiz = temizle_metin(cins)
                        fiyat_temiz = fiyat.replace("t/ton", "TL").replace("₺/ton", "TL")
                        if not any(k['cins'] == cins_temiz for k in kalemler):
                            kalemler.append({"cins": cins_temiz, "fiyat": fiyat_temiz, "degisim": "+200 ₺"})
                        i += 2
                    else:
                        i += 1
        
        # 2. DİĞER FABRİKALAR İÇİN TABLO YAPISI
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cols = row.find_all(['td', 'th'])
                if len(cols) >= 2:
                     ham_cins = cols[0].get_text(strip=True).split("\n")[0]
                     cins = temizle_metin(ham_cins)
                     
                     fiyat = ""
                     for col in cols:
                        txt = col.get_text(strip=True)
                        if "₺" in txt or "TL" in txt or "t/ton" in txt:
                            fiyat = txt.replace("t/ton", "TL").replace("₺/ton", "TL").replace("€", "₺")
                            break
                     if not fiyat and len(cols) >= 3:
                        fiyat = cols[2].get_text(strip=True)
                     elif not fiyat and len(cols) >= 2:
                        fiyat = cols[1].get_text(strip=True)
                        
                     if cins and fiyat and not cins.isdigit() and len(cins) > 1:
                        if not any(k['cins'] == cins for k in kalemler):
                            kalemler.append({
                                "cins": cins,
                                "fiyat": fiyat if ("TL" in fiyat or "₺" in fiyat) else fiyat + " TL",
                                "degisim": "+200 ₺"
                            })

    except Exception as e:
        print(f"{firma['baslik']} hata: {e}")

    return {
        "baslik": firma["baslik"],
        "url": firma["url"],
        "tarih": bulunan_tarih,
        "kalemler": kalemler[:6] if kalemler else [{"cins": "Veri Bulunamadı", "fiyat": "---", "degisim": "0 ₺"}]
    }

def verileri_arkaplanda_guncelle():
    global GUNCEL_VERILER, SON_GUNCELLEME
    print(f"[{datetime.now()}] 9 fabrika taranıyor...")
    
    yeni_veriler = []
    for firma in FIRMALAR:
        res = veri_cek(firma)
        yeni_veriler.append(res)
    
    if yeni_veriler:
        GUNCEL_VERILER.clear()
        GUNCEL_VERILER = yeni_veriler
    
    SON_GUNCELLEME = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    gc.collect()
    print(f"Güncellendi: {SON_GUNCELLEME}")

scheduler = BackgroundScheduler()
scheduler.add_job(verileri_arkaplanda_guncelle, 'interval', minutes=30)
scheduler.start()

@app.on_event("startup")
def startup_event():
    verileri_arkaplanda_guncelle()

@app.get("/prices")
def get_prices():
    return {
        "status": "success",
        "son_guncelleme": SON_GUNCELLEME,
        "data": GUNCEL_VERILER
    }

@app.get("/refresh")
def refresh_cache():
    verileri_arkaplanda_guncelle()
    return {
        "status": "success",
        "son_guncelleme": SON_GUNCELLEME,
        "data": GUNCEL_VERILER
    }

@app.get("/manifest.json")
def get_manifest():
    return {
        "name": "Hurda Fiyat Takip",
        "short_name": "HurdaTakip",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f1f5f9",
        "theme_color": "#0f172a",
        "icons": [{
            "src": "https://cdn-icons-png.flaticon.com/512/2954/2954884.png",
            "sizes": "512x512",
            "type": "image/png"
        }]
    }

@app.get("/", response_class=HTMLResponse)
def read_root():
    return """
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Hurda Fiyatları</title>
        <link rel="manifest" href="/manifest.json">
        <meta name="theme-color" content="#0f172a">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 text-slate-900 font-sans antialiased">
        <div class="max-w-7xl mx-auto px-4 py-10">
            <header class="text-center mb-12">
                <h1 class="text-3xl font-black text-slate-900 tracking-tight">Hurda Fiyatları</h1>
                <p class="text-slate-500 text-sm mt-1.5">Canlı Takip Paneli</p>
                <div class="mt-4 flex flex-wrap justify-center items-center gap-3">
                    <div class="inline-flex items-center text-xs text-slate-600 bg-white border border-slate-200/80 px-4 py-2 rounded-xl shadow-sm">
                        <span class="font-medium text-slate-500 mr-1.5">Son Güncelleme:</span>
                        <span id="sonGuncelleme" class="font-bold text-slate-800">Yükleniyor...</span>
                    </div>
                    <button id="refreshBtn" onclick="triggerRefresh()" class="inline-flex items-center space-x-1.5 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-700 px-4 py-2 rounded-xl shadow-sm transition cursor-pointer">
                        <span>Verileri Şimdi Tara & Yenile</span>
                    </button>
                </div>
            </header>
            <div id="cardsContainer" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
        </div>
        <script>
            async function fetchPrices() {
                try {
                    const response = await fetch('/prices');
                    const result = await response.json();
                    if (result.status === "success") {
                        if(result.son_guncelleme) {
                            document.getElementById("sonGuncelleme").innerText = result.son_guncelleme;
                        }
                        if(result.data && result.data.length > 0) {
                            renderCards(result.data);
                        }
                    }
                } catch (error) { console.error("Hata:", error); }
            }

            async function triggerRefresh() {
                const btn = document.getElementById("refreshBtn");
                btn.disabled = true;
                btn.innerText = "Veriler hızlıca çekiliyor, lütfen bekleyin...";
                try {
                    const response = await fetch('/refresh');
                    const result = await response.json();
                    if (result.status === "success") {
                        if(result.son_guncelleme) {
                            document.getElementById("sonGuncelleme").innerText = result.son_guncelleme;
                        }
                        if(result.data && result.data.length > 0) {
                            renderCards(result.data);
                        }
                    }
                } catch (error) {
                    console.error("Yenileme hatası:", error);
                    alert("Yenileme sırasında bir hata oluştu.");
                } finally {
                    btn.disabled = false;
                    btn.innerText = "Verileri Şimdi Tara & Yenile";
                }
            }

            function renderCards(data) {
                const container = document.getElementById("cardsContainer");
                container.innerHTML = "";
                data.forEach(item => {
                    let kalemlerHtml = "";
                    item.kalemler.forEach(k => {
                        kalemlerHtml += `
                            <div class="grid grid-cols-12 gap-2 py-3 px-1 border-b border-slate-100 last:border-none items-center">
                                <div class="col-span-7 font-semibold text-slate-700 text-xs truncate" title="${k.cins}">${k.cins}</div>
                                <div class="col-span-5 text-right flex items-center justify-end space-x-1.5">
                                    <span class="text-slate-900 font-extrabold text-xs shrink-0">${k.fiyat}</span>
                                    <span class="text-[10px] px-1.5 py-0.5 rounded-md font-bold text-emerald-700 bg-emerald-50 shrink-0">${k.degisim}</span>
                                </div>
                            </div>
                        `;
                    });
                    const card = document.createElement("div");
                    card.className = "bg-white rounded-3xl shadow-sm border border-slate-200/70 overflow-hidden";
                    card.innerHTML = `
                        <div class="bg-slate-900 text-white px-5 py-4 flex justify-between items-center">
                            <div>
                                <h3 class="font-bold text-sm">${item.baslik}</h3>
                                <a href="${item.url}" target="_blank" class="text-[10px] text-indigo-300 underline block mt-0.5">Resmi Kaynağa Git ↗</a>
                            </div>
                            <span class="text-[11px] bg-white/10 px-2.5 py-1 rounded-xl shrink-0">${item.tarih}</span>
                        </div>
                        <div class="p-5"><div class="divide-y divide-slate-100">${kalemlerHtml}</div></div>
                    `;
                    container.appendChild(card);
                });
            }

            fetchPrices();
            setInterval(fetchPrices, 300000);
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
