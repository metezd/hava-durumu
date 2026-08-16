import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import requests

from mgm_client import MGMCircuitOpenError, MGMWeather, MGMWeatherError, _tr_normalize, turkiye_illeri


class _DummyResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _CountingSession:
    def __init__(self, payload):
        self.calls = 0
        self.payload = payload

    def get(self, url, **kwargs):
        self.calls += 1
        return _DummyResponse(self.payload)


class TestMGMClientUnit(unittest.TestCase):
    def test_tr_normalize_ascii_cevirir(self):
        self.assertEqual(_tr_normalize("İstanbul"), "istanbul")
        self.assertEqual(_tr_normalize(" Üsküdar "), "uskudar")

    def test_turkiye_illeri_81_il_plaka_kodu_sirali(self):
        illeri = turkiye_illeri()
        self.assertEqual(len(illeri), 81)
        plaka_kodlari = [kayit["plakaKodu"] for kayit in illeri]
        self.assertEqual(plaka_kodlari, list(range(1, 82)))
        self.assertEqual(illeri[0]["il"], "Adana")
        self.assertEqual(illeri[33]["il"], "İstanbul")

    def test_turkiye_illeri_kopya_doner(self):
        ilk = turkiye_illeri()
        ilk[0]["il"] = "DEĞİŞTİRİLDİ"
        ikinci = turkiye_illeri()
        self.assertEqual(ikinci[0]["il"], "Adana")

    def test_retry_ayarlari_sessiona_uygulanir(self):
        client = MGMWeather(timeout=7, retry_total=4, retry_backoff=0.5)
        https_adapter = client.session.adapters["https://"]
        retries = https_adapter.max_retries

        self.assertEqual(retries.total, 4)
        self.assertEqual(retries.connect, 4)
        self.assertEqual(retries.read, 4)
        self.assertEqual(retries.status, 4)
        self.assertEqual(retries.backoff_factor, 0.5)

    def test_cache_aktifken_ayni_istek_tek_kez_yapilir(self):
        payload = [{"istasyonId": 1}]
        client = MGMWeather(cache_ttl_seconds=60)
        fake_session = _CountingSession(payload)
        client.session = fake_session

        ilk = client._get("merkezler", {"il": "ankara"})
        ikinci = client._get("merkezler", {"il": "ankara"})

        self.assertEqual(fake_session.calls, 1)
        self.assertEqual(ilk, payload)
        self.assertEqual(ikinci, payload)

    def test_cache_kapaliyken_istek_tekrarlanir(self):
        payload = [{"istasyonId": 1}]
        client = MGMWeather(cache_ttl_seconds=0)
        fake_session = _CountingSession(payload)
        client.session = fake_session

        client._get("merkezler", {"il": "ankara"})
        client._get("merkezler", {"il": "ankara"})

        self.assertEqual(fake_session.calls, 2)

    def test_gun_dogumu_batimi_ayni_konum_icin_cache_lenir(self):
        payload = {
            "results": {
                "sunrise": "2026-08-14T04:00:00+00:00",
                "sunset": "2026-08-14T18:30:00+00:00",
            }
        }
        client = MGMWeather(cache_ttl_seconds=3600)
        fake_session = _CountingSession(payload)
        client.session = fake_session

        ilk = client.gun_dogumu_batimi(40.98, 29.02)
        ikinci = client.gun_dogumu_batimi(40.98, 29.02)

        self.assertEqual(fake_session.calls, 1)
        self.assertEqual(ilk, ikinci)
        self.assertEqual(ilk["gunDogumu"], "07:00")
        self.assertEqual(ilk["gunBatimi"], "21:30")

    def test_gun_dogumu_batimi_farkli_konumda_istek_tekrarlanir(self):
        payload = {
            "results": {
                "sunrise": "2026-08-14T04:00:00+00:00",
                "sunset": "2026-08-14T18:30:00+00:00",
            }
        }
        client = MGMWeather(cache_ttl_seconds=3600)
        fake_session = _CountingSession(payload)
        client.session = fake_session

        client.gun_dogumu_batimi(40.98, 29.02)
        client.gun_dogumu_batimi(41.02, 28.97)

        self.assertEqual(fake_session.calls, 2)


class _SinirliYanitliSession:
    """İlk N istekte farklı, sonrasında aynı yükü döndüren sahte oturum.

    SWR akışını test etmek için `_cached_get`'ten gelen istek sayısını ve
    dönen veriyi gözlemlemeye yarar.
    """

    def __init__(self, yukler):
        self.yukler = list(yukler)
        self.calls = 0

    def get(self, url, **kwargs):
        yuk = self.yukler[min(self.calls, len(self.yukler) - 1)]
        self.calls += 1
        return _DummyResponse(yuk)


