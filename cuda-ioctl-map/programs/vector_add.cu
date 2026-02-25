#include <cuda.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

/* PTX for: vector_add(float *A, float *B, float *C)
 * C[tid] = A[tid] + B[tid] for tid < 64 */
static const char ptx_source[] =
    ".version 6.4\n"
    ".target sm_75\n"
    ".address_size 64\n"
    "\n"
    ".visible .entry vector_add(\n"
    "    .param .u64 param_A,\n"
    "    .param .u64 param_B,\n"
    "    .param .u64 param_C\n"
    ")\n"
    "{\n"
    "    .reg .u32 %r0;\n"
    "    .reg .u64 %addr_a, %addr_b, %addr_c, %off;\n"
    "    .reg .f32 %fa, %fb, %fc;\n"
    "    .reg .pred %p;\n"
    "\n"
    "    mov.u32 %r0, %tid.x;\n"
    "    setp.ge.u32 %p, %r0, 64;\n"
    "    @%p bra DONE;\n"
    "\n"
    "    // offset = r0 * 4\n"
    "    mul.wide.u32 %off, %r0, 4;\n"
    "\n"
    "    ld.param.u64 %addr_a, [param_A];\n"
    "    add.u64 %addr_a, %addr_a, %off;\n"
    "    ld.global.f32 %fa, [%addr_a];\n"
    "\n"
    "    ld.param.u64 %addr_b, [param_B];\n"
    "    add.u64 %addr_b, %addr_b, %off;\n"
    "    ld.global.f32 %fb, [%addr_b];\n"
    "\n"
    "    add.f32 %fc, %fa, %fb;\n"
    "\n"
    "    ld.param.u64 %addr_c, [param_C];\n"
    "    add.u64 %addr_c, %addr_c, %off;\n"
    "    st.global.f32 [%addr_c], %fc;\n"
    "\n"
    "DONE:\n"
    "    ret;\n"
    "}\n";

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

    CUmodule mod;
    r = cuModuleLoadData(&mod, ptx_source);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuModuleLoadData FAILED: %s\n", err);
        cuCtxDestroy(ctx);
        return 1;
    }

    CUfunction fn;
    r = cuModuleGetFunction(&fn, mod, "vector_add");
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuModuleGetFunction FAILED: %s\n", err);
        cuModuleUnload(mod);
        cuCtxDestroy(ctx);
        return 1;
    }

    /* Host data */
    float h_A[N], h_B[N], h_C[N];
    for (int i = 0; i < N; i++) {
        h_A[i] = 1.0f;
        h_B[i] = 2.0f;
        h_C[i] = 0.0f;
    }

    /* Device allocations */
    CUdeviceptr d_A, d_B, d_C;
    size_t sz = N * sizeof(float);

    r = cuMemAlloc(&d_A, sz);
    if (r != CUDA_SUCCESS) { printf("cuMemAlloc A FAILED\n"); return 1; }
    r = cuMemAlloc(&d_B, sz);
    if (r != CUDA_SUCCESS) { printf("cuMemAlloc B FAILED\n"); return 1; }
    r = cuMemAlloc(&d_C, sz);
    if (r != CUDA_SUCCESS) { printf("cuMemAlloc C FAILED\n"); return 1; }

    /* HtoD */
    r = cuMemcpyHtoD(d_A, h_A, sz);
    if (r != CUDA_SUCCESS) { printf("cuMemcpyHtoD A FAILED\n"); return 1; }
    r = cuMemcpyHtoD(d_B, h_B, sz);
    if (r != CUDA_SUCCESS) { printf("cuMemcpyHtoD B FAILED\n"); return 1; }

    /* Launch */
    void *args[] = { &d_A, &d_B, &d_C };
    r = cuLaunchKernel(fn, 1,1,1, N,1,1, 0, NULL, args, NULL);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuLaunchKernel FAILED: %s\n", err);
        return 1;
    }
    cuCtxSynchronize();

    /* DtoH */
    r = cuMemcpyDtoH(h_C, d_C, sz);
    if (r != CUDA_SUCCESS) { printf("cuMemcpyDtoH FAILED\n"); return 1; }

    /* Verify */
    int ok = 1;
    for (int i = 0; i < N; i++) {
        if (fabsf(h_C[i] - 3.0f) > 1e-5f) {
            printf("MISMATCH at C[%d] = %f (expected 3.0)\n", i, h_C[i]);
            ok = 0;
            break;
        }
    }

    cuMemFree(d_A);
    cuMemFree(d_B);
    cuMemFree(d_C);
    cuModuleUnload(mod);
    cuCtxDestroy(ctx);

    if (ok) {
        printf("vector_add: all OK — C[i] == 3.0 for all i\n");
        return 0;
    } else {
        printf("vector_add: FAILED\n");
        return 1;
    }
}
