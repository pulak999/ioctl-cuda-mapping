#include <cuda.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

/*
 * PTX for naive matmul: C[row][col] = sum_k A[row][k] * B[k][col]
 *
 * Grid: (N/1, N/1, 1)  Block: (1, 1, 1) — one thread per output element.
 * Each thread computes one element of C.
 *
 * Parameters: A, B, C (device pointers), N (u32)
 *
 * Using a very simple approach: each thread loops over K.
 */
static const char ptx_source[] =
    ".version 6.4\n"
    ".target sm_75\n"
    ".address_size 64\n"
    "\n"
    ".visible .entry matmul(\n"
    "    .param .u64 param_A,\n"
    "    .param .u64 param_B,\n"
    "    .param .u64 param_C,\n"
    "    .param .u32 param_N\n"
    ")\n"
    "{\n"
    "    .reg .u32 %row, %col, %n, %k;\n"
    "    .reg .u64 %pA, %pB, %pC, %off, %base_a, %base_b, %base_c;\n"
    "    .reg .f32 %sum, %va, %vb;\n"
    "    .reg .pred %pk, %pbounds;\n"
    "    .reg .u32 %tmp32;\n"
    "    .reg .u64 %tmp64;\n"
    "\n"
    "    // row = blockIdx.y * blockDim.y + threadIdx.y\n"
    "    mov.u32 %row, %ctaid.y;\n"
    "    // col = blockIdx.x * blockDim.x + threadIdx.x\n"
    "    mov.u32 %col, %ctaid.x;\n"
    "\n"
    "    ld.param.u32 %n, [param_N];\n"
    "\n"
    "    // bounds check\n"
    "    setp.ge.u32 %pbounds, %row, %n;\n"
    "    @%pbounds bra DONE;\n"
    "    setp.ge.u32 %pbounds, %col, %n;\n"
    "    @%pbounds bra DONE;\n"
    "\n"
    "    ld.param.u64 %base_a, [param_A];\n"
    "    ld.param.u64 %base_b, [param_B];\n"
    "    ld.param.u64 %base_c, [param_C];\n"
    "\n"
    "    mov.f32 %sum, 0f00000000;\n"  /* 0.0f */
    "    mov.u32 %k, 0;\n"
    "\n"
    "LOOP:\n"
    "    setp.ge.u32 %pk, %k, %n;\n"
    "    @%pk bra STORE;\n"
    "\n"
    "    // A[row * N + k]: offset = (row * N + k) * 4\n"
    "    mul.lo.u32 %tmp32, %row, %n;\n"
    "    add.u32 %tmp32, %tmp32, %k;\n"
    "    mul.wide.u32 %off, %tmp32, 4;\n"
    "    add.u64 %pA, %base_a, %off;\n"
    "    ld.global.f32 %va, [%pA];\n"
    "\n"
    "    // B[k * N + col]: offset = (k * N + col) * 4\n"
    "    mul.lo.u32 %tmp32, %k, %n;\n"
    "    add.u32 %tmp32, %tmp32, %col;\n"
    "    mul.wide.u32 %off, %tmp32, 4;\n"
    "    add.u64 %pB, %base_b, %off;\n"
    "    ld.global.f32 %vb, [%pB];\n"
    "\n"
    "    fma.rn.f32 %sum, %va, %vb, %sum;\n"
    "\n"
    "    add.u32 %k, %k, 1;\n"
    "    bra LOOP;\n"
    "\n"
    "STORE:\n"
    "    // C[row * N + col] = sum\n"
    "    mul.lo.u32 %tmp32, %row, %n;\n"
    "    add.u32 %tmp32, %tmp32, %col;\n"
    "    mul.wide.u32 %off, %tmp32, 4;\n"
    "    add.u64 %pC, %base_c, %off;\n"
    "    st.global.f32 [%pC], %sum;\n"
    "\n"
    "DONE:\n"
    "    ret;\n"
    "}\n";

#define N 128

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
    r = cuModuleGetFunction(&fn, mod, "matmul");
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuModuleGetFunction FAILED: %s\n", err);
        cuModuleUnload(mod);
        cuCtxDestroy(ctx);
        return 1;
    }

    /* Host data: A = identity, B = ones → C should = ones * 128? No.
     * A = identity 128x128, B = all ones → C[i][j] = sum_k I[i][k]*1 = 1
     * Actually C = A * B = I * B = B = all ones.
     * Let's use: A = all 1.0, B = all 1.0 → C[i][j] = N = 128.0 */
    size_t sz = N * N * sizeof(float);
    float *h_A = (float *)malloc(sz);
    float *h_B = (float *)malloc(sz);
    float *h_C = (float *)malloc(sz);

    for (int i = 0; i < N * N; i++) {
        h_A[i] = 1.0f;
        h_B[i] = 1.0f;
        h_C[i] = 0.0f;
    }

    CUdeviceptr d_A, d_B, d_C;
    r = cuMemAlloc(&d_A, sz);
    if (r != CUDA_SUCCESS) { printf("cuMemAlloc A FAILED\n"); return 1; }
    r = cuMemAlloc(&d_B, sz);
    if (r != CUDA_SUCCESS) { printf("cuMemAlloc B FAILED\n"); return 1; }
    r = cuMemAlloc(&d_C, sz);
    if (r != CUDA_SUCCESS) { printf("cuMemAlloc C FAILED\n"); return 1; }

    r = cuMemcpyHtoD(d_A, h_A, sz);
    if (r != CUDA_SUCCESS) { printf("cuMemcpyHtoD A FAILED\n"); return 1; }
    r = cuMemcpyHtoD(d_B, h_B, sz);
    if (r != CUDA_SUCCESS) { printf("cuMemcpyHtoD B FAILED\n"); return 1; }

    /* Launch: N×N grid of 1×1 blocks (one thread per output element) */
    unsigned int n_val = N;
    void *args[] = { &d_A, &d_B, &d_C, &n_val };
    r = cuLaunchKernel(fn,
                       N, N, 1,   /* grid: N blocks in x, N blocks in y */
                       1, 1, 1,   /* block: 1 thread */
                       0, NULL,
                       args, NULL);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuLaunchKernel FAILED: %s\n", err);
        return 1;
    }

    r = cuCtxSynchronize();
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("cuCtxSynchronize FAILED: %s\n", err);
        return 1;
    }

    r = cuMemcpyDtoH(h_C, d_C, sz);
    if (r != CUDA_SUCCESS) { printf("cuMemcpyDtoH FAILED\n"); return 1; }

    /* Verify: C[i][j] should be N (= 128.0) for all i,j */
    int ok = 1;
    float expected = (float)N;
    for (int i = 0; i < N * N; i++) {
        if (fabsf(h_C[i] - expected) > 0.5f) {
            printf("MISMATCH at C[%d/%d][%d/%d] = %f (expected %f)\n",
                   i / N, N, i % N, N, h_C[i], expected);
            ok = 0;
            break;
        }
    }

    cuMemFree(d_A);
    cuMemFree(d_B);
    cuMemFree(d_C);
    cuModuleUnload(mod);
    cuCtxDestroy(ctx);
    free(h_A);
    free(h_B);
    free(h_C);

    if (ok) {
        printf("matmul: all OK — C[i][j] == %g for all i,j\n", expected);
        return 0;
    } else {
        printf("matmul: FAILED — verification error\n");
        return 1;
    }
}
