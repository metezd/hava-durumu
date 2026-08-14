<h1 align="center">
  <span style="font-weight: 600;">Hava durumu</span>
</h1>

<p align="center">
  Karakter sorunu yaşanırsa il/ilçe adını Türkçe karakter kullanmadan yazın (<code>Istanbul</code>, <code>Bakirkoy</code>).
</p>
<p align="center">
  Resmi bir API değildir. Sadece veriyi <a href="https://www.mgm.gov.tr">mgm.gov.tr</a> sitesinden çeker.
</p>

## Kurulum

```bash
pip install -r requirements.txt
python app.py
```

Sunucu varsayılan olarak `waitress` ile `http://127.0.0.1:5000` üzerinde çalışır.

Geliştirici sunucusuyla (Flask debug) çalıştırmak için:

```powershell
$env:APP_SERVER="flask"; $env:FLASK_DEBUG="1"; python app.py
```

Not: Flask'ın kendi sunucusu geliştirme içindir; production kullanımında `waitress` (varsayılan) tercih edilir.

| Uç noktalar | Açıklama |
|---|---|
| `GET /health` | Servis durumu (`?deep=1` ile MGM bağlantısı da kontrol edilir) |
| `GET /istasyonlar/<il>` | O ildeki istasyonları (ilçeleri) listeler |
| `GET /guncel/<il>?ilce=<ilce>` | Anlık hava durumu |
| `GET /tahmin/<il>?ilce=<ilce>` | 5 günlük tahmin |
| `GET /hava-durumu/<il>?ilce=<ilce>` | Güncel durum + tahmin |

`ilce` parametresi zorunlu değil, verilmezse ilin ilk istasyonu kullanılır.

MGM isteklerinde timeout/retry ayarları ortam değişkeniyle yönetilir:

- `MGM_TIMEOUT` (varsayılan: `10`)
- `MGM_RETRY_TOTAL` (varsayılan: `3`)
- `MGM_RETRY_BACKOFF` (varsayılan: `0.3`)
- `MGM_CACHE_TTL` (varsayılan: `60`, saniye)
- `MGM_CACHE_MAX_ENTRIES` (varsayılan: `512`)

CORS ve güvenlik ayarları:

- `APP_CORS_ALLOW_ORIGIN` (varsayılan: `*`)
- Yanıtlarda otomatik güvenlik header'ları döner (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `CSP`).

## Örnek

```bash
curl "http://127.0.0.1:5000/hava-durumu/Istanbul?ilce=Bakirkoy"
```

```json
{
  "basarili": true,
  "veri": {
    "il": "İSTANBUL",
    "ilce": "Bakırköy",
    "guncel": {
      "sicaklik": 29.4,
      "durum": "Açık"
    },
    "tahmin": [
      {
        "tarih": "2026-08-14",
        "enDusuk": 23,
        "enYuksek": 31,
        "durum": "Az Bulutlu"
      }
    ]
  }
}
```

## Hata örneği

```json
{ "basarili": false, "hata": "'X' ilinde 'Y' ilçesi bulunamadı." }
```

## Test

```bash
python -m unittest discover -s tests -v
```

## Docker

```bash
docker build -t hava-durumu .
docker run --rm -p 5000:5000 hava-durumu
```