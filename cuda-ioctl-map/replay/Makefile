CC     = gcc
CFLAGS = -O2 -Wall -Wextra -I.

all: replay

replay: replay.c handle_map.h
	$(CC) $(CFLAGS) -o $@ replay.c

clean:
	rm -f replay
