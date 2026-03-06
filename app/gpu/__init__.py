"""GPU utility module — gracefully optional.

All functions in this module are no-ops or return None when CUDA is unavailable.
Import safety: this module may be imported on CPU-only machines (e.g., Mac).
"""


def is_gpu_available() -> bool:
    """Return True if CUDA GPU is available."""
    try:
        import torch

        return torch.cuda.is_available()
    except ImportError:
        return False


def gpu_device_info() -> dict:
    """Return GPU device info dict, or empty dict if no GPU."""
    if not is_gpu_available():
        return {}
    import torch

    return {
        "count": torch.cuda.device_count(),
        "devices": [
            {
                "index": i,
                "name": torch.cuda.get_device_name(i),
                "memory_gb": round(torch.cuda.get_device_properties(i).total_memory / 1e9, 1),
            }
            for i in range(torch.cuda.device_count())
        ],
    }
