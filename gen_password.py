from datetime import datetime
import hashlib


def main() -> None:
    month_str = datetime.now().strftime("%Y%m")
    raw = f"{month_str}56666"
    print(hashlib.sha256(raw.encode("utf-8")).hexdigest())
    input("Press Enter to exit...")


if __name__ == "__main__":
    main()
