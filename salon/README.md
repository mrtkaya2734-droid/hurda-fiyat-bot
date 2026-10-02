# Gül Aksu Yönetim Paneli

Bağımsız, bağımlılıksız panel. Çalıştırmak için `index.html` dosyasını tarayıcıda açmak yeterli (internet gerekmez).

- İlk açılışta yönetici şifresi belirlenir (varsayılan şifre yoktur). Personel şifrelerini yönetici belirler.
- Şifreler tuzlu SHA-256 ile saklanır; çıktıda tüm kullanıcı metinleri kaçışlanır (XSS).
- Veri `localStorage`'dadır: **Ayarlar → Yedek indir** ile düzenli yedek alın.

## Web sitesiyle bağlantı (planlanan)
Web sitesi ayrı bir projedir. Yalnızca sitedeki randevu formu panele "randevu talebi" gönderir;
talep panelde **Onay bekleyen** olarak düşer, yönetici onaylayınca kayda geçer.

Sözleşme (`js/store.js` → `GulAksuAPI.randevuTalebi`):
`{ musteri, telefon, hizmet, personelId, baslangic, bitis, tutar }`

Hosting'e geçerken `Adapter` (load/save) bir REST API'ye çevrilir ve bu fonksiyon
`POST /api/randevu-talebi` uç noktasına dönüşür; panel kodu değişmez.
Not: panel ve site farklı adreslerde çalışacağı için, localde ortak veri için küçük bir yerel sunucu gerekecek.
