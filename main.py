import requests
from bs4 import BeautifulSoup
from datetime import datetime

print("Hurda fiyat takip botu başlatıldı...")
# Bimel Metal üzerindeki güncel verileri çekme hazırlığı
URL = "https://www.bimelmetal.com/hurda_demir_fiyatlari.html"

def check_prices():
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(URL, headers=headers)
    if response.status_code == 200:
        print("Web sitesine başarıyla erişildi, veriler kontrol ediliyor.")
    else:
        print("Siteye erişilemedi.")

if __name__ == "__main__":
    check_prices()
