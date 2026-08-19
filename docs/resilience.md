# Dayanıklılık (resilience) mimarisi ve yapılandırma

MGM'nin sitesinin resmi bir garantisi yoktur. Servis zaman zaman yavaşlayabilir veya kesilebilir
Bu proje, kullanıcıya bunu hissettirmemek için üç katman kullanıyor: cache,
stale-while-revalidate ve circuit breaker. Bu belge üçünün nasıl
çalıştığını ve ilgili tüm ortam değişkenlerini anlatır. Hızlı başlangıç ve
endpoint listesi için [README](../README.md)'ye, Docker/test/CI detayları
için [development.md](development.md)'ye bakınız.

## Redis cache (opsiyonel)

Uygulama yanında opsiyonel bir Redis veya [Redis Stack](https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/)
kullanabilirsiniz. Redis sunucusunu `docker run -d --rm -p 6379:6379 redis:7-alpine`
ile çalıştırabilirsiniz.

`MGM_REDIS_URL` ortam değişkeni ayarlanırsa Redis birincil cache olur
(in-memory cache'in önüne geçer):

```bash
MGM_REDIS_URL="redis://localhost:6379/0" python app.py
```

Redis'e bağlanılamazsa uygulama hata verip durur. Redis olmadan kullanmak
istiyorsanız değişkeni ayarlamayın ve bunun sonucunda in-memory cache ile çalışır.
(`docker compose up` kullanıyorsanız bu adımlarla hiç uğraşmanıza gerek yok.
Redis servisi ve `MGM_REDIS_URL` otomatik ayarlanır.)

Redis cache'te socket timeout ve connect timeout (2 sn) zorunlu
olarak uygulanır bu sayede Redis'in yavaşlaması veya çökmesi istek akışını
uzun süre engellemez. Gün doğumu/batımı verisi de aynı cache altyapısından
geçer.

## Stale-while-revalidate ve cache stampede koruması

Cache kayıtları iki aşamalı yaşlanır:

- **Taze dönem (`MGM_CACHE_TTL`):** kayıt doğrudan döner.
- **Stale dönem (`MGM_STALE_WHILE_REVALIDATE`):** TTL dolduktan sonra
  kullanıcıya eski veri anında döner, yeni veri arka planda getirilip
  cache güncellenir. İstek MGM'nin yavaşlığından etkilenmez.
- Stale dönemi de dolarsa istek engelleyici şekilde MGM'den taze veri çeker.

Aynı anahtar için eşzamanlı isteklerde yalnızca biri yenilemeyi yapar. SWR'yi
kapatmak için `MGM_STALE_WHILE_REVALIDATE=0` verin.

## Circuit breaker

MGM art arda hata verdiğinde devre açılır: bunu izleyen süre boyunca MGM'ye hiç istek
atılmaz, doğrudan hata döner. Süre dolunca devre **yarı açık** olur ve tek bir
deneme isteği yapılır eğer başarılıysa devre kapanır, başarısızsa tekrar açılır

Önemli: circuit breaker sadece **asıl ağ isteğini** keser, cache
katmanının önüne geçmez. Yani MGM kesintisi sırasında elinizde stale veri 
varsa kullanıcı bunu almaya devam eder, breaker sadece arka planda MGM'yi gereksiz yere zorlayan istekleri atlar. Cache'te hiç veri yoksa devre açıkken istek hatayla
döner ve retry/backoff süresi boyunca beklemez.

Durum `GET /health` yanıtında `circuit_breaker`
alanıyla görülebilir: `kapali` | `acik` | `yari-acik`

## Saat başına göre dinamik TTL (`guncel_durum`, deneysel)

MGM'nin istasyon ölçümleri gözlemsel olarak genelde her saat başından
birkaç dakika sonra (örn. 14:08, 15:07) sisteme düşüyor, bu MGM'nin 
paylaştığı bir bilgi **DEĞİL** bu yüzden aşağıdaki varsayılanlar tahminidir

Sadece `guncel_durum()` etkilenir — il listesi, tahmin, saatlik, geocoding gibi
farklı güncelleme ritmine sahip veriler bundan etkilenmez, statik
`MGM_CACHE_TTL`'de kalır

- **Sıcak pencere**: TTL kısa tutulur (`MGM_GUNCEL_SICAK_TTL_SANIYE`,`120`)
  ki yeni düşen ölçüm hızlı yakalansın.
- **Soğuk pencere**: TTL uzatılır
  (`MGM_GUNCEL_SOGUK_TTL_SANIYE`, `1800` = 30 dk) — MGM'nin
  bu aralıkta yeni veri yayınlamadığı varsayımıyla gereksiz
  revalidation azaltılır. Not: tamamen durdurulmaz, sadece seyrekleşir
  — varsayım yanlış çıkarsa veri en fazla 30 dk bayat kalır, sonsuza
  kadar değil.
- TTL, cache kaydının yazıldığı anda değil her okunduğu anda o
  anki saate göre yeniden hesaplanır bu yüzden yeni bir saate
  geçildiğinde eski kayıt otomatik "bayat" sayılır ve revalidation tetiklenir
- `MGM_CACHE_TTL=0` ya da `MGM_GUNCEL_DINAMIK_TTL=0`
  ile devre dışı bırakılabilir; devre dışıyken statik `MGM_CACHE_TTL`
  kullanılır.
- Saat dilimi hesaplaması Python'ın stdlib `zoneinfo`'suyla
  (`Europe/Istanbul`) yapılır.`tzdata` paketi bu yüzden bağımlılıklara
  eklendi

## Open-Meteo fallback (sadece anlık durum)

Circuit breaker ve SWR, MGM'nin kısa süreli hatalarını büyük
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
kod alanında ve ikisi doğrudan karşılaştırılamaz, `durum` alanına bakın

Sınır: il/ilçe → istasyon çözümlemesi de MGM'den geliyor
(`merkezler` uç noktası). MGM'nin istasyon listesi ile anlık durum ayrı
cache girdileri kullandığından genelde biri çökükken diğeri hâlâ cache'te
taze olur ama ikisi de aynı anda cache'siz düşerse enlem/boylam da elde olmayacağından bu fallback devreye giremez ve orijinal MGM hatası döner.

## MGM'nin il/ilçe API kısıtı

MGM'nin `merkezler` uç noktası, yalnızca `il` verilip `ilce` verilmediğinde
o ilin **tüm ilçelerini değil, genelde tek bir varsayılan
istasyonu** döner. Denendi onaylandı: `il=istanbul` sadece Bakırköy döner,
ama `il=istanbul&ilce=kadikoy` ayrı ve doğru bir sonuç döner. Yani MGM'nin bu uç noktası "bir ilin tüm ilçelerini listele" değil, "il + (bilinen) ilçe 
adına göre istasyon bul" şeklinde çalışıyor

Bu yüzden `ilce_istasyonu(il, ilce)`, `ilce` verildiğinde onu doğrudan
MGM'ye parametre olarak gönderir — `il_istasyonlari()`'nin döndürdüğü dar
listede client-side arama yapmaz

Sonuç olarak `GET /istasyonlar/<il>` (ilce'siz) bir ilin tüm ilçelerinin
listesi değildir. Sadece MGM'nin o il için döndürdüğü varsayılan
istasyondur. Geçerli bir ilçe adını zaten biliyorsanız `ilce` parametresiyle
doğrudan sorgulayın; MGM'nin ilçe adlarını topluca listeleyen bilinen bir
uç noktası yok, bu yüzden "bu ilde hangi ilçeler var" sorusunu bu API
üzerinden yanıtlayamayız.

## Akıllı arama (`/ara`) katmanlı çözümleme

`/ara?q=...` gerçek ML/embedding tabanlı bir "semantik arama" değil.
Daha hafif, üç katmanlı bir çözümleyici (`akilli_yer_bul`):

1. Tam eşleşme: sorgunun tamamı 81 il listesinden biriyle stdlib
   `difflib` ile eşleşiyorsa anında çözülür, ağ isteği
   yok.
2. Parçalama: `/`, `,` ya da boşlukla ayrılmış girdilerde
   (`"kadikoy/istanbul"`) bir parça bilinen bir ile yakınsa, kalan
   parça(lar) ilçe adayı olarak doğrudan MGM'ye sorulur.
3. Geocoding: ilk iki katman çözemezse, sorgu key gerektirmeyen Open-Meteo
   API'sine gönderilir. Önce sorgunun **tamamı** tek bir yer adı olarak
   denenir ardından canlıda görüldü ki bileşik girdiler (`"maslak itü"`) böyle
   tek parça sorgulandığında sıfır sonuç dönebiliyorsa tam sorgu boş dönerse kelimeler tek tek denenir (`"maslak"` tek
   başına bulunur), ilk sonuç veren kelime kullanılır. Dönen en iyi aday
   önce MGM'de il+ilçe olarak denenir; MGM'de yoksa doğrudan o
   koordinatla Open-Meteo'dan güncel durum döner, bu durumda `tahmin`
   boş liste olur.

Geocoding, ilk birkaç sonuçta farklı illere yayılan adaylar bulursa tahmin
yürütmek yerine `durum: "belirsiz"` ile bir seçenek listesi döner.

## Meteorolojik uyarılar (`/uyarilar`) - deneysel

Bu projede daha önce tam da bu tür bir "görmediğimiz veriyi tahmin ederek
dönüştürme" hatası yaşandı `ilce_istasyonu`'nun eski client-side
filtreleme mantığı, MGM'de gerçekten var olan ilçeleri
yanlışlıkla "bulunamadı" gösteriyordu. Aynı hatayı burada tekrarlamamak
için `uyarilar()` **hiçbir alan adını tahmin etmiyor**

MGM'nin resmi MeteoUYARI sistemi (bkz. mgm.gov.tr/meteouyari) şu
kavramsal şemayı kullanıyor ama bunların MGM'nin JSON yanıtındaki
gerçek alan adları henüz doğrulanmadı:

- **Şiddet:** Yeşil (tehlike yok) → Sarı (az tehlikeli) → Turuncu
  (tehlikeli) → Kırmızı (çok tehlikeli)
- **Hadise tipi:** Soğuk, Sıcak, Sis, Zirai Don, Buzlanma ve Don, Toz
  Taşınımı, Kar Erimesi, Çığ, Kar, Gökgürültülü Sağanak Yağış, Rüzgar,
  Yağmur
- **Kapsam:** Bugün + Yarın, il/ilçe bazlı.

`il` query parametresi MGM'ye doğrudan iletilir ama filtrenin MGM
tarafında gerçekten çalışıp çalışmadığı doğrulanamadı (aktif uyarı
olmadan test edilemedi) — zararsız bir passthrough, MGM parametreyi yok
sayarsa en kötü ihtimalle filtresiz sonuçla aynı şeyi alırsınız.

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

Tüm değişkenler için varsayılanlarıyla birlikte örnek bir dosya: [`.env.example`](../.env.example)
