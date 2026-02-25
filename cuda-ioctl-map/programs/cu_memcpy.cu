#include <cuda.h>
#include <stdio.h>
#include <string.h>

static const char ptx_source[] =
    ".version 6.4\n"
    ".target sm_75\n"
    ".address_size 64\n"
    ".visible .entry null_kernel() { ret; }\n";

#define N 64

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

    /* Load module + get function (null kernel) */
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

    /* Allocate GPU memory */
    CUdeviceptr d_buf;
    r = cuMemAlloc(&d_buf, N * sizeof(float));
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuMemAlloc FAILED: %s\n", err);
        cuModuleUnload(mod);
        cuCtxDestroy(ctx);
        return 1;
    }

    /* Zero the GPU buffer */
    r = cuMemsetD8(d_buf, 0, N * sizeof(float));
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuMemsetD8 FAILED: %s\n", err);
        cuMemFree(d_buf);
        cuModuleUnload(mod);
        cuCtxDestroy(ctx);
        return 1;
    }

    /* Launch null kernel (no writes to d_buf) */
    r = cuLaunchKernel(fn, 1,1,1, 1,1,1, 0, NULL, NULL, NULL);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuLaunchKernel FAILED: %s\n", err);
        cuMemFree(d_buf);
        cuModuleUnload(mod);
        cuCtxDestroy(ctx);
        return 1;
    }
    cuCtxSynchronize();

    /* Copy back to host */
    float h_buf[N];
    memset(h_buf, 0xFF, sizeof(h_buf));  /* fill with non-zero to detect failure */

    r = cuMemcpyDtoH(h_buf, d_buf, N * sizeof(float));
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuMemcpyDtoH FAILED: %s\n", err);
        cuMemFree(d_buf);
        cuModuleUnload(mod);
        cuCtxDestroy(ctx);
        return 1;
    }

    /* Verify all zeros */
    int ok = 1;
    for (int i = 0; i < N; i++) {
        if (h_buf[i] != 0.0f) {
            printf("MISMATCH at h_buf[%d] = %f (expected 0.0)\n", i, h_buf[i]);
            ok = 0;
            break;
        }
    }

    cuMemFree(d_buf);
    cuModuleUnload(mod);
    cuCtxDestroy(ctx);

    if (ok) {
        printf("cu_memcpy: all OK — buffer is all zeros\n");
        return 0;
    } else {
        printf("cu_memcpy: FAILED — buffer mismatch\n");
        return 1;
    }
}
