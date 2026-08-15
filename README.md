<h1 align="center">MGM Unofficial Weather API</h1>

<p align="center">
  <a href="https://github.com/metezd/mgm-api/actions/workflows/main.yml">
    <img src="https://github.com/metezd/mgm-api/actions/workflows/main.yml/badge.svg" alt="CI Status" />
  </a>
  <img src="https://img.shields.io/badge/python-3.13-blue.svg" alt="Python 3.13" />
  <img src="https://img.shields.io/badge/redis-enabled-red.svg" alt="Redis" />
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License" />
</p>

<p align="center">
  Resmi bir API değildir. Sadece veriyi <a href="https://www.mgm.gov.tr">mgm.gov.tr</a> sitesinden çeker.
  Karakter sorunu yaşanırsa il/ilçe adını Türkçe karakter kullanmadan yazın (<code>Istanbul</code>, <code>Bakirkoy</code>).
</p>

## İçindekiler

- [Kurulum](#kurulum)
- [Docker ile çalıştırma](#docker-ile-çalıştırma)
- [Uç noktalar](#uç-noktalar)
- [Örnek](#örnek)
- [Test ve lint](#test-ve-lint)
- [Daha fazla](#daha-fazla)

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

Redis dahil tam yığın, tek komut:

```bash
cp .env.example .env   # değerler düzenlenebilir, boşsa varsayılanlar geçerlidir
docker compose up --build
```

`MGM_REDIS_URL` otomatik olarak bundled Redis'e işaret eder; `.env` yoksa da
çalışır, varsayılanlarla devam eder.

Sadece uygulamayı (Redis'siz) çalıştırmak için:

```bash
docker build -t mgm-api .
docker run -p 5000:5000 mgm-api
```

Container'ın `/health` uç noktasını kullanan bir `HEALTHCHECK`'i var;
`docker ps` çıktısında `healthy`/`unhealthy` olarak görünür.

## Uç noktalar

| Uç nokta | Açıklama |
|---|---|
| `GET /docs` | Swagger UI — tüm endpoint'lerin şeması, örnekleri |
| `GET /openapi.yaml` | Ham OpenAPI 3.0 spesifikasyonu |
| `GET /health` | Servis durumu (`?deep=1` ile MGM + Redis bağlantısı da kontrol edilir) |
| `GET /iller` | Türkiye'nin 81 ilini listeler (sabit veri) |
| `GET /istasyonlar/<il>` | O ildeki istasyonları (ilçeleri) listeler |
| `GET /guncel/<il>?ilce=<ilce>` | Anlık hava durumu (MGM çökükse Open-Meteo'ya düşer) |
| `GET /tahmin/<il>?ilce=<ilce>` | 5 günlük tahmin |
| `GET /saatlik/<il>?ilce=<ilce>` | Saatlik tahmin |
| `GET /hava-durumu/<il>?ilce=<ilce>` | Güncel durum (fallback'li) + tahmin (birleşik) |

`ilce` parametresi verilmezse ilin ilk istasyonu kullanılır. Anlık durum
yanıtındaki `kaynak` alanı (`mgm` | `open-meteo`) verinin nereden geldiğini
gösterir [docs/resilience.md](docs/resilience.md#open-meteo-fallback-yalnızca-anlık-durum)
Alan bazında şema için `/docs`'a bakın

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
    "guncel": { "sicaklik": 29.4, "durum": "Açık" },
    "tahmin": [
      { "tarih": "2026-08-14", "enDusuk": 23, "enYuksek": 31, "durum": "Az Bulutlu" }
    ]
  }
}
```

Hata durumunda:

```json
{ "basarili": false, "hata": "'X' ilinde 'Y' ilçesi bulunamadı." }
```

## Test ve lint

```bash
python -m unittest discover -s tests -v
pip install ruff && ruff check .
```

İkisi de CI'da her push/PR'da otomatik çalışır (`.github/workflows/main.yml`).

## Daha fazla

- **[docs/resilience.md](docs/resilience.md)** — Redis cache, stale-while-revalidate,
  circuit breaker nasıl çalışır ve tüm ortam değişkenlerinin tam listesi.
- Bağımlılıklar CI ve Docker build'lerinde `requirements-lock.txt` (pinlenmiş
  sürümler) ile kurulur. Dependabot (`.github/dependabot.yml`) pip/Docker/GitHub
  Actions bağımlılıklarını haftalık tarar.
- JSON/HTML/YAML yanıtları `Accept-Encoding: gzip` gönderen istemcilere otomatik
  sıkıştırılmış döner (`Flask-Compress`); ek bir yapılandırma gerekmez.