class TestStaleWhileRevalidate(unittest.TestCase):
    def _istek(self, client, path, params):
        return client._get(path, params)

    def test_stale_iken_eski_veri_doner_ve_arka_planda_yenilenir(self):
        eski_yuk = [{"deger": 1}]
        yeni_yuk = [{"deger": 2}]
        session = _SinirliYanitliSession([eski_yuk, yeni_yuk])
        client = MGMWeather(
            cache_ttl_seconds=1,
            stale_while_revalidate_seconds=300,
            timeout=1,
        )
        client.session = session
        # 1. istek: cache miss -> eski yük
        ilk = self._istek(client, "merkezler", {"il": "ankara"})
        self.assertEqual(ilk, eski_yuk)

        # TTL geçsin (1 sn) stale pencere içinde kalsın
        time.sleep(1.2)

        # 2. istek: stale -> eski veri anında döner arka planda yenileme başlar
        ikinci = self._istek(client, "merkezler", {"il": "ankara"})
        self.assertEqual(ikinci, eski_yuk)
        self.assertGreaterEqual(session.calls, 2)
        # Arka plan görevinin bitmesi için kısa bekle
        time.sleep(0.2)
        # Sonraki istek: taze veri cache'ten döner
        ucuncu = self._istek(client, "merkezler", {"il": "ankara"})
        self.assertEqual(ucuncu, yeni_yuk)

    def test_swr_kapaliyken_stale_veri_donmez(self):
        yuk = [{"deger": 1}]
        session = _SinirliYanitliSession([yuk, yuk])
        client = MGMWeather(
            cache_ttl_seconds=1,
            stale_while_revalidate_seconds=0,
            timeout=1,
        )
        client.session = session

        ilk = self._istek(client, "merkezler", {"il": "ankara"})
        time.sleep(1.2)
        ikinci = self._istek(client, "merkezler", {"il": "ankara"})

        # SWR kapalı: TTL dolunca bloklayıcı yeniden yükleme yapılır (2. çağrı)
        self.assertEqual(ilk, yuk)
        self.assertEqual(ikinci, yuk)
        self.assertEqual(session.calls, 2)

    def test_stale_pencere_asilinca_bloklayici_yenileme_olur(self):
        eski_yuk = [{"deger": 1}]
        yeni_yuk = [{"deger": 2}]
        session = _SinirliYanitliSession([eski_yuk, yeni_yuk])
        client = MGMWeather(
            cache_ttl_seconds=1,
            stale_while_revalidate_seconds=1,
            timeout=1,
        )
        client.session = session

        self._istek(client, "merkezler", {"il": "ankara"})
        time.sleep(2.2)  # TTL (1) + SWR (1) toplam ömür doldu

        sonuc = self._istek(client, "merkezler", {"il": "ankara"})
        self.assertEqual(sonuc, yeni_yuk)
        self.assertEqual(session.calls, 2)

    def test_yenileme_kilidi_tektir(self):
        client = MGMWeather(cache_ttl_seconds=60)
        key = "test-key"

        ilk = client._renew_try_lock(key)
        ikinci = client._renew_try_lock(key)
        self.assertTrue(ilk)
        self.assertFalse(ikinci)

        client._renew_release(key)
        ucuncu = client._renew_try_lock(key)
        self.assertTrue(ucuncu)
        client._renew_release(key)

    def test_single_flight_es_zamanli_yuklemede_tek_istek(self):
        client = MGMWeather(cache_ttl_seconds=60)
        yukleme_sayisi = 0
        kilit = threading.Lock()

        def yavas_loader():
            nonlocal yukleme_sayisi
            with kilit:
                yukleme_sayisi += 1
            time.sleep(0.2)
            return [{"deger": yukleme_sayisi}]

        sonuclar = []
        hatalar = []

        def istek_atan():
            try:
                sonuclar.append(client._yukle_singleton("single-key", yavas_loader))
            except Exception as exc:  # noqa: BLE001
                hatalar.append(exc)

        ipler = [threading.Thread(target=istek_atan) for _ in range(5)]
        for ip in ipler:
            ip.start()
        for ip in ipler:
            ip.join()

        self.assertEqual(hatalar, [])
        self.assertEqual(yukleme_sayisi, 1)
        self.assertEqual(len(sonuclar), 5)
        self.assertTrue(all(s == sonuclar[0] for s in sonuclar))

    def test_redis_saglik_ozeti_redis_kapaliyken_skip(self):
        client = MGMWeather()
        self.assertEqual(client.redis_saglik_ozeti(), {"durum": "skip"})


