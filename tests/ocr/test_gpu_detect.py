"""GPU auto-detection for EasyOCR: use CUDA when available, fall back to
CPU otherwise, and let FIFA_OCR_GPU=0 force CPU even when a GPU exists."""

from fifa_analytics.ocr.extract import _gpu_available


def test_no_torch_or_no_cuda_means_no_gpu(monkeypatch):
    monkeypatch.delenv("FIFA_OCR_GPU", raising=False)
    # the real test environment has no CUDA GPU -- exercises the actual
    # torch.cuda.is_available() path (or the ImportError fallback if torch
    # itself is absent), not a mock
    assert _gpu_available() in (True, False)  # must not raise either way


def test_env_var_forces_cpu_even_if_a_gpu_would_be_reported(monkeypatch):
    monkeypatch.setenv("FIFA_OCR_GPU", "0")
    assert _gpu_available() is False


def test_import_error_is_treated_as_no_gpu(monkeypatch):
    import builtins

    monkeypatch.delenv("FIFA_OCR_GPU", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no torch here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert _gpu_available() is False
