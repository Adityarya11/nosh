"""Downloads and unpacks the Recipe Box dataset into recipes_raw/."""

import io
import sys
import urllib.request
import zipfile

from nosh.config import DATASET_URL, RAW_DIR, RAW_FILES


def main() -> None:
    RAW_DIR.mkdir(exist_ok=True)
    if all((RAW_DIR / name).exists() for name in RAW_FILES.values()):
        print(f"Dataset already present in {RAW_DIR}")
        return
    print(f"Downloading {DATASET_URL}")
    with urllib.request.urlopen(DATASET_URL) as resp:
        payload = resp.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for member in zf.namelist():
            name = member.rsplit("/", 1)[-1]
            if name in RAW_FILES.values():
                (RAW_DIR / name).write_bytes(zf.read(member))
                print(f"Wrote {RAW_DIR / name}")
    missing = [n for n in RAW_FILES.values() if not (RAW_DIR / n).exists()]
    if missing:
        sys.exit(f"Missing after unpack: {missing}")


if __name__ == "__main__":
    main()