class _PatlayanSession:
    """Her çağrıda bağlantı hatası fırlatan sahte oturum (MGM kesintisi)."""

    def __init__(self):
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        raise requests.ConnectionError("bağlantı reddedildi")


class _AyarlanabilirSession:
    """İlk `basarisiz_sayisi` çağrıda hata fırlatan, sonrasında başarılı
    yanıt dönen sahte oturum. Yarı açık deneme senaryolarını test etmek
    için kullanılır."""

    def __init__(self, basarisiz_sayisi, payload):
        self.basarisiz_sayisi = basarisiz_sayisi
        self.payload = payload
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        if self.calls <= self.basarisiz_sayisi:
            raise requests.ConnectionError("bağlantı reddedildi")
        return _DummyResponse(self.payload)


class TestCircuitBreaker(unittest.TestCase):
    def test_esik_asilinca_devre_acilir_ve_istek_atlanir(self):
        session = _PatlayanSession()
        client = MGMWeather(
            cache_ttl_seconds=0,
            timeout=1,
            retry_total=0,
            circuit_breaker_failure_threshold=3,
            circuit_breaker_window_seconds=30,
            circuit_breaker_open_seconds=60,
        )
        client.session = session

        for _ in range(3):
            with self.assertRaises(MGMWeatherError):
                client._get("merkezler", {"il": "ankara"})

        self.assertEqual(session.calls, 3)
        self.assertEqual(client.circuit_breaker_saglik_ozeti(), {"durum": "acik"})

        # Devre açıkken 4. çağrı ağa hiç gitmemeli, doğrudan hata dönmeli.
        with self.assertRaises(MGMCircuitOpenError):
            client._get("merkezler", {"il": "ankara"})
        self.assertEqual(session.calls, 3)

    def test_pencere_disindaki_hatalar_devreyi_actirmaz(self):
        session = _PatlayanSession()
        client = MGMWeather(
            cache_ttl_seconds=0,
            timeout=1,
            retry_total=0,
            circuit_breaker_failure_threshold=3,
            circuit_breaker_window_seconds=0.3,
            circuit_breaker_open_seconds=60,
        )
        client.session = session

        with self.assertRaises(MGMWeatherError):
            client._get("merkezler", {"il": "ankara"})
        time.sleep(0.4)  # pencere dışına çık
        with self.assertRaises(MGMWeatherError):
            client._get("merkezler", {"il": "ankara"})
        with self.assertRaises(MGMWeatherError):
            client._get("merkezler", {"il": "ankara"})

        # Pencere kaydığı için sadece son 2 hata sayılır, devre açılmaz.
        self.assertEqual(client.circuit_breaker_saglik_ozeti(), {"durum": "kapali"})
        self.assertEqual(session.calls, 3)

    def test_acik_devre_suresi_dolunca_yari_acik_deneme_basarili_olursa_kapanir(self):
        session = _AyarlanabilirSession(basarisiz_sayisi=2, payload=[{"istasyonId": 1}])
        client = MGMWeather(
            cache_ttl_seconds=0,
            timeout=1,
            retry_total=0,
            circuit_breaker_failure_threshold=2,
            circuit_breaker_window_seconds=30,
            circuit_breaker_open_seconds=0.3,
        )
        client.session = session

        for _ in range(2):
            with self.assertRaises(MGMWeatherError):
                client._get("merkezler", {"il": "ankara"})
        self.assertEqual(client.circuit_breaker_saglik_ozeti(), {"durum": "acik"})

        # open_seconds dolmadan istek atlanmaya devam eder.
        with self.assertRaises(MGMCircuitOpenError):
            client._get("merkezler", {"il": "ankara"})
        self.assertEqual(session.calls, 2)

        time.sleep(0.4)  # open_seconds doldu -> yarı açık
        sonuc = client._get("merkezler", {"il": "ankara"})
        self.assertEqual(sonuc, session.payload)
        self.assertEqual(session.calls, 3)
        self.assertEqual(client.circuit_breaker_saglik_ozeti(), {"durum": "kapali"})

    def test_yari_acik_deneme_basarisiz_olursa_devre_tekrar_acilir(self):
        session = _PatlayanSession()
        client = MGMWeather(
            cache_ttl_seconds=0,
            timeout=1,
            retry_total=0,
            circuit_breaker_failure_threshold=1,
            circuit_breaker_window_seconds=30,
            circuit_breaker_open_seconds=0.3,
        )
        client.session = session

        with self.assertRaises(MGMWeatherError):
            client._get("merkezler", {"il": "ankara"})
        self.assertEqual(client.circuit_breaker_saglik_ozeti(), {"durum": "acik"})

        time.sleep(0.4)  # yarı açık deneme hakkı doğar
        with self.assertRaises(MGMWeatherError):
            client._get("merkezler", {"il": "ankara"})
        self.assertEqual(client.circuit_breaker_saglik_ozeti(), {"durum": "acik"})
        self.assertEqual(session.calls, 2)

    def test_devre_acikken_stale_cache_verisi_donmeye_devam_eder(self):
        """Redis/in-memory cache tampon görevi görür: breaker açık olsa da
        stale-while-revalidate penceresindeki eski veri kullanıcıya
        dönmeye devam eder; sadece arka plandaki asıl ağ isteği atlanır."""
        eski_yuk = [{"deger": 1}]
        session = _PatlayanSession()
        client = MGMWeather(
            cache_ttl_seconds=1,
            stale_while_revalidate_seconds=300,
            timeout=1,
            retry_total=0,
            circuit_breaker_failure_threshold=1,
            circuit_breaker_window_seconds=30,
            circuit_breaker_open_seconds=60,
        )
        # Önce cache'i başarılı bir yanıtla doldur.
        basarili_session = _CountingSession(eski_yuk)
        client.session = basarili_session
        ilk = client._get("merkezler", {"il": "ankara"})
        self.assertEqual(ilk, eski_yuk)

        # Devreyi patlayan oturumla aç.
        client.session = session
        time.sleep(1.2)  # TTL geçsin, stale pencereye düşsün
        ikinci = client._get("merkezler", {"il": "ankara"})
        self.assertEqual(ikinci, eski_yuk)
        self.assertEqual(client.circuit_breaker_saglik_ozeti(), {"durum": "acik"})

        time.sleep(0.2)  # arka plan yenileme denemesinin bitmesini bekle
        ucuncu = client._get("merkezler", {"il": "ankara"})
        self.assertEqual(ucuncu, eski_yuk)  # devre açık: hâlâ eski veri


