import threading
import time
import unittest

import requests

from mgm_client import MGMCircuitOpenError, MGMWeather, MGMWeatherError, _tr_normalize


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


if __name__ == "__main__":
    unittest.main()
