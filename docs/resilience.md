# Dayanıklılık (resilience) mimarisi ve yapılandırma

MGM'nin sitesinin resmi bir garantisi yoktur. Servis zaman zaman yavaşlayabilir veya kesilebilir
Bu proje, kullanıcıya bunu hissettirmemek için üç katman kullanıyor: **cache**,
**stale-while-revalidate** ve **circuit breaker**. Bu belge üçünün nasıl
çalıştığını ve ilgili tüm ortam değişkenlerini anlatır. Hızlı başlangıç ve
endpoint listesi için [README](../README.md)'ye bakınız.

## Redis cache (opsiyonel)

Uygulama yanında opsiyonel bir Redis veya [Redis Stack](https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/)
kullanabilirsiniz. Redis sunucusunu `docker run -d --rm -p 6379:6379 redis:7-alpine`
ile ayağa kaldırabilirsiniz.

`MGM_REDIS_URL` ortam değişkeni set edilirse Redis birincil cache olur
(in-memory cache'in önüne geçer):

```bash
MGM_REDIS_URL="redis://localhost:6379/0" python app.py
```

Redis'e bağlanılamazsa uygulama hata verip durur. Redis olmadan kullanmak
istiyorsanız değişkeni ayarlamayın ve bunun sonucunda in-memory cache ile çalışır.
(`docker compose up` kullanıyorsanız bu adımlarla hiç uğraşmanıza gerek yok.
Redis servisi ve `MGM_REDIS_URL` otomatik ayarlanır.)

Redis cache'te **socket timeout (2 sn)** ve **connect timeout (2 sn)** zorunlu
olarak uygulanır bu sayede Redis'in yavaşlaması veya çökmesi istek akışını
uzun süre engellemez. Gün doğumu/batımı verisi de aynı cache altyapısından
geçer.

## Stale-while-revalidate (SWR) ve cache stampede koruması

Cache kayıtları iki aşamalı yaşlanır:

- **Taze dönem (`MGM_CACHE_TTL`):** kayıt doğrudan döner.
- **Stale dönem (`MGM_STALE_WHILE_REVALIDATE`):** TTL dolduktan sonra
  kullanıcıya eski veri **anında** döner, yeni veri arka planda getirilip
  cache güncellenir. İstek MGM'nin yavaşlığından etkilenmez.
- Stale dönemi de dolarsa istek bloklayıcı şekilde MGM'den taze veri çeker.

Aynı anahtar için eşzamanlı isteklerde yalnızca biri yenilemeyi yapar (işlem
içi kilit + Redis `SET NX EX` kilidi, cache stampede koruması). SWR'yi
kapatmak için `MGM_STALE_WHILE_REVALIDATE=0` verin.

## Circuit breaker

MGM art arda hata verdiğinde (varsayılan: 30 sn içinde 5 hata) devre
**açılır**: bunu izleyen süre boyunca (varsayılan 60 sn) MGM'ye hiç istek
atılmaz, doğrudan hata dönülür. Süre dolunca devre **yarı açık** olur; tek bir
deneme isteği yapılır — başarılıysa devre kapanır, başarısızsa tekrar açılır.

Önemli: circuit breaker yalnızca **asıl ağ isteğini** keser, cache/SWR
katmanının önüne geçmez. Yani MGM kesintisi sırasında elinizde stale (TTL'i
geçmiş ama SWR penceresi içindeki) veri varsa kullanıcı bunu almaya devam
eder; breaker sadece arka planda MGM'yi gereksiz yere zorlayan/bekleten
istekleri atlar. Cache'te hiç veri yoksa devre açıkken istek anında hatayla
döner — retry/backoff süresi boyunca beklemez.

Durum `GET /health` (hem shallow hem `?deep=1`) yanıtında `circuit_breaker`
alanıyla görülebilir: `kapali` | `acik` | `yari-acik`.

## Open-Meteo fallback (yalnızca anlık durum)

Circuit breaker + SWR, MGM'nin kısa süreli hatalarını büyük
ölçüde yutar ama cache'te hiç veri olmayan bir anahtarda
tam MGM kesintisi sırasında yine de elde bir şey kalmaz. 
Bu durumda `GET /guncel` ve `GET /hava-durumu` uçları key
gerektirmeyen ücretsiz bir servis olan [Open-Meteo](https://open-meteo.com)'ya
düşer.

Kapsam bilinçli olarak dar: sadece anlık durum için fallback var; 5
günlük ve saatlik tahmin için yok. `/hava-durumu` MGM tamamen
çökükken bile 200 dönmeye devam eder `tahmin` alanı bu durumda boş liste
olur, `guncel` alanı Open-Meteo'dan gelir.

Yanıtta hangi kaynaktan geldiği her zaman açık:

```json
{ "kaynak": "mgm" }     // normal
{ "kaynak": "open-meteo" }  // MGM'ye ulaşılamadı, yedek devrede
```

`kaynak: "open-meteo"` iken `durumKodu`, MGM'nin değil Open-Meteo'nun WMO
kod uzayındandır — ikisi doğrudan karşılaştırılamaz, `durum` alanındaki
Türkçe açıklamaya bakın.

Sınır: il/ilçe → istasyon çözümlemesi de MGM'den geliyor
(`merkezler` uç noktası). MGM'nin istasyon listesi ile anlık durum ayrı
cache girdileri kullandığından genelde biri çökükken diğeri hâlâ cache'te
taze olur ama ikisi de aynı anda cache'siz düşerse (soğuk anahtar + tam
MGM kesintisi) enlem/boylam da elde olmayacağından bu fallback devreye
giremez ve orijinal MGM hatası döner.

## Tüm ortam değişkenleri

**MGM istemcisi (timeout / retry):**

| Değişken | Varsayılan |
|---|---|
| `MGM_TIMEOUT` | `10` |
| `MGM_RETRY_TOTAL` | `3` |
| `MGM_RETRY_BACKOFF` | `0.3` |

**Cache / SWR:**

| Değişken | Varsayılan |
|---|---|
| `MGM_CACHE_TTL` | `60` |
| `MGM_STALE_WHILE_REVALIDATE` | `300` |
| `MGM_CACHE_MAX_ENTRIES` | `512` |
| `MGM_REDIS_URL` | yok (Redis kapalı) |
| `MGM_REDIS_PREFIX` | `mgm-cache` |

**Circuit breaker:**

| Değişken | Varsayılan |
|---|---|
| `MGM_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` |
| `MGM_CIRCUIT_BREAKER_WINDOW_SECONDS` | `30` |
| `MGM_CIRCUIT_BREAKER_OPEN_SECONDS` | `60` |

**CORS ve güvenlik:**

| Değişken | Varsayılan |
|---|---|
| `APP_CORS_ALLOW_ORIGIN` | `*` |

Yanıtlarda otomatik güvenlik header'ları döner (`X-Content-Type-Options`,
`X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy`)

**Rate limiting:**

| Değişken | Varsayılan |
|---|---|
| `APP_RATE_LIMIT_WINDOW_SECONDS` | `60` |
| `APP_RATE_LIMIT_MAX_REQUESTS` | `60` |

Aynı IP'den pencere içinde limitten fazla istek gelirse `429 Too Many
Requests` döner (`/health`, `/docs`, `/openapi.yaml` bu limitten muaf)

**Sunucu:**

| Değişken | Varsayılan |
|---|---|
| `APP_HOST` | `0.0.0.0` (Docker) / `127.0.0.1` (yerel) |
| `APP_PORT` | `5000` |
| `APP_SERVER` | `waitress` |
| `FLASK_DEBUG` | yalnızca `APP_SERVER=flask` iken etkili |

Tüm değişkenler için varsayılanlarıyla birlikte örnek bir dosya: [`.env.example`](../.env.example).
