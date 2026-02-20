#include <cuda.h>
#include <stdio.h>

int main() {
    cuInit(0);
    CUdevice dev;
    CUresult r = cuDeviceGet(&dev, 0);
    if (r != CUDA_SUCCESS) {
        const char *err; cuGetErrorString(r, &err);
        printf("FAILED: %s\n", err); return 1;
    }
    printf("cuDeviceGet OK, device=%d\n", dev);
    return 0;
}
