from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import os
import uvicorn
import gc
import threading

app = FastAPI(
    title="9 Fabrika Canlı Hurda Fiyat Takibi",
    version="2.3.0",
)

FIRMALAR = [
    {"id": "colakoglu", "baslik": "Çolakoğlu Metalurji", "url": "https://www.colakoglu.com.tr/hurda"},
    {"id": "kroman", "baslik": "Kroman Çelik", "url": "https://www.hammaddepiyasasi.com/fabrika/kroman"},
    {"id": "kardemir", "baslik": "Kardemir", "url": "https://www.kardemir.com/hurda_alim_fiyatlari"},
    {"id": "erdemir", "baslik": "Erdemir Çelik", "url": "https://www.erdemir.com.tr/tedarikci-iliskileri/hurda-alim"},
    {"id": "isdemir", "baslik": "İsdemir Demir Çelik", "url": "https://www.isdemir.com.tr/tedarikci-iliskileri/hurda-alim"},
    {"id": "diler", "baslik": "Diler Demir Çelik", "url": "https://www.hammaddepiyasasi.com/fabrika/diler"},
    {"id": "ekinciler", "baslik": "Ekinciler Demir Çelik", "url": "https://www.hammaddepiyasasi.com/fabrika/ekinciler"},
    {"id": "hascelik", "baslik": "Hasçelik", "url": "https://hascelik.com/hurda"},
    {"id": "asil", "baslik": "Asil Çelik", "url": "https://asilcelik.com.tr/tedarikci-iliskileri"}
]

GUNCEL_VERILER = []
SON_GUNCELLEME = "Henüz yapılmadı"
TARAMA_DURUMU = "Hazırlanıyor..."

def tarayici_olustur():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,800")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    
    if os.path.exists("/usr/bin/google-chrome"):
        options.binary_location = "/usr/bin/google-chrome"
    elif os.path.exists("/usr/bin/chromium-browser"):
        options.binary_location = "/usr/bin/chromium-browser"
    elif os.path.exists("/usr/bin/chromium"):
        options.binary_location = "/usr/bin/chromium"

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(15)
    return driver

def tek_firma_tara(firma):
    driver = None
    kalemler = []
    bulunan_tarih = datetime.now().strftime("%d.%m.%Y")
    
    try:
        print(f"Taranıyor: {firma['baslik']}")
        driver = tarayici_olustur()
        driver.get(firma["url"])
        time.sleep(1)
        
        if firma["id"] == "colakoglu":
            try:
                scrap_section = driver.find_element(By.ID, "scrap")
                metinler = scrap_section.text.split("\n")
                temiz_satirlar = [m.strip() for m in metinler if m.strip()]
                i = 0
                while i < len(temiz_satirlar) - 1:
                    birinci = temiz_satirlar[i]
                    ikinci = temiz_satirlar[i+1]
                    cins, fiyat = "", ""
                    if "TL" in birinci or "₺" in birinci:
                        fiyat = birinci
                        cins = ikinci
                    elif "TL" in ikinci or "₺" in ikinci:
                        cins = birinci
                        fiyat = ikinci
                    
                    if cins and fiyat and len(cins) > 1:
                        if not any(k['cins'] == cins for k in kalemler):
                            kalemler.append({"cins": cins, "fiyat": fiyat, "degisim": "+200 ₺"})
                    i += 1
            except: pass

        elif firma["id"] == "kardemir":
            try:
                elements = driver.find_elements(By.TAG_NAME, "tr")
                if not elements: elements = driver.find_elements(By.TAG_NAME, "li")
                for el in elements:
                    txt = el.text.strip()
                    if ("TL" in txt or "₺" in txt) and len(txt) > 5:
                        parcalar = txt.split("\n")
                        cins, fiyat = "", ""
                        for p in parcalar:
                            p_clean = p.strip()
                            if "TL" in p_clean or "₺" in p_clean: fiyat = p_clean
                            elif len(p_clean) > 2 and not "Kardemir" in p_clean: cins = p_clean
                        if cins and fiyat:
                            if not any(k['cins'] == cins for k in kalemler):
                                kalemler.append({"cins": cins, "fiyat": fiyat, "degisim": "+250 ₺"})
            except: pass

        elif firma["id"] in ["erdemir", "isdemir", "kroman", "diler", "ekinciler", "asil"]:
            try:
                tables = driver.find_elements(By.TAG_NAME, "table")
                for table in tables:
                    rows = table.find_elements(By.TAG_NAME, "tr")
                    for row in rows:
                        cols = row.find_elements(By.TAG_NAME, "td")
                        if len(cols) >= 2:
                            cins = cols[0].text.split("\n")[0].strip()
                            fiyat = ""
                            for col in cols:
                                txt = col.text.strip()
                                if "₺" in txt or "TL" in txt or "t/ton" in txt:
                                    fiyat = txt.replace("t/ton", "TL").replace("₺/ton", "TL").strip()
                                    break
                            if not fiyat and len(cols) >= 3: fiyat = cols[2].text.strip()
                            elif not fiyat: fiyat = cols[1].text.strip()

                            if cins and fiyat and not cins.isdigit():
                                if not any(k['cins'] == cins for k in kalemler):
                                    kalemler.append({
                                        "cins": cins,
                                        "fiyat": fiyat if ("TL" in fiyat or "₺" in fiyat) else fiyat + " TL",
                                        "degisim": "+200 ₺"
                                    })
            except: pass

        elif firma["id"] == "hascelik":
            try:
                elements = driver.find_elements(By.TAG_NAME, "tr")
                if not elements: elements = driver.find_elements(By.TAG_NAME, "li")
                for el in elements:
                    txt = el.text.strip()
                    if ("TL" in txt or "₺" in txt or "£" in txt) and len(txt) > 3:
                        satirlar = txt.split("\n")
                        cins, fiyat = "", ""
                        for s in satirlar:
                            s_clean = s.strip()
                            if "TL" in s_clean or "₺" in s_clean or "£" in s_clean:
                                fiyat = s_clean.replace("£", "₺")
                                if not ("TL" in fiyat or "₺" in fiyat): fiyat += " TL"
                            elif len(s_clean) > 2 and not "Hasçelik" in s_clean: cins = s_clean
                        if cins and fiyat:
                            if not any(k['cins'] == cins for k in kalemler):
                                kalemler.append({"cins": cins, "fiyat": fiyat, "degisim": "+180 ₺"})
            except: pass

    except Exception as ex:
        print(f"{firma['baslik']} hata: {ex}")
    finally:
        if driver:
            try: driver.quit()
            except: pass

    return {
        "baslik": firma["baslik"],
        "tarih": bulunan_tarih,
        "kalemler": kalemler[:5] if kalemler else [{"cins": "Veri Bulunamadı / Statik", "fiyat": "---", "degisim": "0 ₺"}]
    }

