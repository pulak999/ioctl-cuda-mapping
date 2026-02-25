#include <cuda.h>
#include <stdio.h>

int main() {
    CUresult r;

    r = cuInit(0);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuInit FAILED: %s\n", err); return 1;
    }

    CUdevice dev;
    r = cuDeviceGet(&dev, 0);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuDeviceGet FAILED: %s\n", err); return 1;
    }

    CUcontext ctx;
    r = cuCtxCreate(&ctx, 0, dev);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuCtxCreate FAILED: %s\n", err); return 1;
    }

    CUdeviceptr ptr;
    r = cuMemAlloc(&ptr, 1024);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuMemAlloc FAILED: %s\n", err);
        cuCtxDestroy(ctx);
        return 1;
    }
    printf("cuMemAlloc OK, ptr=0x%llx\n", (unsigned long long)ptr);

    r = cuMemFree(ptr);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuMemFree FAILED: %s\n", err);
    }

    cuCtxDestroy(ctx);
    printf("cu_mem_alloc: all OK\n");
    return 0;
}
