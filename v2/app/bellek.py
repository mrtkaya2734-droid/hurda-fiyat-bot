"""
Bellek ve sunucu yükü yardımcıları.

Render Free (512 MB RAM, 0.1 CPU) üzerinde süreç belleği günler içinde
~100 MB'tan ~150 MB'a tırmanıyordu. Python nesneleri serbest bırakılsa da
glibc, iş parçacığı başına açtığı bellek alanlarını (arena) işletim sistemine
geri vermiyor; tarama turları her seferinde yeni iş parçacıkları açtığı için
boşta kalan bellek birikiyor. Burada:

- arena sayısı sınırlanır (en fazla 2),
- tarama turlarından sonra boş bellek işletim sistemine iade edilir,
- dış kaynaklardan veri çeken fonksiyonlar için "tek uçuş" önbellek sağlanır:
  aynı anda gelen istekler kaynağa tek bir istek atar, süresi dolmuş veri
  varsa ziyaretçi beklemeden onu alır ve yenileme arka planda yapılır.
"""

import ctypes
import gc
import threading
import time


_M_ARENA_MAX = -8

try:
    _LIBC = ctypes.CDLL("libc.so.6")
except OSError:
    _LIBC = None


def arena_sinirla(adet=2):
    """glibc arena sayısını sınırlar. glibc yoksa (macOS vb.) sessizce geçer."""
    if _LIBC is None:
        return False
    try:
        return bool(_LIBC.mallopt(_M_ARENA_MAX, int(adet)))
    except (AttributeError, OSError):
        return False


def bellegi_birak():
    """Çöp toplayıcıyı çalıştırır ve boş belleği işletim sistemine iade eder."""
    gc.collect()
    if _LIBC is None:
        return
    try:
        _LIBC.malloc_trim(0)
    except (AttributeError, OSError):
        pass


def rss_mb():
    """Sürecin o anki fiziksel bellek kullanımı (MB); okunamazsa None."""
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as dosya:
            sayfa = int(dosya.read().split()[1])
        import resource

        return round(sayfa * resource.getpagesize() / (1024 * 1024), 1)
    except (OSError, ValueError, IndexError):
        return None


class TekUcusOnbellek:
    """
    Dış kaynaktan veri çeken bir fonksiyon için önbellek.

    - Taze veri varsa doğrudan döner.
    - Süresi dolmuş veri varsa onu hemen döner ve yenilemeyi arka planda,
      aynı anda en fazla bir kez başlatır (stale-while-revalidate).
    - Hiç veri yoksa çağıran bekler; aynı anda gelen diğer istekler de aynı
      sonucu bekler, kaynağa ikinci kez gidilmez.
    - Hata olursa `hata_bekleme` saniye boyunca yeniden denenmez.
    """

    def __init__(self, ad, cek, sure, hata_bekleme=60):
        self.ad = ad
        self._cek = cek
        self.sure = sure
        self.hata_bekleme = hata_bekleme
        self._kilit = threading.Lock()
        self._bayrak_kilidi = threading.Lock()
        self._deger = None
        self._zaman = 0.0
        self._son_hata = 0.0
        self._calisiyor = False

    def _yenile(self):
        try:
            deger = self._cek()
        except Exception as exc:
            self._son_hata = time.monotonic()
            print(f"{self.ad} YENİLEME HATASI: {type(exc).__name__}: {exc}")
            raise
        else:
            self._deger = deger
            self._zaman = time.monotonic()
            self._son_hata = 0.0
            return deger
        finally:
            self._calisiyor = False

    def _arka_planda_yenile(self):
        def calis():
            try:
                with self._kilit:
                    self._yenile()
            except Exception:
                pass

        threading.Thread(
            target=calis,
            name=f"yenile-{self.ad}",
            daemon=True,
        ).start()

    def getir(self, zorla=False):
        simdi = time.monotonic()
        taze = self._deger is not None and simdi - self._zaman < self.sure

        if taze and not zorla:
            return self._deger

        hata_yeni = self._son_hata and simdi - self._son_hata < self.hata_bekleme

        if self._deger is not None and not zorla:
            # Eski veriyi hemen ver; yenilemeyi tek seferde arka planda yap.
            if not hata_yeni and not self._calisiyor:
                with self._bayrak_kilidi:
                    baslat = not self._calisiyor
                    if baslat:
                        self._calisiyor = True
                if baslat:
                    self._arka_planda_yenile()
            return self._deger

        with self._kilit:
            # Kilidi beklerken başka bir iş parçacığı yenilemiş olabilir.
            if (
                not zorla
                and self._deger is not None
                and time.monotonic() - self._zaman < self.sure
            ):
                return self._deger

            if (
                not zorla
                and self._son_hata
                and time.monotonic() - self._son_hata < self.hata_bekleme
                and self._deger is None
            ):
                raise RuntimeError(f"{self.ad} kaynağı şu anda yanıt vermiyor.")

            self._calisiyor = True
            return self._yenile()

    def degeri_ayarla(self, deger):
        self._deger = deger
        self._zaman = time.monotonic()