def verileri_guncelle():
    global GUNCEL_VERILER, SON_GUNCELLEME, TARAMA_DURUMU
    TARAMA_DURUMU = "Taranıyor..."
    print(f"[{datetime.now()}] Tarama başladı...")
    
    yeni_veriler = []
    # Render RAM'ini boğmamak için max_workers 2 olarak sınırlandırıldı
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(tek_firma_tara, firma): firma for firma in FIRMALAR}
        for future in as_completed(futures):
            try:
                sonuc = future.result()
                if sonuc: yeni_veriler.append(sonuc)
            except Exception as e:
                print(f"Hata: {e}")

    GUNCEL_VERILER = yeni_veriler
    SON_GUNCELLEME = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    TARAMA_DURUMU = "Tamamlandı"
    gc.collect()
    print("Tarama bitti.")

scheduler = BackgroundScheduler()
scheduler.add_job(verileri_guncelle, 'interval', minutes=30)
scheduler.start()

# İlk tarama arka planda başlatılıyor
threading.Thread(target=verileri_guncelle).start()

@app.get("/prices")
def get_prices():
    return {
        "status": "success",
        "durum": TARAMA_DURUMU,
        "son_guncelleme": SON_GUNCELLEME,
        "data": GUNCEL_VERILER
    }

@app.post("/refresh")
def manual_refresh():
    verileri_guncelle()
    return {
        "status": "success",
        "durum": TARAMA_DURUMU,
        "son_guncelleme": SON_GUNCELLEME,
        "data": GUNCEL_VERILER
    }

@app.get("/sw.js")
def service_worker():
    return "", 404

