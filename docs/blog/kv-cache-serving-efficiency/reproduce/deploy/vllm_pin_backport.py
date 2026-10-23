#!/usr/bin/env python3
"""Backport of vLLM PR #51081 ("Register the offload region in chunks", commit 40b40d1) to vLLM v0.28.0.

Run once at container start, before vLLM launches. It edits the installed vLLM in place:
  * gpu_worker.pin_mmap_region: register the shared CPU offload region in row-aligned chunks of at most
    64 GiB (upstream MAX_HOST_REGISTER_CHUNK_BYTES) instead of one cudaHostRegister of the whole region,
    and clear the CUDA error after a failed registration.
  * shared_offload_region.cleanup: unregister every chunk that was registered.
One deliberate difference from upstream: if any chunk fails, upstream rolls back and runs on pageable
memory (with a DMA copy path added in the same PR); this backport raises instead, so a benchmark can
never run silently on the slow unpinned path.
Refuses to run unless the installed vLLM is 0.28.0 and the original code is found verbatim."""
import importlib.util, pathlib, sys, re

def path_of(mod):
    spec = importlib.util.find_spec(mod)
    return pathlib.Path(spec.origin)

import vllm
if not vllm.__version__.startswith("0.28.0"):
    sys.exit(f"vllm_pin_backport: expected vLLM 0.28.0, found {vllm.__version__}; refusing to patch")

gw = path_of("vllm.v1.kv_offload.cpu.gpu_worker")
src = gw.read_text()
if "PIN_BACKPORT_51081" in src:
    print("vllm_pin_backport: gpu_worker already patched")
else:
    m = re.search(r"\ndef pin_mmap_region\(region: SharedOffloadRegion\) -> None:\n.*?\n        region\.is_pinned = True\n", src, re.S)
    if not m:
        sys.exit("vllm_pin_backport: original pin_mmap_region not found verbatim; refusing to patch")
    new = '''
# PIN_BACKPORT_51081: backport of vLLM PR #51081 (chunked registration) to v0.28.0.
# Bound registration size to avoid driver limits on large host allocations.
MAX_HOST_REGISTER_CHUNK_BYTES = 64 * 1024**3


def _cudart_get_last_error() -> None:
    """Clear the pending CUDA runtime error left by a failed cudaHostRegister."""
    import ctypes
    from vllm.utils.system_utils import find_loaded_library
    lib = ctypes.CDLL(find_loaded_library("libcudart"))
    lib.cudaGetLastError.restype = ctypes.c_int
    lib.cudaGetLastError()


def pin_mmap_region(region: SharedOffloadRegion) -> None:
    """Register row-aligned chunks of at most MAX_HOST_REGISTER_CHUNK_BYTES."""
    if not current_platform.is_cuda_alike():
        logger.info(
            "Skipping mmap host registration on %s; cudaHostRegister is only "
            "available on CUDA/ROCm.",
            current_platform.device_name,
        )
        return

    rank = region.rank
    base_ptr = region._base.data_ptr()
    total_size = region.total_size_bytes
    # Chunks end on block-row boundaries, which are page aligned, so no single
    # block transfer straddles two registrations.
    rows_per_chunk = max(MAX_HOST_REGISTER_CHUNK_BYTES // region._row_stride, 1)
    chunk_size = rows_per_chunk * region._row_stride
    addresses: list[int] = []
    cudart = torch.cuda.cudart()
    for offset in range(0, total_size, chunk_size):
        address = base_ptr + offset
        size = min(chunk_size, total_size - offset)
        result = cudart.cudaHostRegister(address, size, 0)
        if result.value == 0:
            addresses.append(address)
            continue
        _cudart_get_last_error()
        for registered in reversed(addresses):
            cudart.cudaHostUnregister(registered)
        _cudart_get_last_error()
        raise RuntimeError(
            f"cudaHostRegister failed for rank={rank} at {offset / 1e9:.2f} of "
            f"{total_size / 1e9:.2f} GB (code={result.value}); refusing to run the "
            "CPU offload tier on pageable memory (PIN_BACKPORT_51081)"
        )
    region.pinned_addresses = addresses
    region.is_pinned = True
    logger.info(
        "Host-registered CPU offload region rank=%d %.2f GB in %d chunk(s) (PIN_BACKPORT_51081)",
        rank,
        total_size / 1e9,
        len(addresses),
    )
'''
    gw.write_text(src[: m.start()] + "\n" + new + src[m.end():])
    print(f"vllm_pin_backport: patched {gw}")

so = path_of("vllm.v1.kv_offload.cpu.shared_offload_region")
src = so.read_text()
if "PIN_BACKPORT_51081" in src:
    print("vllm_pin_backport: shared_offload_region already patched")
else:
    old = """            if current_platform.is_cuda_alike():
                base_ptr = self._base.data_ptr()
                result = torch.cuda.cudart().cudaHostUnregister(base_ptr)
                if result.value != 0:
                    logger.warning(
                        "cudaHostUnregister failed for rank=%d (code=%d)",
                        self.rank,
                        result,
                    )
"""
    if old not in src:
        sys.exit("vllm_pin_backport: original cleanup not found verbatim; refusing to patch")
    new = """            if current_platform.is_cuda_alike():
                # PIN_BACKPORT_51081: unregister every registered chunk.
                for base_ptr in reversed(getattr(self, "pinned_addresses", [self._base.data_ptr()])):
                    result = torch.cuda.cudart().cudaHostUnregister(base_ptr)
                    if result.value != 0:
                        logger.warning(
                            "cudaHostUnregister failed for rank=%d (code=%d)",
                            self.rank,
                            result,
                        )
"""
    so.write_text(src.replace(old, new))
    print(f"vllm_pin_backport: patched {so}")
