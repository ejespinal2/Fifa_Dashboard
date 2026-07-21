"""GPU use for EasyOCR is opt-in (FIFA_OCR_GPU=1), not auto-detected.
Auto-detecting and using any CUDA GPU found made a real 40-image run
measurably slower, not faster: every call here OCRs one small, independent
crop at a time (never batched), so a GPU's fixed per-call overhead loses
to plain CPU at that size. Default is CPU; nothing implicitly turns GPU
on."""

from fifa_analytics.ocr.extract import _gpu_available


def test_default_is_cpu(monkeypatch):
    monkeypatch.delenv("FIFA_OCR_GPU", raising=False)
    assert _gpu_available() is False


def test_env_var_opts_into_gpu(monkeypatch):
    monkeypatch.setenv("FIFA_OCR_GPU", "1")
    assert _gpu_available() is True


def test_any_other_value_stays_cpu(monkeypatch):
    monkeypatch.setenv("FIFA_OCR_GPU", "0")
    assert _gpu_available() is False
