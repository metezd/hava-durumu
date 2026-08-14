<h1 align="center">
  <span style="font-weight: 600;">Hava durumu</span>
</h1>

<p align="center">
  Karakter sorunu yaşanırsa il/ilçe adını Türkçe karakter kullanmadan yazın (<code>Istanbul</code>, <code>Uskudar</code>).
</p>
<p align="center">
  Resmi bir API değildir. Sadece veriyi <a href="https://www.mgm.gov.tr">mgm.gov.tr</a> sitesinden çeker.
</p>

## Kurulum

```bash
pip install -r requirements.txt
python app.py
```

Sunucu `http://127.0.0.1:5000` üzerinde çalışmaya başlar

| Uç noktalar | Açıklama |
|---|---|
| `GET /istasyonlar/<il>` | O ildeki istasyonları (ilçeleri) listeler |
| `GET /guncel/<il>?ilce=<ilce>` | Anlık hava durumu |
| `GET /tahmin/<il>?ilce=<ilce>` | 5 günlük tahmin |
| `GET /hava-durumu/<il>?ilce=<ilce>` | Güncel durum + tahmin |

`ilce` parametresi zorunlu değil, verilmezse ilin ilk istasyonu kullanılır.

## Örnek

```bash
curl "http://127.0.0.1:5000/hava-durumu/Istanbul?ilce=Bakirkoy"
```

```json
{
  "basarili": true,
  "veri": {
    "il": "İSTANBUL",
    "ilce": "Bakırköy",
    "guncel": {
      "sicaklik": 29.4,
      "durum": "Açık"
    },
    "tahmin": [
      {
        "tarih": "2026-08-14",
        "enDusuk": 23,
        "enYuksek": 31,
        "durum": "Az Bulutlu"
      }
    ]
  }
}
```

## Hata örneği

```json
{ "basarili": false, "hata": "'X' ilinde 'Y' ilçesi bulunamadı." }
```