class _UrlBazliSession:
    """URL'nin içerdiği alt path'e göre farklı davranan sahte session —
    MGM/Open-Meteo fallback senaryolarını (aynı istek zincirinde bazı
    uçların başarılı, bazılarının başarısız olması) test etmek için."""

    def __init__(self, davranislar):
        # davranislar: {url_parcasi: yük_sözlüğü_veya_Exception}
        self.davranislar = davranislar
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        for parca, davranis in self.davranislar.items():
            if parca in url:
                if isinstance(davranis, Exception):
                    raise davranis
                return _DummyResponse(davranis)
        raise AssertionError(f"Beklenmeyen URL çağrıldı: {url}")


def _open_meteo_basarili_yuk(sicaklik=21.4):
    return {
        "current": {
            "temperature_2m": sicaklik,
            "relative_humidity_2m": 60,
            "wind_speed_10m": 10.5,
            "wind_direction_10m": 180,
            "surface_pressure": 1012.0,
            "pressure_msl": 1015.0,
            "weather_code": 1,
            "time": "2026-08-15T09:00",
        }
    }


class TestOpenMeteoFallback(unittest.TestCase):
    def test_mgm_basariliysa_fallback_hic_denenmez(self):
        session = _UrlBazliSession(
            {
                "sondurumlar": [{"sicaklik": 25.0, "hadiseKodu": "A", "veriZamani": "x"}],
                "open-meteo.com": _open_meteo_basarili_yuk(),
            }
        )
        client = MGMWeather(cache_ttl_seconds=0, timeout=1, retry_total=0)
        client.session = session

        veri = client.guncel_durum_yedekli(17062, 40.98, 28.87)
        self.assertEqual(veri["kaynak"], "mgm")
        self.assertEqual(veri["sicaklik"], 25.0)
        self.assertTrue(all("open-meteo" not in u for u in session.calls))

    def test_mgm_hata_verince_open_meteo_ya_dusuluyor(self):
        session = _UrlBazliSession(
            {
                "sondurumlar": requests.ConnectionError("bağlantı reddedildi"),
                "open-meteo.com": _open_meteo_basarili_yuk(sicaklik=18.2),
            }
        )
        client = MGMWeather(cache_ttl_seconds=0, timeout=1, retry_total=0)
        client.session = session

        veri = client.guncel_durum_yedekli(17062, 40.98, 28.87)
        self.assertEqual(veri["kaynak"], "open-meteo")
        self.assertEqual(veri["sicaklik"], 18.2)
        self.assertEqual(veri["durum"], "Genel Olarak Açık")  # WMO kod 1
        self.assertEqual(veri["istasyonId"], 17062)

    def test_mgm_hata_ve_konum_yoksa_fallback_denenmez_orijinal_hata_doner(self):
        session = _UrlBazliSession({"sondurumlar": requests.ConnectionError("x")})
        client = MGMWeather(cache_ttl_seconds=0, timeout=1, retry_total=0)
        client.session = session

        with self.assertRaises(MGMWeatherError) as ctx:
            client.guncel_durum_yedekli(17062)  # enlem/boylam verilmedi
        self.assertIn("bağlanılamadı", str(ctx.exception))
        self.assertTrue(all("open-meteo" not in u for u in session.calls))

    def test_ikisi_de_basarisizsa_birlesik_hata_mesaji_verilir(self):
        session = _UrlBazliSession(
            {
                "sondurumlar": requests.ConnectionError("mgm çöktü"),
                "open-meteo.com": requests.ConnectionError("open-meteo de çöktü"),
            }
        )
        client = MGMWeather(cache_ttl_seconds=0, timeout=1, retry_total=0)
        client.session = session

        with self.assertRaises(MGMWeatherError) as ctx:
            client.guncel_durum_yedekli(17062, 40.98, 28.87)
        mesaj = str(ctx.exception)
        self.assertIn("MGM", mesaj)
        self.assertIn("Open-Meteo", mesaj)

    def test_hava_durumu_mgm_cokse_bile_fallback_ile_doner(self):
        merkez_yuk = [
            {
                "il": "İstanbul",
                "ilce": "Bakırköy",
                "istasyonId": 17062,
                "enlem": 40.98,
                "boylam": 28.87,
            }
        ]
        session = _UrlBazliSession(
            {
                "merkezler": merkez_yuk,
                "sondurumlar": requests.ConnectionError("mgm çöktü"),
                "tahminler/gunluk": requests.ConnectionError("mgm çöktü"),
                "open-meteo.com": _open_meteo_basarili_yuk(sicaklik=19.9),
                "sunrise-sunset.org": {
                    "results": {
                        "sunrise": "2026-08-15T03:00:00+00:00",
                        "sunset": "2026-08-15T16:00:00+00:00",
                    }
                },
            }
        )
        client = MGMWeather(cache_ttl_seconds=0, timeout=1, retry_total=0)
        client.session = session

        sonuc = client.hava_durumu("İstanbul", "Bakırköy")
        self.assertEqual(sonuc["guncel"]["kaynak"], "open-meteo")
        self.assertEqual(sonuc["guncel"]["sicaklik"], 19.9)
        self.assertEqual(sonuc["tahmin"], [])  # tahmin fallback'i yok, boş liste


