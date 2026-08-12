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
    curl "http://127.0.0.1:5000/hava-durumu/Istanbul?ilce=Kadikoy"
"""

from __future__ import annotations

from flask import Flask, jsonify, request

from mgm_client import MGMWeather, MGMWeatherError

app = Flask(__name__)
mgm = MGMWeather()


def _hata_yanit(exc: Exception, kod: int = 502):
    return jsonify({"basarili": False, "hata": str(exc)}), kod


@app.get("/istasyonlar/<il>")
def istasyonlar(il: str):
    try:
        return jsonify({"basarili": True, "veri": mgm.il_istasyonlari(il)})
    except MGMWeatherError as exc:
        return _hata_yanit(exc, 404)


@app.get("/guncel/<il>")
def guncel(il: str):
    ilce = request.args.get("ilce")
    try:
        istasyon = mgm.ilce_istasyonu(il, ilce)
        istasyon_id = istasyon.get("istasyonId") or istasyon.get("merkezId")
        veri = mgm.guncel_durum(istasyon_id)
        return jsonify({"basarili": True, "veri": veri})
    except MGMWeatherError as exc:
        return _hata_yanit(exc, 404)


@app.get("/tahmin/<il>")
def tahmin(il: str):
    ilce = request.args.get("ilce")
    try:
        istasyon = mgm.ilce_istasyonu(il, ilce)
        istasyon_id = istasyon.get("istasyonId") or istasyon.get("merkezId")
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
    app.run(debug=True)
