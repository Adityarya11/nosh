"""Downloads and unpacks the Recipe Box dataset into recipes_raw/."""

import io
import sys
from pathlib import Path
import urllib.request
import zipfile

from nosh.config import DATASET_URL, DOWNLOAD_USER_AGENT, RAW_DIR, RAW_FILES


def main(dest: Path = RAW_DIR) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if all((dest / name).exists() for name in RAW_FILES.values()):
        print(f"Dataset already present in {dest}")
        return
    print(f"Downloading {DATASET_URL}")
    request = urllib.request.Request(DATASET_URL, headers={"User-Agent": DOWNLOAD_USER_AGENT})
    with urllib.request.urlopen(request) as resp:
        payload = resp.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for member in zf.namelist():
            name = member.rsplit("/", 1)[-1]
            if name in RAW_FILES.values():
                (dest / name).write_bytes(zf.read(member))
                print(f"Wrote {dest / name}")
    missing = [n for n in RAW_FILES.values() if not (dest / n).exists()]
    if missing:
        sys.exit(f"Missing after unpack: {missing}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else RAW_DIR)
