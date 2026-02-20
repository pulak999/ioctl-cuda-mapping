#include <cuda.h>
#include <stdio.h>

int main() {
    cuInit(0);
    CUdevice dev;
    cuDeviceGet(&dev, 0);
    CUcontext ctx;
    CUresult r = cuCtxCreate(&ctx, 0, dev);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("FAILED create: %s\n", err); return 1;
    }
    r = cuCtxDestroy(ctx);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("FAILED destroy: %s\n", err); return 1;
    }
    printf("cuCtxDestroy OK\n");
    return 0;
}
