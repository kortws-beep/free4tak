"""
whisper_loader.py — faster-whisper 모델 로더 (GPU 있으면 GPU, 없으면 CPU)

2026-11-09: 새 서버(RTX 3060 Ti)에서 Whisper가 device="cpu" 고정이라 GPU를
전혀 안 쓰고 있었음 → 공용 로더로 바꿈.

- CUDA 라이브러리(cuBLAS·cuDNN)는 pip 패키지(nvidia-cublas-cu12,
  nvidia-cudnn-cu12)로 venv 안에 깔려 있음. 시스템 LD_LIBRARY_PATH에 없으니
  ctranslate2가 찾을 수 있게 미리 ctypes로 올려둠.
- GPU 로드가 돼도 cuDNN 문제는 실제 추론 때 터지는 경우가 있어서,
  1초 무음으로 한 번 돌려보고 실패하면 CPU로 내려감.
- 환경변수 WHISPER_DEVICE=cpu 로 강제 CPU 가능 (GPU를 다른 일에 비울 때).
"""
import ctypes
import glob
import os
import sys


def _preload_cuda_libs():
    """venv의 nvidia-* pip 패키지 .so 를 미리 로드 (실패해도 조용히 넘어감)."""
    for sp in sys.path:
        base = os.path.join(sp, "nvidia")
        if not os.path.isdir(base):
            continue
        for pattern in ("cublas/lib/libcublasLt.so.*", "cublas/lib/libcublas.so.*",
                        "cudnn/lib/libcudnn*.so.*"):
            for so in sorted(glob.glob(os.path.join(base, pattern))):
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass
        return


def load_whisper(model_size="small", log=print):
    """(model, device_str) 반환. GPU 실패 시 자동으로 CPU int8."""
    from faster_whisper import WhisperModel

    want = os.getenv("WHISPER_DEVICE", "auto").lower()
    if want != "cpu":
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                _preload_cuda_libs()
                model = WhisperModel(model_size, device="cuda", compute_type="float16")
                import numpy as np
                segs, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="ko")
                list(segs)  # 실제 추론까지 해봐야 cuDNN 문제가 드러남
                return model, "GPU(cuda float16)"
            log("ℹ️ CUDA 장치 없음 → CPU로 Whisper 로드")
        except Exception as e:
            log(f"⚠️ Whisper GPU 로드 실패 → CPU로 전환: {e}")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    return model, "CPU(int8)"
