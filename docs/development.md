# Geliştirme

Hızlı başlangıç için [README](../README.md)'ye bakın. Burada Docker'ın tüm
seçenekleri, test/lint/CI ve bağımlılık yönetimi detayları var.

## Docker

Redis dahil tam yığın, tek komut:

```bash
cp .env.example .env   # değerler düzenlenebilir, boşsa varsayılanlar geçerlidir
docker compose up --build
```

`MGM_REDIS_URL` otomatik olarak bundled Redis'e işaret eder; `.env` yoksa da
çalışır, varsayılanlarla devam eder.

Sadece uygulamayı (Redis'siz, in-memory cache ile) çalıştırmak için:

```bash
docker build -t mgm-api .
docker run -p 5000:5000 mgm-api
```

Container'ın `/health` uç noktasını kullanan bir `HEALTHCHECK`'i var;
`docker ps` çıktısında `healthy`/`unhealthy` olarak görünür.

## Test ve lint

```bash
python -m unittest discover -s tests -v
pip install ruff && ruff check .
```

İkisi de CI'da her push/PR'da otomatik çalışır (`.github/workflows/main.yml`).

## Bağımlılıklar

`requirements.txt` gevşek (`>=`) aralıklar; `requirements-lock.txt` bundan
üretilen tam pinlenmiş sürümlerdir — CI ve Docker build'leri lock dosyasını
kullanır, reproducible build sağlar. Güncelleme adımları dosyanın başındaki
yorumda yazıyor.

Dependabot (`.github/dependabot.yml`) pip/Docker/GitHub Actions
bağımlılıklarını haftalık tarar, güncelleme PR'ı açar.

## Response sıkıştırma

JSON/HTML/YAML yanıtları `Accept-Encoding: gzip` gönderen istemcilere
otomatik sıkıştırılmış döner (`Flask-Compress`); ek bir yapılandırma
gerekmez.

## Deploy (Render)

Repo'daki `render.yaml` Blueprint'i, web servisini ve Redis-uyumlu bir Key
Value servisini tek seferde kurar — kredi kartı istemeyen free plan. Render
Dashboard'da **New +** → **Blueprint** → bu repoyu seçin.
