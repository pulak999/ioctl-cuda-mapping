#include <cuda.h>
#include <stdio.h>

int main() {
    CUresult r = cuInit(0);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("FAILED: %s\n", err); return 1;
    }
    printf("cuInit OK\n");
    return 0;
}
