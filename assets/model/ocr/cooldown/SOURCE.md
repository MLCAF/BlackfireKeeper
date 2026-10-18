# Local OCR model provenance

Copied unchanged from the user's existing reference package:
`AALC/_internal/rapidocr/models/`

No models or images are downloaded or sent to an external service at runtime.
Recognition uses the existing MaaFramework 5.13.1 native OCR backend (only_rec).
The detection model is bundled because this Maa build initializes the combined OCR object even for only_rec.

| Deployed file | Source file | Bytes | SHA256 |
|---|---|---:|---|
| rec.onnx | ch_PP-OCRv4_rec_infer.onnx | 10857958 | 48FC40F24F6D2A207A2B1091D3437EB3CC3EB6B676DC3EF9C37384005483683B |
| det.onnx | ch_PP-OCRv4_det_infer.onnx | 4745517 | D2A7720D45A54257208B1E13E36A8479894CB74155A5EFE29462512D42F49DA9 |
| keys.txt | ppocr_keys_v1.txt | 26249 | 28B2362AD4AB2DC38769AA72FEB535E3A9DDB3FD2A7585A05920E6393B1DC7F7 |
