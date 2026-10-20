FROM alpine:3.20 AS builder

RUN apk add --no-cache make wget bzip2 gcc musl-dev linux-headers perl

# Install musl cross-compiler for ARM
RUN wget -q https://musl.cc/arm-linux-musleabi-cross.tgz -O /tmp/toolchain.tgz && \
    tar xf /tmp/toolchain.tgz -C /opt && \
    rm /tmp/toolchain.tgz

ENV PATH="/opt/arm-linux-musleabi-cross/bin:$PATH"

WORKDIR /build
RUN wget -q https://busybox.net/downloads/busybox-1.38.0.tar.bz2 && \
    echo "34f9ea6ff8636f2c9241153b9114eefa9e65674a45318ae1ef95bb5f31c53bb2  busybox-1.38.0.tar.bz2" | sha256sum -c - && \
    tar xf busybox-1.38.0.tar.bz2

WORKDIR /build/busybox-1.38.0
RUN make CROSS_COMPILE=arm-linux-musleabi- defconfig && \
    sed -i 's/# CONFIG_STATIC is not set/CONFIG_STATIC=y/' .config && \
    sed -i 's/CONFIG_FEATURE_HAVE_RPC=y/# CONFIG_FEATURE_HAVE_RPC is not set/' .config && \
    sed -i 's/CONFIG_FEATURE_INETD_RPC=y/# CONFIG_FEATURE_INETD_RPC is not set/' .config && \
    for o in FLASH_ERASEALL FLASHCP FLASH_LOCK FLASH_UNLOCK; do \
      sed -i "s/# CONFIG_$o is not set/CONFIG_$o=y/" .config; done && \
    yes '' | make CROSS_COMPILE=arm-linux-musleabi- oldconfig >/dev/null && \
    grep -E '^CONFIG_(FLASH_ERASEALL|FLASHCP|FLASH_LOCK|FLASH_UNLOCK)=y' .config && \
    make CROSS_COMPILE=arm-linux-musleabi- -j$(nproc) && \
    cp busybox /out_busybox

WORKDIR /build
RUN wget -q https://matt.ucc.asn.au/dropbear/releases/dropbear-2026.94.tar.bz2 && \
    echo "e098034a843699200c8c977a991fff73159735bf795d5f72ef672c41a6b1ae81  dropbear-2026.94.tar.bz2" | sha256sum -c - && \
    tar xf dropbear-2026.94.tar.bz2
WORKDIR /build/dropbear-2026.94
RUN ./configure --host=arm-linux-musleabi --enable-static --disable-zlib \
      --disable-lastlog --disable-utmp --disable-utmpx --disable-wtmp --disable-wtmpx >/dev/null && \
    make -j$(nproc) PROGRAMS="dropbear dropbearkey scp" MULTI=1 STATIC=1 && \
    arm-linux-musleabi-strip dropbearmulti && \
    cp dropbearmulti /out_dropbearmulti

FROM scratch
COPY --from=builder /out_busybox /busybox
COPY --from=builder /out_dropbearmulti /dropbearmulti
