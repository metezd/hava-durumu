import unittest

from mgm_client import MGMWeather, _tr_normalize


class _DummyResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


class _CountingSession:
    def __init__(self, payload):
        self.calls = 0
        self.payload = payload

    def get(self, url, headers, params, timeout):
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


if __name__ == "__main__":
    unittest.main()
