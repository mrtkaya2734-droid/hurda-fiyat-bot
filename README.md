# Hurda Fiyat Takibi

Fabrika hurda alım fiyatlarını otomatik çeken ve yayınlayan FastAPI uygulaması.

- Uygulama kodu: `v2/` (Render `rootDir`)
- Başlatma: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Yönetim paneli: `/admin` (`ADMIN_USER` / `ADMIN_PASS` ortam değişkenleri)
- Scraper'lar: `v2/app/scrapers/`
- Yerelde tüm kaynakları denemek için: `cd v2 && python -m app.run_once`
