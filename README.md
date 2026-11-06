# Hurda Fiyat Takibi

Fabrika hurda alım fiyatlarını otomatik çeken ve yayınlayan FastAPI uygulaması.

- Uygulama kodu: `v2/` (Render `rootDir`)
- Başlatma: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Yönetim paneli: `/admin` (`ADMIN_USER` / `ADMIN_PASS` ortam değişkenleri)
- Scraper'lar: `v2/app/scrapers/`
- Yerelde tüm kaynakları denemek için: `cd v2 && python -m app.run_once`
- Canlı LME (isteğe bağlı): Render'da `METALCHARTS_API_KEY` tanımlanırsa Alüminyum, Nikel,
  Çinko ve Kurşun için gün içi fiyat MetalCharts'tan alınır (`v2/app/lme_canli.py`).
  Ücretsiz planda ayda 200 istek var; istek aralığı kalan hakka göre otomatik ayarlanır
  (hafta içi 09:00-21:00, ~1 saatte bir). Bakır ve Kalay resmî (1 gün gecikmeli) veriden gelir.
