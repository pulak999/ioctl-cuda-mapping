# PR1-fix: build script for all CUDA tracing binaries.
#
# Usage:
#   make              — build all four binaries
#   make cu_init      — build a single binary
#   make clean        — remove compiled binaries
#
# nvcc is not in PATH by default on this machine; set NVCC to override.
#   NVCC=/usr/local/cuda-12.6/bin/nvcc make

NVCC     ?= /usr/local/cuda-12.5/bin/nvcc
NVCCFLAGS = -arch=native -O0

TARGETS = cu_init cu_device_get cu_ctx_create cu_ctx_destroy

.PHONY: all clean

all: $(TARGETS)

cu_init: cu_init.cu
	$(NVCC) $(NVCCFLAGS) -o $@ $<

cu_device_get: cu_device_get.cu
	$(NVCC) $(NVCCFLAGS) -o $@ $<

cu_ctx_create: cu_ctx_create.cu
	$(NVCC) $(NVCCFLAGS) -o $@ $<

cu_ctx_destroy: cu_ctx_destroy.cu
	$(NVCC) $(NVCCFLAGS) -o $@ $<

clean:
	rm -f $(TARGETS)
