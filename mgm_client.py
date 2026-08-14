"""
mgm_client.py
-------------
Not: MGM'nin eski "web servis" (SOAP/REST) API'sinin yerini alan,
www.mgm.gov.tr sitesinin kendisinin kullandığı iç JSON servislerine dayanır.
Resmi belgelenmiş bir API değildir; MGM bu uç noktaların yapısını
değiştirirse istemcinin de güncellenmesi gerekir.

Kullanım:
    from mgm_client import MGMWeather

    mgm = MGMWeather()
    istasyonlar = mgm.il_istasyonlari("İstanbul")
    guncel = mgm.guncel_durum(istasyonlar[0]["istasyonId"])
    tahmin = mgm.gunluk_tahmin(istasyonlar[0]["istasyonId"])
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class MGMWeatherError(Exception):
    """MGM istemcisiyle ilgili tüm hatalar için temel sınıf."""


# Durum kodları
CONDITION_CODES: dict[str, str] = {
    "PB": "Parçalı Bulutlu",
    "GSY": "Gökgürültülü Sağanak Yağışlı",
    "HSY": "Hafif Sağanak Yağışlı",
    "SY": "Sağanak Yağışlı",
    "A": "Açık",
    "AB": "Az Bulutlu",
    "CB": "Çok Bulutlu",
    "D": "Duman",
    "HY": "Hafif Yağmurlu",
    "HKY": "Hafif Kar Yağışlı",
    "MSY": "Yer Yer Sağanak Yağışlı",
    "KKY": "Karla Karışık Yağmurlu",
    "GKR": "Güneyli Kuvvetli Rüzgar",
    "SCK": "Sıcak",
    "PUS": "Puslu",
    "Y": "Yağmurlu",
    "K": "Kar Yağışlı",
    "DY": "Dolu",
    "R": "Rüzgarlı",
    "KKR": "Kuzeyli Kuvvetli Rüzgar",
    "SGK": "Soğuk",
    "SIS": "Sisli",
    "KY": "Kuvvetli Yağmurlu",
    "KSY": "Kuvvetli Sağanak Yağışlı",
    "YKY": "Yoğun Kar Yağışlı",
    "KF": "Toz veya Kum Fırtınası",
    "KGY": "Kuvvetli Gökgürültülü Sağanak Yağışlı",
}

_TR_MAP = str.maketrans("ıİüÜğĞşŞöÖçÇ", "iIuUgGsSoOcC")


def _tr_normalize(text: str) -> str:
    """Şehir ve ilçe adlarını MGM servisinin beklediği sadeleştirilmiş forma çevirir."""
    return text.translate(_TR_MAP).lower().strip()


@dataclass
class MGMWeather:
    """servis.mgm.gov.tr uç noktalarına istek atan basit istemci."""

    timeout: int = 10
    retry_total: int = 3
    retry_backoff: float = 0.3
    cache_ttl_seconds: int = 60
    cache_max_entries: int = 512
    redis_url: str | None = None
    redis_prefix: str = "mgm-cache:"
    redis_client: Any | None = None
    session: requests.Session = field(default_factory=requests.Session)
    _cache: dict[str, tuple[float, Any]] = field(default_factory=dict, init=False)
    _cache_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _redis_available: bool = field(default=False, init=False)
    _redis_error_cls: type[BaseException] | None = field(default=None, init=False)

    BASE_URL = "https://servis.mgm.gov.tr/web"
    SUNRISE_URL = "https://api.sunrise-sunset.org/json"

    HEADERS = {
        "Host": "servis.mgm.gov.tr",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Origin": "https://www.mgm.gov.tr",
        "Referer": "https://www.mgm.gov.tr/",
    }

    def __post_init__(self) -> None:
        retry = Retry(
            total=self.retry_total,
            connect=self.retry_total,
            read=self.retry_total,
            status=self.retry_total,
            backoff_factor=self.retry_backoff,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        if self.redis_client is not None:
            self._redis_available = True
            return

        if self.redis_url:
            try:
                import redis
            except ImportError as exc:
                raise MGMWeatherError(
                    "Redis cache icin 'redis' paketi kurulu degil. "
                    "`pip install -r requirements.txt` calistirin."
                ) from exc
            try:
                self.redis_client = redis.Redis.from_url(self.redis_url, decode_responses=True)
                self.redis_client.ping()
            except redis.RedisError as exc:
                raise MGMWeatherError(f"Redis baglantisi kurulamadi: {exc}") from exc
            self._redis_error_cls = redis.RedisError
            self._redis_available = True

    # Düşük seviye yardımcılar
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        cache_key = self._cache_key(path, params)
        redis_cached = self._redis_get(cache_key)
        if redis_cached is not None:
            return redis_cached

        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        url = f"{self.BASE_URL}/{path}"
        try:
            resp = self.session.get(
                url, headers=self.HEADERS, params=params, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise MGMWeatherError(f"MGM servisine bağlanılamadı: {exc}") from exc

        if resp.status_code != 200:
            raise MGMWeatherError(
                f"MGM servisi beklenmeyen durum kodu döndürdü: {resp.status_code} "
                f"({url})"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise MGMWeatherError(
                f"MGM servisinden geçerli JSON alınamadı ({url})"
            ) from exc
        self._cache_set(cache_key, data)
        self._redis_set(cache_key, data)
        return data

    def _cache_key(self, path: str, params: dict[str, Any] | None = None) -> str:
        serialized = json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
        return f"{path}?{serialized}"

    def _cache_get(self, key: str) -> Any | None:
        if self.cache_ttl_seconds <= 0:
            return None
        now = time.monotonic()
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, payload = entry
            if expires_at <= now:
                del self._cache[key]
                return None
            return copy.deepcopy(payload)

    def _cache_set(self, key: str, payload: Any) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        expires_at = time.monotonic() + self.cache_ttl_seconds
        with self._cache_lock:
            if len(self._cache) >= self.cache_max_entries:
                oldest_key = min(self._cache.items(), key=lambda item: item[1][0])[0]
                del self._cache[oldest_key]
            self._cache[key] = (expires_at, copy.deepcopy(payload))

    def _redis_key(self, key: str) -> str:
        return f"{self.redis_prefix}{key}"

    def _redis_get(self, key: str) -> Any | None:
        if not self._redis_available or self.cache_ttl_seconds <= 0:
            return None
        assert self.redis_client is not None

        if self._redis_error_cls is not None:
            try:
                value = self.redis_client.get(self._redis_key(key))
            except self._redis_error_cls as exc:
                raise MGMWeatherError(f"Redis cache okuma hatasi: {exc}") from exc
        else:
            value = self.redis_client.get(self._redis_key(key))

        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8")
        if not isinstance(value, str):
            raise MGMWeatherError("Redis cache verisi beklenen formatta degil.")
        try:
            return json.loads(value)
        except ValueError as exc:
            raise MGMWeatherError(f"Redis cache verisi cozumlenemedi: {exc}") from exc

    def _redis_set(self, key: str, payload: Any) -> None:
        if not self._redis_available or self.cache_ttl_seconds <= 0:
            return
        assert self.redis_client is not None
        serialized = json.dumps(payload, ensure_ascii=False)

        if self._redis_error_cls is not None:
            try:
                self.redis_client.setex(self._redis_key(key), self.cache_ttl_seconds, serialized)
            except self._redis_error_cls as exc:
                raise MGMWeatherError(f"Redis cache yazma hatasi: {exc}") from exc
        else:
            self.redis_client.setex(self._redis_key(key), self.cache_ttl_seconds, serialized)

    def il_istasyonlari(self, il: str) -> list[dict[str, Any]]:
        """
        Bir il adına göre o ildeki tüm MGM istasyonlarını (merkezleri) döndürür.
        Örn: il_istasyonlari("İstanbul") -> [{"istasyonId": ..., "il": ..., "ilce": ...}, ...]
        """
        data = self._get("merkezler", {"il": _tr_normalize(il)})
        if not data:
            raise MGMWeatherError(f"'{il}' için istasyon bulunamadı.")
        return data

    def ilce_istasyonu(self, il: str, ilce: str | None = None) -> dict[str, Any]:
        """
        İl (ve isteğe bağlı ilçe) adına göre tek bir istasyon kaydı döndürür.
        ilce verilmezse ilin merkez istasyonu (listedeki ilk kayıt) döndürülür.
        """
        istasyonlar = self.il_istasyonlari(il)
        if ilce:
            hedef = _tr_normalize(ilce)
            for kayit in istasyonlar:
                if _tr_normalize(kayit.get("ilce", "")) == hedef:
                    return kayit
            mevcut_ilceler = sorted(
                {
                    str(kayit.get("ilce", "")).strip()
                    for kayit in istasyonlar
                    if str(kayit.get("ilce", "")).strip()
                }
            )
            if mevcut_ilceler:
                raise MGMWeatherError(
                    f"'{il}' ilinde '{ilce}' ilçesi bulunamadı. "
                    f"Kullanılabilir ilçe(ler): {', '.join(mevcut_ilceler)}."
                )
            raise MGMWeatherError(f"'{il}' ilinde '{ilce}' ilçesi bulunamadı.")
        return istasyonlar[0]

    # Güncel durum
    def guncel_durum(self, istasyon_id: int | str) -> dict[str, Any]:
        """Bir istasyon için anlık (güncel) hava durumu verisini döndürür."""
        data = self._get("sondurumlar", {"merkezid": istasyon_id})
        if not data:
            raise MGMWeatherError(
                f"{istasyon_id} numaralı istasyon için güncel veri bulunamadı."
            )
        kayit = data[0]
        kod = kayit.get("hadiseKodu")
        return {
            "istasyonId": istasyon_id,
            "sicaklik": kayit.get("sicaklik"),
            "nem": kayit.get("nem"),
            "ruzgarHizi": kayit.get("ruzgarHiz"),
            "ruzgarYonu": kayit.get("ruzgarYon"),
            "basinc": kayit.get("aktuelBasinc"),
            "denizSeviyesiBasinc": kayit.get("denizeIndirgenmisBasinc"),
            "durumKodu": kod,
            "durum": CONDITION_CODES.get(kod, kod),
            "olcumZamani": kayit.get("veriZamani"),
        }


    # Günlük tahmin (5 günlük)

    def gunluk_tahmin(self, istasyon_id: int | str) -> list[dict[str, Any]]:
        """Bir istasyon için 5 günlük tahmini gün gün liste olarak döndürür."""
        data = self._get("tahminler/gunluk", {"istno": istasyon_id})
        if not data:
            raise MGMWeatherError(
                f"{istasyon_id} numaralı istasyon için tahmin verisi bulunamadı."
            )
        kayit = data[0]
        bugun = _dt.date.today()
        sonuc = []
        for gun in range(1, 6):
            kod = kayit.get(f"hadiseGun{gun}")
            sonuc.append(
                {
                    "tarih": (bugun + _dt.timedelta(days=gun - 1)).isoformat(),
                    "enDusuk": kayit.get(f"enDusukGun{gun}"),
                    "enYuksek": kayit.get(f"enYuksekGun{gun}"),
                    "enDusukNem": kayit.get(f"enDusukNemGun{gun}"),
                    "enYuksekNem": kayit.get(f"enYuksekNemGun{gun}"),
                    "ruzgarHizi": kayit.get(f"ruzgarHizGun{gun}"),
                    "durumKodu": kod,
                    "durum": CONDITION_CODES.get(kod, kod),
                }
            )
        return sonuc

    # Saatlik tahmin
    def saatlik_tahmin(self, istasyon_id: int | str) -> list[dict[str, Any]]:
        """Bir istasyon için saatlik tahmin verisini döndürür (mevcutsa)."""
        data = self._get("tahminler/saatlik", {"istno": istasyon_id})
        return data or []

    # Gün doğumu ve batımı (sunrise-sunset.org üzerinden)
    def gun_dogumu_batimi(self, enlem: float, boylam: float) -> dict[str, str]:
        try:
            resp = self.session.get(
                self.SUNRISE_URL,
                params={"lat": enlem, "lng": boylam, "formatted": 0},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            sonuc = resp.json()["results"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise MGMWeatherError(f"Gün doğumu/batımı verisi alınamadı: {exc}") from exc

        tz = _dt.timezone(_dt.timedelta(hours=3))  # Bizim saat (UTC+3)
        dogum = _dt.datetime.fromisoformat(sonuc["sunrise"]).astimezone(tz)
        batim = _dt.datetime.fromisoformat(sonuc["sunset"]).astimezone(tz)
        return {"gunDogumu": dogum.strftime("%H:%M"), "gunBatimi": batim.strftime("%H:%M")}

    def hava_durumu(self, il: str, ilce: str | None = None) -> dict[str, Any]:
        """
        Verilen il/ilçe için güncel durum + 5 günlük tahmini tek seferde
        toplayıp döndüren üst düzey yardımcı fonksiyon.
        """
        istasyon = self.ilce_istasyonu(il, ilce)
        istasyon_id = istasyon.get("istasyonId") or istasyon.get("merkezId")

        sonuc: dict[str, Any] = {
            "il": istasyon.get("il", il),
            "ilce": istasyon.get("ilce"),
            "istasyonId": istasyon_id,
            "enlem": istasyon.get("enlem") or istasyon.get("lat"),
            "boylam": istasyon.get("boylam") or istasyon.get("lon"),
        }
        sonuc["guncel"] = self.guncel_durum(istasyon_id)
        sonuc["tahmin"] = self.gunluk_tahmin(istasyon_id)

        try:
            if sonuc["enlem"] and sonuc["boylam"]:
                sonuc.update(self.gun_dogumu_batimi(sonuc["enlem"], sonuc["boylam"]))
        except MGMWeatherError:
            pass

        return sonuc


if __name__ == "__main__":
    import json
    import sys

    il_adi = sys.argv[1] if len(sys.argv) > 1 else "İstanbul"
    ilce_adi = sys.argv[2] if len(sys.argv) > 2 else None

    client = MGMWeather()
    print(json.dumps(client.hava_durumu(il_adi, ilce_adi), ensure_ascii=False, indent=2))