class TestRedisBaslangicYenidenDeneme(unittest.TestCase):
    """Render/Docker Compose gibi ortamlarda web servis ile Redis'in
    yaklaşık eşzamanlı başlatılması, ilk ping'in geçici olarak (DNS henüz
    hazır değilken) başarısız olmasına yol açabiliyor. Bu, gerçek bir
    yanlış yapılandırmadan ayırt edilmeli — birkaç deneme sonra toparlanmalı,
    ama denemeler gerçekten tükenirse hâlâ sert şekilde hata vermeli."""

    def test_gecici_baglanti_hatasi_birkac_denemeden_sonra_toparlanir(self):
        import redis as redis_module

        sahte_client = MagicMock()
        sahte_client.ping.side_effect = [
            redis_module.exceptions.ConnectionError("dns henüz hazır değil"),
            redis_module.exceptions.ConnectionError("dns henüz hazır değil"),
            True,
        ]

        with (
            patch("redis.Redis.from_url", return_value=sahte_client),
            patch("mgm_client.REDIS_STARTUP_RETRY_DELAY_SECONDS", 0.01),
        ):
            client = MGMWeather(redis_url="redis://sahte-host:6379/0")

        self.assertTrue(client._redis_available)
        self.assertEqual(sahte_client.ping.call_count, 3)

    def test_denemeler_tukenirse_hata_firlatilir(self):
        import redis as redis_module

        sahte_client = MagicMock()
        sahte_client.ping.side_effect = redis_module.exceptions.ConnectionError(
            "gerçekten çökük"
        )

        with (
            patch("redis.Redis.from_url", return_value=sahte_client),
            patch("mgm_client.REDIS_STARTUP_RETRY_DELAY_SECONDS", 0.01),
            patch("mgm_client.REDIS_STARTUP_RETRY_ATTEMPTS", 3),
        ):
            with self.assertRaises(MGMWeatherError) as ctx:
                MGMWeather(redis_url="redis://sahte-host:6379/0")

        self.assertEqual(sahte_client.ping.call_count, 3)
        self.assertIn("3 denemeden", str(ctx.exception))


