# Build script for all CUDA tracing binaries.
#
# Usage:
#   make              — build all binaries
#   make cu_init      — build a single binary
#   make clean        — remove compiled binaries
#
# nvcc is not in PATH by default on this machine; set NVCC to override.
#   NVCC=/usr/local/cuda-12.6/bin/nvcc make

NVCC      ?= /usr/local/cuda-12.5/bin/nvcc
NVCCFLAGS  = -arch=native -O0 -lcuda

TARGETS = cu_init cu_device_get cu_ctx_create cu_ctx_destroy \
          cu_mem_alloc cu_module_load cu_launch_null cu_memcpy \
          vector_add matmul

.PHONY: all clean

all: $(TARGETS)

# Pattern rule — every binary is built from its .cu source
%: %.cu
	$(NVCC) $(NVCCFLAGS) -o $@ $<

clean:
	rm -f $(TARGETS)
