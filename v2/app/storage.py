import json
import os
import re
from datetime import datetime
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
    return datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# =========================================================
# VERİ YÜKLEME
# =========================================================

def load_data():

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

    temporary_file = DATA_FILE + ".tmp"

    with open(
        temporary_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=4
        )

    os.replace(
        temporary_file,
        DATA_FILE
    )


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

    save_data(data)

    return True


# =========================================================
# MANUEL FİYAT SİL
# =========================================================

def manuel_fiyat_sil(
    firma_id,
    kalem
):

    firma_id = firma_id_normalize(
        firma_id
    )

    data = load_data()

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