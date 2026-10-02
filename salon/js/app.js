(function () {
  'use strict';
  var S = Store, esc = S.esc, tl = S.tl;
  var root = document.getElementById('root');
  var ui = { tab: 'randevu', aralik: 'ay', loginTip: 'admin', deneme: 0, kilit: 0 };

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function sum(arr, f) { return arr.reduce(function (a, x) { return a + (Number(f(x)) || 0); }, 0); }
  function fmtDT(s) { return s ? s.replace('T', ' ') : ''; }
  function saat(s) { return (s || '').split('T')[1] || ''; }
  function dakika(r) { var m = (new Date(r.bitis) - new Date(r.baslangic)) / 60000; return m > 0 ? m : 0; }

  /* ---------- bildirim / onay penceresi ---------- */
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
  function confirmBox(msg, okText) {
    return new Promise(function (res) {
      var m = document.createElement('div'); m.className = 'modal'; m.setAttribute('role', 'dialog');
      m.innerHTML = '<div class="box"><p>' + esc(msg) + '</p><div class="btns"><button class="btn ghost" data-r="0">Vazgeç</button><button class="btn bad" data-r="1">' + esc(okText || 'Evet') + '</button></div></div>';
      m.addEventListener('click', function (e) { var b = e.target.closest('[data-r]'); if (b || e.target === m) { m.remove(); res(!!b && b.dataset.r === '1'); } });
      document.body.appendChild(m); $('[data-r="0"]', m).focus();
    });
  }
  S.onYeniTalep = function () { ses(); toast('Yeni randevu talebi geldi'); };

  /* ---------- dönem filtresi ---------- */
  function aralik() {
    var n = new Date(), b = new Date(n.getFullYear(), n.getMonth(), n.getDate()), e = null;
    if (ui.aralik === 'bugun') { e = new Date(b); }
    else if (ui.aralik === 'hafta') { var g = (b.getDay() + 6) % 7; b.setDate(b.getDate() - g); e = new Date(b); e.setDate(e.getDate() + 6); }
    else if (ui.aralik === 'ay') { b = new Date(n.getFullYear(), n.getMonth(), 1); e = new Date(n.getFullYear(), n.getMonth() + 1, 0); }
    else return null;
    return [S.ymd(b), S.ymd(e)];
  }
  function icinde(tarih) { var a = aralik(); var d = (tarih || '').slice(0, 10); return !a || (d >= a[0] && d <= a[1]); }
  function veri() {
    var d = S.data;
    return {
      onayli: d.islemler.filter(function (r) { return r.durum === 'Onaylı' && icinde(r.baslangic); }),
      bekleyen: d.islemler.filter(function (r) { return r.durum === 'Bekliyor'; }),
      avans: d.avanslar.filter(function (a) { return icinde(a.tarih); }),
      gider: d.giderler.filter(function (g) { return icinde(g.tarih); })
    };
  }
  function ozet(v) {
    var ciro = sum(v.onayli, function (r) { return r.tutar; });
    var gider = sum(v.gider, function (g) { return g.tutar; }), avans = sum(v.avans, function (a) { return a.tutar; });
    function odeme(t) { return sum(v.onayli.filter(function (r) { return r.odeme === t; }), function (r) { return r.tutar; }); }
    return { ciro: ciro, gider: gider, avans: avans, net: ciro - gider - avans, bahsis: sum(v.onayli, function (r) { return r.bahsis; }),
      nakit: odeme('Nakit'), kart: odeme('Kredi Kartı'), havale: odeme('Havale / EFT'), adet: v.onayli.length };
  }
  function hakedis(p, v) {
    var ciro = sum(v.onayli.filter(function (r) { return r.personelId === p.id; }), function (r) { return r.tutar; });
    var prim = ciro * p.prim / 100;
    var avans = sum(v.avans.filter(function (a) { return a.personelId === p.id; }), function (a) { return a.tutar; });
    return { ciro: ciro, prim: prim, avans: avans, net: p.maas + prim - avans };
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
          '<button class="btn" style="width:100%">Kurulumu Tamamla</button><div class="err" id="err"></div></form>'
        : '<div class="seg"><button data-lt="admin" class="' + (ui.loginTip === 'admin' ? 'on' : '') + '">Yönetici</button><button data-lt="personel" class="' + (ui.loginTip === 'personel' ? 'on' : '') + '">Personel</button></div>' +
          '<form id="giris">' +
          (ui.loginTip === 'personel'
            ? '<div class="field"><label for="ps">Personel</label><select id="ps">' + S.data.personeller.filter(function (p) { return p.aktif; }).map(function (p) { return '<option value="' + esc(p.id) + '">' + esc(p.ad) + '</option>'; }).join('') + '</select></div>'
            : '') +
          '<div class="field"><label for="pw">Şifre</label><input id="pw" type="password" autocomplete="current-password" required></div>' +
          '<button class="btn" style="width:100%">Giriş Yap</button><div class="err" id="err"></div></form>') +
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
      var pw = $('#pw').value, ok = false, ses_;
      if (ui.loginTip === 'admin') { ok = await S.checkCred(S.data.admin, pw); ses_ = { rol: 'admin', ad: 'Yönetici' }; }
      else { var p = S.personel($('#ps').value); ok = !!p && !!p.cred && await S.checkCred(p.cred, pw); ses_ = p && { rol: 'personel', ad: p.ad, pid: p.id };
        if (p && !p.cred) return ($('#err').textContent = 'Bu personel için şifre henüz belirlenmemiş. Yöneticiye başvurun.'); }
      if (!ok) { if (++ui.deneme >= 5) { ui.kilit = Date.now() + 30000; ui.deneme = 0; } return ($('#err').textContent = 'Hatalı şifre.'); }
      ui.deneme = 0; S.session.set(ses_); S.save(ses_.ad, 'Giriş yapıldı'); boot();
    });
  }
  root.addEventListener('click', function (e) {
    var b = e.target.closest('[data-lt]'); if (b) { ui.loginTip = b.dataset.lt; renderLogin(); }
  });

  /* ---------- ortak form parçaları ---------- */
  function hizmetSelect(id) {
    var gr = {};
    S.data.hizmetler.forEach(function (h) { (gr[h.kategori] = gr[h.kategori] || []).push(h); });
    return '<select id="' + id + '" required><option value="">Hizmet seçin</option>' + Object.keys(gr).map(function (k) {
      return '<optgroup label="' + esc(k) + '">' + gr[k].map(function (h) {
        return '<option value="' + esc(h.id) + '">' + esc(h.ad) + '</option>'; }).join('') + '</optgroup>'; }).join('') + '</select>';
  }
  function hizmetAd(h) { return h.kategori + ' · ' + h.ad; }
  function islemForm(pre, personelSecimi) {
    return '<form id="' + pre + 'form"><div class="field"><label for="' + pre + 'm">Müşteri adı soyadı</label><input id="' + pre + 'm" required maxlength="80"></div>' +
      '<div class="field"><label for="' + pre + 'h">Hizmet</label>' + hizmetSelect(pre + 'h') + '</div>' +
      (personelSecimi ? '<div class="field"><label for="' + pre + 'p">Personel</label><select id="' + pre + 'p">' +
        S.data.personeller.filter(function (p) { return p.aktif; }).map(function (p) { return '<option value="' + esc(p.id) + '">' + esc(p.ad) + '</option>'; }).join('') + '</select></div>' : '') +
      '<div class="row2"><div class="field"><label for="' + pre + 'b">Başlangıç</label><input id="' + pre + 'b" type="datetime-local" required></div>' +
      '<div class="field"><label for="' + pre + 'e">Bitiş</label><input id="' + pre + 'e" type="datetime-local" required></div></div>' +
      '<div class="row2"><div class="field"><label for="' + pre + 't">Tutar (₺)</label><input id="' + pre + 't" type="number" min="0" required></div>' +
      '<div class="field"><label for="' + pre + 's">Bahşiş (₺)</label><input id="' + pre + 's" type="number" min="0" value="0"></div></div>' +
      '<div class="field"><label for="' + pre + 'o">Ödeme türü</label><select id="' + pre + 'o"><option>Nakit</option><option>Kredi Kartı</option><option>Havale / EFT</option></select></div>' +
      '<div id="' + pre + 'uyari"></div><button class="btn" style="width:100%">' + (personelSecimi ? 'Randevuyu Kaydet' : 'Onaya Gönder') + '</button></form>';
  }
  function bindIslemForm(pre, getPid, durum, giren) {
    var f = $('#' + pre + 'form');
    var hz = $('#' + pre + 'h');
    hz.addEventListener('change', function () { // hizmet seçilince süre ve fiyatı doldur
      var h = S.data.hizmetler.filter(function (x) { return x.id === hz.value; })[0]; if (!h) return;
      $('#' + pre + 't').value = h.fiyat;
      var b = $('#' + pre + 'b').value;
      if (b) { var d = new Date(b); d.setMinutes(d.getMinutes() - d.getTimezoneOffset() + h.sure); $('#' + pre + 'e').value = d.toISOString().slice(0, 16); }
    });
    $('#' + pre + 'b').addEventListener('change', function () { hz.dispatchEvent(new Event('change')); });
    f.addEventListener('submit', function (e) {
      e.preventDefault();
      var h = S.data.hizmetler.filter(function (x) { return x.id === hz.value; })[0], pid = getPid();
      var bas = $('#' + pre + 'b').value, bit = $('#' + pre + 'e').value;
      if (bit <= bas) return toast('Bitiş, başlangıçtan sonra olmalı.', true);
      var c = S.cakisma(pid, bas, bit);
      if (c && !f.dataset.zorla) {
        $('#' + pre + 'uyari').innerHTML = '<div class="warnbox">⚠ ' + esc(S.personelAd(pid)) + ' için ' + esc(fmtDT(c.baslangic)) + ' - ' + esc(saat(c.bitis)) + ' arasında "' + esc(c.musteri) + '" kaydı var. Yine de kaydetmek için tekrar basın.</div>';
        f.dataset.zorla = '1'; return;
      }
      S.data.islemler.push({ id: S.uid(), musteri: $('#' + pre + 'm').value.trim(), telefon: '', hizmet: hizmetAd(h), personelId: pid,
        giren: giren(), kaynak: 'Panel', baslangic: bas, bitis: bit, tutar: Number($('#' + pre + 't').value) || 0,
        bahsis: Number($('#' + pre + 's').value) || 0, odeme: $('#' + pre + 'o').value, durum: durum });
      S.save(giren(), durum === 'Onaylı' ? 'Randevu eklendi' : 'Onaya gönderildi');
      f.reset(); delete f.dataset.zorla; $('#' + pre + 'uyari').innerHTML = '';
      toast(durum === 'Onaylı' ? 'Randevu eklendi.' : 'İşlem yönetici onayına gönderildi.');
    });
  }

  /* ---------- yönetici paneli ---------- */
  var TABS = [['randevu', '📅 Randevular'], ['ajanda', '🗓️ Ajanda'], ['personel', '👥 Personel & Hakediş'], ['kasa', '💰 Kasa'], ['ayar', '⚙️ Ayarlar']];

  function renderAdmin() {
    root.innerHTML = '<header class="top"><div><div class="t">GÜL AKSU GÜZELLİK SALONU</div><div class="muted small">Yönetim ve Finansal Kontrol Paneli</div></div>' +
      '<div class="actions"><button class="btn ghost sm" data-a="rapor">📊 Gün sonu raporu</button><span class="tag">👑 Yönetici</span><button class="btn ghost sm" data-a="cikis">Çıkış</button></div></header>' +
      '<main><div class="toolbar"><div class="chips" id="aralik">' + [['bugun', 'Bugün'], ['hafta', 'Bu hafta'], ['ay', 'Bu ay'], ['hepsi', 'Tümü']].map(function (x) {
        return '<button class="chip" data-ar="' + x[0] + '">' + x[1] + '</button>'; }).join('') + '</div></div>' +
      '<section class="kpis" id="kpis"></section><nav class="tabs" id="tabs"></nav><div id="view"></div></main>';
    renderAdminView();
  }

  function renderAdminView() {
    var v = veri(), o = ozet(v);
    $$('#aralik .chip').forEach(function (c) { c.classList.toggle('on', c.dataset.ar === ui.aralik); });
    $('#kpis').innerHTML =
      kpi('Ciro', tl(o.ciro), o.adet + ' işlem · bahşiş ' + tl(o.bahsis), 'main') +
      kpi('Tahsilat dağılımı', tl(o.nakit) + ' nakit', 'Kart ' + tl(o.kart) + ' · Havale ' + tl(o.havale)) +
      kpi('Gider & avans', tl(o.gider + o.avans), 'Gider ' + tl(o.gider) + ' · Avans ' + tl(o.avans)) +
      kpi('Net kâr', tl(o.net), 'Ciro − gider − avans', 'net' + (o.net < 0 ? ' neg' : ''));
    var bek = v.bekleyen.length;
    $('#tabs').innerHTML = TABS.map(function (t) {
      return '<button class="tab' + (ui.tab === t[0] ? ' on' : '') + '" data-tab="' + t[0] + '">' + t[1] + (t[0] === 'randevu' && bek ? '<span class="badge">' + bek + '</span>' : '') + '</button>'; }).join('');
    var view = $('#view');
    // form alanları yeniden çizimde silinmesin: sekme değişmediyse yalnızca listeleri yenile
    if (view.dataset.gorunum !== ui.tab) { view.dataset.gorunum = ui.tab; view.innerHTML = shell(ui.tab); bindShell(ui.tab); }
    fill(ui.tab, v);
  }
  function kpi(l, v, s, cls) { return '<div class="kpi ' + (cls || '') + '"><div class="l">' + l + '</div><div class="v">' + v + '</div><div class="s">' + s + '</div></div>'; }

  function shell(tab) {
    if (tab === 'randevu') return '<div id="onaybox"></div><div class="grid3"><div class="card"><h3>Yeni randevu / işlem</h3>' + islemForm('a', true) + '</div>' +
      '<div class="card"><div class="toolbar"><h3>Randevu ve işlem listesi</h3><button class="btn ghost sm" data-a="csv">⬇ CSV</button></div><div class="scroll"><table><thead><tr><th>Müşteri / hizmet</th><th>Personel</th><th>Kaynak</th><th>Saat</th><th>Tutar</th><th></th></tr></thead><tbody id="liste"></tbody></table></div></div></div>';
    if (tab === 'ajanda') return '<div class="card"><div class="toolbar"><h3>Günlük doluluk takvimi</h3><input type="date" id="ajtarih" style="width:auto"></div><div class="agenda" id="agenda" style="margin-top:12px"></div></div>' +
      '<div class="card"><h3>Ortalama işlem süreleri</h3><div class="scroll"><table><thead><tr><th>Personel</th><th>Hizmet</th><th>Adet</th><th>Ort. süre</th></tr></thead><tbody id="sure"></tbody></table></div></div>';
    if (tab === 'personel') return '<div class="grid3"><div class="card"><h3>Yeni personel</h3><form id="pform"><div class="field"><label for="pad">Ad soyad</label><input id="pad" required maxlength="60"></div><div class="field"><label for="puz">Uzmanlık</label><input id="puz" required maxlength="60"></div><div class="row2"><div class="field"><label for="pma">Maaş (₺)</label><input id="pma" type="number" min="0" required></div><div class="field"><label for="ppr">Prim (%)</label><input id="ppr" type="number" min="0" max="100" required></div></div><div class="field"><label for="psi">Şifre (en az 6)</label><input id="psi" type="password" minlength="6" required autocomplete="new-password"></div><button class="btn" style="width:100%">Personeli ekle</button></form></div>' +
      '<div class="card"><div class="toolbar"><h3>Hakediş özeti (seçili dönem)</h3><button class="btn ghost sm" data-a="hakgider">Hakedişleri gider yaz</button></div><div class="scroll"><table><thead><tr><th>Personel</th><th>Maaş</th><th>Prim</th><th>Net hakediş</th><th>Hesap</th></tr></thead><tbody id="plist"></tbody></table></div></div></div>';
    if (tab === 'kasa') return '<div class="grid3"><div class="card"><h3>Avans ver</h3><form id="avform"><div class="field"><label for="avp">Personel</label><select id="avp">' + S.data.personeller.map(function (p) { return '<option value="' + esc(p.id) + '">' + esc(p.ad) + '</option>'; }).join('') + '</select></div><div class="field"><label for="avt">Tutar (₺)</label><input id="avt" type="number" min="1" required></div><div class="field"><label for="avn">Not</label><input id="avn" maxlength="80"></div><button class="btn" style="width:100%">Avansı kaydet</button></form>' +
      '<h3 style="margin-top:20px">Gider ekle</h3><form id="gform"><div class="field"><label for="ga">Açıklama</label><input id="ga" required maxlength="100"></div><div class="row2"><div class="field"><label for="gk">Kategori</label><select id="gk"><option>Fatura</option><option>Kira</option><option>Malzeme</option><option>Diğer</option></select></div><div class="field"><label for="gt">Tutar (₺)</label><input id="gt" type="number" min="1" required></div></div><button class="btn" style="width:100%">Gideri kaydet</button></form></div>' +
      '<div style="display:grid;gap:18px"><div class="card"><h3>Avanslar</h3><table><thead><tr><th>Tarih</th><th>Personel</th><th>Not</th><th>Tutar</th><th></th></tr></thead><tbody id="avlist"></tbody></table></div><div class="card"><div class="toolbar"><h3>Giderler</h3><button class="btn ghost sm" data-a="csvgider">⬇ CSV</button></div><table><thead><tr><th>Tarih</th><th>Açıklama</th><th>Kategori</th><th>Tutar</th><th></th></tr></thead><tbody id="glist"></tbody></table></div></div></div>';
    return '<div class="grid3"><div class="card"><h3>Güvenlik</h3><form id="sform"><div class="field"><label for="sy">Yeni yönetici şifresi</label><input id="sy" type="password" minlength="6" required autocomplete="new-password"></div><button class="btn">Şifreyi değiştir</button></form></div>' +
      '<div style="display:grid;gap:18px"><div class="card"><h3>Yedekleme</h3><p class="muted small">Veriler bu tarayıcıda saklanır. Düzenli yedek alın.</p><button class="btn" data-a="yedek">⬇ Yedek indir (JSON)</button> <label class="btn ghost" style="display:inline-block;width:auto;margin:0">Yedek yükle<input type="file" id="yedekdosya" accept=".json" class="sr"></label> <button class="btn ghost" data-a="demo">Demo veri yükle</button></div>' +
      '<div class="card"><h3>Hizmet fiyat listesi</h3><div class="scroll"><table><thead><tr><th>Hizmet</th><th>Süre (dk)</th><th>Fiyat (₺)</th></tr></thead><tbody id="hizlist"></tbody></table></div></div>' +
      '<div class="card"><h3>İşlem kaydı (son 50)</h3><div class="scroll"><table><tbody id="loglist"></tbody></table></div></div></div></div>';
  }

  function bindShell(tab) {
    if (tab === 'randevu') bindIslemForm('a', function () { return $('#ap').value; }, 'Onaylı', function () { return 'Yönetici'; });
    if (tab === 'ajanda') { $('#ajtarih').value = S.ymd(new Date()); $('#ajtarih').addEventListener('change', function () { fill('ajanda', veri()); }); }
    if (tab === 'personel') $('#pform').addEventListener('submit', async function (e) {
      e.preventDefault();
      S.data.personeller.push({ id: S.uid(), ad: $('#pad').value.trim(), uzmanlik: $('#puz').value.trim(), maas: Number($('#pma').value), prim: Number($('#ppr').value), cred: await S.makeCred($('#psi').value), aktif: true });
      S.save('Yönetici', 'Personel eklendi: ' + $('#pad').value); e.target.reset(); $('#view').dataset.gorunum = ''; renderAdminView(); toast('Personel eklendi.');
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
      $('#sform').addEventListener('submit', async function (e) { e.preventDefault();
        S.data.admin = await S.makeCred($('#sy').value); S.save('Yönetici', 'Şifre değiştirildi'); e.target.reset(); toast('Yönetici şifresi güncellendi.'); });
      $('#yedekdosya').addEventListener('change', function (e) {
        var fr = new FileReader(); fr.onload = function () { try { S.replaceAll(JSON.parse(fr.result)); $('#view').dataset.gorunum = ''; toast('Yedek yüklendi.'); } catch (x) { toast('Geçersiz yedek dosyası.', true); } };
        if (e.target.files[0]) fr.readAsText(e.target.files[0]); });
    }
  }

  function fill(tab, v) {
    var d = S.data;
    if (tab === 'randevu') {
      var ob = $('#onaybox');
      ob.innerHTML = !v.bekleyen.length ? '' : '<div class="card pending"><h3>⏳ Onay bekleyen işlemler <span class="badge">' + v.bekleyen.length + '</span></h3><div class="scroll"><table><tbody>' +
        v.bekleyen.map(function (r) { return '<tr><td><b>' + esc(r.musteri) + '</b><br><span class="muted small">' + esc(r.hizmet) + (r.telefon ? ' · ' + esc(r.telefon) : '') + '</span></td><td>' + esc(S.personelAd(r.personelId)) + '</td><td>' + tag(r) + '</td><td>' + esc(fmtDT(r.baslangic)) + ' - ' + esc(saat(r.bitis)) + '</td><td><b>' + tl(r.tutar) + '</b><br><span class="muted small">' + esc(r.odeme) + '</span></td><td style="text-align:right;white-space:nowrap"><button class="btn ok sm" data-onay="' + esc(r.id) + '">Onayla</button> <button class="btn ghost sm" data-red="' + esc(r.id) + '">Reddet</button></td></tr>'; }).join('') + '</tbody></table></div></div>';
      var l = v.onayli.slice().sort(function (a, b) { return a.baslangic < b.baslangic ? 1 : -1; });
      $('#liste').innerHTML = l.length ? l.map(function (r) { return '<tr><td><b>' + esc(r.musteri) + '</b><br><span class="muted small">' + esc(r.hizmet) + '</span></td><td>' + esc(S.personelAd(r.personelId)) + '</td><td>' + tag(r) + '</td><td>' + esc(fmtDT(r.baslangic)) + ' - ' + esc(saat(r.bitis)) + '</td><td><b>' + tl(r.tutar) + '</b>' + (r.bahsis ? ' <span class="muted small">+' + tl(r.bahsis) + ' b.</span>' : '') + '<br><span class="muted small">' + esc(r.odeme) + '</span></td><td><button class="link-bad" data-sil-islem="' + esc(r.id) + '">Sil</button></td></tr>'; }).join('')
        : '<tr><td colspan="6" class="empty">Bu dönemde onaylı işlem yok.</td></tr>';
    }
    if (tab === 'ajanda') {
      var gun = $('#ajtarih').value;
      $('#agenda').innerHTML = d.personeller.filter(function (p) { return p.aktif; }).map(function (p) {
        var rs = d.islemler.filter(function (r) { return r.personelId === p.id && r.baslangic.slice(0, 10) === gun; }).sort(function (a, b) { return a.baslangic < b.baslangic ? -1 : 1; });
        var dk = sum(rs, dakika);
        return '<div class="card" style="background:var(--soft)"><div class="toolbar"><b>' + esc(p.ad) + '</b><span class="tag">' + rs.length + ' randevu · ' + Math.round(dk / 60 * 10) / 10 + ' sa</span></div>' +
          (rs.length ? rs.map(function (r) { return '<div class="slot"><b>' + esc(saat(r.baslangic)) + ' - ' + esc(saat(r.bitis)) + '</b> ' + (r.durum === 'Bekliyor' ? '<span class="tag wait">Bekliyor</span>' : '') + '<br>' + esc(r.musteri) + '<br><span class="muted small">' + esc(r.hizmet) + '</span></div>'; }).join('') : '<p class="muted small">Bu güne ait randevu yok.</p>') + '</div>'; }).join('');
      var st = {};
      d.islemler.filter(function (r) { return r.durum === 'Onaylı'; }).forEach(function (r) { var k = r.personelId + '|' + r.hizmet; st[k] = st[k] || { p: r.personelId, h: r.hizmet, n: 0, dk: 0 }; st[k].n++; st[k].dk += dakika(r) || 45; });
      var ks = Object.keys(st);
      $('#sure').innerHTML = ks.length ? ks.map(function (k) { var s = st[k]; return '<tr><td>' + esc(S.personelAd(s.p)) + '</td><td>' + esc(s.h) + '</td><td>' + s.n + '</td><td><b>~' + Math.round(s.dk / s.n) + ' dk</b></td></tr>'; }).join('') : '<tr><td colspan="4" class="empty">Veri yok.</td></tr>';
    }
    if (tab === 'personel') $('#plist').innerHTML = d.personeller.map(function (p) {
      var h = hakedis(p, v);
      return '<tr><td><b>' + esc(p.ad) + '</b><br><span class="muted small">' + esc(p.uzmanlik) + (p.cred ? '' : ' · <span style="color:var(--bad)">şifre yok</span>') + '</span></td><td>' + tl(p.maas) + '</td><td>%' + p.prim + '<br><span class="muted small">+' + tl(h.prim) + '</span></td><td><b style="color:var(--ok)">' + tl(h.net) + '</b><br><span class="muted small">−' + tl(h.avans) + ' avans</span></td><td style="white-space:nowrap"><button class="btn ghost sm" data-sifre="' + esc(p.id) + '">Şifre</button> <button class="link-bad" data-sil-personel="' + esc(p.id) + '">Sil</button></td></tr>'; }).join('');
    if (tab === 'kasa') {
      $('#avlist').innerHTML = v.avans.length ? v.avans.map(function (a) { return '<tr><td>' + esc(a.tarih) + '</td><td>' + esc(S.personelAd(a.personelId)) + '</td><td>' + esc(a.not || '-') + '</td><td><b>' + tl(a.tutar) + '</b></td><td><button class="link-bad" data-sil-avans="' + esc(a.id) + '">Sil</button></td></tr>'; }).join('') : '<tr><td colspan="5" class="empty">Kayıt yok.</td></tr>';
      $('#glist').innerHTML = v.gider.length ? v.gider.map(function (g) { return '<tr><td>' + esc(g.tarih) + '</td><td>' + esc(g.aciklama) + '</td><td>' + esc(g.kategori) + '</td><td><b>' + tl(g.tutar) + '</b></td><td><button class="link-bad" data-sil-gider="' + esc(g.id) + '">Sil</button></td></tr>'; }).join('') : '<tr><td colspan="5" class="empty">Kayıt yok.</td></tr>';
    }
    if (tab === 'ayar') {
      $('#hizlist').innerHTML = d.hizmetler.map(function (h) { return '<tr><td>' + esc(hizmetAd(h)) + '</td><td><input type="number" min="5" value="' + h.sure + '" data-hs="' + esc(h.id) + '" style="width:80px"></td><td><input type="number" min="0" value="' + h.fiyat + '" data-hf="' + esc(h.id) + '" style="width:100px"></td></tr>'; }).join('');
      $('#loglist').innerHTML = d.log.slice(0, 50).map(function (x) { return '<tr><td class="muted small">' + esc(fmtDT(x.t.slice(0, 16))) + '</td><td>' + esc(x.kim) + '</td><td>' + esc(x.islem) + '</td></tr>'; }).join('') || '<tr><td class="empty">Kayıt yok.</td></tr>';
    }
  }
  function tag(r) { return r.kaynak === 'Web' ? '<span class="tag web">🌐 Web</span>' : '<span class="tag">' + esc(r.giren) + '</span>'; }

  /* ---------- dışa aktarma ---------- */
  function indir(ad, icerik, tip) {
    var a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([icerik], { type: tip })); a.download = ad; a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); }, 500);
  }
  function csv(rows) {
    return '﻿' + rows.map(function (r) { return r.map(function (c) { c = String(c == null ? '' : c); if (/^[=+\-@]/.test(c)) c = "'" + c; return '"' + c.replace(/"/g, '""') + '"'; }).join(';'); }).join('\r\n');
  }
  function gunSonu() {
    var o = ozet(veri()), a = aralik();
    return '✨ GÜL AKSU GÜZELLİK SALONU - ÖZET RAPOR ✨\n' + (a ? a[0] + ' / ' + a[1] : 'Tüm zamanlar') + '\n\n' +
      '💰 Ciro: ' + tl(o.ciro) + '\n💵 Nakit: ' + tl(o.nakit) + '\n💳 Kart: ' + tl(o.kart) + '\n📱 Havale: ' + tl(o.havale) + '\n🎁 Bahşiş: ' + tl(o.bahsis) + '\n📉 Gider: ' + tl(o.gider) + '\n💸 Avans: ' + tl(o.avans) + '\n💎 Net kâr: ' + tl(o.net);
  }

  /* ---------- tıklama olayları (yönetici) ---------- */
  document.addEventListener('click', async function (e) {
    var t = e.target, el;
    if ((el = t.closest('[data-ar]'))) { ui.aralik = el.dataset.ar; return renderAdminView(); }
    if ((el = t.closest('[data-tab]'))) { ui.tab = el.dataset.tab; return renderAdminView(); }
    var d = S.data;
    if ((el = t.closest('[data-onay]'))) { var r = d.islemler.filter(function (x) { return x.id === el.dataset.onay; })[0];
      if (r) { var c = S.cakisma(r.personelId, r.baslangic, r.bitis, r.id); if (c && !(await confirmBox('Bu saatte ' + S.personelAd(r.personelId) + ' için "' + c.musteri + '" kaydı var. Yine de onaylansın mı?', 'Onayla'))) return;
        r.durum = 'Onaylı'; S.save('Yönetici', 'Onaylandı: ' + r.musteri); toast('İşlem onaylandı.'); } return; }
    if ((el = t.closest('[data-red]'))) { var rr = d.islemler.filter(function (x) { return x.id === el.dataset.red; })[0];
      if (rr && await confirmBox('"' + rr.musteri + '" talebi reddedilip silinsin mi?', 'Reddet')) { d.islemler = d.islemler.filter(function (x) { return x !== rr; }); S.save('Yönetici', 'Reddedildi: ' + rr.musteri); toast('Talep reddedildi.', true); } return; }
    var sil = [['sil-islem', 'islemler', 'Kayıt silinsin mi?'], ['sil-avans', 'avanslar', 'Avans kaydı silinsin mi?'], ['sil-gider', 'giderler', 'Gider kaydı silinsin mi?']];
    for (var i = 0; i < sil.length; i++) {
      if ((el = t.closest('[data-' + sil[i][0] + ']'))) { var id = el.getAttribute('data-' + sil[i][0]);
        if (await confirmBox(sil[i][2], 'Sil')) { d[sil[i][1]] = d[sil[i][1]].filter(function (x) { return x.id !== id; }); S.save('Yönetici', 'Silindi: ' + sil[i][1]); toast('Silindi.', true); } return; } }
    if ((el = t.closest('[data-sil-personel]'))) { var pid = el.dataset.silPersonel, p = S.personel(pid);
      var kullanim = d.islemler.some(function (x) { return x.personelId === pid; });
      if (kullanim) { if (await confirmBox(p.ad + ' geçmiş kayıtlara sahip; silmek yerine pasife alınsın mı?', 'Pasife al')) { p.aktif = false; S.save('Yönetici', 'Personel pasif: ' + p.ad); } }
      else if (await confirmBox(p.ad + ' silinsin mi?', 'Sil')) { d.personeller = d.personeller.filter(function (x) { return x.id !== pid; }); S.save('Yönetici', 'Personel silindi: ' + p.ad); }
      $('#view').dataset.gorunum = ''; return renderAdminView(); }
    if ((el = t.closest('[data-sifre]'))) { var pp = S.personel(el.dataset.sifre), yeni = prompt(pp.ad + ' için yeni şifre (en az 6 karakter):');
      if (yeni && yeni.length >= 6) { pp.cred = await S.makeCred(yeni); S.save('Yönetici', 'Personel şifresi: ' + pp.ad); toast('Şifre güncellendi.'); } else if (yeni) toast('Şifre en az 6 karakter olmalı.', true); return; }
    if ((el = t.closest('[data-a]'))) {
      var a = el.dataset.a;
      if (a === 'cikis') { S.session.clear(); return boot(); }
      if (a === 'rapor') return navigator.clipboard.writeText(gunSonu()).then(function () { toast('Rapor panoya kopyalandı.'); }, function () { toast('Kopyalanamadı.', true); });
      if (a === 'csv') return indir('randevular.csv', csv([['Müşteri', 'Hizmet', 'Personel', 'Kaynak', 'Başlangıç', 'Bitiş', 'Tutar', 'Bahşiş', 'Ödeme']].concat(veri().onayli.map(function (r) { return [r.musteri, r.hizmet, S.personelAd(r.personelId), r.kaynak, r.baslangic, r.bitis, r.tutar, r.bahsis, r.odeme]; }))), 'text/csv');
      if (a === 'csvgider') return indir('giderler.csv', csv([['Tarih', 'Açıklama', 'Kategori', 'Tutar']].concat(veri().gider.map(function (g) { return [g.tarih, g.aciklama, g.kategori, g.tutar]; }))), 'text/csv');
      if (a === 'yedek') return indir('gulaksu-yedek-' + S.ymd(new Date()) + '.json', JSON.stringify(S.data), 'application/json');
      if (a === 'demo') { if (await confirmBox('Mevcut randevu, avans ve giderler demo veriyle değiştirilecek. Devam?', 'Yükle')) { S.resetDemo(); toast('Demo veri yüklendi.'); } return; }
      if (a === 'hakgider') { var vv = veri(), ay = S.ymd(new Date()).slice(0, 7), var_ = d.giderler.some(function (g) { return g.kategori === 'Personel Hakediş' && g.tarih.slice(0, 7) === ay; });
        if (var_ && !(await confirmBox('Bu ay hakedişler zaten gider yazılmış. Tekrar eklensin mi?', 'Ekle'))) return;
        var top = 0, det = d.personeller.filter(function (p) { return p.aktif; }).map(function (p) { var n = hakedis(p, vv).net; top += n; return p.ad + ': ' + tl(n); });
        d.giderler.push({ id: S.uid(), aciklama: 'Personel hakedişleri → ' + det.join(' | '), kategori: 'Personel Hakediş', tutar: top, tarih: S.ymd(new Date()) });
        S.save('Yönetici', 'Hakedişler gider yazıldı'); toast('Hakedişler gider olarak eklendi.'); }
    }
  });
  document.addEventListener('change', function (e) { // hizmet fiyat/süre düzenleme
    var el = e.target, h;
    if (el.dataset && (el.dataset.hs || el.dataset.hf)) {
      h = S.data.hizmetler.filter(function (x) { return x.id === (el.dataset.hs || el.dataset.hf); })[0];
      if (h) { if (el.dataset.hs) h.sure = Math.max(5, Number(el.value) || h.sure); else h.fiyat = Math.max(0, Number(el.value) || 0); S.save('Yönetici', 'Hizmet güncellendi: ' + h.ad); toast('Güncellendi.'); }
    }
  });

  /* ---------- personel paneli ---------- */
  function renderPersonel(ses_) {
    root.innerHTML = '<header class="top"><div><div class="t">' + esc(ses_.ad.toUpperCase()) + ' · PERSONEL PANELİ</div><div class="muted small">Kendi işlemleriniz ve hakedişiniz</div></div><div class="actions"><button class="btn ghost sm" data-a="cikis">Çıkış</button></div></header>' +
      '<main><section class="kpis" id="pk"></section><div class="grid3"><div class="card"><h3>Yeni işlem (yönetici onayına gider)</h3>' + islemForm('s', false) + '</div><div class="card"><h3>İşlemlerim</h3><div class="scroll"><table><thead><tr><th>Müşteri / hizmet</th><th>Saat</th><th>Tutar</th><th>Ödeme</th><th>Durum</th></tr></thead><tbody id="pl"></tbody></table></div></div></div></main>';
    bindIslemForm('s', function () { return ses_.pid; }, 'Bekliyor', function () { return ses_.ad; });
    renderPersonelData();
  }
  function renderPersonelData() {
    var ses_ = S.session.get(); if (!ses_ || ses_.rol !== 'personel') return;
    var p = S.personel(ses_.pid); if (!p || !p.aktif) { S.session.clear(); return boot(); }
    var v = veri(), h = hakedis(p, v), benim = S.data.islemler.filter(function (r) { return r.personelId === p.id; });
    $('#pk').innerHTML = kpi('Cirom (bu ay)', tl(h.ciro), '', 'main') + kpi('Bahşiş', tl(sum(v.onayli.filter(function (r) { return r.personelId === p.id; }), function (r) { return r.bahsis; })), '') + kpi('Avanslarım', tl(h.avans), '') + kpi('Tahmini hakediş', tl(h.net), 'Maaş + prim − avans', 'net');
    var l = benim.slice().sort(function (a, b) { return a.baslangic < b.baslangic ? 1 : -1; });
    $('#pl').innerHTML = l.length ? l.map(function (r) { return '<tr><td><b>' + esc(r.musteri) + '</b><br><span class="muted small">' + esc(r.hizmet) + '</span></td><td>' + esc(fmtDT(r.baslangic)) + ' - ' + esc(saat(r.bitis)) + '</td><td><b>' + tl(r.tutar) + '</b></td><td>' + esc(r.odeme) + '</td><td>' + (r.durum === 'Onaylı' ? '<span class="tag done">Onaylandı</span>' : '<span class="tag wait">Onay bekliyor</span>') + '</td></tr>'; }).join('') : '<tr><td colspan="5" class="empty">Henüz işleminiz yok.</td></tr>';
  }

  /* ---------- açılış ---------- */
  function boot() {
    var s = S.session.get();
    if (!s || (s.rol === 'personel' && !S.personel(s.pid))) { S.session.clear(); return renderLogin(); }
    if (s.rol === 'admin') { if (!S.data.admin) { S.session.clear(); return renderLogin(); } ui.aralik = ui.aralik || 'ay'; renderAdmin(); }
    else { ui.aralik = 'ay'; renderPersonel(s); }
  }
  S.subscribe(function () {
    var s = S.session.get(); if (!s) return;
    if (s.rol === 'admin' && $('#view')) renderAdminView(); else if (s.rol === 'personel') renderPersonelData();
  });
  boot();
})();
