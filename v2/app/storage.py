import json
import unicodedata
import os
import re
import shutil
import tempfile
import threading
import time
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# =========================================================
# DOSYA YOLLARI
# =========================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

BUNDLED_DATA_FILE = os.path.join(
    BASE_DIR,
    "data.json"
)

DEFAULT_DATA_DIR = (
    "/var/data"
    if os.path.isdir("/var/data")
    else BASE_DIR
)

DATA_FILE = os.getenv(
    "DATA_FILE",
    os.path.join(
        DEFAULT_DATA_DIR,
        "data.json"
    )
)



# =========================================================
# SUPABASE KALICI DEPOLAMA
# =========================================================
# Render Free yerel dosya sistemi kalıcı değildir. Supabase Storage
# ayarlanmışsa uygulamanın JSON verilerini ve medya dosyalarını
# güvenli sunucu tarafında kalıcı olarak saklarız.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()
SUPABASE_BUCKET = os.getenv(
    "SUPABASE_BUCKET",
    "hurda-data",
).strip()

_SUPABASE_DATA_SYNCED = False
_SAVE_LOCK = threading.Lock()


def _supabase_enabled():
    return bool(
        SUPABASE_URL
        and SUPABASE_SECRET_KEY
        and SUPABASE_BUCKET
    )


def _supabase_headers(content_type=None):
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        # Supabase secret key sunucu tarafında service key olarak
        # Storage RLS kontrollerini bypass edebilir.
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
    }

    if content_type:
        headers["Content-Type"] = content_type

    return headers


def _supabase_object_url(object_name):
    object_name = str(
        object_name or ""
    ).strip().lstrip("/")

    return (
        f"{SUPABASE_URL}/storage/v1/object/"
        f"{SUPABASE_BUCKET}/{object_name}"
    )


def supabase_storage_indir_durumlu(object_name):
    """
    ("ok", bayt) | ("yok", None) | ("hata", None).

    404 = dosya gerçekten yok. Zaman aşımı/5xx = "hata": bu durumda uzak
    verinin var olup olmadığı bilinmez ve üzerine YAZILMAMALIDIR.
    """
    if not _supabase_enabled():
        return "yok", None

    try:
        response = requests.get(
            _supabase_object_url(object_name),
            headers=_supabase_headers(),
            timeout=20,
        )

        # Supabase, olmayan nesne için 400/404 döndürebilir.
        if response.status_code == 404 or (
            response.status_code == 400 and "not_found" in response.text.lower()
        ):
            return "yok", None

        response.raise_for_status()

        return "ok", response.content

    except Exception as exc:
        print(
            "SUPABASE İNDİRME HATASI: "
            f"{type(exc).__name__}: {exc}"
        )
        return "hata", None


def supabase_storage_download(object_name):
    durum, icerik = supabase_storage_indir_durumlu(object_name)
    return icerik if durum == "ok" else None


def supabase_storage_upload(
    local_path,
    object_name,
    content_type=None,
):
    if not _supabase_enabled():
        return False

    if not os.path.exists(local_path):
        return False

    try:
        with open(
            local_path,
            "rb",
        ) as file:
            response = requests.post(
                _supabase_object_url(object_name),
                headers={
                    **_supabase_headers(content_type),
                    "x-upsert": "true",
                },
                data=file,
                timeout=30,
            )

        response.raise_for_status()

        return True

    except Exception as exc:
        print(
            "SUPABASE YÜKLEME HATASI: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


def supabase_restore_file(
    local_path,
    object_name,
):
    content = supabase_storage_download(
        object_name
    )

    if content is None:
        return False

    try:
        os.makedirs(
            os.path.dirname(local_path),
            exist_ok=True,
        )

        temporary = (
            local_path
            + ".supabase.tmp"
        )

        with open(
            temporary,
            "wb",
        ) as file:
            file.write(content)

        os.replace(
            temporary,
            local_path,
        )

        return True

    except Exception as exc:
        print(
            "SUPABASE YEREL KOPYA HATASI: "
            f"{type(exc).__name__}: {exc}"
        )
        return False


_SUPABASE_SYNC_DENEME = 0.0
_SUPABASE_SYNC_BEKLEME = 30


def _supabase_sync_data_once():
    """
    Açılışta uzak data.json'ı yerel dosyaya alır.

    Güvenlik kuralı: uzak veri yalnızca Supabase "dosya yok" (404) dediğinde
    yerel dosyayla doldurulur. İndirme geçici olarak başarısız olursa
    (zaman aşımı, 5xx) hiçbir şey yüklenmez ve senkron 30 sn sonra yeniden
    denenir; böylece eski/paket içindeki bir kopya gerçek verinin üzerine
    yazılamaz.
    """
    global _SUPABASE_DATA_SYNCED
    global _SUPABASE_SYNC_DENEME

    if _SUPABASE_DATA_SYNCED:
        return

    if not _supabase_enabled():
        _SUPABASE_DATA_SYNCED = True
        return

    simdi = time.time()
    if simdi - _SUPABASE_SYNC_DENEME < _SUPABASE_SYNC_BEKLEME:
        return
    _SUPABASE_SYNC_DENEME = simdi

    durum, remote = "hata", None
    for deneme in range(3):
        durum, remote = supabase_storage_indir_durumlu("data.json")
        if durum != "hata":
            break
        time.sleep(2 * (deneme + 1))

    if durum == "hata":
        print(
            "SUPABASE: data.json indirilemedi; uzak veri korunuyor, "
            "yükleme yapılmayacak, yeniden denenecek."
        )
        return

    try:
        if durum == "ok":
            parsed = json.loads(remote.decode("utf-8"))

            if isinstance(parsed, dict):
                os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)

                temporary = DATA_FILE + ".supabase.tmp"

                with open(temporary, "w", encoding="utf-8") as file:
                    json.dump(parsed, file, ensure_ascii=False, indent=4)

                os.replace(temporary, DATA_FILE)

                print("SUPABASE: data.json geri yüklendi.")
                _SUPABASE_DATA_SYNCED = True
                return

            print("SUPABASE: uzak data.json geçersiz biçimde; dokunulmadı.")
            return

        # durum == "yok": uzakta gerçekten dosya yok, ilk kez yükle.
        if os.path.exists(DATA_FILE):
            supabase_storage_upload(
                DATA_FILE,
                "data.json",
                "application/json",
            )
            print("SUPABASE: mevcut data.json ilk kez yüklendi.")

        _SUPABASE_DATA_SYNCED = True

    except Exception as exc:
        print(
            "SUPABASE VERİ SENKRON HATASI: "
            f"{type(exc).__name__}: {exc}"
        )


