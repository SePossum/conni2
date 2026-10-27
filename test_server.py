import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

import server


class ConcurrentLikeWritesTest(unittest.TestCase):
    def setUp(self):
        self.original_data_file = server.DATA_FILE
        self.temporary_directory = tempfile.TemporaryDirectory(dir=server.ROOT)
        server.DATA_FILE = Path(self.temporary_directory.name) / "fixture.json"
        server.DATA_FILE.write_text(json.dumps({
            "program": [{"name": "Load Test Session", "likers": [], "likeCount": 0}],
            "topics": [],
            "locations": [],
        }), encoding="utf-8")

        self.http_server = server.ConniHTTPServer(("127.0.0.1", 0), server.ConniHandler)
        self.server_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self.server_thread.start()
        self.endpoint = f"http://127.0.0.1:{self.http_server.server_port}/api/like"

    def tearDown(self):
        self.http_server.shutdown()
        self.http_server.server_close()
        self.server_thread.join()
        server.DATA_FILE = self.original_data_file
        self.temporary_directory.cleanup()

    def test_250_concurrent_likes_are_all_persisted(self):
        def add_like(user_index):
            payload = {
                "itemType": "program",
                "itemName": "Load Test Session",
                "userName": f"User {user_index}",
                "liked": True,
            }
            request = Request(
                self.endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=30) as response:
                return response.status

        with ThreadPoolExecutor(max_workers=250) as clients:
            statuses = list(clients.map(add_like, range(250)))

        data = json.loads(server.DATA_FILE.read_text(encoding="utf-8"))
        item = data["program"][0]
        self.assertEqual(statuses, [200] * 250)
        self.assertEqual(item["likeCount"], 250)
        self.assertEqual(len(item["likers"]), 250)


if __name__ == "__main__":
    unittest.main()
