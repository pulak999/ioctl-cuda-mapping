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

    CUfunction fn;
    r = cuModuleGetFunction(&fn, mod, "null_kernel");
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuModuleGetFunction FAILED: %s\n", err);
        cuModuleUnload(mod);
        cuCtxDestroy(ctx);
        return 1;
    }
    printf("cuModuleGetFunction OK\n");

    r = cuLaunchKernel(fn,
                       1, 1, 1,   /* grid */
                       1, 1, 1,   /* block */
                       0, NULL,   /* shared mem, stream */
                       NULL, NULL);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuLaunchKernel FAILED: %s\n", err);
        cuModuleUnload(mod);
        cuCtxDestroy(ctx);
        return 1;
    }
    printf("cuLaunchKernel OK\n");

    r = cuCtxSynchronize();
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuCtxSynchronize FAILED: %s\n", err);
        cuModuleUnload(mod);
        cuCtxDestroy(ctx);
        return 1;
    }
    printf("cuCtxSynchronize OK\n");

    cuModuleUnload(mod);
    cuCtxDestroy(ctx);
    printf("cu_launch_null: all OK\n");
    return 0;
}