_SUPABASE_YEDEK_SON = 0.0


def _supabase_yedek_al():
    """Günde en fazla iki kez Supabase'de zaman damgalı yedek kopya tutar."""
    global _SUPABASE_YEDEK_SON

    simdi = time.time()
    if simdi - _SUPABASE_YEDEK_SON < 6 * 3600:
        return
    _SUPABASE_YEDEK_SON = simdi

    zaman = datetime.now(ZoneInfo("Europe/Istanbul"))
    yarim = "a" if zaman.hour < 12 else "p"

    supabase_storage_upload(
        DATA_FILE,
        f"backups/data-{zaman.strftime('%Y%m%d')}-{yarim}.json",
        "application/json",
    )


def supabase_nesneleri_listele(onek):
    """Bucket'ta `onek/` altındaki dosya yollarını döndürür; hata olursa None."""
    onek = str(onek).strip("/")

    try:
        response = requests.post(
            f"{SUPABASE_URL}/storage/v1/object/list/{SUPABASE_BUCKET}",
            headers=_supabase_headers("application/json"),
            json={
                "prefix": onek,
                "limit": 1000,
                "offset": 0,
                "sortBy": {"column": "name", "order": "asc"},
            },
            timeout=20,
        )
        response.raise_for_status()

        return [
            f"{onek}/{item['name']}"
            for item in response.json()
            if item.get("id")
        ]

    except Exception as exc:
        print(f"SUPABASE LİSTELEME HATASI: {type(exc).__name__}: {exc}")
        return None


def supabase_nesne_sil(object_name):
    try:
        response = requests.delete(
            _supabase_object_url(object_name),
            headers=_supabase_headers(),
            timeout=20,
        )
        return response.status_code in (200, 204, 404)

    except Exception as exc:
        print(f"SUPABASE SİLME HATASI: {type(exc).__name__}: {exc}")
        return False


def yedekleri_yenile(etiket, ek_dosyalar=None):
    """
    Güncel durumun yedeğini alır, doğrular ve ANCAK SONRA eski yedekleri siler.

    - Yerel: BACKUP_DIR içine data-<etiket>.json; diğer *.json yedekler silinir.
    - Uzak (Supabase): backups/ altına data-<etiket>.json (+ ek dosyalar);
      yeni dosyalar listede görünmeden hiçbir şey silinmez.
    ek_dosyalar: {"ads": "/yol/ads.json"}
    """
    ozet = {"etiket": etiket, "yerel_silinen": 0, "uzak_silinen": 0, "uzak_yeni": []}

    os.makedirs(BACKUP_DIR, exist_ok=True)
    yeni_yerel = os.path.join(BACKUP_DIR, f"data-{etiket}.json")
    shutil.copy2(DATA_FILE, yeni_yerel)

    yeni_uzak = []

    if _supabase_enabled():
        kaynaklar = {f"backups/data-{etiket}.json": DATA_FILE}
        for ad, yol in (ek_dosyalar or {}).items():
            if yol and os.path.exists(yol):
                kaynaklar[f"backups/{ad}-{etiket}.json"] = yol

        for nesne, yol in kaynaklar.items():
            if not supabase_storage_upload(yol, nesne, "application/json"):
                raise RuntimeError(f"Yeni yedek yüklenemedi: {nesne}")
            yeni_uzak.append(nesne)

        mevcut = supabase_nesneleri_listele("backups")
        if mevcut is None or not all(n in mevcut for n in yeni_uzak):
            raise RuntimeError("Yeni yedek doğrulanamadı; eski yedekler silinmedi.")

        for yol in mevcut:
            if yol not in yeni_uzak and supabase_nesne_sil(yol):
                ozet["uzak_silinen"] += 1

    for dosya in os.listdir(BACKUP_DIR):
        yol = os.path.join(BACKUP_DIR, dosya)
        if dosya.endswith(".json") and yol != yeni_yerel:
            try:
                os.remove(yol)
                ozet["yerel_silinen"] += 1
            except OSError:
                pass

    ozet["uzak_yeni"] = yeni_uzak
    return ozet


def manuel_yedek_al(ek_dosyalar=None):
    """Mevcut yedeklere dokunmadan zaman damgalı yeni yedek alır."""
    etiket = "manuel-" + datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%Y%m%d-%H%M%S")
    yuklenen = []

    os.makedirs(BACKUP_DIR, exist_ok=True)
    shutil.copy2(DATA_FILE, os.path.join(BACKUP_DIR, f"data-{etiket}.json"))

    if _supabase_enabled():
        kaynaklar = {f"backups/data-{etiket}.json": DATA_FILE}
        for ad, yol in (ek_dosyalar or {}).items():
            if yol and os.path.exists(yol):
                kaynaklar[f"backups/{ad}-{etiket}.json"] = yol

        for nesne, yol in kaynaklar.items():
            if supabase_storage_upload(yol, nesne, "application/json"):
                yuklenen.append(nesne)

    return {"etiket": etiket, "uzak": yuklenen}


