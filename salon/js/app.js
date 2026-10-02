(function () {
  'use strict';
  var S = Store, esc = S.esc, tl = S.tl;
  var root = document.getElementById('root');
  var ui = { tab: 'bugun', aralik: 'ay', loginTip: 'admin', deneme: 0, kilit: 0, ajTarih: S.ymd(new Date()), musteriAra: '', durumFiltre: '', moreAcik: false };
  var SALON = 'Gül Aksu Güzellik Salonu';
  var DURUMLAR = ['Bekliyor', 'Onaylı', 'Geldi', 'Gelmedi', 'İptal'];
  var H = 56; // takvimde bir saatin piksel yüksekliği

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function sum(arr, f) { return arr.reduce(function (a, x) { return a + (Number(f(x)) || 0); }, 0); }
  function pad(n) { return String(n).padStart(2, '0'); }
  function fmtDT(s) { return s ? s.replace('T', ' ') : ''; }
  function saat(s) { return (s || '').split('T')[1] || ''; }
  function dkOf(hm) { var p = (hm || '0:0').split(':'); return (+p[0]) * 60 + (+p[1]); }
  function dakika(r) { var m = (new Date(r.bitis) - new Date(r.baslangic)) / 60000; return m > 0 ? m : 0; }
  function nowStr() { var n = new Date(); return S.ymd(n) + 'T' + pad(n.getHours()) + ':' + pad(n.getMinutes()); }
  function tarihUzun(g) { return new Date(g + 'T00:00').toLocaleDateString('tr-TR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }); }
  function tarihKisa(s) { var d = new Date(s); return d.toLocaleDateString('tr-TR', { day: 'numeric', month: 'long' }); }
  function bas(adi) { return (adi || '?').trim().split(/\s+/).map(function (x) { return x[0]; }).slice(0, 2).join('').toUpperCase(); }
  function aktif(r) { return r.durum === 'Onaylı' || r.durum === 'Geldi'; }
  function mAd(r) { var m = S.musteri(r.musteriId); return m ? m.ad : (r.musteri || '(müşteri)'); }
  function mTel(r) { var m = S.musteri(r.musteriId); return (m && m.telefon) || r.telefon || ''; }

  /* ---------- ikonlar ---------- */
  var IC = {
    home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    wallet: '<path d="M3 7a2 2 0 0 1 2-2h14v4"/><path d="M3 7v11a2 2 0 0 0 2 2h16V9H5a2 2 0 0 1-2-2z"/><path d="M16 14h.01"/>',
    settings: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    chat: '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.8-.9L3 21l2-5.2A8.4 8.4 0 1 1 21 11.5z"/>',
    printer: '<path d="M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/>',
    edit: '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    left: '<path d="m15 18-6-6 6-6"/>', right: '<path d="m9 18 6-6-6-6"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20z"/>',
    check: '<path d="M20 6 9 17l-5-5"/>', x: '<path d="M18 6 6 18M6 6l12 12"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'
  };
  function ic(n) { return '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + IC[n] + '</svg>'; }

  /* ---------- bildirim / pencereler ---------- */
  function ses() {
    try {
      var c = new (window.AudioContext || window.webkitAudioContext)(), o = c.createOscillator(), g = c.createGain();
      o.frequency.setValueAtTime(587, c.currentTime); o.frequency.setValueAtTime(880, c.currentTime + .08);
      g.gain.setValueAtTime(.08, c.currentTime); g.gain.exponentialRampToValueAtTime(.0001, c.currentTime + .3);
      o.connect(g); g.connect(c.destination); o.start(); o.stop(c.currentTime + .3);
    } catch (e) {}
  }
  function toast(msg, bad) {
    var t = document.createElement('div'); t.className = 'toast' + (bad ? ' bad' : ''); t.textContent = msg;
    $('#toasts').appendChild(t); setTimeout(function () { t.remove(); }, 3200);
  }
  function modal(html, small) {
    var m = document.createElement('div'); m.className = 'modal' + (small ? ' sm' : ''); m.setAttribute('role', 'dialog'); m.setAttribute('aria-modal', 'true');
    m.innerHTML = '<div class="box">' + html + '</div>';
    m.addEventListener('mousedown', function (e) { if (e.target === m) m.remove(); });
    document.body.appendChild(m); return m;
  }
  function confirmBox(msg, okText) {
    return new Promise(function (res) {
      var m = modal('<p>' + esc(msg) + '</p><div class="btns"><button class="btn ghost" data-r="0">Vazgeç</button><button class="btn bad" data-r="1">' + esc(okText || 'Evet') + '</button></div>', true);
      m.addEventListener('click', function (e) { var b = e.target.closest('[data-r]'); if (b) { m.remove(); res(b.dataset.r === '1'); } });
      m.addEventListener('mousedown', function (e) { if (e.target === m) res(false); });
      $('[data-r="0"]', m).focus();
    });
  }
  S.onYeniTalep = function () { ses(); toast('Yeni randevu talebi geldi'); };

  /* ---------- WhatsApp ---------- */
  function waNo(tel) { var n = S.normTel(tel); if (n.charAt(0) === '0') n = '9' + n; else if (n.length === 10) n = '90' + n; return n.length >= 11 ? n : null; }
  function waMsg(r) {
    var ad = mAd(r), kim = 'Merhaba ' + ad + ', ';
    if (r.durum === 'Bekliyor') return kim + tarihKisa(r.baslangic) + ' saat ' + saat(r.baslangic) + ' için ' + r.hizmet + ' randevu talebinizi aldık. Uygunluğu teyit etmek için yazıyoruz. ' + SALON;
    return kim + tarihKisa(r.baslangic) + ' saat ' + saat(r.baslangic) + ' tarihindeki ' + r.hizmet + ' randevunuzu hatırlatmak isteriz. ' + SALON;
  }
  function waBtn(r, sm) {
    var n = waNo(mTel(r));
    return n ? '<a class="icon-btn wa" href="https://wa.me/' + n + '?text=' + encodeURIComponent(waMsg(r)) + '" target="_blank" rel="noopener" title="WhatsApp ile yaz" aria-label="WhatsApp ile yaz">' + ic('chat') + '</a>'
      : '<button class="icon-btn" data-nowa title="Telefon numarası yok" aria-label="Telefon numarası yok">' + ic('chat') + '</button>';
  }

  /* ---------- dönem filtresi ve hesaplar ---------- */
  function aralik() {
    var n = new Date(), b = new Date(n.getFullYear(), n.getMonth(), n.getDate()), e = null;
    if (ui.aralik === 'bugun') { e = new Date(b); }
    else if (ui.aralik === 'hafta') { var g = (b.getDay() + 6) % 7; b.setDate(b.getDate() - g); e = new Date(b); e.setDate(e.getDate() + 6); }
    else if (ui.aralik === 'ay') { b = new Date(n.getFullYear(), n.getMonth(), 1); e = new Date(n.getFullYear(), n.getMonth() + 1, 0); }
    else return null;
    return [S.ymd(b), S.ymd(e)];
  }
  function icinde(t) { var a = aralik(), d = (t || '').slice(0, 10); return !a || (d >= a[0] && d <= a[1]); }
  function aralikAd() { return { bugun: 'Bugün', hafta: 'Bu hafta', ay: 'Bu ay', hepsi: 'Tüm zamanlar' }[ui.aralik]; }
  function veri() {
    var d = S.data, bugun = S.ymd(new Date());
    var donem = d.islemler.filter(function (r) { return r.durum !== 'Bekliyor' && icinde(r.baslangic); });
    return {
      donem: donem, aktif: donem.filter(aktif),
      bekleyen: d.islemler.filter(function (r) { return r.durum === 'Bekliyor'; }),
      bugun: d.islemler.filter(function (r) { return r.baslangic.slice(0, 10) === bugun && r.durum !== 'İptal'; }).sort(function (a, b) { return a.baslangic < b.baslangic ? -1 : 1; }),
      avans: d.avanslar.filter(function (a) { return icinde(a.tarih); }),
      gider: d.giderler.filter(function (g) { return icinde(g.tarih); })
    };
  }
  function ozet(v) {
    var ciro = sum(v.aktif, function (r) { return r.tutar; });
    var gider = sum(v.gider, function (g) { return g.tutar; }), avans = sum(v.avans, function (a) { return a.tutar; });
    function od(t) { return sum(v.aktif.filter(function (r) { return r.odeme === t; }), function (r) { return r.tutar; }); }
    var geldi = v.donem.filter(function (r) { return r.durum === 'Geldi'; }).length, gelmedi = v.donem.filter(function (r) { return r.durum === 'Gelmedi'; }).length;
    return { ciro: ciro, gider: gider, avans: avans, net: ciro - gider - avans, bahsis: sum(v.aktif, function (r) { return r.bahsis; }),
      nakit: od('Nakit'), kart: od('Kredi Kartı'), havale: od('Havale / EFT'), adet: v.aktif.length, gelmedi: gelmedi,
      gelmeOran: geldi + gelmedi ? Math.round(gelmedi / (geldi + gelmedi) * 100) : null };
  }
  function hakedis(p, v) {
    var ciro = sum(v.aktif.filter(function (r) { return r.personelId === p.id; }), function (r) { return r.tutar; });
    var prim = ciro * p.prim / 100;
    var avans = sum(v.avans.filter(function (a) { return a.personelId === p.id; }), function (a) { return a.tutar; });
    return { ciro: ciro, prim: prim, avans: avans, net: p.maas + prim - avans };
  }

  /* ---------- grafikler (SVG) ---------- */
  function gunlukGrafik() {
    var gunler = [], i, n = new Date();
    for (i = 13; i >= 0; i--) { var d = new Date(n.getFullYear(), n.getMonth(), n.getDate() - i); gunler.push({ k: S.ymd(d), d: d, v: 0 }); }
    S.data.islemler.forEach(function (r) { if (!aktif(r)) return; var g = gunler.filter(function (x) { return x.k === r.baslangic.slice(0, 10); })[0]; if (g) g.v += Number(r.tutar) || 0; });
    var mx = Math.max.apply(null, gunler.map(function (g) { return g.v; })); var top = Math.max(1000, Math.ceil(mx / 1000) * 1000);
    var W = 600, Ht = 190, L = 62, B = 24, T = 8, bw = (W - L) / gunler.length;
    var svg = '<svg class="chart" viewBox="0 0 ' + W + ' ' + Ht + '" role="img" aria-label="Son 14 günün ciro grafiği">';
    [0, .5, 1].forEach(function (f) {
      var y = T + (Ht - B - T) * (1 - f);
      svg += '<line x1="' + L + '" x2="' + W + '" y1="' + y + '" y2="' + y + '" stroke="var(--line)"/><text x="' + (L - 8) + '" y="' + (y + 4) + '" text-anchor="end">' + tl(top * f) + '</text>';
    });
    gunler.forEach(function (g, k) {
      var h = (Ht - B - T) * g.v / top, x = L + k * bw + 4;
      svg += '<rect x="' + x + '" y="' + (Ht - B - h) + '" width="' + (bw - 8) + '" height="' + Math.max(h, g.v ? 2 : 0) + '" rx="4" fill="var(--brand)"><title>' + esc(g.d.toLocaleDateString('tr-TR', { day: 'numeric', month: 'long' })) + ': ' + tl(g.v) + '</title></rect>';
      if (k % 2 === 1 || k === gunler.length - 1) svg += '<text x="' + (x + (bw - 8) / 2) + '" y="' + (Ht - 7) + '" text-anchor="middle">' + pad(g.d.getDate()) + '.' + pad(g.d.getMonth() + 1) + '</text>';
    });
    return svg + '</svg>';
  }
  function hbars(items, bos) {
    items = items.filter(function (x) { return x.v > 0; }).sort(function (a, b) { return b.v - a.v; }).slice(0, 6);
    if (!items.length) return '<p class="empty">' + (bos || 'Bu dönemde veri yok.') + '</p>';
    var mx = items[0].v;
    return '<div class="hbars">' + items.map(function (x) { return '<div class="hbar"><div class="top"><span>' + esc(x.ad) + '</span><b>' + tl(x.v) + '</b></div><div class="track"><div class="fill" style="width:' + Math.max(3, x.v / mx * 100) + '%"></div></div></div>'; }).join('') + '</div>';
  }

  /* ---------- giriş ---------- */
  function renderLogin() {
    var kurulum = !S.data.admin;
    root.innerHTML = '<div class="center"><div class="card login">' +
      '<div class="brand"><b>GÜL AKSU</b><div class="muted small">Güzellik ve Bakım Salonu Yönetim Sistemi</div></div>' +
      (kurulum
        ? '<form id="kurulum"><p class="muted small">İlk kurulum: yönetici şifrenizi belirleyin (en az 6 karakter).</p>' +
          '<div class="field"><label for="k1">Yönetici şifresi</label><input id="k1" type="password" autocomplete="new-password" required></div>' +
          '<div class="field"><label for="k2">Şifre (tekrar)</label><input id="k2" type="password" autocomplete="new-password" required></div>' +
          '<button class="btn block">Kurulumu Tamamla</button><div class="err" id="err"></div></form>'
        : '<div class="seg"><button data-lt="admin" class="' + (ui.loginTip === 'admin' ? 'on' : '') + '">Yönetici</button><button data-lt="personel" class="' + (ui.loginTip === 'personel' ? 'on' : '') + '">Personel</button></div>' +
          '<form id="giris">' +
          (ui.loginTip === 'personel'
            ? '<div class="field"><label for="ps">Personel</label><select id="ps">' + S.data.personeller.filter(function (p) { return p.aktif; }).map(function (p) { return '<option value="' + esc(p.id) + '">' + esc(p.ad) + '</option>'; }).join('') + '</select></div>'
            : '') +
          '<div class="field"><label for="pw">Şifre</label><input id="pw" type="password" autocomplete="current-password" required></div>' +
          '<button class="btn block">Giriş Yap</button><div class="err" id="err"></div></form>') +
      '</div></div>';
    var k = $('#kurulum');
    if (k) k.addEventListener('submit', async function (e) {
      e.preventDefault();
      var a = $('#k1').value, b = $('#k2').value;
      if (a.length < 6) return ($('#err').textContent = 'Şifre en az 6 karakter olmalı.');
      if (a !== b) return ($('#err').textContent = 'Şifreler eşleşmiyor.');
      S.data.admin = await S.makeCred(a); S.save('Sistem', 'Yönetici şifresi belirlendi');
      S.session.set({ rol: 'admin', ad: 'Yönetici' }); boot();
    });
    var g = $('#giris');
    if (g) g.addEventListener('submit', async function (e) {
      e.preventDefault();
      if (Date.now() < ui.kilit) return ($('#err').textContent = 'Çok fazla deneme. ' + Math.ceil((ui.kilit - Date.now()) / 1000) + ' sn bekleyin.');
      var pw = $('#pw').value, ok = false, oturum;
      if (ui.loginTip === 'admin') { ok = await S.checkCred(S.data.admin, pw); oturum = { rol: 'admin', ad: 'Yönetici' }; }
      else {
        var p = S.personel($('#ps').value);
        if (p && !p.cred) return ($('#err').textContent = 'Bu personel için şifre henüz belirlenmemiş. Yöneticiye başvurun.');
        ok = !!p && await S.checkCred(p.cred, pw); oturum = p && { rol: 'personel', ad: p.ad, pid: p.id };
      }
      if (!ok) { if (++ui.deneme >= 5) { ui.kilit = Date.now() + 30000; ui.deneme = 0; } return ($('#err').textContent = 'Hatalı şifre.'); }
      ui.deneme = 0; S.session.set(oturum); S.save(oturum.ad, 'Giriş yapıldı'); boot();
    });
  }

  /* ---------- randevu formu (satır içi + pencere) ---------- */
  function hizmetAd(h) { return h.kategori + ' · ' + h.ad; }
  function hizmetSelect(id) {
    var gr = {};
    S.data.hizmetler.forEach(function (h) { (gr[h.kategori] = gr[h.kategori] || []).push(h); });
    return '<select id="' + id + '" required><option value="">Hizmet seçin</option>' + Object.keys(gr).map(function (k) {
      return '<optgroup label="' + esc(k) + '">' + gr[k].map(function (h) { return '<option value="' + esc(h.id) + '">' + esc(h.ad) + ' · ' + h.sure + ' dk</option>'; }).join('') + '</optgroup>'; }).join('') + '</select>';
  }
  function islemForm(pre, o) {
    return '<form id="' + pre + 'form">' +
      '<div class="field"><label for="' + pre + 'm">Müşteri adı soyadı</label><input id="' + pre + 'm" list="' + pre + 'dl" required maxlength="80" autocomplete="off"><datalist id="' + pre + 'dl">' + S.data.musteriler.map(function (m) { return '<option value="' + esc(m.ad) + '">'; }).join('') + '</datalist></div>' +
      '<div class="field"><label for="' + pre + 'tel">Telefon (WhatsApp için)</label><input id="' + pre + 'tel" type="tel" maxlength="30" placeholder="05xx xxx xx xx"></div>' +
      '<div class="field"><label for="' + pre + 'h">Hizmet</label>' + hizmetSelect(pre + 'h') + '</div>' +
      (o.personelSecimi ? '<div class="field"><label for="' + pre + 'p">Personel</label><select id="' + pre + 'p">' + S.data.personeller.map(function (p) { return '<option value="' + esc(p.id) + '"' + (p.aktif ? '' : ' disabled') + '>' + esc(p.ad) + (p.aktif ? '' : ' (pasif)') + '</option>'; }).join('') + '</select></div>' : '') +
      '<div class="row2"><div class="field"><label for="' + pre + 'b">Başlangıç</label><input id="' + pre + 'b" type="datetime-local" required></div>' +
      '<div class="field"><label for="' + pre + 'e">Bitiş</label><input id="' + pre + 'e" type="datetime-local" required></div></div>' +
      '<div class="row2"><div class="field"><label for="' + pre + 't">Tutar (₺)</label><input id="' + pre + 't" type="number" min="0" required></div>' +
      '<div class="field"><label for="' + pre + 's">Bahşiş (₺)</label><input id="' + pre + 's" type="number" min="0" value="0"></div></div>' +
      '<div class="row2"><div class="field"><label for="' + pre + 'o">Ödeme türü</label><select id="' + pre + 'o"><option>Nakit</option><option>Kredi Kartı</option><option>Havale / EFT</option></select></div>' +
      (o.durumSecimi ? '<div class="field"><label for="' + pre + 'd">Durum</label><select id="' + pre + 'd">' + DURUMLAR.map(function (d) { return '<option>' + d + '</option>'; }).join('') + '</select></div>' : '') + '</div>' +
      '<div id="' + pre + 'uyari"></div><button class="btn block">' + esc(o.btn) + '</button></form>';
  }
  function setForm(pre, r) {
    $('#' + pre + 'm').value = mAd(r); $('#' + pre + 'tel').value = mTel(r);
    var hz = $('#' + pre + 'h'), h = S.data.hizmetler.filter(function (x) { return hizmetAd(x) === r.hizmet; })[0];
    if (h) hz.value = h.id; else { hz.insertAdjacentHTML('afterbegin', '<option value="__eski">' + esc(r.hizmet) + '</option>'); hz.value = '__eski'; }
    if ($('#' + pre + 'p')) $('#' + pre + 'p').value = r.personelId;
    $('#' + pre + 'b').value = r.baslangic; $('#' + pre + 'e').value = r.bitis;
    $('#' + pre + 't').value = r.tutar; $('#' + pre + 's').value = r.bahsis; $('#' + pre + 'o').value = r.odeme;
    if ($('#' + pre + 'd')) $('#' + pre + 'd').value = r.durum;
  }
  function bindIslemForm(pre, cfg) {
    var f = $('#' + pre + 'form'), hz = $('#' + pre + 'h'), edit = cfg.edit;
    function sureDoldur() {
      var h = S.data.hizmetler.filter(function (x) { return x.id === hz.value; })[0]; if (!h) return;
      $('#' + pre + 't').value = h.fiyat;
      var b = $('#' + pre + 'b').value;
      if (b) { var d = new Date(b); d.setMinutes(d.getMinutes() - d.getTimezoneOffset() + h.sure); $('#' + pre + 'e').value = d.toISOString().slice(0, 16); }
    }
    hz.addEventListener('change', sureDoldur);
    $('#' + pre + 'b').addEventListener('change', function () { if (!edit) sureDoldur(); });
    $('#' + pre + 'm').addEventListener('change', function () {
      var m = S.data.musteriler.filter(function (x) { return x.ad.toLowerCase() === this.value.trim().toLowerCase(); }, this)[0];
      if (m && !$('#' + pre + 'tel').value) $('#' + pre + 'tel').value = m.telefon;
    });
    if (edit) setForm(pre, edit); else if (cfg.prefill) {
      var pf = cfg.prefill; if (pf.baslangic) $('#' + pre + 'b').value = pf.baslangic; if (pf.personelId && $('#' + pre + 'p')) $('#' + pre + 'p').value = pf.personelId;
      if (pf.musteri) $('#' + pre + 'm').value = pf.musteri.ad, $('#' + pre + 'tel').value = pf.musteri.telefon;
    }
    f.addEventListener('submit', function (e) {
      e.preventDefault();
      var pid = cfg.getPid(), bs = $('#' + pre + 'b').value, bt = $('#' + pre + 'e').value;
      if (bt <= bs) return toast('Bitiş, başlangıçtan sonra olmalı.', true);
      var durum = cfg.durum || $('#' + pre + 'd').value;
      var c = (durum === 'İptal' || durum === 'Gelmedi') ? null : S.cakisma(pid, bs, bt, edit && edit.id);
      if (c && !f.dataset.zorla) {
        $('#' + pre + 'uyari').innerHTML = '<div class="warnbox">⚠ ' + esc(S.personelAd(pid)) + ' için ' + esc(fmtDT(c.baslangic)) + ' - ' + esc(saat(c.bitis)) + ' arasında "' + esc(mAd(c)) + '" kaydı var. Yine de kaydetmek için tekrar basın.</div>';
        f.dataset.zorla = '1'; return;
      }
      var h = S.data.hizmetler.filter(function (x) { return x.id === hz.value; })[0];
      var ad = $('#' + pre + 'm').value.trim(), tel = $('#' + pre + 'tel').value.trim();
      var alan = { musteriId: S.musteriBul(ad, tel), musteri: ad, telefon: tel, hizmet: h ? hizmetAd(h) : (edit ? edit.hizmet : ''), personelId: pid, baslangic: bs, bitis: bt,
        tutar: Number($('#' + pre + 't').value) || 0, bahsis: Number($('#' + pre + 's').value) || 0, odeme: $('#' + pre + 'o').value, durum: durum };
      var kim = cfg.giren();
      if (edit) { Object.assign(edit, alan); S.save(kim, 'Randevu güncellendi: ' + ad); toast('Randevu güncellendi.'); }
      else { alan.id = S.uid(); alan.giren = kim; alan.kaynak = 'Panel'; S.data.islemler.push(alan); S.save(kim, durum === 'Bekliyor' ? 'Onaya gönderildi: ' + ad : 'Randevu eklendi: ' + ad); toast(durum === 'Bekliyor' ? 'İşlem yönetici onayına gönderildi.' : 'Randevu eklendi.'); }
      if (cfg.onDone) cfg.onDone(); else { f.reset(); delete f.dataset.zorla; $('#' + pre + 'uyari').innerHTML = ''; }
    });
  }
  function islemModal(edit, prefill) {
    var m = modal('<div class="mh"><h3>' + (edit ? 'Randevuyu düzenle' : 'Yeni randevu') + '</h3><button class="icon-btn" data-close aria-label="Kapat">' + ic('x') + '</button></div>' + islemForm('x', { personelSecimi: true, durumSecimi: true, btn: edit ? 'Değişiklikleri kaydet' : 'Randevuyu kaydet' }));
    if (!edit) $('#xd', m).value = 'Onaylı';
    bindIslemForm('x', { getPid: function () { return $('#xp').value; }, giren: function () { return 'Yönetici'; }, edit: edit, prefill: prefill, onDone: function () { m.remove(); } });
    $('#xm').focus();
  }

  /* ---------- müşteri penceresi ---------- */
  function musteriIstat(m) {
    var rs = S.data.islemler.filter(function (r) { return r.musteriId === m.id; });
    var ak = rs.filter(aktif), gelmedi = rs.filter(function (r) { return r.durum === 'Gelmedi'; }).length;
    var son = rs.filter(function (r) { return r.durum !== 'İptal'; }).map(function (r) { return r.baslangic; }).sort().pop();
    return { rs: rs, adet: rs.length, harcama: sum(ak, function (r) { return r.tutar; }), gelmedi: gelmedi, son: son };
  }
  function musteriModal(id) {
    var m = id && S.musteri(id), st = m ? musteriIstat(m) : null;
    var wa = m && waNo(m.telefon) ? '<a class="btn wa sm" href="https://wa.me/' + waNo(m.telefon) + '" target="_blank" rel="noopener">' + ic('chat') + ' WhatsApp</a>' : '';
    var box = modal('<div class="mh"><h3>' + (m ? esc(m.ad) : 'Yeni müşteri') + '</h3><button class="icon-btn" data-close aria-label="Kapat">' + ic('x') + '</button></div>' +
      '<form id="mf"><div class="field"><label for="mad">Ad soyad</label><input id="mad" required maxlength="80" value="' + esc(m ? m.ad : '') + '"></div>' +
      '<div class="field"><label for="mtel">Telefon</label><input id="mtel" type="tel" maxlength="30" value="' + esc(m ? m.telefon : '') + '"></div>' +
      '<div class="field"><label for="mnot">Not (alerji, tercih vb.)</label><textarea id="mnot" maxlength="500">' + esc(m ? m.not : '') + '</textarea></div>' +
      (st ? '<div class="row2" style="margin-bottom:12px"><div class="kpi mini"><div class="l">Toplam harcama</div><div class="v">' + tl(st.harcama) + '</div></div><div class="kpi mini warn"><div class="l">Randevu / gelmedi</div><div class="v">' + st.adet + ' / ' + st.gelmedi + '</div></div></div>' : '') +
      '<div class="toolbar"><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn">Kaydet</button>' + wa + (m ? '<button type="button" class="btn ghost sm" data-yeni-randevu="' + esc(m.id) + '">' + ic('plus') + ' Randevu ekle</button>' : '') + '</div>' +
      (m && !st.adet ? '<button type="button" class="link-bad" data-sil-musteri="' + esc(m.id) + '">Müşteriyi sil</button>' : '') + '</div></form>' +
      (st && st.adet ? '<h3 style="margin:18px 0 8px">Geçmiş</h3><div class="scroll"><table><tbody>' + st.rs.slice().sort(function (a, b) { return a.baslangic < b.baslangic ? 1 : -1; }).slice(0, 12).map(function (r) { return '<tr><td>' + esc(fmtDT(r.baslangic)) + '</td><td>' + esc(r.hizmet) + '<br><span class="muted small">' + esc(S.personelAd(r.personelId)) + '</span></td><td>' + durumTag(r.durum) + '</td><td><b>' + tl(r.tutar) + '</b></td></tr>'; }).join('') + '</tbody></table></div>' : ''));
    $('#mf', box).addEventListener('submit', function (e) {
      e.preventDefault();
      var ad = $('#mad').value.trim(); if (!ad) return;
      if (m) { m.ad = ad; m.telefon = $('#mtel').value.trim(); m.not = $('#mnot').value.trim(); S.save('Yönetici', 'Müşteri güncellendi: ' + ad); }
      else { S.data.musteriler.push({ id: S.uid(), ad: ad, telefon: $('#mtel').value.trim(), not: $('#mnot').value.trim(), olusturma: S.ymd(new Date()) }); S.save('Yönetici', 'Müşteri eklendi: ' + ad); }
      box.remove(); toast('Müşteri kaydedildi.');
    });
  }
  function durumTag(d) { var c = { Bekliyor: 'wait', Onaylı: 'brand', Geldi: 'done', Gelmedi: 'bad', İptal: '' }[d]; return '<span class="tag ' + (c || '') + '">' + esc(d) + '</span>'; }

  /* ---------- yazdırma ---------- */
  function yazdir(html) { $('#printarea').innerHTML = html; setTimeout(function () { window.print(); }, 50); }
  function fis(r) {
    yazdir('<div class="fis"><h2>GÜL AKSU</h2><p style="text-align:center;margin:2px 0 12px">Güzellik ve Bakım Salonu</p><hr>' +
      '<p><b>Müşteri:</b> ' + esc(mAd(r)) + '<br><b>Hizmet:</b> ' + esc(r.hizmet) + '<br><b>Uzman:</b> ' + esc(S.personelAd(r.personelId)) + '<br><b>Tarih:</b> ' + esc(fmtDT(r.baslangic)) + '</p><hr>' +
      '<p style="font-size:16px"><b>Tutar: ' + tl(r.tutar) + '</b>' + (r.bahsis ? '<br>Bahşiş: ' + tl(r.bahsis) : '') + '<br>Ödeme: ' + esc(r.odeme) + '</p><hr><p style="text-align:center">Bizi tercih ettiğiniz için teşekkür ederiz.</p></div>');
  }
  function raporYazdir() {
    var v = veri(), o = ozet(v);
    yazdir('<h2>GÜL AKSU</h2><p style="text-align:center">Özet rapor · ' + esc(aralikAd()) + ' · ' + esc(new Date().toLocaleDateString('tr-TR')) + '</p>' +
      '<table><tbody><tr><td>Ciro</td><td>' + tl(o.ciro) + '</td></tr><tr><td>Nakit / Kart / Havale</td><td>' + tl(o.nakit) + ' / ' + tl(o.kart) + ' / ' + tl(o.havale) + '</td></tr><tr><td>Bahşiş</td><td>' + tl(o.bahsis) + '</td></tr><tr><td>Gider</td><td>' + tl(o.gider) + '</td></tr><tr><td>Avans</td><td>' + tl(o.avans) + '</td></tr><tr><td><b>Net kâr</b></td><td><b>' + tl(o.net) + '</b></td></tr></tbody></table>' +
      '<h3 style="margin-top:16px">İşlemler</h3><table><thead><tr><th>Tarih</th><th>Müşteri</th><th>Hizmet</th><th>Personel</th><th>Tutar</th></tr></thead><tbody>' +
      v.aktif.slice().sort(function (a, b) { return a.baslangic < b.baslangic ? -1 : 1; }).map(function (r) { return '<tr><td>' + esc(fmtDT(r.baslangic)) + '</td><td>' + esc(mAd(r)) + '</td><td>' + esc(r.hizmet) + '</td><td>' + esc(S.personelAd(r.personelId)) + '</td><td>' + tl(r.tutar) + '</td></tr>'; }).join('') + '</tbody></table>');
  }
  function indir(ad, icerik, tip) {
    var a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([icerik], { type: tip })); a.download = ad; a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); }, 500);
  }
  function csv(rows) {
    return '﻿' + rows.map(function (r) { return r.map(function (c) { c = String(c == null ? '' : c); if (/^[=+\-@]/.test(c)) c = "'" + c; return '"' + c.replace(/"/g, '""') + '"'; }).join(';'); }).join('\r\n');
  }
  function ozetMetni() {
    var o = ozet(veri());
    return '✨ GÜL AKSU GÜZELLİK SALONU - ÖZET RAPOR ✨\n' + aralikAd() + '\n\n💰 Ciro: ' + tl(o.ciro) + '\n💵 Nakit: ' + tl(o.nakit) + '\n💳 Kart: ' + tl(o.kart) + '\n📱 Havale: ' + tl(o.havale) + '\n🎁 Bahşiş: ' + tl(o.bahsis) + '\n📉 Gider: ' + tl(o.gider) + '\n💸 Avans: ' + tl(o.avans) + '\n💎 Net kâr: ' + tl(o.net);
  }

  /* ---------- yönetici iskeleti ---------- */
  var TABS = [['bugun', 'Bugün', 'home'], ['randevu', 'Randevular', 'list'], ['ajanda', 'Ajanda', 'calendar'], ['musteri', 'Müşteriler', 'users'], ['personel', 'Personel', 'user'], ['kasa', 'Kasa', 'wallet'], ['ayar', 'Ayarlar', 'settings']];
  var FILTRELI = ['bugun', 'randevu', 'personel', 'kasa'], YENI = ['bugun', 'randevu', 'ajanda'];
  var BASLIK = { bugun: 'Bugün', randevu: 'Randevular', ajanda: 'Ajanda', musteri: 'Müşteriler', personel: 'Personel & Hakediş', kasa: 'Kasa', ayar: 'Ayarlar' };

  function renderAdmin() {
    root.innerHTML = '<div class="shell"><aside class="side"><div class="logo"><b>GÜL AKSU</b><span>Güzellik ve Bakım Salonu</span></div><nav class="nav" id="nav" aria-label="Ana menü"></nav>' +
      '<div class="foot"><div class="muted small" style="color:inherit;opacity:.6">Giriş: Yönetici</div><button data-a="cikis">' + ic('logout') + ' Çıkış yap</button></div></aside>' +
      '<div class="content"><div class="head" id="head"></div><main><section class="kpis hidden" id="kpis"></section><div id="view"></div></main></div></div><nav class="bnav" id="bnav" aria-label="Alt menü"></nav>';
    renderAdminView();
  }
  function bekSay() { return S.data.islemler.filter(function (r) { return r.durum === 'Bekliyor'; }).length; }

  function renderAdminView() {
    var v = veri(), o = ozet(v), bek = bekSay();
    $('#nav').innerHTML = TABS.map(function (t) { return '<button data-tab="' + t[0] + '" class="' + (ui.tab === t[0] ? 'on' : '') + '"' + (ui.tab === t[0] ? ' aria-current="page"' : '') + '>' + ic(t[2]) + t[1] + (t[0] === 'randevu' && bek ? '<span class="badge">' + bek + '</span>' : '') + '</button>'; }).join('');
    var ana = TABS.slice(0, 4), dahaAktif = ['personel', 'kasa', 'ayar'].indexOf(ui.tab) >= 0 || ui.moreAcik;
    $('#bnav').innerHTML = ana.map(function (t) { return '<button data-tab="' + t[0] + '" class="' + (ui.tab === t[0] ? 'on' : '') + '">' + ic(t[2]) + t[1].replace('Randevular', 'Randevu').replace('Müşteriler', 'Müşteri') + '</button>'; }).join('') +
      '<button data-more class="' + (dahaAktif ? 'on' : '') + '">' + ic('more') + 'Daha</button>';
    var mo = $('#morebox'); if (mo) mo.remove();
    if (ui.moreAcik) document.body.insertAdjacentHTML('beforeend', '<div class="more" id="morebox">' + TABS.slice(4).map(function (t) { return '<button data-tab="' + t[0] + '">' + ic(t[2]) + t[1] + '</button>'; }).join('') + '<button data-a="cikis">' + ic('logout') + 'Çıkış</button></div>');

    $('#head').innerHTML = '<div><h1>' + BASLIK[ui.tab] + '</h1><div class="muted small">' + esc(tarihUzun(S.ymd(new Date()))) + '</div></div><div class="actions">' +
      (FILTRELI.indexOf(ui.tab) >= 0 ? '<div class="chips" role="group" aria-label="Dönem">' + [['bugun', 'Bugün'], ['hafta', 'Bu hafta'], ['ay', 'Bu ay'], ['hepsi', 'Tümü']].map(function (x) { return '<button class="chip' + (ui.aralik === x[0] ? ' on' : '') + '" data-ar="' + x[0] + '">' + x[1] + '</button>'; }).join('') + '</div>' : '') +
      (ui.tab === 'bugun' || ui.tab === 'kasa' ? '<button class="btn ghost sm" data-a="rapor">' + ic('chat') + ' Rapor kopyala</button><button class="btn ghost sm" data-a="raporyaz">' + ic('printer') + ' Yazdır</button>' : '') +
      (YENI.indexOf(ui.tab) >= 0 ? '<button class="btn" data-yeni-randevu="">' + ic('plus') + ' Yeni randevu</button>' : '') + '</div>';

    var kp = $('#kpis');
    if (ui.tab === 'bugun') {
      kp.classList.remove('hidden');
      var bg = v.bugun, tam = bg.filter(function (r) { return r.durum === 'Geldi'; }).length;
      kp.innerHTML = kpi('Bugünkü randevu', bg.length, tam + ' tamamlandı · ' + bg.filter(function (r) { return r.durum === 'Onaylı'; }).length + ' sırada') +
        kpi('Ciro · ' + aralikAd(), tl(o.ciro), o.adet + ' işlem') +
        kpi('Net kâr', tl(o.net), 'Ciro − gider − avans', o.net < 0 ? 'bad' : 'ok') +
        kpi('Gelmeme oranı', o.gelmeOran == null ? '—' : '%' + o.gelmeOran, o.gelmedi + ' randevuya gelinmedi', o.gelmeOran > 15 ? 'warn' : '');
    } else kp.classList.add('hidden');

    var view = $('#view');
    if (view.dataset.gorunum !== ui.tab) { view.dataset.gorunum = ui.tab; view.innerHTML = shell(ui.tab); bindShell(ui.tab); }
    fill(ui.tab, v, o);
  }
  function kpi(l, v, s, cls) { return '<div class="kpi ' + (cls || '') + '"><div class="l">' + l + '</div><div class="v">' + v + '</div><div class="s">' + s + '</div></div>'; }
  function yenileGorunum() { $('#view').dataset.gorunum = ''; renderAdminView(); }

  /* ---------- sekme iskeletleri ve içerikleri ---------- */
  function shell(tab) {
    if (tab === 'randevu') return '<div id="onaybox"></div><div class="grid3"><div class="card"><h3>Hızlı randevu girişi</h3>' + islemForm('a', { personelSecimi: true, durumSecimi: false, btn: 'Randevuyu kaydet' }) + '</div>' +
      '<div class="card"><div class="toolbar" style="margin-bottom:10px"><h3>Randevu ve işlem listesi</h3><div style="display:flex;gap:8px"><select id="durumfiltre" style="width:auto" aria-label="Durum filtresi"><option value="">Tüm durumlar</option>' + DURUMLAR.slice(1).map(function (d) { return '<option>' + d + '</option>'; }).join('') + '</select><button class="btn ghost sm" data-a="csv">' + ic('download') + ' CSV</button></div></div><div class="scroll"><table><thead><tr><th>Müşteri / hizmet</th><th>Personel</th><th>Zaman</th><th>Tutar</th><th>Durum</th><th></th></tr></thead><tbody id="liste"></tbody></table></div></div></div>';
    if (tab === 'musteri') return '<div class="card"><div class="toolbar" style="margin-bottom:12px"><div style="position:relative;flex:1;min-width:200px;max-width:360px"><input id="mara" placeholder="Müşteri ara (ad veya telefon)" aria-label="Müşteri ara"></div><button class="btn" data-yeni-musteri>' + ic('plus') + ' Yeni müşteri</button></div><div class="scroll"><table><thead><tr><th>Müşteri</th><th>Telefon</th><th>Randevu</th><th>Harcama</th><th>Son randevu</th><th></th></tr></thead><tbody id="mlist"></tbody></table></div></div>';
    if (tab === 'personel') return '<div class="grid3"><div class="card"><h3>Yeni personel</h3><form id="pform"><div class="field"><label for="pad">Ad soyad</label><input id="pad" required maxlength="60"></div><div class="field"><label for="puz">Uzmanlık</label><input id="puz" required maxlength="60"></div><div class="row2"><div class="field"><label for="pma">Maaş (₺)</label><input id="pma" type="number" min="0" required></div><div class="field"><label for="ppr">Prim (%)</label><input id="ppr" type="number" min="0" max="100" required></div></div><div class="field"><label for="psi">Şifre (en az 6)</label><input id="psi" type="password" minlength="6" required autocomplete="new-password"></div><button class="btn block">Personeli ekle</button></form></div>' +
      '<div class="card"><div class="toolbar" style="margin-bottom:10px"><h3>Hakediş özeti</h3><button class="btn ghost sm" data-a="hakgider">Hakedişleri gider yaz</button></div><div class="scroll"><table><thead><tr><th>Personel</th><th>Maaş</th><th>Ciro / prim</th><th>Net hakediş</th><th></th></tr></thead><tbody id="plist"></tbody></table></div></div></div>';
    if (tab === 'kasa') return '<section class="kpis" id="kasaozet"></section><div class="grid3"><div class="stack"><div class="card"><h3>Avans ver</h3><form id="avform"><div class="field"><label for="avp">Personel</label><select id="avp">' + S.data.personeller.map(function (p) { return '<option value="' + esc(p.id) + '">' + esc(p.ad) + '</option>'; }).join('') + '</select></div><div class="field"><label for="avt">Tutar (₺)</label><input id="avt" type="number" min="1" required></div><div class="field"><label for="avn">Not</label><input id="avn" maxlength="80"></div><button class="btn block">Avansı kaydet</button></form></div>' +
      '<div class="card"><h3>Gider ekle</h3><form id="gform"><div class="field"><label for="ga">Açıklama</label><input id="ga" required maxlength="100"></div><div class="row2"><div class="field"><label for="gk">Kategori</label><select id="gk"><option>Fatura</option><option>Kira</option><option>Malzeme</option><option>Diğer</option></select></div><div class="field"><label for="gt">Tutar (₺)</label><input id="gt" type="number" min="1" required></div></div><button class="btn block">Gideri kaydet</button></form></div></div>' +
      '<div class="stack"><div class="card"><h3>Avanslar</h3><div class="scroll"><table><thead><tr><th>Tarih</th><th>Personel</th><th>Not</th><th>Tutar</th><th></th></tr></thead><tbody id="avlist"></tbody></table></div></div><div class="card"><div class="toolbar" style="margin-bottom:10px"><h3>Giderler</h3><button class="btn ghost sm" data-a="csvgider">' + ic('download') + ' CSV</button></div><div class="scroll"><table><thead><tr><th>Tarih</th><th>Açıklama</th><th>Kategori</th><th>Tutar</th><th></th></tr></thead><tbody id="glist"></tbody></table></div></div></div></div>';
    if (tab === 'ayar') return '<div class="grid3"><div class="stack"><div class="card"><h3>Güvenlik</h3><form id="sform"><div class="field"><label for="sy">Yeni yönetici şifresi</label><input id="sy" type="password" minlength="6" required autocomplete="new-password"></div><button class="btn">Şifreyi değiştir</button></form></div>' +
      '<div class="card"><h3>Çalışma saatleri (ajanda)</h3><form id="saform"><div class="row2"><div class="field"><label for="sa1">Açılış</label><input id="sa1" type="number" min="0" max="23" required></div><div class="field"><label for="sa2">Kapanış</label><input id="sa2" type="number" min="1" max="24" required></div></div><button class="btn">Kaydet</button></form></div>' +
      '<div class="card"><h3>Yeni hizmet</h3><form id="hzform"><div class="field"><label for="hk">Kategori</label><input id="hk" required maxlength="40" list="hkl"><datalist id="hkl">' + Object.keys(S.data.hizmetler.reduce(function (a, h) { a[h.kategori] = 1; return a; }, {})).map(function (k) { return '<option value="' + esc(k) + '">'; }).join('') + '</datalist></div><div class="field"><label for="ha">Hizmet adı</label><input id="ha" required maxlength="60"></div><div class="row2"><div class="field"><label for="hs">Süre (dk)</label><input id="hs" type="number" min="5" required></div><div class="field"><label for="hf">Fiyat (₺)</label><input id="hf" type="number" min="0" required></div></div><button class="btn">Hizmet ekle</button></form></div></div>' +
      '<div class="stack"><div class="card"><h3>Yedekleme</h3><p class="muted small">Veriler bu tarayıcıda saklanır. Düzenli yedek alın.</p><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" data-a="yedek">' + ic('download') + ' Yedek indir</button><label class="btn ghost" style="margin:0;color:var(--ink)">Yedek yükle<input type="file" id="yedekdosya" accept=".json" class="sr"></label><button class="btn ghost" data-a="demo">Demo veri yükle</button></div></div>' +
      '<div class="card"><h3>Hizmet fiyat listesi</h3><div class="scroll"><table><thead><tr><th>Hizmet</th><th>Süre (dk)</th><th>Fiyat (₺)</th></tr></thead><tbody id="hizlist"></tbody></table></div></div>' +
      '<div class="card"><h3>İşlem kaydı (son 50)</h3><div class="scroll"><table><tbody id="loglist"></tbody></table></div></div></div></div>';
    return '';
  }

  function bindShell(tab) {
    if (tab === 'randevu') {
      bindIslemForm('a', { getPid: function () { return $('#ap').value; }, durum: 'Onaylı', giren: function () { return 'Yönetici'; } });
      $('#durumfiltre').value = ui.durumFiltre;
    }
    if (tab === 'musteri') $('#mara').value = ui.musteriAra;
    if (tab === 'personel') $('#pform').addEventListener('submit', async function (e) {
      e.preventDefault();
      S.data.personeller.push({ id: S.uid(), ad: $('#pad').value.trim(), uzmanlik: $('#puz').value.trim(), maas: Number($('#pma').value), prim: Number($('#ppr').value), cred: await S.makeCred($('#psi').value), aktif: true });
      S.save('Yönetici', 'Personel eklendi: ' + $('#pad').value); e.target.reset(); yenileGorunum(); toast('Personel eklendi.');
    });
    if (tab === 'kasa') {
      $('#avform').addEventListener('submit', function (e) { e.preventDefault();
        S.data.avanslar.push({ id: S.uid(), personelId: $('#avp').value, tutar: Number($('#avt').value), not: $('#avn').value.trim(), tarih: S.ymd(new Date()) });
        S.save('Yönetici', 'Avans verildi'); e.target.reset(); toast('Avans kaydedildi.'); });
      $('#gform').addEventListener('submit', function (e) { e.preventDefault();
        S.data.giderler.push({ id: S.uid(), aciklama: $('#ga').value.trim(), kategori: $('#gk').value, tutar: Number($('#gt').value), tarih: S.ymd(new Date()) });
        S.save('Yönetici', 'Gider eklendi'); e.target.reset(); toast('Gider kaydedildi.'); });
    }
    if (tab === 'ayar') {
      $('#sa1').value = S.data.ayar.acilis; $('#sa2').value = S.data.ayar.kapanis;
      $('#sform').addEventListener('submit', async function (e) { e.preventDefault();
        S.data.admin = await S.makeCred($('#sy').value); S.save('Yönetici', 'Şifre değiştirildi'); e.target.reset(); toast('Yönetici şifresi güncellendi.'); });
      $('#saform').addEventListener('submit', function (e) { e.preventDefault();
        var a = Number($('#sa1').value), k = Number($('#sa2').value); if (k <= a) return toast('Kapanış, açılıştan sonra olmalı.', true);
        S.data.ayar = { acilis: a, kapanis: k }; S.save('Yönetici', 'Çalışma saatleri güncellendi'); toast('Kaydedildi.'); });
      $('#hzform').addEventListener('submit', function (e) { e.preventDefault();
        S.data.hizmetler.push({ id: 'h' + S.uid(), kategori: $('#hk').value.trim(), ad: $('#ha').value.trim(), sure: Number($('#hs').value), fiyat: Number($('#hf').value) });
        S.save('Yönetici', 'Hizmet eklendi: ' + $('#ha').value); e.target.reset(); yenileGorunum(); toast('Hizmet eklendi.'); });
      $('#yedekdosya').addEventListener('change', function (e) {
        var fr = new FileReader(); fr.onload = function () { try { S.replaceAll(JSON.parse(fr.result)); yenileGorunum(); toast('Yedek yüklendi.'); } catch (x) { toast('Geçersiz yedek dosyası.', true); } };
        if (e.target.files[0]) fr.readAsText(e.target.files[0]); });
    }
  }

  function onayKutusu(bekleyen) {
    if (!bekleyen.length) return '';
    return '<div class="card pending"><h3>⏳ Onay bekleyen işlemler <span class="badge">' + bekleyen.length + '</span></h3><div class="scroll"><table><tbody>' +
      bekleyen.map(function (r) { return '<tr><td><div class="who"><span class="avatar">' + esc(bas(mAd(r))) + '</span><div><b>' + esc(mAd(r)) + '</b><br><span class="muted small">' + esc(r.hizmet) + (mTel(r) ? ' · ' + esc(mTel(r)) : '') + '</span></div></div></td><td>' + esc(S.personelAd(r.personelId)) + '</td><td>' + kaynak(r) + '</td><td>' + esc(fmtDT(r.baslangic)) + ' - ' + esc(saat(r.bitis)) + '</td><td><b>' + tl(r.tutar) + '</b></td><td><div class="acts">' + waBtn(r) + '<button class="btn ok sm" data-onay="' + esc(r.id) + '">' + ic('check') + ' Onayla</button><button class="btn ghost sm" data-red="' + esc(r.id) + '">Reddet</button></div></td></tr>'; }).join('') + '</tbody></table></div></div>';
  }
  function kaynak(r) { return r.kaynak === 'Web' ? '<span class="tag web">' + ic('globe') + ' Web sitesi</span>' : '<span class="tag">' + esc(r.giren) + '</span>'; }
  function satirAksiyon(r) {
    return '<div class="acts">' + waBtn(r) + '<button class="icon-btn" data-fis="' + esc(r.id) + '" title="Fiş yazdır" aria-label="Fiş yazdır">' + ic('printer') + '</button><button class="icon-btn" data-edit="' + esc(r.id) + '" title="Düzenle" aria-label="Düzenle">' + ic('edit') + '</button><button class="icon-btn bad" data-sil-islem="' + esc(r.id) + '" title="Sil" aria-label="Sil">' + ic('trash') + '</button></div>';
  }
  function durumSelect(r) { return '<select class="durum" data-durum="' + esc(r.id) + '" aria-label="Durum">' + DURUMLAR.map(function (d) { return '<option' + (d === r.durum ? ' selected' : '') + '>' + d + '</option>'; }).join('') + '</select>'; }

  function fill(tab, v, o) {
    var d = S.data;
    if (tab === 'bugun') $('#view').innerHTML = bugunHTML(v);
    if (tab === 'ajanda') $('#view').innerHTML = ajandaHTML();
    if (tab === 'randevu') {
      $('#onaybox').innerHTML = onayKutusu(v.bekleyen);
      var l = v.donem.filter(function (r) { return !ui.durumFiltre || r.durum === ui.durumFiltre; }).sort(function (a, b) { return a.baslangic < b.baslangic ? 1 : -1; });
      $('#liste').innerHTML = l.length ? l.map(function (r) { return '<tr><td><div class="who"><span class="avatar">' + esc(bas(mAd(r))) + '</span><div><b>' + esc(mAd(r)) + '</b><br><span class="muted small">' + esc(r.hizmet) + '</span></div></div></td><td>' + esc(S.personelAd(r.personelId)) + '<br>' + kaynak(r) + '</td><td>' + esc(fmtDT(r.baslangic)) + '<br><span class="muted small">' + esc(saat(r.bitis)) + '' + 'e kadar</span></td><td><b>' + tl(r.tutar) + '</b>' + (r.bahsis ? '<br><span class="muted small">+' + tl(r.bahsis) + ' bahşiş</span>' : '') + '<br><span class="muted small">' + esc(r.odeme) + '</span></td><td>' + durumSelect(r) + '</td><td>' + satirAksiyon(r) + '</td></tr>'; }).join('')
        : '<tr><td colspan="6" class="empty">Bu dönemde kayıt yok.</td></tr>';
    }
    if (tab === 'musteri') musteriListe();
    if (tab === 'personel') $('#plist').innerHTML = d.personeller.map(function (p) {
      var h = hakedis(p, v);
      return '<tr><td><b>' + esc(p.ad) + (p.aktif ? '' : ' <span class="tag">pasif</span>') + '</b><br><span class="muted small">' + esc(p.uzmanlik) + (p.cred ? '' : ' · <span style="color:var(--bad)">şifre yok</span>') + '</span></td><td>' + tl(p.maas) + '</td><td>' + tl(h.ciro) + '<br><span class="muted small">%' + p.prim + ' prim: +' + tl(h.prim) + '</span></td><td><b style="color:var(--ok)">' + tl(h.net) + '</b><br><span class="muted small">−' + tl(h.avans) + ' avans</span></td><td><div class="acts"><button class="btn ghost sm" data-sifre="' + esc(p.id) + '">Şifre</button><button class="icon-btn bad" data-sil-personel="' + esc(p.id) + '" aria-label="Personeli sil">' + ic('trash') + '</button></div></td></tr>'; }).join('');
    if (tab === 'kasa') {
      $('#kasaozet').innerHTML = kpi('Nakit', tl(o.nakit), '') + kpi('Kredi kartı', tl(o.kart), '') + kpi('Havale / EFT', tl(o.havale), '') + kpi('Bahşiş', tl(o.bahsis), '') +
        kpi('Gider', tl(o.gider), '', 'bad') + kpi('Avans', tl(o.avans), '', 'warn') + kpi('Ciro', tl(o.ciro), '') + kpi('Net kâr', tl(o.net), '', o.net < 0 ? 'bad' : 'ok');
      $('#avlist').innerHTML = v.avans.length ? v.avans.map(function (a) { return '<tr><td>' + esc(a.tarih) + '</td><td>' + esc(S.personelAd(a.personelId)) + '</td><td>' + esc(a.not || '-') + '</td><td><b>' + tl(a.tutar) + '</b></td><td><button class="icon-btn bad" data-sil-avans="' + esc(a.id) + '" aria-label="Sil">' + ic('trash') + '</button></td></tr>'; }).join('') : '<tr><td colspan="5" class="empty">Kayıt yok.</td></tr>';
      $('#glist').innerHTML = v.gider.length ? v.gider.map(function (g) { return '<tr><td>' + esc(g.tarih) + '</td><td>' + esc(g.aciklama) + '</td><td>' + esc(g.kategori) + '</td><td><b>' + tl(g.tutar) + '</b></td><td><button class="icon-btn bad" data-sil-gider="' + esc(g.id) + '" aria-label="Sil">' + ic('trash') + '</button></td></tr>'; }).join('') : '<tr><td colspan="5" class="empty">Kayıt yok.</td></tr>';
    }
    if (tab === 'ayar') {
      $('#hizlist').innerHTML = d.hizmetler.map(function (h) { return '<tr><td>' + esc(hizmetAd(h)) + '</td><td><input type="number" min="5" value="' + h.sure + '" data-hs="' + esc(h.id) + '" style="width:80px" aria-label="Süre"></td><td><input type="number" min="0" value="' + h.fiyat + '" data-hf="' + esc(h.id) + '" style="width:100px" aria-label="Fiyat"></td></tr>'; }).join('');
      $('#loglist').innerHTML = d.log.slice(0, 50).map(function (x) { return '<tr><td class="muted small">' + esc(fmtDT(x.t.slice(0, 16))) + '</td><td>' + esc(x.kim) + '</td><td>' + esc(x.islem) + '</td></tr>'; }).join('') || '<tr><td class="empty">Kayıt yok.</td></tr>';
    }
  }

  function musteriListe() {
    var q = ui.musteriAra.trim().toLowerCase(), qn = S.normTel(q);
    var l = S.data.musteriler.filter(function (m) { return !q || m.ad.toLowerCase().indexOf(q) >= 0 || (qn && S.normTel(m.telefon).indexOf(qn) >= 0); })
      .map(function (m) { return { m: m, s: musteriIstat(m) }; }).sort(function (a, b) { return (b.s.son || '') < (a.s.son || '') ? -1 : 1; });
    $('#mlist').innerHTML = l.length ? l.map(function (x) {
      var wa = waNo(x.m.telefon) ? '<a class="icon-btn wa" href="https://wa.me/' + waNo(x.m.telefon) + '" target="_blank" rel="noopener" aria-label="WhatsApp">' + ic('chat') + '</a>' : '';
      return '<tr><td><div class="who"><span class="avatar">' + esc(bas(x.m.ad)) + '</span><div><b>' + esc(x.m.ad) + '</b>' + (x.s.gelmedi ? ' <span class="tag bad">' + x.s.gelmedi + ' gelmedi</span>' : '') + (x.m.not ? '<br><span class="muted small">' + esc(x.m.not.slice(0, 50)) + '</span>' : '') + '</div></div></td><td>' + esc(x.m.telefon || '—') + '</td><td>' + x.s.adet + '</td><td><b>' + tl(x.s.harcama) + '</b></td><td>' + esc(x.s.son ? fmtDT(x.s.son) : '—') + '</td><td><div class="acts">' + wa + '<button class="icon-btn" data-musteri="' + esc(x.m.id) + '" aria-label="Müşteri kartı">' + ic('edit') + '</button></div></td></tr>'; }).join('')
      : '<tr><td colspan="6" class="empty">Müşteri bulunamadı.</td></tr>';
  }

  /* ---------- Bugün ---------- */
  function bugunHTML(v) {
    var simdi = nowStr(), siradaki = v.bugun.filter(function (r) { return r.durum === 'Onaylı' && r.baslangic >= simdi; })[0];
    var hero;
    if (siradaki) {
      var fark = Math.round((new Date(siradaki.baslangic) - new Date()) / 60000);
      hero = '<div class="card hero"><div class="next"><div class="grow"><div class="small muted">SIRADAKİ MÜŞTERİ · ' + (fark < 60 ? fark + ' dk sonra' : 'saat ' + esc(saat(siradaki.baslangic))) + '</div><div class="big">' + esc(mAd(siradaki)) + '</div><div>' + esc(siradaki.hizmet) + ' · ' + esc(S.personelAd(siradaki.personelId)) + ' · ' + esc(saat(siradaki.baslangic)) + '-' + esc(saat(siradaki.bitis)) + '</div></div><div class="acts"><a class="btn ghost" href="' + (waNo(mTel(siradaki)) ? 'https://wa.me/' + waNo(mTel(siradaki)) + '?text=' + encodeURIComponent(waMsg(siradaki)) : '#') + '" target="_blank" rel="noopener">' + ic('chat') + ' Hatırlat</a><button class="btn ghost" data-edit="' + esc(siradaki.id) + '">' + ic('edit') + ' Detay</button></div></div></div>';
    } else hero = '<div class="card hero"><div class="next"><div class="grow"><div class="small muted">SIRADAKİ MÜŞTERİ</div><div class="big">Bugün için bekleyen randevu yok</div></div><button class="btn ghost" data-yeni-randevu="">' + ic('plus') + ' Randevu ekle</button></div></div>';

    var prog = v.bugun.length ? '<div class="tl">' + v.bugun.map(function (r) {
      return '<div class="tl-item' + (r.bitis < simdi ? ' past' : '') + '"><div class="tm">' + esc(saat(r.baslangic)) + '</div><div><b>' + esc(mAd(r)) + '</b> ' + durumTag(r.durum) + '<br><span class="muted small">' + esc(r.hizmet) + ' · ' + esc(S.personelAd(r.personelId)) + '</span></div><div class="acts">' + (r.durum === 'Onaylı' ? '<button class="btn ok sm" data-geldi="' + esc(r.id) + '">' + ic('check') + ' Geldi</button>' : '') + waBtn(r) + '<button class="icon-btn" data-edit="' + esc(r.id) + '" aria-label="Düzenle">' + ic('edit') + '</button></div></div>'; }).join('') + '</div>'
      : '<p class="empty">Bugün için randevu yok.</p>';

    var kat = {}, per = {};
    v.aktif.forEach(function (r) { var k = r.hizmet.split(' · ')[0]; kat[k] = (kat[k] || 0) + (Number(r.tutar) || 0); per[r.personelId] = (per[r.personelId] || 0) + (Number(r.tutar) || 0); });
    return onayKutusu(v.bekleyen) + hero +
      '<div class="grid2"><div class="card"><h3>Bugünün programı</h3>' + prog + '</div><div class="card"><h3>Son 14 gün ciro</h3>' + gunlukGrafik() + '</div></div>' +
      '<div class="grid2"><div class="card"><h3>Hizmet grubuna göre ciro · ' + esc(aralikAd()) + '</h3>' + hbars(Object.keys(kat).map(function (k) { return { ad: k, v: kat[k] }; })) + '</div>' +
      '<div class="card"><h3>Personele göre ciro · ' + esc(aralikAd()) + '</h3>' + hbars(Object.keys(per).map(function (k) { return { ad: S.personelAd(k), v: per[k] }; })) + '</div></div>';
  }

  /* ---------- Ajanda (saat ızgarası) ---------- */
  function ajandaHTML() {
    var gun = ui.ajTarih, ac = S.data.ayar.acilis, kp = S.data.ayar.kapanis, saatler = kp - ac, bugunMu = gun === S.ymd(new Date());
    var pers = S.data.personeller.filter(function (p) { return p.aktif; });
    var simdi = new Date(), nowMin = simdi.getHours() * 60 + simdi.getMinutes();
    var sayi = S.data.islemler.filter(function (r) { return r.baslangic.slice(0, 10) === gun && r.durum !== 'İptal'; }).length;
    var html = '<div class="card"><div class="toolbar" style="margin-bottom:12px"><div class="cal-nav"><button class="icon-btn" data-aj="-1" aria-label="Önceki gün">' + ic('left') + '</button><div class="dtitle">' + esc(new Date(gun + 'T00:00').toLocaleDateString('tr-TR', { weekday: 'long', day: 'numeric', month: 'long' })) + '</div><button class="icon-btn" data-aj="1" aria-label="Sonraki gün">' + ic('right') + '</button><button class="btn ghost sm" data-aj="0">Bugün</button><input type="date" id="ajtarih" value="' + esc(gun) + '" aria-label="Tarih seç"></div>' +
      '<div class="legend"><span><i style="background:var(--brand-soft);border:1px solid var(--brand)"></i>Onaylı</span><span><i style="background:var(--ok-soft);border:1px solid var(--ok)"></i>Geldi</span><span><i style="background:var(--warn-soft);border:1px solid var(--warn)"></i>Onay bekliyor</span><span><i style="background:var(--bad-soft);border:1px solid var(--bad)"></i>Gelmedi</span></div></div>' +
      '<p class="muted small" style="margin:0 0 10px">' + sayi + ' randevu. Boş bir saate tıklayarak o saate randevu ekleyebilir, bir kutuya tıklayarak düzenleyebilirsiniz.</p><div class="scroll"><div class="tg" style="--cols:' + pers.length + ';--h:' + H + 'px">';
    html += '<div class="tg-time"><div class="tg-spacer" style="height:40px"></div>';
    for (var i = 0; i < saatler; i++) html += '<div>' + pad(ac + i) + ':00</div>';
    html += '</div>';
    pers.forEach(function (p) {
      var rs = S.data.islemler.filter(function (r) { return r.personelId === p.id && r.baslangic.slice(0, 10) === gun && r.durum !== 'İptal'; });
      html += '<div class="tg-col"><div class="tg-head">' + esc(p.ad) + '</div><div class="tg-body" data-pid="' + esc(p.id) + '" style="height:' + saatler * H + 'px">';
      rs.forEach(function (r) {
        var b = dkOf(saat(r.baslangic)) - ac * 60, e = dkOf(saat(r.bitis)) - ac * 60;
        if (e <= 0 || b >= saatler * 60) return;
        b = Math.max(b, 0); e = Math.min(e, saatler * 60);
        var cls = r.durum === 'Bekliyor' ? 'wait' : r.durum === 'Geldi' ? 'geldi' : r.durum === 'Gelmedi' ? 'gelmedi' : '';
        html += '<div class="blk ' + cls + '" data-edit="' + esc(r.id) + '" style="top:' + (b * H / 60) + 'px;height:' + Math.max(22, (e - b) * H / 60 - 2) + 'px" title="' + esc(mAd(r) + ' · ' + r.hizmet) + '"><b>' + esc(saat(r.baslangic)) + ' ' + esc(mAd(r)) + '</b>' + esc(r.hizmet.split(' · ').pop()) + '</div>';
      });
      if (bugunMu && nowMin >= ac * 60 && nowMin <= kp * 60) html += '<div class="now-line" style="top:' + ((nowMin - ac * 60) * H / 60) + 'px"></div>';
      html += '</div></div>';
    });
    html += '</div></div></div>';
    if (!pers.length) html = '<div class="card"><p class="empty">Aktif personel yok.</p></div>';
    return html;
  }

  /* ---------- olaylar ---------- */
  document.addEventListener('click', async function (e) {
    var t = e.target, el, d = S.data;
    if ((el = t.closest('[data-lt]'))) { ui.loginTip = el.dataset.lt; return renderLogin(); }
    if ((el = t.closest('[data-close]'))) { var mm = el.closest('.modal'); if (mm) mm.remove(); return; }
    if (t.closest('[data-nowa]')) return toast('Bu müşteri için telefon numarası kayıtlı değil.', true);
    if ((el = t.closest('[data-more]'))) { ui.moreAcik = !ui.moreAcik; return renderAdminView(); }
    if ((el = t.closest('[data-tab]'))) { ui.tab = el.dataset.tab; ui.moreAcik = false; return renderAdminView(); }
    if (ui.moreAcik && !t.closest('#morebox')) { ui.moreAcik = false; renderAdminView(); }
    if ((el = t.closest('[data-ar]'))) { ui.aralik = el.dataset.ar; return renderAdminView(); }
    if ((el = t.closest('[data-aj]'))) { var dd = new Date(ui.ajTarih + 'T00:00'); if (el.dataset.aj === '0') dd = new Date(); else dd.setDate(dd.getDate() + Number(el.dataset.aj)); ui.ajTarih = S.ymd(dd); return renderAdminView(); }
    if ((el = t.closest('[data-yeni-randevu]'))) { var mid = el.dataset.yeniRandevu, mus = mid && S.musteri(mid); if (mus) { var op = el.closest('.modal'); if (op) op.remove(); } return islemModal(null, mus ? { musteri: mus } : null); }
    if ((el = t.closest('[data-yeni-musteri]'))) return musteriModal(null);
    if ((el = t.closest('[data-musteri]'))) return musteriModal(el.dataset.musteri);
    if ((el = t.closest('[data-fis]'))) { var rf = d.islemler.filter(function (x) { return x.id === el.dataset.fis; })[0]; return rf && fis(rf); }
    if ((el = t.closest('[data-edit]'))) { var re = d.islemler.filter(function (x) { return x.id === el.dataset.edit; })[0]; return re && islemModal(re); }
    if ((el = t.closest('[data-geldi]'))) { var rg = d.islemler.filter(function (x) { return x.id === el.dataset.geldi; })[0]; if (rg) { rg.durum = 'Geldi'; S.save('Yönetici', 'Geldi: ' + mAd(rg)); toast(mAd(rg) + ' geldi olarak işaretlendi.'); } return; }
    if ((el = t.closest('.tg-body'))) { // boş saate tıklama
      var rect = el.getBoundingClientRect(), dk = Math.round(((e.clientY - rect.top) / H * 60) / 15) * 15 + d.ayar.acilis * 60;
      return islemModal(null, { personelId: el.dataset.pid, baslangic: ui.ajTarih + 'T' + pad(Math.floor(dk / 60)) + ':' + pad(dk % 60) });
    }
    if ((el = t.closest('[data-onay]'))) {
      var r = d.islemler.filter(function (x) { return x.id === el.dataset.onay; })[0];
      if (r) { var c = S.cakisma(r.personelId, r.baslangic, r.bitis, r.id); if (c && !(await confirmBox('Bu saatte ' + S.personelAd(r.personelId) + ' için "' + mAd(c) + '" kaydı var. Yine de onaylansın mı?', 'Onayla'))) return;
        r.durum = 'Onaylı'; S.save('Yönetici', 'Onaylandı: ' + mAd(r)); toast('İşlem onaylandı.'); } return;
    }
    if ((el = t.closest('[data-red]'))) {
      var rr = d.islemler.filter(function (x) { return x.id === el.dataset.red; })[0];
      if (rr && await confirmBox('"' + mAd(rr) + '" talebi reddedilip silinsin mi?', 'Reddet')) { d.islemler = d.islemler.filter(function (x) { return x !== rr; }); S.save('Yönetici', 'Reddedildi: ' + mAd(rr)); toast('Talep reddedildi.', true); } return;
    }
    var sil = [['sil-islem', 'islemler', 'Kayıt silinsin mi?'], ['sil-avans', 'avanslar', 'Avans kaydı silinsin mi?'], ['sil-gider', 'giderler', 'Gider kaydı silinsin mi?'], ['sil-musteri', 'musteriler', 'Müşteri silinsin mi?']];
    for (var i = 0; i < sil.length; i++) {
      if ((el = t.closest('[data-' + sil[i][0] + ']'))) { var id = el.getAttribute('data-' + sil[i][0]), kapsa = el.closest('.modal');
        if (await confirmBox(sil[i][2], 'Sil')) { d[sil[i][1]] = d[sil[i][1]].filter(function (x) { return x.id !== id; }); if (kapsa) kapsa.remove(); S.save('Yönetici', 'Silindi: ' + sil[i][1]); toast('Silindi.', true); } return; }
    }
    if ((el = t.closest('[data-sil-personel]'))) {
      var pid = el.dataset.silPersonel, p = S.personel(pid), kullanim = d.islemler.some(function (x) { return x.personelId === pid; });
      if (kullanim) { if (await confirmBox(p.ad + ' geçmiş kayıtlara sahip; silmek yerine pasife alınsın mı?', 'Pasife al')) { p.aktif = false; S.save('Yönetici', 'Personel pasif: ' + p.ad); } }
      else if (await confirmBox(p.ad + ' silinsin mi?', 'Sil')) { d.personeller = d.personeller.filter(function (x) { return x.id !== pid; }); S.save('Yönetici', 'Personel silindi: ' + p.ad); }
      return yenileGorunum();
    }
    if ((el = t.closest('[data-sifre]'))) {
      var pp = S.personel(el.dataset.sifre), yeni = prompt(pp.ad + ' için yeni şifre (en az 6 karakter):');
      if (yeni && yeni.length >= 6) { pp.cred = await S.makeCred(yeni); S.save('Yönetici', 'Personel şifresi: ' + pp.ad); toast('Şifre güncellendi.'); } else if (yeni) toast('Şifre en az 6 karakter olmalı.', true); return;
    }
    if ((el = t.closest('[data-a]'))) {
      var a = el.dataset.a;
      if (a === 'cikis') { S.session.clear(); ui.moreAcik = false; var mb = $('#morebox'); if (mb) mb.remove(); return boot(); }
      if (a === 'rapor') return navigator.clipboard.writeText(ozetMetni()).then(function () { toast('Rapor panoya kopyalandı.'); }, function () { toast('Kopyalanamadı.', true); });
      if (a === 'raporyaz') return raporYazdir();
      if (a === 'csv') return indir('randevular.csv', csv([['Müşteri', 'Telefon', 'Hizmet', 'Personel', 'Kaynak', 'Başlangıç', 'Bitiş', 'Tutar', 'Bahşiş', 'Ödeme', 'Durum']].concat(veri().donem.map(function (r) { return [mAd(r), mTel(r), r.hizmet, S.personelAd(r.personelId), r.kaynak, r.baslangic, r.bitis, r.tutar, r.bahsis, r.odeme, r.durum]; }))), 'text/csv');
      if (a === 'csvgider') return indir('giderler.csv', csv([['Tarih', 'Açıklama', 'Kategori', 'Tutar']].concat(veri().gider.map(function (g) { return [g.tarih, g.aciklama, g.kategori, g.tutar]; }))), 'text/csv');
      if (a === 'yedek') return indir('gulaksu-yedek-' + S.ymd(new Date()) + '.json', JSON.stringify(S.data), 'application/json');
      if (a === 'demo') { if (await confirmBox('Mevcut randevu, müşteri, avans ve giderler demo veriyle değiştirilecek. Devam?', 'Yükle')) { S.resetDemo(); yenileGorunum(); toast('Demo veri yüklendi.'); } return; }
      if (a === 'hakgider') {
        var vv = veri(), ay = S.ymd(new Date()).slice(0, 7), var_ = d.giderler.some(function (g) { return g.kategori === 'Personel Hakediş' && g.tarih.slice(0, 7) === ay; });
        if (var_ && !(await confirmBox('Bu ay hakedişler zaten gider yazılmış. Tekrar eklensin mi?', 'Ekle'))) return;
        var top = 0, det = d.personeller.filter(function (x) { return x.aktif; }).map(function (x) { var n = hakedis(x, vv).net; top += n; return x.ad + ': ' + tl(n); });
        d.giderler.push({ id: S.uid(), aciklama: 'Personel hakedişleri → ' + det.join(' | '), kategori: 'Personel Hakediş', tutar: top, tarih: S.ymd(new Date()) });
        S.save('Yönetici', 'Hakedişler gider yazıldı'); toast('Hakedişler gider olarak eklendi.');
      }
    }
  });
  document.addEventListener('change', function (e) {
    var el = e.target, d = S.data, x;
    if (el.dataset && el.dataset.durum) { x = d.islemler.filter(function (r) { return r.id === el.dataset.durum; })[0]; if (x) { x.durum = el.value; S.save('Yönetici', 'Durum: ' + mAd(x) + ' → ' + el.value); toast('Durum güncellendi.'); } return; }
    if (el.id === 'durumfiltre') { ui.durumFiltre = el.value; return renderAdminView(); }
    if (el.id === 'ajtarih') { if (el.value) { ui.ajTarih = el.value; renderAdminView(); } return; }
    if (el.dataset && (el.dataset.hs || el.dataset.hf)) {
      x = d.hizmetler.filter(function (h) { return h.id === (el.dataset.hs || el.dataset.hf); })[0];
      if (x) { if (el.dataset.hs) x.sure = Math.max(5, Number(el.value) || x.sure); else x.fiyat = Math.max(0, Number(el.value) || 0); S.save('Yönetici', 'Hizmet güncellendi: ' + x.ad); toast('Güncellendi.'); }
    }
  });
  document.addEventListener('input', function (e) { if (e.target.id === 'mara') { ui.musteriAra = e.target.value; musteriListe(); } });

  /* ---------- personel paneli ---------- */
  function renderPersonel(oturum) {
    root.innerHTML = '<div class="content"><div class="head"><div><h1 class="serif" style="letter-spacing:.14em;color:var(--brand)">GÜL AKSU</h1><div class="muted small">' + esc(oturum.ad) + ' · Personel paneli</div></div><div class="actions"><button class="btn ghost sm" data-a="cikis">' + ic('logout') + ' Çıkış</button></div></div>' +
      '<main><section class="kpis" id="pk"></section><div class="grid3"><div class="card"><h3>Yeni işlem (yönetici onayına gider)</h3>' + islemForm('s', { personelSecimi: false, durumSecimi: false, btn: 'Onaya gönder' }) + '</div><div class="card"><h3>Bugünkü randevularım</h3><div id="pbugun"></div><h3 style="margin-top:18px">Tüm işlemlerim</h3><div class="scroll"><table><thead><tr><th>Müşteri / hizmet</th><th>Zaman</th><th>Tutar</th><th>Durum</th></tr></thead><tbody id="pl"></tbody></table></div></div></div></main></div>';
    bindIslemForm('s', { getPid: function () { return oturum.pid; }, durum: 'Bekliyor', giren: function () { return oturum.ad; } });
    renderPersonelData();
  }
  function renderPersonelData() {
    var oturum = S.session.get(); if (!oturum || oturum.rol !== 'personel' || !$('#pk')) return;
    var p = S.personel(oturum.pid); if (!p || !p.aktif) { S.session.clear(); return boot(); }
    ui.aralik = 'ay';
    var v = veri(), h = hakedis(p, v), benim = S.data.islemler.filter(function (r) { return r.personelId === p.id; });
    $('#pk').innerHTML = kpi('Cirom (bu ay)', tl(h.ciro), 'Prim: ' + tl(h.prim)) + kpi('Bahşiş', tl(sum(v.aktif.filter(function (r) { return r.personelId === p.id; }), function (r) { return r.bahsis; })), '') + kpi('Avanslarım', tl(h.avans), '', 'warn') + kpi('Tahmini hakediş', tl(h.net), 'Maaş + prim − avans', 'ok');
    var simdi = S.ymd(new Date()), bg = benim.filter(function (r) { return r.baslangic.slice(0, 10) === simdi && r.durum !== 'İptal'; }).sort(function (a, b) { return a.baslangic < b.baslangic ? -1 : 1; });
    $('#pbugun').innerHTML = bg.length ? '<div class="tl">' + bg.map(function (r) { return '<div class="tl-item"><div class="tm">' + esc(saat(r.baslangic)) + '</div><div><b>' + esc(mAd(r)) + '</b> ' + durumTag(r.durum) + '<br><span class="muted small">' + esc(r.hizmet) + '</span></div><div></div></div>'; }).join('') + '</div>' : '<p class="empty">Bugün için randevunuz yok.</p>';
    var l = benim.slice().sort(function (a, b) { return a.baslangic < b.baslangic ? 1 : -1; });
    $('#pl').innerHTML = l.length ? l.map(function (r) { return '<tr><td><b>' + esc(mAd(r)) + '</b><br><span class="muted small">' + esc(r.hizmet) + '</span></td><td>' + esc(fmtDT(r.baslangic)) + ' - ' + esc(saat(r.bitis)) + '</td><td><b>' + tl(r.tutar) + '</b></td><td>' + durumTag(r.durum) + '</td></tr>'; }).join('') : '<tr><td colspan="4" class="empty">Henüz işleminiz yok.</td></tr>';
  }

  /* ---------- açılış ---------- */
  function boot() {
    var s = S.session.get();
    if (!s || (s.rol === 'personel' && !S.personel(s.pid))) { S.session.clear(); return renderLogin(); }
    if (s.rol === 'admin') { if (!S.data.admin) { S.session.clear(); return renderLogin(); } renderAdmin(); }
    else renderPersonel(s);
  }
  S.subscribe(function () {
    var s = S.session.get(); if (!s) return;
    if (s.rol === 'admin' && $('#view')) renderAdminView(); else if (s.rol === 'personel') renderPersonelData();
  });
  setInterval(function () { // "sıradaki müşteri" ve şimdi çizgisi güncel kalsın
    var s = S.session.get();
    if (s && s.rol === 'admin' && $('#view') && (ui.tab === 'bugun' || ui.tab === 'ajanda') && !$('.modal')) renderAdminView();
  }, 60000);
  boot();
})();
