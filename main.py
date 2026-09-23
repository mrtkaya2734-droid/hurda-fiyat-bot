import os
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hurda Fiyat Botu aktif ve çalışıyor!"}

# Buraya projenizin diğer endpoint'leri ve bot kodları gelecek

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)