"""
app.py
------
mgm_client.MGMWeather sınıfını basit bir REST API olarak dışarı açan
Flask uygulaması.

Çalıştırma:
    pip install -r requirements.txt
    python app.py
    # varsayılan olarak http://127.0.0.1:5000 üzerinde çalışır

Uç noktalar:
    GET /istasyonlar/<il>
        -> İldeki tüm istasyonları listeler

    GET /guncel/<il>?ilce=<ilce>
        -> Anlık hava durumu

    GET /tahmin/<il>?ilce=<ilce>
        -> 5 günlük tahmin

    GET /hava-durumu/<il>?ilce=<ilce>
        -> Güncel durum + tahmin + gün doğumu ve batımı

Örnek:
    curl "http://127.0.0.1:5000/hava-durumu/Istanbul?ilce=Bakirkoy"
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from flask import Flask, jsonify, request

from mgm_client import MGMWeather, MGMWeatherError

app = Flask(__name__)
mgm = MGMWeather(
    timeout=int(os.getenv("MGM_TIMEOUT", "10")),
    retry_total=int(os.getenv("MGM_RETRY_TOTAL", "3")),
    retry_backoff=float(os.getenv("MGM_RETRY_BACKOFF", "0.3")),
    cache_ttl_seconds=int(os.getenv("MGM_CACHE_TTL", "60")),
    cache_max_entries=int(os.getenv("MGM_CACHE_MAX_ENTRIES", "512")),
)
CORS_ALLOW_ORIGIN = os.getenv("APP_CORS_ALLOW_ORIGIN", "*")
RATE_LIMIT_WINDOW = int(os.getenv("APP_RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_MAX = int(os.getenv("APP_RATE_LIMIT_MAX_REQUESTS", "60"))
RATE_LIMIT_BUCKETS = defaultdict(deque)


def _hata_yanit(exc: Exception, kod: int = 502):
    return jsonify({"basarili": False, "hata": str(exc)}), kod


def _istasyon_id_getir(il: str, ilce: str | None) -> int | str:
    istasyon = mgm.ilce_istasyonu(il, ilce)
    istasyon_id = istasyon.get("istasyonId") or istasyon.get("merkezId")
    if istasyon_id is None:
        raise MGMWeatherError(f"'{il}' için geçerli istasyon kimliği bulunamadı.")
    return istasyon_id


@app.before_request
def rate_limit():
    if request.method == "OPTIONS":
        return None
    if request.path == "/health":
        return None

    ip = request.remote_addr or "unknown"
    now = time.monotonic()
    bucket = RATE_LIMIT_BUCKETS[ip]
    window_seconds = max(1, RATE_LIMIT_WINDOW)
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= max(1, RATE_LIMIT_MAX):
        retry_after = max(1, window_seconds)
        response = jsonify({
            "basarili": False,
            "hata": "Çok fazla istek gönderdiniz. Lütfen birkaç saniye sonra tekrar deneyin.",
        })
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response
    bucket.append(now)


@app.after_request
def guvenlik_ve_cors_headerlari(response):
    response.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none';"
    return response


@app.get("/istasyonlar/<il>")
def istasyonlar(il: str):
    try:
        return jsonify({"basarili": True, "veri": mgm.il_istasyonlari(il)})
    except MGMWeatherError as exc:
        return _hata_yanit(exc, 404)


@app.get("/health")
def health():
    deep = request.args.get("deep", "").strip().lower() in {"1", "true", "yes", "on"}
    if not deep:
        return jsonify({"basarili": True, "durum": "ok", "servis": "hava-durumu", "mgm": "skip"})
    try:
        mgm.il_istasyonlari("Ankara")
        return jsonify({"basarili": True, "durum": "ok", "servis": "hava-durumu", "mgm": "ok"})
    except MGMWeatherError as exc:
        return (
            jsonify(
                {
                    "basarili": False,
                    "durum": "degraded",
                    "servis": "hava-durumu",
                    "mgm": "hata",
                    "hata": str(exc),
                }
            ),
            503,
        )


@app.get("/guncel/<il>")
def guncel(il: str):
    ilce = request.args.get("ilce")
    try:
        istasyon_id = _istasyon_id_getir(il, ilce)
        veri = mgm.guncel_durum(istasyon_id)
        return jsonify({"basarili": True, "veri": veri})
    except MGMWeatherError as exc:
        return _hata_yanit(exc, 404)


@app.get("/tahmin/<il>")
def tahmin(il: str):
    ilce = request.args.get("ilce")
    try:
        istasyon_id = _istasyon_id_getir(il, ilce)
        veri = mgm.gunluk_tahmin(istasyon_id)
        return jsonify({"basarili": True, "veri": veri})
    except MGMWeatherError as exc:
        return _hata_yanit(exc, 404)


@app.get("/hava-durumu/<il>")
def hava_durumu(il: str):
    ilce = request.args.get("ilce")
    try:
        veri = mgm.hava_durumu(il, ilce)
        return jsonify({"basarili": True, "veri": veri})
    except MGMWeatherError as exc:
        return _hata_yanit(exc, 404)


@app.errorhandler(404)
def not_found(_exc):
    return jsonify({"basarili": False, "hata": "Uç nokta bulunamadı."}), 404


if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "5000"))
    server = os.getenv("APP_SERVER", "waitress").strip().lower()

    if server == "waitress":
        try:
            from waitress import serve
        except ImportError as exc:
            raise RuntimeError(
                "Waitress kurulu değil. `pip install -r requirements.txt` çalıştırın "
                "veya geçici olarak `APP_SERVER=flask` ile başlatın."
            ) from exc
        serve(app, host=host, port=port)
    elif server == "flask":
        debug = os.getenv("FLASK_DEBUG", "1").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        app.run(host=host, port=port, debug=debug)
    else:
        raise RuntimeError(
            f"Geçersiz APP_SERVER değeri: '{server}'. Geçerli değerler: waitress, flask."
        )
