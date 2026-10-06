"""Return completed image allocations to the OS before handing off the slot."""
import ctypes
import gc
import sys


def release_image_memory():
    gc.collect()
    if sys.platform == "linux":
        # Pillow/OpenCV allocations can remain in glibc arenas after objects
        # close. The API and worker must not each retain that high-water mark.
        try:
            ctypes.CDLL(None).malloc_trim(0)
        except (AttributeError, OSError):
            pass  # Standalone installations may use a different allocator.
