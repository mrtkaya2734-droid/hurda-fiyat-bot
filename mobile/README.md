# Mobil uygulama (Android + iOS)

Web uygulamasını (`v2/`) telefon uygulaması olarak paketleyen Capacitor kabuğu.
Uygulama açılınca web sitesini yükler; fiyat/özellik değişiklikleri için yeniden
derlemeye gerek yoktur. İnternet yoksa "Tekrar dene" ekranı gösterilir.

- Paket adı: `com.cevhersan.exchange` — Ad: **Cevhersan Exchange**
- Simge/açılış ekranı: `assets/` (değiştirip yeniden derleyin)
- `android/` ve `ios/` klasörleri derleme sırasında üretilir, depoda tutulmaz.

## APK / IPA üretmek (bilgisayar gerekmez)

1. GitHub > **Actions** > *Cevhersan Exchange mobil (APK + iOS)* > **Run workflow**
2. `app_url` alanına sitenin https adresini yazın (ör. `https://hurda.ornek.com`).
   Her seferinde yazmamak için: Settings > Secrets and variables > Actions >
   Variables > `APP_URL`.
3. İş bitince çalıştırma sayfasının altındaki **Artifacts** bölümünden indirin:
   - `CevhersanExchange-android-apk` → `app-debug.apk` (telefona doğrudan kurulur; "bilinmeyen kaynaklara izin ver" gerekir)
   - `CevhersanExchange-ios-unsigned-ipa` → imzasız `.ipa`

## iOS kurulumu hakkında

iOS imzasız IPA'yı doğrudan açmaz. Mağazasız denemek için ücretsiz Apple ID ile
AltStore / Sideloadly ile kurulur (7 günde bir yenilenir). Kalıcı kurulum, TestFlight
ve App Store için ücretli Apple Developer hesabı (99 $/yıl) ve imzalama sertifikası gerekir.

## VPS'e taşınınca

Yeni adresle iş akışını yeniden çalıştırın; başka değişiklik gerekmez.

## Bilinen sınırlama

Mevcut Web Push bildirimleri telefon WebView'inde çalışmaz. Uygulamada bildirim için
Firebase (Android) / APNs (iOS) ile yerel push eklenmesi gerekir — sonraki adım.

## Yerelde derlemek (isteğe bağlı)

```
cd mobile && npm ci
APP_URL=https://siteniz npm run www
npx cap add android && npm run icons && npx cap sync android
cd android && ./gradlew assembleDebug
```
