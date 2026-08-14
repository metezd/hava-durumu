<h1 align="center">MGM Unofficial Weather API</h1>

<p align="center">
  <a href="https://github.com/metezd/mgm-api/actions/workflows/test.yml">
    <img src="https://github.com/metezd/mgm-api/actions/workflows/main.yml/badge.svg" alt="CI Status" />
  </a>
  <img src="https://img.shields.io/badge/python-3.13-blue.svg" alt="Python 3.13" />
  <img src="https://img.shields.io/badge/redis-enabled-red.svg" alt="Redis" />
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License" />
</p>

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

## Docker ile çalıştırma

```bash
cp .env.example .env   # değerler düzenlenebilir, boşsa varsayılanlar geçerlidir
docker compose up --build
```

Bu komut `redis:7-alpine` konteynerini ve uygulamayı ayağa kaldırır `MGM_REDIS_URL` otomatik olarak bundled Redis'e (`redis://redis:6379/0`) işaret eder `.env` yoksa da çalışır ve tüm ayarlar `docker-compose.yml`'dekilerle devam eder.

Sadece uygulamayı çalıştırmak için:

```bash
docker build -t hava-durumu .
docker run -p 5000:5000 hava-durumu
```

Container'ın `/health` uç noktasını kullanan bir `HEALTHCHECK`'i var `docker ps` çıktısında `healthy`/`unhealthy` olarak görünür.

| Uç noktalar | Açıklama |
|---|---|
| `GET /health` | Servis durumu (`?deep=1` ile MGM + Redis bağlantısı da kontrol edilir) |
| `GET /iller` | Türkiye'nin 81 ilini plaka kodu sırasıyla listeler (sabit veri, MGM'ye istek atmaz) |
| `GET /istasyonlar/<il>` | O ildeki istasyonları (ilçeleri) listeler |
| `GET /guncel/<il>?ilce=<ilce>` | Anlık hava durumu |
| `GET /tahmin/<il>?ilce=<ilce>` | 5 günlük tahmin |
| `GET /saatlik/<il>?ilce=<ilce>` | Saatlik tahmin |
| `GET /hava-durumu/<il>?ilce=<ilce>` | Güncel durum + tahmin |

`ilce` parametresi zorunlu değil, verilmezse ilin ilk istasyonu kullanılır.

## Redis cache (opsiyonel)

Uygulama yanında opsiyonel bir Redis veya [Redis Stack](https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/) kullanabilirsiniz. Redis sunucusunu `docker run -d --rm -p 6379:6379 redis:7-alpine` ile ayağa kaldırabilirsiniz.

`MGM_REDIS_URL` ortam değişkeni set edilirse Redis birincil cache olur (in-memory cache'in önüne geçer):

```bash
MGM_REDIS_URL="redis://localhost:6379/0" python app.py
```

Redis'e bağlanılamazsa uygulama hata verip durur. Eğer uygulamayı Redis olmadan kullanmak istiyorsanız değişkeni set etmeyin, bu durumda in-memory cache devreye girer. (`docker compose up` kullanıyorsanız bu adımlarla uğraşmanıza gerek yok, Redis servisi ve `MGM_REDIS_URL` otomatik ayarlanır, bkz. Docker ile çalıştırma)

MGM isteklerinde timeout/retry/cache ayarları ortam değişkeniyle yönetilir:

- `MGM_TIMEOUT` (varsayılan: `10`)
- `MGM_RETRY_TOTAL` (varsayılan: `3`)
- `MGM_RETRY_BACKOFF` (varsayılan: `0.3`)
- `MGM_CACHE_TTL` (varsayılan: `60`)
- `MGM_STALE_WHILE_REVALIDATE` (varsayılan: `300`)
- `MGM_CACHE_MAX_ENTRIES` (varsayılan: `512`)
- `MGM_REDIS_URL` (varsayılan: yok — Redis kapalı)
- `MGM_REDIS_PREFIX` (varsayılan: `mgm-cache:`)

Redis cache'te **socket timeout (2 sn)** ve **connect timeout (2 sn)** zorunlu olarak uygulanır ve bu sayede Redis'in yavaşlaması veya çökmesi istek akışını uzun süre bloklamaz. Gün doğumu/batımı verisi de aynı cache altyapısından geçer.

### Stale-while-revalidate (SWR) ve cache stampede koruması

Cache kayıtları iki aşamalı yaşlanır:

- **Taze dönem (`MGM_CACHE_TTL`):** kayıt doğrudan döner.
- **Stale dönem (`MGM_STALE_WHILE_REVALIDATE`):** TTL dolduktan sonra kullanıcıya eski veri **anında** döner, yeni veri arka planda getirilip cache güncellenir. İstek MGM'nin yavaşlığından etkilenmez.
- Stale dönemi de dolarsa istek bloklayıcı şekilde MGM'den taze veri çeker.

Aynı anahtar için eşzamanlı isteklerde yalnızca biri yenilemeyi yapar (işlem içi kilit + Redis `SET NX EX` kilidi). SWR'yi kapatmak için `MGM_STALE_WHILE_REVALIDATE=0` verin.

### Circuit breaker

MGM art arda hata verdiğinde (30 sn içinde 5 hata) devre açılır: bunu izleyen süre boyunca (60 sn) MGM'ye hiç istek atılmaz, doğrudan hata dönülür. Süre dolunca devre yarı açık olur bu tek bir deneme isteği yapılır başarılıysa devre kapanır, başarısızsa tekrar açılır.

Önemli: circuit breaker yalnızca asıl ağ isteğini keser, cache/SWR katmanının önüne geçmez. Yani MGM kesintisi sırasında elinizde stale (TTL'i geçmiş ama SWR penceresi içindeki) veri varsa kullanıcı bunu almaya devam eder, breaker sadece arka planda MGM'yi gereksiz yere zorlayan/bekleten istekleri atlar. Cache'te hiç veri yoksa devre açıkken istek anında hatayla döner retry, backoff süresi boyunca beklemez.

- `MGM_CIRCUIT_BREAKER_FAILURE_THRESHOLD` (varsayılan: `5`)
- `MGM_CIRCUIT_BREAKER_WINDOW_SECONDS` (varsayılan: `30`)
- `MGM_CIRCUIT_BREAKER_OPEN_SECONDS` (varsayılan: `60`)

Durum `GET /health` (hem shallow hem `?deep=1`) yanıtında `circuit_breaker` alanıyla görülebilir: `kapali` | `acik` | `yari-acik`.

CORS ve güvenlik ayarları:

- `APP_CORS_ALLOW_ORIGIN` (varsayılan: `*`)
- Yanıtlarda otomatik güvenlik header'ları döner (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `CSP`).

Rate limiting ayarları:

- `APP_RATE_LIMIT_WINDOW_SECONDS` (varsayılan: `60`)
- `APP_RATE_LIMIT_MAX_REQUESTS` (varsayılan: `60`)
- Aynı IP'den fazla istek gelirse `429 Too Many Requests` döner

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
