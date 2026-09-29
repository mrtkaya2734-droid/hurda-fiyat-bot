from app.scrapers import TUMU

for firma_id, fonksiyon in TUMU:
    print(f"\n=== {firma_id} ===")
    try:
        s = fonksiyon()
        print(f"Fiyat tarihi: {s.fiyat_tarihi}")
        for k in s.kalemler:
            fark = f"  (eski: {k.eski_fiyat}, fark: {k.fiyat - k.eski_fiyat:+d})" if k.eski_fiyat else ""
            print(f"  {k.cins:<20} {k.fiyat:>7} TL{fark}")
    except Exception as e:
        print(f"  HATA: {type(e).__name__}: {e}")