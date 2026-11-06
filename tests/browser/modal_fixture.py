"""Disposable long page for modal_scroll.js; uses no real proxy or client data."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from egress_anchor.gateway import GatewayService
from egress_anchor.server import create_server


def main():
    with TemporaryDirectory(prefix="egress-anchor-modal-scroll-") as data_dir:
        service = GatewayService(Path(data_dir))
        for index in range(18):
            service.upsert_landing({
                "name": f"QA Landing {index + 1}", "type": "socks5",
                "server": "landing.example.invalid", "port": 1080,
            })
        server = create_server(service, port=0)
        print(f"Modal fixture: http://localhost:{server.server_port}/legacy", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
