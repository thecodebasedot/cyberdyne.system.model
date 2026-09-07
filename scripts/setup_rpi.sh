#!/usr/bin/env bash
# One-shot setup for a Raspberry Pi OS (Bookworm) brain. Run as the user that will run the robot.
set -euo pipefail
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip espeak-ng libportaudio2 libatlas-base-dev git
sudo usermod -aG dialout,audio,video "$USER"
cd "$(dirname "$0")/.."
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]" pyserial opencv-python-headless vosk sounddevice
# small English model (~40 MB); Bangla: vosk-model-small-bn-0.4 (swap the URL / dir name)
if [ ! -d model ]; then
  curl -L -o /tmp/vosk.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
  python3 -c "import zipfile; zipfile.ZipFile('/tmp/vosk.zip').extractall('.')" && mv vosk-model-small-en-us-0.15 model
fi
cat > robot.toml <<'TOML'
name = "cyberdyne-pi"
[kernel]
mode = "rpi"
[hardware]
port = "/dev/ttyUSB0"
baud = 115200
camera_index = 0
vosk_model = "model"
tts = true
[dashboard]
enabled = true
host = "0.0.0.0"
port = 8080
[learning]
data_dir = "data"
TOML
echo "done. flash firmware/cyberdyne_fw to the Arduino, then: .venv/bin/cyberdyne check -s robot.toml"
echo "service: sudo cp deploy/cyberdyne.service /etc/systemd/system/ && sudo systemctl enable --now cyberdyne"
