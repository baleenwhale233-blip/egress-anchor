"""Local browser acceptance server; synthetic source and disposable state only."""
from pathlib import Path
import json
import sqlite3
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from egress_anchor.gateway import FetchResult, GatewayService
from egress_anchor.client_discovery import ClientDiscovery
from egress_anchor.flclash_readonly import FlClashLocation
from egress_anchor.server import create_server


def main():
    with TemporaryDirectory(prefix="egress-anchor-service-ui-") as directory:
        source = (ROOT / "tests/fixtures/inline.yaml").read_text()
        discovery = ClientDiscovery()
        if "--empty" not in sys.argv:
            client = Path(directory) / "synthetic-client"
            client.mkdir()
            with sqlite3.connect(client / 'database.sqlite') as db:
                db.execute('PRAGMA user_version=21')
                db.execute('CREATE TABLE profiles(id INTEGER PRIMARY KEY, label TEXT, extend_type TEXT)')
                db.execute("INSERT INTO profiles VALUES(7, '合成客户端订阅', 'standard')")
            db.close()
            preferences = client / 'shared_preferences.json'
            preferences.write_text(json.dumps({'flutter.config': {'currentProfileId': 7}}))
            discovery = ClientDiscovery(locations=[FlClashLocation(client, preferences)])
        service = GatewayService(Path(directory), fetcher=lambda *_a, **_kw: FetchResult(source),
                                 client_discovery=discovery)
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
