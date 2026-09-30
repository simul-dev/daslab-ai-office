"""Optional developer-only public fixtures; no owner recordings or profile writes.

Examples referenced by https://huggingface.co/Xenova/wavlm-base-plus-sv.
The dataset has no explicit repository license: downloaded for local verification,
excluded from Git, and not included in a published/distributed product.
"""
from pathlib import Path
import hashlib
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1] / 'static/voice/fixtures'
REVISION = 'fbe92bd97d48f3ec17779d8d8f2964e1c6bc7634'
FILES = {
    'sv_speaker-1_1.wav': 'c65258289d72485624a5ea06b0defd2c4012e672ed83198a327c3f5fc6a1f42b',
    'sv_speaker-1_2.wav': 'a4e032b319a4ac5933a29a090ad23431f334528564eb381017da2692e56ec45f',
    'sv_speaker-2_1.wav': '20d3a722fecc2090ba5bc90fdfe7e4f3863b3695247b5ffc25f047a7e25d077d',
    'sv_speaker-2_2.wav': '9a2e91feae8d4b39a1c07572b852ccdec38707a0b8b13d3159cf957304a4eaf6',
}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, expected in FILES.items():
        destination = ROOT / name
        if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
            continue
        url = f'https://huggingface.co/datasets/Xenova/transformers.js-docs/resolve/{REVISION}/{name}'
        with urlopen(url, timeout=30) as response:
            data = response.read(1000000)
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError('Test fixture checksum mismatch: ' + name)
        destination.write_bytes(data)
    print('Four public speaker fixtures verified. These are not the owner and do not enroll anyone.')


if __name__ == '__main__':
    main()
