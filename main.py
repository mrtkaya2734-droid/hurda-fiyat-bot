from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time
import re
import os
import uvicorn
import gc

app = FastAPI(
    title="9 Fabrika Canlı Hurda Fiyat Takibi",
    version="46.0.0",
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

def veri_cek(firma):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--window-size=1024,768")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

    kalemler = []
    bulunan_tarih = datetime.now().strftime("%d.%m.%Y")
    driver = None
    
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(15)
        driver.get(firma["url"])
        time.sleep(2)
        
        # 1. ÇOLAKOĞLU METALURJİ
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
            except Exception as e:
                print(f"Çolakoğlu hata: {e}")

        # 2. KARDEMİR
       # 2. KARDEMİR
        elif firma["id"] == "kardemir":
            try:
                # Sayfadaki tüm satırları ve tablo elemanlarını daha esnek tarayalım
                elements = driver.find_elements(By.TAG_NAME, "tr")
                if not elements:
                    elements = driver.find_elements(By.TAG_NAME, "li")
                
                for el in elements:
                    txt = el.text.strip()
                    if ("TL" in txt or "₺" in txt) and len(txt) > 5:
                        parcalar = txt.split("\n")
                        cins, fiyat = "", ""
                        for p in parcalar:
                            p_clean = p.strip()
                            if "TL" in p_clean or "₺" in p_clean:
                                fiyat = p_clean
                            elif len(p_clean) > 2 and not "Kardemir" in p_clean:
                                cins = p_clean
                        
                        if cins and fiyat:
                            if not any(k['cins'] == cins for k in kalemler):
                                kalemler.append({"cins": cins, "fiyat": fiyat, "degisim": "+250 ₺"})
                
                # Eğer tablolardan bulunamadıysa body metninden esnek arama yapalım
                if not kalemler:
                    tum_metin = driver.find_element(By.TAG_NAME, "body").text
                    satirlar = tum_metin.split("\n")
                    for satir in satirlar:
                        satir = satir.strip()
                        if ("TL" in satir or "₺" in satir) and len(satir) > 5:
                            if not any(k['cins'] == satir for k in kalemler):
                                kalemler.append({"cins": "Kardemir Hurda Çeşitleri", "fiyat": satir, "degisim": "+250 ₺"})
            except Exception as e:
                print(f"Kardemir hata: {e}")

        # 3. ERDEMİR & 4. İSDEMİR
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

        # 5. KROMAN, 6. DİLER, 7. EKİNCİLER
        elif firma["id"] in ["kroman", "diler", "ekinciler"]:
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
                            if not fiyat:
                                fiyat = cols[1].text.strip()

                            if cins and fiyat:
                                if not any(k['cins'] == cins for k in kalemler):
                                    kalemler.append({
                                        "cins": cins,
                                        "fiyat": fiyat if ("TL" in fiyat or "₺" in fiyat) else fiyat + " TL",
                                        "degisim": "+220 ₺"
                                    })
            except Exception as e:
                print(f"{firma['baslik']} hata: {e}")

        # 8. HASÇELİK
       # 8. HASÇELİK
        elif firma["id"] == "hascelik":
            try:
                # Önce standart tablolara bakalım
                tables = driver.find_elements(By.TAG_NAME, "table")
                for table in tables:
                    rows = table.find_elements(By.TAG_NAME, "tr")
                    for row in rows:
                        cols = row.find_elements(By.TAG_NAME, "td")
                        if len(cols) >= 2:
                            cins = cols[0].text.strip()
                            fiyat = cols[1].text.strip()
                            if cins and fiyat:
                                temiz_fiyat = fiyat.replace("£", "₺").strip()
                                if not ("TL" in temiz_fiyat or "₺" in temiz_fiyat):
                                    temiz_fiyat += " TL"
                                if not any(k['cins'] == cins for k in kalemler):
                                    kalemler.append({
                                        "cins": cins,
                                        "fiyat": temiz_fiyat,
                                        "degisim": "+180 ₺"
                                    })
                
                # Eğer tablolardan veri gelmediyse div, p veya listeleme etiketlerinden esnek arama yapalım
                if not kalemler:
                    elementler = driver.find_elements(By.TAG_NAME, "div")
                    for el in elementler:
                        txt = el.text.strip()
                        if ("TL" in txt or "₺" in txt or "£" in txt) and len(txt) < 100:
                            satirlar = txt.split("\n")
                            cins, fiyat = "", ""
                            for satir in satirlar:
                                s = satir.strip()
                                if "TL" in s or "₺" in s or "£" in s:
                                    fiyat = s.replace("£", "₺")
                                elif len(s) > 2 and not "Hasçelik" in s:
                                    cins = s
                            if cins and fiyat:
                                if not any(k['cins'] == cins for k in kalemler):
                                    kalemler.append({"cins": cins, "fiyat": fiyat, "degisim": "+180 ₺"})
            except Exception as e:
                print(f"Hasçelik hata: {e}")

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
            except Exception as e:
                print(f"Asil Çelik hata: {e}")

    except Exception as e:
        print(f"{firma['baslik']} tarama hatası: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
        gc.collect()

    return {
        "baslik": firma["baslik"],
        "url": firma["url"],
        "tarih": bulunan_tarih,
        "kalemler": kalemler[:6] if kalemler else [{"cins": "Güncel Veri Bekleniyor", "fiyat": "---", "degisim": "0 ₺"}]
    }

def verileri_arkaplanda_guncelle():
    global GUNCEL_VERILER, SON_GUNCELLEME
    print(f"[{datetime.now()}] 9 fabrika sırayla taranıyor (RAM Dostu Mod)...")
    
    yeni_veriler = []
    # RAM'i patlatmamak için paralel işlem yerine döngüyle tek tek sırayla çekiyoruz
    for firma in FIRMALAR:
        try:
            res = veri_cek(firma)
            yeni_veriler.append(res)
        except Exception as ex:
            print(f"{firma['baslik']} taranamadı: {ex}")
        time.sleep(1) # Tarayıcılar arası kısa dinlenme
    
    GUNCEL_VERILER.clear()
    GUNCEL_VERILER = yeni_veriler
    SON_GUNCELLEME = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    gc.collect()
    print("Tüm tarama tamamlandı, bellek temizlendi.")

# Güncelleme aralığını 4 saate çıkararak RAM tüketim sınırlarında güvenle çalışmasını sağlıyoruz
scheduler = BackgroundScheduler()
scheduler.add_job(verileri_arkaplanda_guncelle, 'interval', hours=4)
scheduler.start()

@app.on_event("startup")
def startup_event():
    # Uygulama açılışında ilk taramayı tetikle
    verileri_arkaplanda_guncelle()

@app.get("/prices")
def get_prices():
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
        "icons": [
            {
                "src": "https://cdn-icons-png.flaticon.com/512/2954/2954884.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable"
            }
        ]
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
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
        <meta name="apple-mobile-web-app-title" content="Hurda Takip">
        <link rel="apple-touch-icon" href="https://cdn-icons-png.flaticon.com/512/2954/2954884.png">
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 text-slate-900 font-sans antialiased">
        <div class="max-w-7xl mx-auto px-4 py-10">
            <header class="text-center mb-12">
                <div class="inline-flex items-center space-x-2 bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs font-bold px-3.5 py-1.5 rounded-full mb-3 shadow-sm">
                    <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                    <span>Mobil Uygulama (PWA) Modu Aktif</span>
                </div>
                <h1 class="text-3xl font-black text-slate-900 tracking-tight">9 Fabrika Güncel Hurda Fiyatları</h1>
                <p class="text-slate-500 text-sm mt-1.5">Canlı Takip Paneli</p>
                <div class="mt-4 inline-flex items-center text-xs text-slate-600 bg-white border border-slate-200/80 px-4 py-2 rounded-xl shadow-sm">
                    <span class="font-medium text-slate-500 mr-1.5">Son Güncelleme:</span>
                    <span id="sonGuncelleme" class="font-bold text-slate-800">Yükleniyor...</span>
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
                        document.getElementById("sonGuncelleme").innerText = result.son_guncelleme;
                        renderCards(result.data);
                    }
                } catch (error) { console.error("Hata:", error); }
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
                                </div>
                            </div>
                        `;
                    });

                    const card = document.createElement("div");
                    card.className = "bg-white rounded-3xl shadow-sm border border-slate-200/70 overflow-hidden hover:shadow-md transition duration-300";
                    card.innerHTML = `
                        <div class="bg-gradient-to-r from-slate-900 via-slate-800 to-indigo-950 text-white px-6 py-5 flex justify-between items-center">
                            <div>
                                <h3 class="font-bold text-base tracking-wide">${item.baslik}</h3>
                                <a href="${item.url}" target="_blank" class="text-[11px] text-indigo-300 hover:text-white underline transition block mt-0.5 font-medium">Resmi Kaynağa Git ↗</a>
                            </div>
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
            setInterval(fetchPrices, 300000);
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)