class _IlceFarkindaSession:
    """merkezler isteklerinde `il`/`ilce` parametrelerine göre farklı sahte
    yanıt döner — MGM'nin gerçek (canlıda doğrulanmış) davranışını taklit
    eder: il-only sorgu o ilin sadece varsayılan istasyonunu döner, il+ilce
    sorgusu o ilçeye özel (farklı) bir istasyon döner."""

    def __init__(self):
        self.calls: list[dict] = []

    def get(self, url, params=None, **kwargs):
        params = dict(params or {})
        self.calls.append(params)
        if "merkezler" not in url:
            raise AssertionError(f"Beklenmeyen URL: {url}")
        il, ilce = params.get("il"), params.get("ilce")
        if il == "istanbul" and not ilce:
            return _DummyResponse(
                [{"il": "İstanbul", "ilce": "Bakırköy", "istasyonId": 93401,
                  "enlem": 40.98, "boylam": 28.82}]
            )
        if il == "istanbul" and ilce == "kadikoy":
            return _DummyResponse(
                [{"il": "İstanbul", "ilce": "Kadıköy", "istasyonId": 93409,
                  "enlem": 40.99, "boylam": 29.02}]
            )
        if il == "istanbul" and ilce == "olmayanilce":
            return _DummyResponse([])  # MGM: bulunamadı, boş dizi
        raise AssertionError(f"Beklenmeyen params: {params}")


class TestIlceDogrudanSorgu(unittest.TestCase):
    """MGM'nin merkezler uç noktası il-only sorguda o ilin sadece bir
    (varsayılan) istasyonunu döner, tüm ilçelerini değil — bu canlıda
    doğrulandı (İstanbul: il=istanbul -> yalnızca Bakırköy, ama
    il=istanbul&ilce=kadikoy -> ayrı ve doğru bir sonuç döner). Doğru
    davranış: ilce verildiğinde MGM'ye doğrudan parametre olarak
    gönderilmeli, il_istasyonlari()'nin dar listesinde client-side arama
    yapılmamalı."""

    def test_ilce_verilmezse_ilin_varsayilan_istasyonu_doner(self):
        session = _IlceFarkindaSession()
        client = MGMWeather(cache_ttl_seconds=0, timeout=1, retry_total=0)
        client.session = session

        istasyon = client.ilce_istasyonu("istanbul")
        self.assertEqual(istasyon["ilce"], "Bakırköy")

    def test_ilce_mgmye_dogrudan_parametre_olarak_gonderilir(self):
        session = _IlceFarkindaSession()
        client = MGMWeather(cache_ttl_seconds=0, timeout=1, retry_total=0)
        client.session = session

        istasyon = client.ilce_istasyonu("istanbul", "kadikoy")
        self.assertEqual(istasyon["ilce"], "Kadıköy")
        self.assertEqual(istasyon["istasyonId"], 93409)
        self.assertTrue(any(c.get("ilce") == "kadikoy" for c in session.calls))

    def test_mgmde_gercekten_olmayan_ilce_icin_durust_hata_verir(self):
        session = _IlceFarkindaSession()
        client = MGMWeather(cache_ttl_seconds=0, timeout=1, retry_total=0)
        client.session = session

        with self.assertRaises(MGMWeatherError) as ctx:
            client.ilce_istasyonu("istanbul", "olmayanilce")
        mesaj = str(ctx.exception)
        # Artık "Kullanılabilir ilçe(ler): X" gibi yanlışlıkla tam liste
        # iddia eden bir ifade yok; sadece varsayılan istasyonu öneriyor.
        self.assertNotIn("Kullanılabilir ilçe(ler)", mesaj)
        self.assertIn("Bakırköy", mesaj)


if __name__ == "__main__":
    unittest.main()
