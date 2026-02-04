import sys
from pathlib import Path
from streamlit.web import cli as stcli


def main() -> int:
    app_path = Path(__file__).resolve().parent / "streamlit_app.py"
    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        "--server.address=127.0.0.1",
        "--server.port=3000",
        "--global.developmentMode=false",
        "--server.headless=true",
        "--server.enableCORS=false",
    ]
    return stcli.main()


if __name__ == "__main__":
    raise SystemExit(main())
