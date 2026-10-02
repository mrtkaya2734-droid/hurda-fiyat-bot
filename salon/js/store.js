/* Veri katmanı.
 * Şimdilik tarayıcıda (localStorage) çalışır. Hosting'e geçerken yalnızca `Adapter`
 * nesnesi (load/save) bir REST API'ye çevrilir; uygulamanın geri kalanı değişmez. */
(function () {
  'use strict';

  var KEY = 'gulaksu_v2';
  var SESSION_KEY = 'gulaksu_session';

  var Adapter = {
    load: function () {
      try { return JSON.parse(localStorage.getItem(KEY)); } catch (e) { return null; }
    },
    save: function (data) {
      localStorage.setItem(KEY, JSON.stringify(data));
    }
  };

  /* ---------- yardımcılar ---------- */
  function uid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }
  function pad(n) { return String(n).padStart(2, '0'); }
  function ymd(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function tl(n) { return new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 0 }).format(Number(n) || 0) + ' ₺'; }

  /* ---------- şifre (tuzlu SHA-256) ---------- */
  function randomSalt() {
    var a = new Uint8Array(16);
    (window.crypto || {}).getRandomValues ? crypto.getRandomValues(a) : a.forEach(function (_, i) { a[i] = Math.random() * 256; });
    return Array.prototype.map.call(a, function (b) { return pad(b.toString(16)); }).join('');
  }
  function fallbackHash(s) { // yalnızca crypto.subtle yoksa (güvenli olmayan bağlam)
    var h = 5381; for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return 'f' + (h >>> 0).toString(16);
  }
  async function hash(salt, pass) {
    var text = salt + ':' + pass;
    if (window.crypto && crypto.subtle) {
      var buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
      return Array.prototype.map.call(new Uint8Array(buf), function (b) { return pad(b.toString(16)); }).join('');
    }
    return fallbackHash(text);
  }
  async function makeCred(pass) { var salt = randomSalt(); return { salt: salt, hash: await hash(salt, pass) }; }
  async function checkCred(cred, pass) { return !!cred && (await hash(cred.salt, pass)) === cred.hash; }

  /* ---------- varsayılan veri ---------- */
  var HIZMETLER = [
    ['Kalıcı Makyaj', 'Mikroblading', 120, 6000], ['Kalıcı Makyaj', 'Pudralama Kaş', 120, 6500],
    ['Kalıcı Makyaj', 'Dudak Renklendirme', 120, 6000], ['Kalıcı Makyaj', 'Eyeliner / Dipliner', 90, 4500],
    ['Kaş & Kirpik', 'Kaş Laminasyonu', 45, 1200], ['Kaş & Kirpik', 'Kirpik Lifting', 60, 1500],
    ['Kaş & Kirpik', 'İpek Kirpik', 120, 2000],
    ['Cilt Bakımı', 'Profesyonel Cilt Bakımı', 60, 1500], ['Cilt Bakımı', 'Medikal Cilt Bakımı', 75, 2200],
    ['Lazer Epilasyon', 'Lazer Epilasyon', 45, 1500],
    ['Tırnak', 'Protez Tırnak', 90, 1200], ['Tırnak', 'Manikür & Pedikür', 90, 1000],
    ['Vücut Bakımı', 'Vücut İncelme', 60, 1800], ['Vücut Bakımı', 'Bölgesel Zayıflama', 60, 1500],
    ['Vücut Bakımı', 'G5', 45, 900],
    ['Bakım & Rahatlama', 'Kafa Masajı', 30, 600],
    ['Erkek Kuaförü', 'Saç', 30, 400], ['Erkek Kuaförü', 'Sakal', 20, 250],
    ['Erkek Kuaförü', 'Saç & Sakal', 45, 600], ['Erkek Kuaförü', 'Erkek Bakımı', 45, 700],
    ['Erkek Kuaförü', 'Medikal Pedikür', 60, 800]
  ].map(function (h, i) { return { id: 'h' + (i + 1), kategori: h[0], ad: h[1], sure: h[2], fiyat: h[3] }; });

  function defaults() {
    return {
      v: 2,
      admin: null, // ilk açılışta kurulum ekranında belirlenir
      personeller: [
        { id: 'p1', ad: 'Gül Aksu', uzmanlik: 'Kurucu & Baş Uzman', maas: 25000, prim: 15, cred: null, aktif: true },
        { id: 'p2', ad: 'Sena Yıldız', uzmanlik: 'Cilt Bakım Uzmanı', maas: 18000, prim: 10, cred: null, aktif: true },
        { id: 'p3', ad: 'Esra Demir', uzmanlik: 'Kalıcı Makyaj Uzmanı', maas: 20000, prim: 12, cred: null, aktif: true }
      ],
      hizmetler: HIZMETLER,
      islemler: [], avanslar: [], giderler: [], log: []
    };
  }

  function demoData(data) {
    var today = new Date(); var d = ymd(today);
    function iso(day, hm) { return day + 'T' + hm; }
    data.islemler = [
      { id: uid(), musteri: 'Ayşe Yılmaz', telefon: '', hizmet: 'Cilt Bakımı · Medikal Cilt Bakımı', personelId: 'p1', giren: 'Yönetici', kaynak: 'Panel', baslangic: iso(d, '10:00'), bitis: iso(d, '11:15'), tutar: 2200, bahsis: 100, odeme: 'Kredi Kartı', durum: 'Onaylı' },
      { id: uid(), musteri: 'Fatma Demir', telefon: '', hizmet: 'Kaş & Kirpik · Kirpik Lifting', personelId: 'p2', giren: 'Sena Yıldız', kaynak: 'Panel', baslangic: iso(d, '13:00'), bitis: iso(d, '14:00'), tutar: 1500, bahsis: 0, odeme: 'Nakit', durum: 'Bekliyor' },
      { id: uid(), musteri: 'Zeynep Kaya', telefon: '0555 000 00 00', hizmet: 'Tırnak · Protez Tırnak', personelId: 'p3', giren: 'Web Sitesi', kaynak: 'Web', baslangic: iso(d, '15:00'), bitis: iso(d, '16:30'), tutar: 1200, bahsis: 0, odeme: 'Nakit', durum: 'Bekliyor' }
    ];
    data.avanslar = [{ id: uid(), personelId: 'p2', tutar: 1000, not: 'Avans', tarih: d }];
    data.giderler = [{ id: uid(), aciklama: 'Elektrik Faturası', kategori: 'Fatura', tutar: 2500, tarih: d }];
    return data;
  }

  /* ---------- store ---------- */
  var data = Adapter.load();
  if (!data || data.v !== 2) data = defaults();
  var listeners = [];

  function commit() {
    try { Adapter.save(data); } catch (e) { return false; }
    return true;
  }
  function notify() { listeners.forEach(function (fn) { fn(); }); }

  window.addEventListener('storage', function (e) { // başka sekmede değişiklik
    if (e.key !== KEY) return;
    var fresh = Adapter.load();
    if (!fresh) return;
    var onceki = data.islemler.filter(function (x) { return x.durum === 'Bekliyor'; }).length;
    data = fresh;
    var simdi = data.islemler.filter(function (x) { return x.durum === 'Bekliyor'; }).length;
    notify();
    if (simdi > onceki && Store.onYeniTalep) Store.onYeniTalep();
  });

  var Store = {
    get data() { return data; },
    uid: uid, esc: esc, tl: tl, ymd: ymd,
    subscribe: function (fn) { listeners.push(fn); },
    save: function (kim, islem) {
      if (kim) {
        data.log.unshift({ t: new Date().toISOString(), kim: kim, islem: islem });
        data.log = data.log.slice(0, 300);
      }
      var ok = commit(); notify(); return ok;
    },
    makeCred: makeCred, checkCred: checkCred,
    resetDemo: function () { var keep = data.admin; data = demoData(defaults()); data.admin = keep; return Store.save('Sistem', 'Demo veri yüklendi'); },
    replaceAll: function (obj) { if (!obj || obj.v !== 2) throw new Error('Geçersiz yedek dosyası'); data = obj; return Store.save('Sistem', 'Yedek geri yüklendi'); },
    personel: function (id) { return data.personeller.find(function (p) { return p.id === id; }); },
    personelAd: function (id) { var p = Store.personel(id); return p ? p.ad : '(silinmiş personel)'; },
    session: {
      get: function () { try { return JSON.parse(sessionStorage.getItem(SESSION_KEY)); } catch (e) { return null; } },
      set: function (s) { sessionStorage.setItem(SESSION_KEY, JSON.stringify(s)); },
      clear: function () { sessionStorage.removeItem(SESSION_KEY); }
    },
    /* Çakışma: aynı personel için süresi kesişen onaylı/bekleyen kayıt var mı? */
    cakisma: function (personelId, bas, bit, haricId) {
      return data.islemler.find(function (r) {
        return r.id !== haricId && r.personelId === personelId && r.baslangic < bit && r.bitis > bas;
      });
    },
    onYeniTalep: null
  };

  /* ---------- Web sitesi entegrasyonu ----------
   * Web sitesi randevu formu bu fonksiyonu (veya ileride aynı alanlarla bir POST /api/randevu-talebi)
   * çağırır. Talep "Bekliyor" olarak düşer ve yönetici onayını bekler. */
  window.GulAksuAPI = {
    hizmetler: function () { return data.hizmetler.map(function (h) { return { ad: h.kategori + ' · ' + h.ad, sure: h.sure, fiyat: h.fiyat }; }); },
    personeller: function () { return data.personeller.filter(function (p) { return p.aktif; }).map(function (p) { return { id: p.id, ad: p.ad, uzmanlik: p.uzmanlik }; }); },
    randevuTalebi: function (t) {
      if (!t || !t.musteri || !t.hizmet || !t.baslangic || !t.bitis) throw new Error('Eksik alan');
      data.islemler.push({
        id: uid(), musteri: String(t.musteri).slice(0, 80), telefon: String(t.telefon || '').slice(0, 30),
        hizmet: String(t.hizmet).slice(0, 120), personelId: t.personelId || (data.personeller[0] || {}).id,
        giren: 'Web Sitesi', kaynak: 'Web', baslangic: t.baslangic, bitis: t.bitis,
        tutar: Number(t.tutar) || 0, bahsis: 0, odeme: 'Nakit', durum: 'Bekliyor'
      });
      Store.save('Web Sitesi', 'Yeni randevu talebi: ' + t.musteri);
      return true;
    }
  };

  window.Store = Store;
})();
