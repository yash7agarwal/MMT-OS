# MMT-OS UAT System — Cloud Docker image
#
# This image runs the Telegram bot + supports headless Android emulator UAT.
# For emulator support the host must expose /dev/kvm (Linux KVM acceleration).
#
# Build:  docker build -t mmt-os .
# Run:    docker run --env-file .env mmt-os

FROM ubuntu:22.04

# ── System deps ──────────────────────────────────────────────────────────────
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3-pip \
    openjdk-17-jdk-headless \
    wget unzip curl git \
    # ADB deps
    adb \
    # Android emulator deps (KVM-accelerated on Linux)
    libvirt-clients qemu-kvm \
    # Image processing
    libglib2.0-0 libsm6 libxext6 libxrender-dev libgl1 \
    && rm -rf /var/lib/apt/lists/*

# ── Android SDK ───────────────────────────────────────────────────────────────
ENV ANDROID_HOME=/opt/android-sdk
ENV PATH="${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/emulator:${PATH}"

RUN mkdir -p ${ANDROID_HOME}/cmdline-tools && \
    wget -q https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip \
         -O /tmp/cmdline-tools.zip && \
    unzip -q /tmp/cmdline-tools.zip -d ${ANDROID_HOME}/cmdline-tools && \
    mv ${ANDROID_HOME}/cmdline-tools/cmdline-tools ${ANDROID_HOME}/cmdline-tools/latest && \
    rm /tmp/cmdline-tools.zip

# Accept licenses + install SDK components
RUN yes | sdkmanager --licenses > /dev/null 2>&1 && \
    sdkmanager \
        "platform-tools" \
        "emulator" \
        "system-images;android-34;google_apis;x86_64" \
        "platforms;android-34"

# Create AVD (x86_64 for cloud — better performance than arm64 under KVM)
RUN echo "no" | avdmanager create avd \
        --name mmt_test \
        --package "system-images;android-34;google_apis;x86_64" \
        --device "pixel_6" \
        --force

# ── Python app ────────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN python3.11 -m pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure persistent dirs exist
RUN mkdir -p apks reports memory .tmp/evidence

# ── Entrypoint ────────────────────────────────────────────────────────────────
# Default: run the Telegram bot (headless, always-on)
CMD ["python3.11", "telegram_bot/run_bot.py"]
