CC      = gcc
CFLAGS  = -fPIC -shared -O2 -Wall -Wextra
LDFLAGS = -ldl

all: libnv_sniff.so

libnv_sniff.so: nv_sniff.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f libnv_sniff.so
