import unittest

import app as app_module
from mgm_client import MGMWeatherError


class FakeMGM:
    def __init__(self, should_fail_health: bool = False):
        self.should_fail_health = should_fail_health

    def il_istasyonlari(self, il: str):
        if self.should_fail_health:
            raise MGMWeatherError("MGM servisine bağlanılamadı")
        return [{"il": il, "ilce": "Bakırköy", "merkezId": 93401}]

    def hava_durumu(self, il: str, ilce: str | None = None):
        return {
            "il": il,
            "ilce": ilce or "Bakırköy",
            "guncel": {"sicaklik": 27.1, "durum": "Çok Bulutlu"},
            "tahmin": [{"tarih": "2026-08-14", "durum": "Parçalı Bulutlu"}],
        }


class TestAppIntegration(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.original_mgm = app_module.mgm
        app_module.mgm = FakeMGM()

    def tearDown(self):
        app_module.mgm = self.original_mgm

    def test_health_shallow_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["basarili"])
        self.assertEqual(data["mgm"], "skip")

    def test_health_deep_ok(self):
        resp = self.client.get("/health?deep=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["basarili"])
        self.assertEqual(data["mgm"], "ok")

    def test_health_deep_fail_503(self):
        app_module.mgm = FakeMGM(should_fail_health=True)
        resp = self.client.get("/health?deep=1")
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertFalse(data["basarili"])
        self.assertEqual(data["durum"], "degraded")

    def test_hava_durumu_endpoint(self):
        resp = self.client.get("/hava-durumu/Istanbul?ilce=Bakirkoy")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["basarili"])
        self.assertEqual(data["veri"]["ilce"], "Bakirkoy")


if __name__ == "__main__":
    unittest.main()
