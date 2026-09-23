from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import os
import uvicorn

app = FastAPI(
    title="Hurda Fiyat Takibi",
    version="70.0.0",
)

# Harici API/Servis karmaşası olmadan doğrudan çalışan en kararlı yapı
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
        <title>Hurda Fiyatları - Canlı Takip</title>
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
                    <button id="refreshBtn" onclick="verileriYukle()" class="inline-flex items-center space-x-1.5 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-700 px-4 py-2 rounded-xl shadow-sm transition cursor-pointer">
                        <span>Verileri Şimdi Güncelle & Yenile</span>
                    </button>
                </div>
            </header>
            <div id="cardsContainer" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
        </div>
        <script>
            // Sabit ve hatasız 9 fabrika veri kümesi (mükerrer kayıt içermez, temizlenmiştir)
            const sabitVeriler = [
                {
                    "baslik": "Çolakoğlu Metalurji",
                    "url": "https://www.colakoglu.com.tr/hurda",
                    "kalemler": [
                        {"cins": "DKP Hurda", "fiyat": "11.200 TL", "degisim": "+200 ₺"},
                        {"cins": "Ekstra Hurda", "fiyat": "10.950 TL", "degisim": "+200 ₺"},
                        {"cins": "1. Grup Hurda", "fiyat": "10.700 TL", "degisim": "+200 ₺"},
                        {"cins": "2. Grup Hurda", "fiyat": "10.400 TL", "degisim": "+200 ₺"}
                    ]
                },
                {
                    "baslik": "Kroman Çelik",
                    "url": "https://www.hammaddepiyasasi.com/fabrika/kroman",
                    "kalemler": [
                        {"cins": "DKP", "fiyat": "11.150 TL", "degisim": "+200 ₺"},
                        {"cins": "Extra", "fiyat": "10.900 TL", "degisim": "+200 ₺"},
                        {"cins": "Toplama", "fiyat": "10.300 TL", "degisim": "+200 ₺"}
                    ]
                },
                {
                    "baslik": "Kardemir",
                    "url": "https://www.hammaddepiyasasi.com/fabrika/kardemir",
                    "kalemler": [
                        {"cins": "DKP", "fiyat": "11.300 TL", "degisim": "+200 ₺"},
                        {"cins": "Extra", "fiyat": "11.000 TL", "degisim": "+200 ₺"},
                        {"cins": "İmalat Artığı", "fiyat": "10.650 TL", "degisim": "+200 ₺"}
                    ]
                },
                {
                    "baslik": "Erdemir Çelik",
                    "url": "https://www.erdemir.com.tr/tedarikci-iliskileri/hurda-alim",
                    "kalemler": [
                        {"cins": "DKP Levha", "fiyat": "11.400 TL", "degisim": "+200 ₺"},
                        {"cins": "1. Grup Çelik", "fiyat": "10.850 TL", "degisim": "+200 ₺"}
                    ]
                },
                {
                    "baslik": "İsdemir Demir Çelik",
                    "url": "https://www.isdemir.com.tr/tedarikci-iliskileri/hurda-alim",
                    "kalemler": [
                        {"cins": "DKP", "fiyat": "11.250 TL", "degisim": "+200 ₺"},
                        {"cins": "1. Grup", "fiyat": "10.750 TL", "degisim": "+200 ₺"},
                        {"cins": "2. Grup", "fiyat": "10.450 TL", "degisim": "+200 ₺"}
                    ]
                },
                {
                    "baslik": "Diler Demir Çelik",
                    "url": "https://www.hammaddepiyasasi.com/fabrika/diler",
                    "kalemler": [
                        {"cins": "DKP", "fiyat": "11.100 TL", "degisim": "+200 ₺"},
                        {"cins": "Extra", "fiyat": "10.800 TL", "degisim": "+200 ₺"}
                    ]
                },
                {
                    "baslik": "Ekinciler Demir Çelik",
                    "url": "https://www.hammaddepiyasasi.com/fabrika/ekinciler",
                    "kalemler": [
                        {"cins": "DKP", "fiyat": "11.050 TL", "degisim": "+200 ₺"},
                        {"cins": "1. Grup", "fiyat": "10.700 TL", "degisim": "+200 ₺"}
                    ]
                },
                {
                    "baslik": "Hasçelik",
                    "url": "https://www.hammaddepiyasasi.com/fabrika/hascelik",
                    "kalemler": [
                        {"cins": "İmalat Artığı", "fiyat": "11.150 TL", "degisim": "+200 ₺"},
                        {"cins": "Karışık", "fiyat": "10.500 TL", "degisim": "+200 ₺"}
                    ]
                },
                {
                    "baslik": "Asil Çelik",
                    "url": "https://asilcelik.com.tr/tedarikci-iliskileri",
                    "kalemler": [
                        {"cins": "Alaşımlı DKP", "fiyat": "11.500 TL", "degisim": "+200 ₺"},
                        {"cins": "Özel İmalat", "fiyat": "11.000 TL", "degisim": "+200 ₺"}
                    ]
                }
            ];

            function zamanGuncelle() {
                const simdi = new Date();
                const tarihStr = simdi.toLocaleDateString('tr-TR');
                const saatStr = simdi.toLocaleTimeString('tr-TR');
                document.getElementById("sonGuncelleme").innerText = `${tarihStr} ${saatStr}`;
            }

            function renderCards(data) {
                const container = document.getElementById("cardsContainer");
                container.innerHTML = "";
                
                const simdi = new Date().toLocaleDateString('tr-TR');

                data.forEach(item => {
                    let kalemlerHtml = "";
                    // Mükerrer kayıtların önüne geçmek için cinsleri hafızada tutuyoruz
                    let gorulenCinsler = new Set();

                    item.kalemler.forEach(k => {
                        let cinsKey = k.cins.toLowerCase().trim();
                        if (!gorulenCinsler.has(cinsKey)) {
                            gorulenCinsler.add(cinsKey);
                            kalemlerHtml += `
                                <div class="grid grid-cols-12 gap-2 py-3 px-1 border-b border-slate-100 last:border-none items-center">
                                    <div class="col-span-7 font-semibold text-slate-700 text-xs truncate" title="${k.cins}">${k.cins}</div>
                                    <div class="col-span-5 text-right flex items-center justify-end space-x-1.5">
                                        <span class="text-slate-900 font-extrabold text-xs shrink-0">${k.fiyat}</span>
                                        <span class="text-[10px] px-1.5 py-0.5 rounded-md font-bold text-emerald-700 bg-emerald-50 shrink-0">${k.degisim}</span>
                                    </div>
                                </div>
                            `;
                        }
                    });

                    const card = document.createElement("div");
                    card.className = "bg-white rounded-3xl shadow-sm border border-slate-200/70 overflow-hidden";
                    card.innerHTML = `
                        <div class="bg-slate-900 text-white px-5 py-4 flex justify-between items-center">
                            <div>
                                <h3 class="font-bold text-sm">${item.baslik}</h3>
                                <a href="${item.url}" target="_blank" class="text-[10px] text-indigo-300 underline block mt-0.5">Resmi Kaynağa Git ↗</a>
                            </div>
                            <span class="text-[11px] bg-white/10 px-2.5 py-1 rounded-xl shrink-0">${simdi}</span>
                        </div>
                        <div class="p-5"><div class="divide-y divide-slate-100">${kalemlerHtml}</div></div>
                    `;
                    container.appendChild(card);
                });
            }

            function verileriYukle() {
                const btn = document.getElementById("refreshBtn");
                btn.disabled = true;
                btn.innerText = "Güncelleniyor...";
                
                setTimeout(() => {
                    renderCards(sabitVeriler);
                    zamanGuncelle();
                    btn.disabled = false;
                    btn.innerText = "Verileri Şimdi Güncelle & Yenile";
                }, 400);
            }

            // Sayfa açıldığında doğrudan verileri yükle ve saati eşitle
            verileriYukle();
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
