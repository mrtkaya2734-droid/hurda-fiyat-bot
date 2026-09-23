from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time
import os
import uvicorn
import gc

app = FastAPI(
    title="9 Fabrika Canlı Hurda Fiyat Takibi",
    version="49.0.0",
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
SON_GUNCELLEME = "Henüz yapılmadı"

def veri_cek(firma):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1024,768")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

    kalemler = []
    bulunan_tarih = datetime.now().strftime("%d.%m.%Y")
    driver = None
    
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(15)
        driver.get(firma["url"])
        time.sleep(3)
        
        # 1. ÇOLAKOĞLU
        if firma["id"] == "colakoglu":
            try:
                scrap_section = driver.find_element(By.ID, "scrap")
                metinler = scrap_section.text.split("\n")
                temiz_satirlar = [m.strip() for m in metinler if m.strip()]
                i = 0
                while i < len(temiz_satirlar) - 1:
                    birinci, ikinci = temiz_satirlar[i], temiz_satirlar[i+1]
                    cins, fiyat = "", ""
                    if "TL" in birinci or "₺" in birinci:
                        fiyat, cins = birinci, ikinci
                    elif "TL" in ikinci or "₺" in ikinci:
                        cins, fiyat = birinci, ikinci
                    if cins and fiyat and len(cins) > 1:
                        if not any(k['cins'] == cins for k in kalemler):
                            kalemler.append({"cins": cins, "fiyat": fiyat, "degisim": "+200 ₺"})
                    i += 1
            except Exception as e:
                print(f"Çolakoğlu hata: {e}")

        # 2. ERDEMİR & 3. İSDEMİR
        elif firma["id"] in ["erdemir", "isdemir"]:
            try:
                tables = driver.find_elements(By.TAG_NAME, "table")
                for table in tables:
                    rows = table.find_elements(By.TAG_NAME, "tr")
                    for row in rows:
                        cols = row.find_elements(By.TAG_NAME, "td")
                        if len(cols) >= 3:
                            cins = cols[0].text.strip()
                            yeni_fiyat = cols[2].text.strip()
                            if cins and yeni_fiyat and not cins.isdigit():
                                if not any(k['cins'] == cins for k in kalemler):
                                    kalemler.append({
                                        "cins": cins,
                                        "fiyat": yeni_fiyat if ("TL" in yeni_fiyat or "₺" in yeni_fiyat) else yeni_fiyat + " TL",
                                        "degisim": "+200 ₺"
                                    })
            except Exception as e:
                print(f"{firma['baslik']} hata: {e}")

        # 4. KROMAN, 5. KARDEMİR, 6. DİLER, 7. EKİNCİLER, 8. HASÇELİK (Ortak Tablo Yapısı)
        elif firma["id"] in ["kroman", "kardemir", "diler", "ekinciler", "hascelik"]:
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
                                    fiyat = txt.replace("t/ton", "TL").replace("₺/ton", "TL").replace("€", "₺").strip()
                                    break
                            if not fiyat:
                                fiyat = cols[1].text.strip().replace("€", "₺")
                            if cins and fiyat:
                                if not ("TL" in fiyat or "₺" in fiyat):
                                    fiyat += " TL"
                                if not any(k['cins'] == cins for k in kalemler):
                                    kalemler.append({
                                        "cins": cins,
                                        "fiyat": fiyat,
                                        "degisim": "+220 ₺"
                                    })
            except Exception as e:
                print(f"{firma['baslik']} hata: {e}")

        # 9. ASİL ÇELİK
        elif firma["id"] == "asil":
            try:
                tables = driver.find_elements(By.TAG_NAME, "table")
                for table in tables:
                    rows = table.find_elements(By.TAG_NAME, "tr")
                    for row in rows:
                        cols = row.find_elements(By.TAG_NAME, "td")
                        if len(cols) >= 2:
                            cins = cols[0].text.strip()
                            fiyat = cols[1].text.strip()
                            if cins and fiyat:
                                if not any(k['cins'] == cins for k in kalemler):
                                    kalemler.append({
                                        "cins": cins,
                                        "fiyat": fiyat.replace("(TL/ton)", "TL").strip(),
                                        "degisim": "+200 ₺"
                                    })
                if not kalemler:
                    for tag in ["div", "li", "span", "p"]:
                        elements = driver.find_elements(By.TAG_NAME, tag)
                        for el in elements:
                            txt = el.text.strip()
                            if ("TL" in txt or "₺" in txt) and len(txt) < 80:
                                if not any(k['fiyat'] == txt for k in kalemler):
                                    kalemler.append({"cins": "Asil Çelik Hurda Kalemi", "fiyat": txt, "degisim": "+200 ₺"})
            except Exception as e:
                print(f"Asil Çelik hata: {e}")

    except Exception as e:
        print(f"{firma['baslik']} tarama hatası: {e}")
    finally:
        if driver:
            try: driver.quit()
            except: pass
        gc.collect()

    return {
        "baslik": firma["baslik"],
        "url": firma["url"],
        "tarih": bulunan_tarih,
        "kalemler": kalemler[:6] if kalemler else [{"cins": "Güncel Veri Bekleniyor", "fiyat": "---", "degisim": "0 ₺"}]
    }

def verileri_arkaplanda_guncelle():
    global GUNCEL_VERILER, SON_GUNCELLEME
    print(f"[{datetime.now()}] 9 fabrika sırayla taranıyor...")
    
    yeni_veriler = []
    for firma in FIRMALAR:
        try:
            res = veri_cek(firma)
            yeni_veriler.append(res)
        except Exception as ex:
            print(f"{firma['baslik']} taranamadı: {ex}")
        time.sleep(1)
    
    if yeni_veriler:
        GUNCEL_VERILER.clear()
        GUNCEL_VERILER = yeni_veriler
        SON_GUNCELLEME = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    gc.collect()
    print(f"Önbellek güncellendi. Yeni saat: {SON_GUNCELLEME}")

scheduler = BackgroundScheduler()
scheduler.add_job(verileri_arkaplanda_guncelle, 'interval', minutes=30)
scheduler.start()

@app.on_event("startup")
def startup_event():
    # Render port timeout yememesi için başlangıçta tarama yapmıyoruz.
    # Arka plan planlayıcısı (scheduler) ilk 30 dakikada bir veya manuel yenileme ile dolacak.
    pass

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
        <title>9 Fabrika Canlı Hurda Fiyat Takibi</title>
        <link rel="manifest" href="/manifest.json">
        <meta name="theme-color" content="#0f172a">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 text-slate-900 font-sans antialiased">
        <div class="max-w-7xl mx-auto px-4 py-10">
            <header class="text-center mb-12">
                <h1 class="text-3xl font-black text-slate-900 tracking-tight">9 Fabrika Güncel Hurda Fiyatları</h1>
                <p class="text-slate-500 text-sm mt-1.5">Canlı Takip Paneli</p>
                <div class="mt-4 flex flex-wrap justify-center items-center gap-3">
                    <div class="inline-flex items-center text-xs text-slate-600 bg-white border border-slate-200/80 px-4 py-2 rounded-xl shadow-sm">
                        <span class="font-medium text-slate-500 mr-1.5">Son Güncelleme:</span>
                        <span id="sonGuncelleme" class="font-bold text-slate-800">Yükleniyor...</span>
                    </div>
                    <button id="refreshBtn" onclick="triggerRefresh()" class="inline-flex items-center space-x-1.5 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-700 px-4 py-2 rounded-xl shadow-sm transition cursor-pointer">
                        <span>Önbelleği Temizle & Yenile</span>
                    </button>
                    <button onclick="requestNotificationPermission()" class="inline-flex items-center text-xs font-bold text-slate-700 bg-white border border-slate-200 hover:bg-slate-50 px-4 py-2 rounded-xl shadow-sm transition cursor-pointer">
                        🔔 Bildirimleri Aç
                    </button>
                </div>
            </header>
            <div id="cardsContainer" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
        </div>
        <script>
            let eskiVerilerKarsilastirma = null;

            function requestNotificationPermission() {
                if (!("Notification" in window)) {
                    alert("Tarayıcınız bildirimleri desteklemiyor.");
                    return;
                }
                Notification.requestPermission().then(permission => {
                    if (permission === "granted") {
                        new Notification("Hurda Takip Sistemi", { body: "Fiyat değişim bildirimleri başarıyla aktif edildi!" });
                    } else {
                        alert("Bildirim izni reddedildi.");
                    }
                });
            }

            function fiyatlariKarsilastirVeBildir(yeniVeriListesi) {
                if (!eskiVerilerKarsilastirma) {
                    eskiVerilerKarsilastirma = JSON.stringify(yeniVeriListesi);
                    return;
                }
                
                const eskiListe = JSON.parse(eskiVerilerKarsilastirma);
                let degisiklikVarMi = false;

                yeniVeriListesi.forEach(yeniFirma => {
                    const eskiFirma = eskiListe.find(f => f.baslik === yeniFirma.baslik);
                    if (eskiFirma) {
                        yeniFirma.kalemler.forEach(yeniKalem => {
                            const eskiKalem = eskiFirma.kalemler.find(k => k.cins === yeniKalem.cins);
                            if (eskiKalem && eskiKalem.fiyat !== yeniKalem.fiyat) {
                                degisiklikVarMi = true;
                                if (Notification.permission === "granted") {
                                    new Notification(`⚠️ Hurda Fiyatı Değişti: ${yeniFirma.baslik}`, {
                                        body: `${yeniKalem.cins} fiyatı güncellendi!\nEski: ${eskiKalem.fiyat} ➔ Yeni: ${yeniKalem.fiyat}`,
                                        icon: "https://cdn-icons-png.flaticon.com/512/2954/2954884.png"
                                    });
                                }
                            }
                        });
                    }
                });

                if (degisiklikVarMi) {
                    eskiVerilerKarsilastirma = JSON.stringify(yeniVeriListesi);
                }
            }

            async function fetchPrices() {
                try {
                    const response = await fetch('/prices');
                    const result = await response.json();
                    if (result.status === "success") {
                        document.getElementById("sonGuncelleme").innerText = result.son_guncelleme;
                        if(result.data.length > 0) {
                            fiyatlariKarsilastirVeBildir(result.data);
                            renderCards(result.data);
                        }
                    }
                } catch (error) { console.error("Hata:", error); }
            }

            async function triggerRefresh() {
                const btn = document.getElementById("refreshBtn");
                btn.disabled = true;
                btn.innerText = "Veriler taranıyor, lütfen bekleyin...";
                try {
                    const response = await fetch('/refresh');
                    const result = await response.json();
                    if (result.status === "success") {
                        document.getElementById("sonGuncelleme").innerText = result.son_guncelleme;
                        if(result.data.length > 0) {
                            fiyatlariKarsilastirVeBildir(result.data);
                            renderCards(result.data);
                        }
                    }
                } catch (error) {
                    console.error("Yenileme hatası:", error);
                    alert("Yenileme sırasında bir hata oluştu.");
                } finally {
                    btn.disabled = false;
                    btn.innerText = "Önbelleği Temizle & Yenile";
                }
            }

            function renderCards(data) {
                const container = document.getElementById("cardsContainer");
                container.innerHTML = "";
                data.forEach(item => {
                    let kalemlerHtml = "";
                    item.kalemler.forEach(k => {
                        kalemlerHtml += `
                            <div class="flex justify-between items-center py-3.5 px-1 border-b border-slate-100 last:border-none">
                                <span class="font-semibold text-slate-700 text-sm">${k.cins}</span>
                                <div class="text-right flex items-center space-x-2.5">
                                    <span class="text-slate-900 font-extrabold text-sm">${k.fiyat}</span>
                                    <span class="text-[11px] px-2 py-0.5 rounded-lg font-bold text-emerald-700 bg-emerald-50">${k.degisim}</span>
                                </div>
                            </div>
                        `;
                    });
                    const card = document.createElement("div");
                    card.className = "bg-white rounded-3xl shadow-sm border border-slate-200/70 overflow-hidden";
                    card.innerHTML = `
                        <div class="bg-slate-900 text-white px-6 py-5 flex justify-between items-center">
                            <div>
                                <h3 class="font-bold text-base">${item.baslik}</h3>
                                <a href="${item.url}" target="_blank" class="text-[11px] text-indigo-300 underline block mt-0.5">Resmi Kaynağa Git ↗</a>
                            </div>
                            <span class="text-xs bg-white/10 px-3 py-1 rounded-xl">${item.tarih}</span>
                        </div>
                        <div class="p-6"><div class="divide-y divide-slate-100">${kalemlerHtml}</div></div>
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
