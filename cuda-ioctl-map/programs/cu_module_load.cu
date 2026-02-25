#include <cuda.h>
#include <stdio.h>

static const char ptx_source[] =
    ".version 6.4\n"
    ".target sm_75\n"
    ".address_size 64\n"
    ".visible .entry null_kernel() { ret; }\n";

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

    CUmodule mod;
    r = cuModuleLoadData(&mod, ptx_source);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuModuleLoadData FAILED: %s\n", err);
        cuCtxDestroy(ctx);
        return 1;
    }
    printf("cuModuleLoadData OK\n");

    r = cuModuleUnload(mod);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuModuleUnload FAILED: %s\n", err);
    }

    cuCtxDestroy(ctx);
    printf("cu_module_load: all OK\n");
    return 0;
}