def supabase_upload_json(
    local_path,
    object_name,
):
    return supabase_storage_upload(
        local_path,
        object_name,
        "application/json",
    )


# =========================================================
# OTOMATİK VERİ YEDEĞİ
# =========================================================
# Render /var/data kalıcı disk kullanıyorsa, veri dosyasının
# üzerine yazılmadan önce periyodik bir geri dönüş kopyası tutulur.
# Mevcut veri yapısı değiştirilmez; yalnızca yedek dosyası oluşturulur.
BACKUP_ENABLED = (
    os.getenv(
        "DATA_BACKUP_ENABLED",
        "1",
    ).strip().lower()
    in {"1", "true", "yes", "on"}
)

BACKUP_DIR = os.path.join(
    os.path.dirname(DATA_FILE),
    "backups",
)

BACKUP_INTERVAL_SECONDS = 6 * 60 * 60


def _periyodik_veri_yedegi():

    if not BACKUP_ENABLED:
        return

    if not os.path.exists(DATA_FILE):
        return

    try:
        os.makedirs(
            BACKUP_DIR,
            exist_ok=True,
        )

        yedekler = [
            os.path.join(BACKUP_DIR, isim)
            for isim in os.listdir(BACKUP_DIR)
            if isim.endswith(".json")
        ]

        simdi = datetime.now().timestamp()

        if yedekler:
            son_yedek = max(
                yedekler,
                key=lambda yol: os.path.getmtime(yol),
            )

            if (
                simdi - os.path.getmtime(son_yedek)
                < BACKUP_INTERVAL_SECONDS
            ):
                return

        damga = datetime.now().strftime(
            "%Y%m%d-%H%M%S"
        )

        hedef = os.path.join(
            BACKUP_DIR,
            f"data-{damga}.json",
        )

        shutil.copy2(
            DATA_FILE,
            hedef,
        )

        # Disk alanını gereksiz tüketmemesi için son 10 yedeği tut.
        yedekler = sorted(
            [
                os.path.join(BACKUP_DIR, isim)
                for isim in os.listdir(BACKUP_DIR)
                if isim.endswith(".json")
            ],
            key=lambda yol: os.path.getmtime(yol),
            reverse=True,
        )

        for eski in yedekler[10:]:
            try:
                os.remove(eski)
            except OSError:
                pass

    except Exception as exc:
        print(
            "VERİ YEDEK HATASI: "
            f"{type(exc).__name__}: {exc}"
        )

if DATA_FILE != BUNDLED_DATA_FILE:
    os.makedirs(
        os.path.dirname(DATA_FILE),
        exist_ok=True,
    )


# =========================================================
# TEMEL VERİ YAPISI
# =========================================================

DEFAULT_DATA = {
    "firms": {},
    "prices": {},
    "history": [],
    "notifications": [],
    "gizlenen_kalemler": {},
}


# =========================================================
# TARİH / SAAT
# =========================================================

