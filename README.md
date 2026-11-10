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
  (LME işlem saatlerinde, hafta içi Londra 01:00-19:00; ücretsiz planda ~2 saatte bir). Bakır için LME karşılığı olmadığından COMEX fiyatı
  "COMEX" etiketiyle ayrıca gösterilir (TL/Ton LME resmî fiyatından); Kalay resmî (1 gün gecikmeli) veriden gelir.
- Supabase uyumasın diye: Free plan, veritabanı sorgusu almayan projeyi 7 günde duraklatıyor
  (Storage trafiği sayılmıyor). Uygulama 8 saatte bir `public.canli_kalma` tablosundaki tek
  satırı günceller (`storage.veritabani_canli_tut`). Tablo yoksa Supabase SQL düzenleyicisinde
  oluşturun: `create table public.canli_kalma (id smallint primary key default 1 check (id = 1),
  son_ping timestamptz not null default now(), kaynak text); alter table public.canli_kalma enable
  row level security; grant select, update on public.canli_kalma to service_role;
  insert into public.canli_kalma (id) values (1);` (Bu projede yeni tablolara varsayılan yazma
  yetkisi verilmiyor; `grant` satırı olmadan uygulama 403 alır.)
