import time
import unittest

from mgm_client import MGMWeather, _tr_normalize


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


if __name__ == "__main__":
    unittest.main()