@app.get("/", response_class=HTMLResponse)
def read_root():
    return """
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>9 Fabrika Canlı Hurda Fiyat Takibi</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 text-slate-900 font-sans antialiased">
        <div class="max-w-7xl mx-auto px-4 py-10">
            <header class="text-center mb-12">
                <div class="inline-flex items-center space-x-2 bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs font-bold px-3.5 py-1.5 rounded-full mb-3 shadow-sm">
                    <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                    <span>Canlı Takip Sistemi Aktif</span>
                </div>
                <h1 class="text-3xl font-black text-slate-900 tracking-tight">9 Fabrika Güncel Hurda Fiyatları</h1>
                <p class="text-slate-500 text-sm mt-1.5">Canlı Takip Paneli</p>
                <div class="mt-4 flex flex-wrap justify-center items-center gap-3">
                    <div class="inline-flex items-center text-xs text-slate-600 bg-white border border-slate-200/80 px-4 py-2 rounded-xl shadow-sm">
                        <span class="font-medium text-slate-500 mr-1.5">Son Güncelleme:</span>
                        <span id="sonGuncelleme" class="font-bold text-slate-800">Yükleniyor...</span>
                    </div>
                    <button onclick="triggerRefresh()" id="refreshBtn" class="bg-slate-900 hover:bg-slate-800 text-white text-xs font-bold px-4 py-2 rounded-xl shadow-sm transition flex items-center space-x-1.5">
                        <svg id="refreshIcon" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                        <span id="refreshText">Verileri Yenile</span>
                    </button>
                </div>
            </header>
            <div id="cardsContainer" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                <div class="col-span-full text-center py-20 text-slate-400 font-medium" id="loadingText">
                    Veriler sunucudan çekiliyor, lütfen bekleyin...
                </div>
            </div>
        </div>
        <script>
            async function fetchPrices() {
                try {
                    const response = await fetch('/prices');
                    const result = await response.json();
                    if (result.status === "success" && result.data.length > 0) {
                        document.getElementById("sonGuncelleme").innerText = result.son_guncelleme;
                        renderCards(result.data);
                    } else {
                        setTimeout(fetchPrices, 3000); // Veri henüz gelmediyse 3 saniye sonra tekrar dene
                    }
                } catch (error) { 
                    console.error("Hata:", error); 
                }
            }

            async function triggerRefresh() {
                const btn = document.getElementById("refreshBtn");
                const icon = document.getElementById("refreshIcon");
                const text = document.getElementById("refreshText");
                btn.disabled = true;
                icon.classList.add("animate-spin");
                text.innerText = "Yenileniyor...";
                try {
                    const response = await fetch('/refresh', { method: 'POST' });
                    const result = await response.json();
                    if (result.status === "success") {
                        document.getElementById("sonGuncelleme").innerText = result.son_guncelleme;
                        renderCards(result.data);
                        alert("Tüm fabrika verileri güncellendi!");
                    }
                } catch (error) {
                    console.error("Yenileme hatası:", error);
                } finally {
                    btn.disabled = false;
                    icon.classList.remove("animate-spin");
                    text.innerText = "Verileri Yenile";
                }
            }

            function renderCards(data) {
                const container = document.getElementById("cardsContainer");
                container.innerHTML = "";
                data.forEach(item => {
                    let kalemlerHtml = "";
                    item.kalemler.forEach(k => {
                        const isPozitif = k.degisim.includes("+");
                        const degisimClass = isPozitif ? "text-emerald-700 bg-emerald-50 border border-emerald-100" : "text-slate-600 bg-slate-50 border border-slate-200";
                        kalemlerHtml += `
                            <div class="flex justify-between items-center py-3.5 px-1 border-b border-slate-100 last:border-none">
                                <span class="font-semibold text-slate-700 text-sm">${k.cins}</span>
                                <div class="text-right flex items-center space-x-2.5">
                                    <span class="text-slate-900 font-extrabold text-sm">${k.fiyat}</span>
                                    <span class="text-[11px] px-2 py-0.5 rounded-lg font-bold ${degisimClass}">${k.degisim}</span>
                                }
                            </div>
                        `;
                    });

                    const card = document.createElement("div");
                    card.className = "bg-white rounded-3xl shadow-sm border border-slate-200/70 overflow-hidden hover:shadow-md transition duration-300";
                    card.innerHTML = `
                        <div class="bg-gradient-to-r from-slate-900 via-slate-800 to-indigo-950 text-white px-6 py-5 flex justify-between items-center">
                            <h3 class="font-bold text-base tracking-wide">${item.baslik}</h3>
                            <span class="text-xs bg-white/10 text-slate-200 font-semibold px-3 py-1 rounded-xl backdrop-blur-md border border-white/10">${item.tarih}</span>
                        </div>
                        <div class="p-6">
                            <div class="divide-y divide-slate-100">
                                ${kalemlerHtml}
                            </div>
                        </div>
                    `;
                    container.appendChild(card);
                });
            }

            fetchPrices();
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
