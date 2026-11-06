"""Local browser acceptance server; synthetic source and disposable state only."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from egress_anchor.gateway import FetchResult, GatewayService
from egress_anchor.server import create_server


def main():
    with TemporaryDirectory(prefix="egress-anchor-service-ui-") as directory:
        source = (ROOT / "tests/fixtures/inline.yaml").read_text()
        service = GatewayService(Path(directory), fetcher=lambda *_a, **_kw: FetchResult(source))
        if "--empty" not in sys.argv:
            service.import_source("日常订阅", "https://fixture.example.invalid/subscription")
            service.upsert_landing({"name": "美国住宅 01", "type": "http",
                "server": "landing.example.invalid", "port": 8080})
        server = create_server(service, port=0)
        print(f"Synthetic UI fixture: http://localhost:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
