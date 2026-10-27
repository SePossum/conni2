import argparse
import json
import os
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "program.json"
MAX_REQUEST_SIZE = 16_384


class ConniHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/api/like":
            self.send_json(404, {"error": "Not found"})
            return

        try:
            request_size = int(self.headers.get("Content-Length", "0"))
            if request_size <= 0 or request_size > MAX_REQUEST_SIZE:
                self.send_json(400, {"error": "Invalid request size"})
                return

            request = json.loads(self.rfile.read(request_size))
            item_type = request.get("itemType")
            item_name = request.get("itemName")
            user_name = request.get("userName")
            liked = request.get("liked")

            if item_type not in ("program", "topics"):
                self.send_json(400, {"error": "Invalid item type"})
                return
            if not isinstance(item_name, str) or not item_name.strip():
                self.send_json(400, {"error": "Invalid item name"})
                return
            if not isinstance(user_name, str) or not user_name.strip() or len(user_name) > 120:
                self.send_json(400, {"error": "Invalid user name"})
                return
            if not isinstance(liked, bool):
                self.send_json(400, {"error": "Invalid like state"})
                return

            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            item = next(
                (entry for entry in data.get(item_type, []) if entry.get("name") == item_name),
                None,
            )
            if item is None:
                self.send_json(404, {"error": "Item not found"})
                return

            user_key = user_name.strip().casefold()
            likers = item.setdefault("likers", [])
            already_liked = any(liker.casefold() == user_key for liker in likers)

            if liked and not already_liked:
                likers.append(user_name.strip())
            elif not liked and already_liked:
                item["likers"] = [liker for liker in likers if liker.casefold() != user_key]
                likers = item["likers"]

            item["likeCount"] = len(likers)
            self.write_data(data)
            self.send_json(200, {"itemType": item_type, "item": item})
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self.send_json(400, {"error": "Invalid JSON request"})
        except OSError:
            self.send_json(500, {"error": "Could not save program.json"})

    @staticmethod
    def write_data(data):
        file_descriptor, temporary_path = tempfile.mkstemp(
            dir=ROOT,
            prefix=".program-",
            suffix=".tmp",
            text=True,
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as output:
                json.dump(data, output, ensure_ascii=False, indent=4)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, DATA_FILE)
        except Exception:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise


def main():
    parser = argparse.ArgumentParser(description="Serve SemWo and save likes to program.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), ConniHandler)
    print(f"Serving SemWo at http://{args.host}:{args.port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()