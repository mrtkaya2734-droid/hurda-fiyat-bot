// www/index.html içindeki adresi APP_URL ile doldurur ve Capacitor'un
// allowNavigation ayarını (uygulama içinde açılacak alan adı) günceller.
// Kullanım: APP_URL=https://site.com node scripts/www-hazirla.mjs
import { readFileSync, writeFileSync } from "node:fs";

const url = (process.env.APP_URL || "").trim().replace(/\/+$/, "");
if (!/^https:\/\/[^/\s]+/i.test(url)) {
  console.error("HATA: APP_URL https:// ile başlayan tam adres olmalı. Örn: APP_URL=https://hurda.example.com");
  process.exit(1);
}

const sablon = readFileSync(new URL("../www/index.sablon.html", import.meta.url), "utf8");
writeFileSync(new URL("../www/index.html", import.meta.url), sablon.replaceAll("__APP_URL__", url));

const yol = new URL("../capacitor.config.json", import.meta.url);
const cfg = JSON.parse(readFileSync(yol, "utf8"));
cfg.server = { ...(cfg.server || {}), allowNavigation: [new URL(url).host], androidScheme: "https" };
writeFileSync(yol, JSON.stringify(cfg, null, 2) + "\n");
console.log("Uygulama adresi:", url);
