import unittest

import app as app_module
from mgm_client import MGMWeatherError


class FakeMGM:
    def __init__(
        self,
        should_fail_health: bool = False,
        redis_durum: dict[str, str] | None = None,
        circuit_breaker_durum: dict[str, str] | None = None,
    ):
        self.should_fail_health = should_fail_health
        self.redis_durum = redis_durum if redis_durum is not None else {"durum": "skip"}
        self.circuit_breaker_durum = (
            circuit_breaker_durum if circuit_breaker_durum is not None else {"durum": "kapali"}
        )

    def il_istasyonlari(self, il: str):
        if self.should_fail_health:
            raise MGMWeatherError("MGM servisine bağlanılamadı")
        return [{"il": il, "ilce": "Bakırköy", "merkezId": 93401}]

    def redis_saglik_ozeti(self):
        return dict(self.redis_durum)

    def circuit_breaker_saglik_ozeti(self):
        return dict(self.circuit_breaker_durum)

    def ilce_istasyonu(self, il: str, ilce: str | None = None):
        return {"il": il, "ilce": ilce or "Bakırköy", "merkezId": 93401}

    def hava_durumu(self, il: str, ilce: str | None = None):
        return {
            "il": il,
            "ilce": ilce or "Bakırköy",
            "guncel": {"sicaklik": 27.1, "durum": "Çok Bulutlu"},
            "tahmin": [{"tarih": "2026-08-14", "durum": "Parçalı Bulutlu"}],
        }

    def saatlik_tahmin(self, istasyon_id: int | str):
        return [{"gun": "2026-08-14", "saat": "12:00", "sicaklik": 27.0}]


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
        self.assertEqual(data["redis"], "skip")

    def test_health_deep_ok(self):
        resp = self.client.get("/health?deep=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["basarili"])
        self.assertEqual(data["mgm"], "ok")

    def test_health_deep_ok_redis_ok(self):
        app_module.mgm = FakeMGM(redis_durum={"durum": "ok"})
        resp = self.client.get("/health?deep=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["basarili"])
        self.assertEqual(data["redis"], "ok")

    def test_health_deep_redis_hata_503(self):
        app_module.mgm = FakeMGM(redis_durum={"durum": "hata", "hata": "Down"})
        resp = self.client.get("/health?deep=1")
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertFalse(data["basarili"])
        self.assertEqual(data["durum"], "degraded")
        self.assertEqual(data["redis"], "hata")

    def test_health_deep_fail_503(self):
        app_module.mgm = FakeMGM(should_fail_health=True)
        resp = self.client.get("/health?deep=1")
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertFalse(data["basarili"])
        self.assertEqual(data["durum"], "degraded")

    def test_health_shallow_circuit_breaker_alani_doner(self):
        app_module.mgm = FakeMGM(circuit_breaker_durum={"durum": "acik"})
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["circuit_breaker"], "acik")

    def test_health_deep_circuit_breaker_alani_doner(self):
        app_module.mgm = FakeMGM(circuit_breaker_durum={"durum": "yari-acik"})
        resp = self.client.get("/health?deep=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["circuit_breaker"], "yari-acik")

    def test_hava_durumu_endpoint(self):
        resp = self.client.get("/hava-durumu/Istanbul?ilce=Bakirkoy")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["basarili"])
        self.assertEqual(data["veri"]["ilce"], "Bakirkoy")

    def test_saatlik_endpoint(self):
        resp = self.client.get("/saatlik/Istanbul?ilce=Bakirkoy")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["basarili"])
        self.assertEqual(data["veri"], [{"gun": "2026-08-14", "saat": "12:00", "sicaklik": 27.0}])

    def test_cors_ve_guvenlik_headerlari(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(resp.headers.get("Referrer-Policy"), "no-referrer")

    def test_rate_limit_429(self):
        app_module.RATE_LIMIT_MAX = 1
        app_module.RATE_LIMIT_BUCKETS.clear()

        first = self.client.get("/istasyonlar/Istanbul")
        second = self.client.get("/istasyonlar/Istanbul")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

        app_module.RATE_LIMIT_MAX = 60
        app_module.RATE_LIMIT_BUCKETS.clear()


if __name__ == "__main__":
    unittest.main()