def now_string():
    return datetime.now(
        ZoneInfo("Europe/Istanbul")
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# =========================================================
# VERİ YÜKLEME
# =========================================================

def load_data():

    _supabase_sync_data_once()

    if not os.path.exists(DATA_FILE):

        if (
            DATA_FILE != BUNDLED_DATA_FILE
            and os.path.exists(
                BUNDLED_DATA_FILE
            )
        ):

            try:

                with open(
                    BUNDLED_DATA_FILE,
                    "r",
                    encoding="utf-8"
                ) as source:

                    initial_data = json.load(
                        source
                    )

                save_data(
                    initial_data
                )

            except Exception:
                pass

        if not os.path.exists(
            DATA_FILE
        ):

            return {
                "firms": {},
                "prices": {},
                "history": [],
                "notifications": [],
                "gizlenen_kalemler": {},
            }

    try:

        with open(
            DATA_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if not isinstance(data, dict):
            data = {}

        if not isinstance(
            data.get("firms"),
            dict
        ):
            data["firms"] = {}

        if not isinstance(
            data.get("prices"),
            dict
        ):
            data["prices"] = {}

        if not isinstance(
            data.get("history"),
            list
        ):
            data["history"] = []

        if not isinstance(
            data.get("notifications"),
            list
        ):
            data["notifications"] = []

        if not isinstance(
            data.get("gizlenen_kalemler"),
            dict
        ):
            data["gizlenen_kalemler"] = {}

        # Eski kayıtlarda firma ID büyük/küçük harf nedeniyle
        # aynı firma iki ayrı anahtar altında oluşabiliyordu.
        # Örn: "Ekinciler" ve "ekinciler".
        # Bunları tek kanonik ID altında birleştir.
        def _canonicalize_map(mapping):
            normalized = {}

            for key, value in mapping.items():
                canonical = str(
                    key or ""
                ).strip().lower()

                if not canonical:
                    continue

                if canonical not in normalized:
                    if isinstance(value, dict):
                        normalized[canonical] = dict(value)
                    elif isinstance(value, list):
                        normalized[canonical] = list(value)
                    else:
                        normalized[canonical] = value
                    continue

                existing = normalized[canonical]

                if isinstance(existing, dict) and isinstance(value, dict):
                    # Aynı firma farklı harf kullanımıyla iki kez kayıtlıysa
                    # firma/fiyat alt kayıtlarını tek tek birleştir.
                    # Böylece bir kaydın diğerinin kalemlerini ezmesi önlenir.
                    merged = dict(existing)

                    for sub_key, sub_value in value.items():
                        if (
                            sub_key in merged
                            and isinstance(merged[sub_key], dict)
                            and isinstance(sub_value, dict)
                        ):
                            item = dict(merged[sub_key])
                            item.update(sub_value)
                            merged[sub_key] = item
                        elif (
                            sub_key in merged
                            and isinstance(merged[sub_key], list)
                            and isinstance(sub_value, list)
                        ):
                            combined = list(merged[sub_key])
                            for item in sub_value:
                                if item not in combined:
                                    combined.append(item)
                            merged[sub_key] = combined
                        else:
                            merged[sub_key] = sub_value

                    normalized[canonical] = merged

                elif isinstance(existing, list) and isinstance(value, list):
                    combined = list(existing)
                    for item in value:
                        if item not in combined:
                            combined.append(item)
                    normalized[canonical] = combined

                elif not existing:
                    normalized[canonical] = value

            return normalized

        data["firms"] = _canonicalize_map(
            data["firms"]
        )

        data["prices"] = _canonicalize_map(
            data["prices"]
        )

        data["gizlenen_kalemler"] = _canonicalize_map(
            data["gizlenen_kalemler"]
        )

        for firma_id, firma in data["firms"].items():
            if isinstance(firma, dict):
                firma["firma_id"] = firma_id

        return data

    except Exception:

        return {
            "firms": {},
            "prices": {},
            "history": [],
            "notifications": [],
            "gizlenen_kalemler": {},
        }

# =========================================================
# VERİ KAYDETME
# =========================================================

def save_data(data):

    os.makedirs(
        os.path.dirname(DATA_FILE),
        exist_ok=True,
    )

    # Aynı anda gelen kayıtlar aynı .tmp dosyasını paylaşmasın.
    # Özellikle Render üzerinde zamanlayıcı + admin işlemleri
    # çakıştığında veri dosyasının kaybolmasını önler.
    with _SAVE_LOCK:
        _periyodik_veri_yedegi()

        temporary_file = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=os.path.dirname(DATA_FILE),
                prefix=".data-",
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary_file = file.name
                json.dump(
                    data,
                    file,
                    ensure_ascii=False,
                    indent=4,
                )
                file.flush()
                os.fsync(file.fileno())

            os.replace(
                temporary_file,
                DATA_FILE,
            )
            temporary_file = None

            # Render Free yerel dosyası kalıcı olmadığından,
            # her başarılı veri kaydından sonra Supabase'i güncelle.
            if _supabase_enabled():
                if _SUPABASE_DATA_SYNCED:
                    if supabase_upload_json(DATA_FILE, "data.json"):
                        _supabase_yedek_al()
                else:
                    print(
                        "SUPABASE: senkron tamamlanmadığı için yükleme "
                        "atlandı (uzak veri ezilmesin)."
                    )

        finally:
            if temporary_file:
                try:
                    os.remove(temporary_file)
                except OSError:
                    pass


# =========================================================
# FİRMA ID NORMALİZASYONU
# =========================================================

def firma_id_normalize(firma_id):

    if firma_id is None:
        return ""

    return str(
        firma_id
    ).strip().lower()


# =========================================================
# FİRMA GETİR
# =========================================================

def firma_getir(firma_id):

    firma_id = firma_id_normalize(
        firma_id
    )

    data = load_data()

    return data["firms"].get(
        firma_id
    )


# =========================================================
# FİRMA KAYDET
# =========================================================

def firma_kaydet(
    firma_id,
    ad,
    url="",
    aktif=True
):

    firma_id = firma_id_normalize(
        firma_id
    )

    data = load_data()

    eski = data["firms"].get(
        firma_id,
        {}
    )

    data["firms"][firma_id] = {

        "id": firma_id,

        "ad": ad,

        "url": url,

        "aktif": bool(aktif),

        "eklenme": eski.get(
            "eklenme",
            now_string()
        ),

        "guncelleme": now_string(),
    }

    save_data(data)

    return data["firms"][firma_id]


# =========================================================
# FİRMA SİL
# =========================================================

def firma_sil(firma_id):

    firma_id = firma_id_normalize(
        firma_id
    )

    data = load_data()

    if firma_id not in data["firms"]:
        return False

    del data["firms"][firma_id]

    if firma_id in data["prices"]:
        del data["prices"][firma_id]

    save_data(data)

    return True


# =========================================================
# FİRMA GÜNCELLE
# =========================================================

def firma_guncelle(
    firma_id,
    **kwargs
):

    firma_id = firma_id_normalize(
        firma_id
    )

    data = load_data()

    if firma_id not in data["firms"]:
        return False

    firma = data["firms"][firma_id]

    for key, value in kwargs.items():

        if value is not None:
            firma[key] = value

    firma["guncelleme"] = now_string()

    save_data(data)

    return True


# =========================================================
# FİYAT KAYDET
# =========================================================

def fiyat_kaydet(
    firma_id,
    kalem,
    otomatik_fiyat=None,
    manuel_fiyat=None,
    fiyat_tarihi=None
):

    firma_id_gelen = str(firma_id or "").strip()
    data = load_data()

    # Mevcut fiyat anahtarını büyük/küçük harf duyarsız bul.
    # Böylece "Ekinciler" ile "ekinciler" için ikinci bir kayıt oluşmaz.
    firma_id = next(
        (
            mevcut_id
            for mevcut_id in data.get("prices", {})
            if str(mevcut_id).strip().casefold()
            == firma_id_gelen.casefold()
        ),
        firma_id_normalize(firma_id_gelen),
    )

    if firma_id not in data["prices"]:
        data["prices"][firma_id] = {}

    mevcut = data["prices"][firma_id].get(
        kalem,
        {}
    )

    if otomatik_fiyat is None:

        otomatik_fiyat = mevcut.get(
            "otomatik_fiyat"
        )

    if manuel_fiyat is None:

        manuel_fiyat = mevcut.get(
            "manuel_fiyat"
        )

    # Yeniden manuel olarak kaydedilen kalemi
    # gizli listesinden çıkar.
    gizlenen = data.get(
        "gizlenen_kalemler",
        {}
    ).get(
        firma_id,
        []
    )

    kalem_anahtari = (
        " ".join(
            str(kalem or "").strip().split()
        ).casefold()
    )

    if kalem_anahtari in {
        " ".join(
            str(x or "").strip().split()
        ).casefold()
        for x in gizlenen
    }:
        data["gizlenen_kalemler"][firma_id] = [
            x for x in gizlenen
            if (
                " ".join(
                    str(x or "").strip().split()
                ).casefold()
                != kalem_anahtari
            )
        ]

    if fiyat_tarihi is None:

        fiyat_tarihi = mevcut.get(
            "fiyat_tarihi"
        )

    data["prices"][firma_id][kalem] = {

        "otomatik_fiyat":
            otomatik_fiyat,

        "manuel_fiyat":
            manuel_fiyat,

        "fiyat_tarihi":
            fiyat_tarihi,

        "guncelleme":
            now_string(),
    }

    save_data(data)

    return data["prices"][firma_id][kalem]


_GORUNMEZ = re.compile(r"[\u200b-\u200f\u2060\ufeff\u00ad]")
_FIYAT_GECMISI = re.compile(
    r"\s+Hurda\s+Fiyat\s+geçmişi\s*$",
    re.IGNORECASE,
)


_KATLA = str.maketrans("şŞçÇğĞöÖüÜıİIâÂîÎûÛ", "ssccggoouuiiiaaiiuu")
_KENAR = " ,;:/|•·–—-()[]\"'"
_GECMIS_EKLERI = (
    ("hurda", "fiyat", "gecmisi"),
    ("hurda", "fiyati", "gecmisi"),
    ("fiyat", "gecmisi"),
    ("fiyati", "gecmisi"),
)


def _tr_anahtar(metin):
    """Büyük/küçük harf ve Türkçe karakter farkını yok sayan karşılaştırma anahtarı."""
    return str(metin or "").translate(_KATLA).lower()


def _jeton_anahtari(jeton):
    return _tr_anahtar(jeton).strip(_KENAR)


def kalem_adi_temizle(deger):
    """
    Kalem adını tek biçime getirir.

    Tekrarlar şu biçimlerde de yakalanır: 'Talaş Talaş', 'TALAŞ Talaş',
    'Talaş Talas', 'Talaş, Talaş', 'Talaş - Talaş', 'TalaşTalaş',
    'Talaş Talaş Hurda' ve 'Talaş Talaş Hurda Fiyat geçmişi' -> 'Talaş'.
    Tekrar yoksa ad yalnızca boşluk/görünmez karakter açısından düzeltilir.
    """
    metin = unicodedata.normalize("NFKC", str(deger or ""))
    metin = _GORUNMEZ.sub("", metin)
    metin = " ".join(metin.split())
    metin = _FIYAT_GECMISI.sub("", metin).strip()

    cift = [(j, _jeton_anahtari(j)) for j in metin.split(" ")]
    cift = [c for c in cift if c[1]]

    # Sondaki "Hurda Fiyat geçmişi" gibi site ekleri.
    eki_kirpildi = False
    for ek in _GECMIS_EKLERI:
        n = len(ek)
        if len(cift) > n and tuple(c[1] for c in cift[-n:]) == ek:
            cift = cift[:-n]
            eki_kirpildi = True
            break

    # Ardışık tekrar eden kelime dizilerini (her yerde) tek kez bırak.
    tekrar_var = False
    while True:
        n = len(cift)
        bulundu = False
        for k in range(n // 2, 0, -1):
            for i in range(0, n - 2 * k + 1):
                if [c[1] for c in cift[i:i + k]] == [c[1] for c in cift[i + k:i + 2 * k]]:
                    del cift[i + k:i + 2 * k]
                    bulundu = tekrar_var = True
                    break
            if bulundu:
                break
        if not bulundu:
            break

    if tekrar_var:
        metin = " ".join(j for j, _ in cift).strip(_KENAR)
    elif eki_kirpildi:
        # Yalnızca site eki kırpıldı.
        metin = " ".join(j for j, _ in cift).strip()

    # Bitişik tekrar: "TalaşTalaş"
    kati = _tr_anahtar(metin)
    eslesme = re.fullmatch(r"(.{3,}?)\1", kati)
    if eslesme:
        metin = metin[: len(eslesme.group(1))]

    return metin.strip()


def _yazim_anahtari(ad):
    """Boşluk, noktalama, harf boyu ve Türkçe karakter farkını yok sayar."""
    return re.sub(r"[\W_]+", "", _tr_anahtar(ad))


def colakoglu_yazimlarini_birlestir(data):
    """
    Çolakoğlu'nda aynı cinsin farklı yazımlarını ('1.GRUP' / '1. GRUP',
    'TALAŞ' / 'Talas') ilk görülen ada çevirir; sonrasını veriyi_duzelt birleştirir.
    """
    firma = "colakoglu"
    kanonik = {}
    degisti = False

    def ad_bul(ad):
        nonlocal degisti
        temiz = kalem_adi_temizle(ad) or ad
        anahtar = _yazim_anahtari(temiz)
        if not anahtar:
            return ad
        hedef = kanonik.setdefault(anahtar, temiz)
        if hedef != ad:
            degisti = True
        return hedef

    kalemler = (data.get("prices") or {}).get(firma)
    if kalemler:
        data["prices"][firma] = {}
        for ad, bilgi in kalemler.items():
            hedef = ad_bul(ad)
            mevcut = data["prices"][firma].get(hedef)
            if mevcut and str(mevcut.get("guncelleme", "")) > str(bilgi.get("guncelleme", "")):
                continue
            data["prices"][firma][hedef] = bilgi

    for h in data.get("history") or []:
        if str(h.get("firma_id", "")).strip().casefold() == firma:
            h["kalem"] = ad_bul(h.get("kalem"))

    gizli = (data.get("gizlenen_kalemler") or {}).get(firma)
    if gizli:
        data["gizlenen_kalemler"][firma] = [ad_bul(x) for x in gizli]

    return degisti


def veriyi_duzelt(data):
    """
    Mevcut kayıtları temizler (idempotent). Değişiklik olduysa True döner.

    - Kalem adlarındaki tekrarları birleştirir (fiyat + geçmiş + gizli liste).
    - Geçmişte aynı fiyatın art arda tekrarlarını tek kayda indirir.
    """
    degisti = colakoglu_yazimlarini_birlestir(data)

    prices = data.get("prices", {})
    for firma_id, kalemler in list(prices.items()):
        yeni = {}
        for kalem, bilgi in kalemler.items():
            temiz = kalem_adi_temizle(kalem) or kalem
            if temiz != kalem:
                degisti = True
            if temiz in yeni:
                # Aynı kalem iki adla tutulmuş: en yeni güncellemeyi seç.
                eski = yeni[temiz]
                if str(bilgi.get("guncelleme", "")) >= str(eski.get("guncelleme", "")):
                    if eski.get("manuel_fiyat") is not None and bilgi.get("manuel_fiyat") is None:
                        bilgi = dict(bilgi, manuel_fiyat=eski["manuel_fiyat"])
                    yeni[temiz] = bilgi
                elif eski.get("manuel_fiyat") is None and bilgi.get("manuel_fiyat") is not None:
                    eski["manuel_fiyat"] = bilgi["manuel_fiyat"]
                degisti = True
            else:
                yeni[temiz] = bilgi
        prices[firma_id] = yeni

    gizli = data.get("gizlenen_kalemler", {})
    for firma_id, liste in list(gizli.items()):
        temiz_liste = []
        for x in liste:
            t = kalem_adi_temizle(x) or x
            if t not in temiz_liste:
                temiz_liste.append(t)
        if temiz_liste != liste:
            gizli[firma_id] = temiz_liste
            degisti = True

    history = data.get("history", [])
    if isinstance(history, list):
        sirali = sorted(
            history,
            key=lambda h: str(h.get("tarih", "")),
        )
        son = {}
        yeni_gecmis = []
        for h in sirali:
            h = dict(h)
            temiz = kalem_adi_temizle(h.get("kalem")) or h.get("kalem")
            h["kalem"] = temiz
            anahtar = (str(h.get("firma_id", "")).strip().casefold(), temiz)
            if anahtar in son and son[anahtar] == h.get("fiyat"):
                continue
            son[anahtar] = h.get("fiyat")
            yeni_gecmis.append(h)
        if len(yeni_gecmis) != len(history) or any(
            a.get("kalem") != b.get("kalem")
            for a, b in zip(sirali, yeni_gecmis)
        ):
            data["history"] = yeni_gecmis
            degisti = True

    return degisti


def gecmise_fiyat_yaz(data, firma_id, kalem, onceki, yeni, fiyat_tarihi=None, zaman=None):
    """
    Bir fiyat değişimini geçmişe yazar (yalnızca son kayıtlı fiyattan farklıysa).
    Kalem için hiç geçmiş yoksa, değişimin hesaplanabilmesi için önceki fiyat
    1 saniye öncesine 'tohum' kayıt olarak eklenir. data üzerinde çalışır,
    kaydetmez. Dönüş: yeni kayıt eklendiyse True.
    """
    if yeni is None:
        return False

    history = data.setdefault("history", [])
    temiz = kalem_adi_temizle(kalem) or kalem
    anahtar = (str(firma_id or "").strip().casefold(), temiz)

    var_mi = False
    son = None
    for h in reversed(history):
        h_anahtar = (
            str(h.get("firma_id", "")).strip().casefold(),
            kalem_adi_temizle(h.get("kalem")) or h.get("kalem"),
        )
        if h_anahtar == anahtar:
            var_mi = True
            son = h.get("fiyat")
            break

    if var_mi and son == yeni:
        return False

    simdi = zaman or datetime.now(ZoneInfo("Europe/Istanbul"))
    damga = simdi.strftime("%Y-%m-%d %H:%M:%S")

    if not var_mi and onceki is not None and onceki != yeni:
        history.append(
            {
                "firma_id": firma_id,
                "kalem": temiz,
                "fiyat": onceki,
                "fiyat_tarihi": fiyat_tarihi,
                "tarih": (simdi - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    history.append(
        {
            "firma_id": firma_id,
            "kalem": temiz,
            "fiyat": yeni,
            "fiyat_tarihi": fiyat_tarihi,
            "tarih": damga,
        }
    )
    return True


def manuel_gecmisi_tamamla(data):
    """
    Geçmişe hiç yazılmamış elle fiyatları (eski toplu kayıt / yeni kalem yolları)
    geçmişe işler. Aynı fiyat geçmişte zaten varsa dokunmaz (idempotent).
    Dönüş: eklenen kalem sayısı.
    """
    eklenen = 0

    for firma_id, kalemler in (data.get("prices") or {}).items():
        for kalem, bilgi in kalemler.items():
            manuel = bilgi.get("manuel_fiyat")
            if manuel is None:
                continue

            temiz = kalem_adi_temizle(kalem) or kalem
            anahtar = (str(firma_id).strip().casefold(), temiz)

            kayitli = any(
                (
                    str(h.get("firma_id", "")).strip().casefold(),
                    kalem_adi_temizle(h.get("kalem")) or h.get("kalem"),
                ) == anahtar
                and h.get("fiyat") == manuel
                for h in data.get("history", [])
            )

            if kayitli:
                continue

            if gecmise_fiyat_yaz(
                data, firma_id, kalem, bilgi.get("otomatik_fiyat"), manuel,
                bilgi.get("fiyat_tarihi"),
            ):
                eklenen += 1

    return eklenen


def fiyatlari_toplu_kaydet(
    firma_id,
    kalemler,
    fiyat_tarihi=None,
):
    """
    Bir firmanın otomatik fiyatlarını tek load/save ile kaydeder.

    kalemler: [(cins, fiyat[, kaynak_eski_fiyat]), ...]
    Geçmişe yalnızca fiyat gerçekten değiştiğinde (veya ilk kez) kayıt
    eklenir. Dönüş: {cins: onceki_fiyat_veya_None} (yalnız değişenler).
    """
    firma_id_gelen = str(firma_id or "").strip()
    data = load_data()

    prices = data.setdefault("prices", {})
    history = data.setdefault("history", [])

    firma_id = next(
        (
            mevcut_id
            for mevcut_id in prices
            if str(mevcut_id).strip().casefold()
            == firma_id_gelen.casefold()
        ),
        firma_id_normalize(firma_id_gelen),
    )

    firma_fiyatlari = prices.setdefault(firma_id, {})

    # Her kalemin geçmişteki son fiyatını tek geçişte bul.
    son_gecmis = {}
    for item in history:
        if str(item.get("firma_id", "")).strip().casefold() != firma_id_gelen.casefold():
            continue
        son_gecmis[item.get("kalem")] = item.get("fiyat")

    simdi = now_string()
    degisenler = {}

    for giris in kalemler:
        kalem, fiyat = kalem_adi_temizle(giris[0]) or giris[0], giris[1]
        kaynak_eski = giris[2] if len(giris) > 2 else None
        mevcut = firma_fiyatlari.get(kalem, {})
        onceki = mevcut.get("otomatik_fiyat")

        firma_fiyatlari[kalem] = {
            "otomatik_fiyat": fiyat,
            "manuel_fiyat": mevcut.get("manuel_fiyat"),
            "fiyat_tarihi": (
                fiyat_tarihi
                if fiyat_tarihi is not None
                else mevcut.get("fiyat_tarihi")
            ),
            "guncelleme": simdi,
        }

        # Kaynağın kendi yayınladığı önceki fiyat (Erdemir/İsdemir gibi).
        if kaynak_eski is not None:
            firma_fiyatlari[kalem]["kaynak_eski_fiyat"] = kaynak_eski

        manuel_aktif = mevcut.get("manuel_fiyat") is not None

        if not manuel_aktif and (kalem not in son_gecmis or son_gecmis[kalem] != fiyat):
            history.append(
                {
                    "firma_id": firma_id_gelen,
                    "kalem": kalem,
                    "fiyat": fiyat,
                    "fiyat_tarihi": fiyat_tarihi,
                    "tarih": simdi,
                }
            )

        if onceki is not None and onceki != fiyat:
            degisenler[kalem] = onceki

    save_data(data)

    return degisenler


# =========================================================
# FİYAT GETİR
# =========================================================

def fiyat_getir(
    firma_id,
    kalem=None
):

    firma_id = firma_id_normalize(
        firma_id
    )

    data = load_data()

    firma_fiyatlari = data["prices"].get(
        firma_id,
        {}
    )

    if kalem is None:
        return firma_fiyatlari

    return firma_fiyatlari.get(
        kalem
    )


# =========================================================
# MANUEL FİYAT KAYDET
# =========================================================

def manuel_fiyat_kaydet(
    firma_id,
    kalem,
    fiyat
):

    firma_id = firma_id_normalize(
        firma_id
    )

    data = load_data()

    if firma_id not in data["prices"]:
        data["prices"][firma_id] = {}

    mevcut = data["prices"][firma_id].get(
        kalem,
        {}
    )

    onceki_efektif = (
        mevcut.get("manuel_fiyat")
        if mevcut.get("manuel_fiyat") is not None
        else mevcut.get("otomatik_fiyat")
    )

    mevcut["manuel_fiyat"] = fiyat

    mevcut["fiyat_tarihi"] = (
        datetime.now(
            ZoneInfo("Europe/Istanbul")
        ).strftime("%Y-%m-%d")
    )

    mevcut["guncelleme"] = now_string()

    gizlenen = data.get(
        "gizlenen_kalemler",
        {}
    ).get(
        firma_id,
        []
    )

    if kalem in gizlenen:
        data["gizlenen_kalemler"][firma_id] = [
            x for x in gizlenen
            if x != kalem
        ]

    data["prices"][firma_id][kalem] = mevcut

    gecmise_fiyat_yaz(
        data,
        firma_id,
        kalem,
        onceki_efektif,
        fiyat,
        mevcut.get("fiyat_tarihi"),
    )

    save_data(data)

    return True


# =========================================================
# MANUEL FİYAT SİL
# =========================================================

def manuel_fiyat_sil(
    firma_id,
    kalem
):

    firma_id_gelen = str(firma_id or "").strip()
    data = load_data()

    firma_id = next(
        (
            mevcut_id
            for mevcut_id in data.get("prices", {})
            if str(mevcut_id).strip().casefold()
            == firma_id_gelen.casefold()
        ),
        firma_id_normalize(firma_id_gelen),
    )

    firma_fiyatlari = data["prices"].get(
        firma_id
    )

    if not firma_fiyatlari:
        return False

    if kalem not in firma_fiyatlari:
        return False

    # Kalemi tamamen kaldır.
    firma_fiyatlari.pop(
        kalem,
        None,
    )

    # Otomatik scraper aynı kalemi tekrar gönderse bile
    # bir sonraki güncellemede geri gelmesini engelle.
    gizlenen = data.get(
        "gizlenen_kalemler",
        {}
    )

    mevcut_gizli = gizlenen.get(
        firma_id,
        []
    )

    kalem_anahtari = (
        " ".join(
            str(kalem or "").strip().split()
        ).casefold()
    )

    mevcut_gizli_anahtarlari = {
        " ".join(
            str(x or "").strip().split()
        ).casefold()
        for x in mevcut_gizli
    }

    if kalem_anahtari not in mevcut_gizli_anahtarlari:
        mevcut_gizli.append(
            kalem
        )

    gizlenen[
        firma_id
    ] = mevcut_gizli

    data[
        "gizlenen_kalemler"
    ] = gizlenen

    save_data(
        data
    )

    return True


# =========================================================
# BİLDİRİM EKLE
# =========================================================

def bildirim_ekle(
    firma_id,
    tur,
    mesaj
):

    data = load_data()

    mevcut_idler = []

    for item in data["notifications"]:

        try:

            mevcut_idler.append(
                int(
                    item.get(
                        "id",
                        0
                    )
                )
            )

        except (
            TypeError,
            ValueError
        ):

            pass

    yeni_id = max(
        mevcut_idler,
        default=0
    ) + 1

    bildirim = {

        "id": yeni_id,

        "firma_id": firma_id,

        "tur": tur,

        "mesaj": mesaj,

        "tarih": now_string(),

        "okundu": False,
    }

    data["notifications"].append(
        bildirim
    )

    save_data(data)

    return bildirim


# =========================================================
# BİLDİRİMLERİ GETİR
# =========================================================

def bildirimleri_getir(
    sadece_okunmamis=False
):

    data = load_data()

    bildirimler = data["notifications"]

    if sadece_okunmamis:

        bildirimler = [
            item
            for item in bildirimler
            if not item.get(
                "okundu",
                False
            )
        ]

    return bildirimler


# =========================================================
# BİLDİRİMİ OKUNDU YAP
# =========================================================

def bildirim_okundu(
    bildirim_id
):

    data = load_data()

    try:

        bildirim_id = int(
            bildirim_id
        )

    except (
        TypeError,
        ValueError
    ):

        return False

    for item in data["notifications"]:

        if item.get("id") == bildirim_id:

            item["okundu"] = True

            save_data(data)

            return True

    return False


# =========================================================
# BİLDİRİM SİL
# =========================================================

def bildirim_sil(
    bildirim_id
):

    data = load_data()

    try:

        bildirim_id = int(
            bildirim_id
        )

    except (
        TypeError,
        ValueError
    ):

        return False

    bildirimler = data["notifications"]

    yeni_bildirimler = [
        item
        for item in bildirimler
        if item.get("id") != bildirim_id
    ]

    if len(yeni_bildirimler) == len(
        bildirimler
    ):

        return False

    data["notifications"] = yeni_bildirimler

    save_data(data)

    return True


# =========================================================
# TÜM BİLDİRİMLERİ OKUNDU YAP
# =========================================================

def bildirimleri_okundu_yap():

    data = load_data()

    for item in data["notifications"]:

        item["okundu"] = True

    save_data(data)


# =========================================================
# TÜM BİLDİRİMLERİ SİL
# =========================================================

def bildirimleri_sil():

    data = load_data()

    adet = len(
        data.get(
            "notifications",
            [],
        )
    )

    data["notifications"] = []

    save_data(data)

    return adet


# =========================================================
# GEÇMİŞ KAYDI EKLE
# =========================================================

def gecmis_ekle(
    firma_id,
    kalem,
    fiyat,
    fiyat_tarihi=None
):

    data = load_data()

    kayit = {

        "firma_id": firma_id,

        "kalem": kalem,

        "fiyat": fiyat,

        "fiyat_tarihi": fiyat_tarihi,

        "tarih": now_string(),
    }

    data["history"].append(
        kayit
    )

    save_data(data)

    return kayit


# =========================================================
# GEÇMİŞ GETİR
# =========================================================

def gecmis_getir(
    firma_id=None,
    kalem=None
):

    data = load_data()

    sonuc = data["history"]

    if firma_id is not None:

        sonuc = [
            item
            for item in sonuc
            if item.get(
                "firma_id"
            ) == firma_id
        ]

    if kalem is not None:

        sonuc = [
            item
            for item in sonuc
            if item.get(
                "kalem"
            ) == kalem
        ]

    return sonuc


# =========================================================
# SİSTEM ÖZETİ
# =========================================================

def sistem_ozeti():

    data = load_data()

    firmalar = data["firms"]

    fiyatlar = data["prices"]

    bildirimler = data["notifications"]

    return {

        "firma_sayisi":
            len(firmalar),

        "fiyatli_firma_sayisi":
            len(fiyatlar),

        "bildirim_sayisi":
            len(bildirimler),

        "okunmamis_bildirim":
            len([
                item
                for item in bildirimler
                if not item.get(
                    "okundu",
                    False
                )
            ]),

        "gecmis_kayit_sayisi":
            len(data["history"]),
    }