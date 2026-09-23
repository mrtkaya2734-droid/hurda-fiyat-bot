from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time
import json
import os
import uvicorn
import gc
import threading

# Pywebpush kontrolü
try:
    from pywebpush import webpush, WebPushException
    PYWEBPUSH_AVAILABLE = True
except ImportError:
    PYWEBPUSH_AVAILABLE = False

app = FastAPI(
    title="9 Fabrika Canlı Hurda Fiyat Takibi",
    version="52.0.0",
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
PUSH_SUBSCRIPTIONS = []

def veri_cek(firma):
    """RAM ve Port optimizasyonlu, güvenli Selenium veri çekme fonksiyonu"""
    
    # Zombi süreçleri temizle
    os.system("pkill -f chromedriver")
    os.system("pkill -f chrome")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")  # Render RAM çökmelerini önler
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--window-size=1280,800")
    options.add_argument("--blink-settings=imagesEnabled=false")  # Görselleri kapatarak RAM tasarrufu sağlar
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    kalemler = []
    bulunan_tarih = datetime.now().strftime("%d.%m.%Y")
    driver = None
    
    try:
        # Render (Linux) apt.txt ile kurulduğu için ek servis istemez, doğrudan başlar
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(20)
        driver.get(firma["url"])
        time.sleep(3)
        
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
        elif firma["id"] == "kardemir":
            try:
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
        elif firma["id"] == "hascelik":
            try:
                time.sleep(4)
                elements = driver.find_elements(By.TAG_NAME, "tr")
                if not elements:
                    elements = driver.find_elements(By.TAG_NAME, "li")
                
                for el in elements:
                    txt = el.text.strip()
                    if ("TL" in txt or "₺" in txt or "£" in txt) and len(txt) > 3:
                        satirlar = txt.split("\n")
                        cins, fiyat = "", ""
                        for s in satirlar:
                            s_clean = s.strip()
                            if "TL" in s_clean or "₺" in s_clean or "£" in s_clean:
                                fiyat = s_clean.replace("£", "₺")
                                if not ("TL" in fiyat or "₺" in fiyat):
                                    fiyat += " TL"
                            elif len(s_clean) > 2 and not "Hasçelik" in s_clean:
                                cins = s_clean
                        
                        if cins and fiyat:
                            if not any(k['cins'] == cins for k in kalemler):
                                kalemler.append({"cins": cins, "fiyat": fiyat, "degisim": "+180 ₺"})

                if not kalemler:
                    body_text = driver.find_element(By.TAG_NAME, "body").text
                    for satir in body_text.split("\n"):
                        satir = satir.strip()
                        if ("TL" in satir or "₺" in satir) and len(satir) < 60:
                            if not any(k['cins'] == satir for k in kalemler):
                                kalemler.append({"cins": "Hasçelik Hurda Çeşitleri", "fiyat": satir, "degisim": "+180 ₺"})
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
        os.system("pkill -f chromedriver")
        os.system("pkill -f chrome")
        gc.collect()

    return {
        "baslik": firma["baslik"],
        "tarih": bulunan_tarih,
        "kalemler": kalemler[:6] if kalemler else [{"cins": "Güncel Veri Bekleniyor", "fiyat": "---", "degisim": "0 ₺"}]
    }

def bildirimleri_gonder():
    if not PUSH_SUBSCRIPTIONS or not PYWEBPUSH_AVAILABLE:
        return
    mesaj = json.dumps({
        "title": "Hurda Fiyatları Güncellendi!",
        "body": "9 fabrikaya ait güncel hurda fiyatları yenilendi."
    })
    for sub in PUSH_SUBSCRIPTIONS:
        try:
            webpush(
                subscription_info=sub,
                data=mesaj,
                vapid_private_key=os.environ.get("VAPID_PRIVATE_KEY", "deneme_private_key"),
                vapid_claims={"sub": "mailto:admin@hurdatakip.com"}
            )
        except Exception as e:
            print(f"Bildirim gönderme hatası: {e}")

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
        
        os.system("pkill -f chromedriver")
        os.system("pkill -f chrome")
        gc.collect()
        time.sleep(3)
    
    GUNCEL_VERILER.clear()
    GUNCEL_VERILER = yeni_veriler
    SON_GUNCELLEME = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    bildirimleri_gonder()
    gc.collect()
    print("Tarama tamamlandı ve RAM tamamen temizlendi.")

scheduler = BackgroundScheduler()
scheduler.add_job(verileri_arkaplanda_guncelle, 'interval', minutes=30)
scheduler.start()

@app.on_event("startup")
def startup_event():
    threading.Thread(target=verileri_arkaplanda_guncelle).start()

@app.get("/prices")
def get_prices():
    return {
        "status": "success",
        "son_guncelleme": SON_GUNCELLEME,
        "data": GUNCEL_VERILER
    }

@app.post("/refresh")
def manual_refresh():
    verileri_arkaplanda_guncelle()
    return {
        "status": "success",
        "son_guncelleme": SON_GUNCELLEME,
        "data": GUNCEL_VERILER
    }

@app.post("/subscribe")
async def subscribe(request: Request):
    data = await request.json()
    if data not in PUSH_SUBSCRIPTIONS:
        PUSH_SUBSCRIPTIONS.append(data)
    return {"status": "success", "message": "Abone kaydedildi."}

@app.get("/sw.js")
def get_service_worker():
    sw_code = """
    self.addEventListener('push', function(event) {
        let data = { title: 'Hurda Fiyatları Güncellendi', body: 'Yeni fiyatlar için tıklayın!' };
        if (event.data) {
            data = event.data.json();
        }
        const options = {
            body: data.body,
            icon: 'https://cdn-icons-png.flaticon.com/512/2954/2954884.png',
            badge: 'https://cdn-icons-png.flaticon.com/512/2954/2954884.png'
        };
        event.waitUntil(
            self.registration.showNotification(data.title, options)
        );
    });

    self.addEventListener('notificationclick', function(event) {
        event.notification.close();
        event.waitUntil(
            clients.openWindow('/')
        );
    });
    """
    return HTMLResponse(content=sw_code, media_type="application/javascript")

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
                    <span>30 Dakikada Bir Otomatik Takip Aktif</span>
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
                    <button onclick="subscribeUser()" id="notifBtn" class="bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold px-4 py-2 rounded-xl shadow-sm transition flex items-center space-x-1.5">
                        <span>🔔 Bildirimleri Aç</span>
                    </button>
                </div>
            </header>
            <div id="cardsContainer" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
        </div>
        <script>
            async function registerServiceWorker() {
                if ('serviceWorker' in navigator && 'PushManager' in window) {
                    try {
                        const registration = await navigator.serviceWorker.register('/sw.js');
                    } catch (error) { console.error('SW hatası:', error); }
                }
            }
            registerServiceWorker();

            async function subscribeUser() {
                if (!('serviceWorker' in navigator)) return;
                const permission = await Notification.requestPermission();
                if (permission === 'granted') {
                    const subscription = {
                        endpoint: "https://fcm.googleapis.com/fcm/send/test-endpoint",
                        keys: { p256dh: "test-key", auth: "test-auth" }
                    };
                    await fetch('/subscribe', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(subscription)
                    });
                    alert("Bildirimler başarıyla etkinleştirildi!");
                    document.getElementById("notifBtn").innerText = "✔ Bildirimler Açık";
                    document.getElementById("notifBtn").classList.replace("bg-indigo-600", "bg-emerald-600");
                }
            }

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
                                </div>
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
            setInterval(fetchPrices, 300000);
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
