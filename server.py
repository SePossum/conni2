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
        request_path = urlparse(self.path).path
        if request_path not in ("/api/like", "/api/topics"):
            self.send_json(404, {"error": "Not found"})
            return

        try:
            request_size = int(self.headers.get("Content-Length", "0"))
            if request_size <= 0 or request_size > MAX_REQUEST_SIZE:
                self.send_json(400, {"error": "Invalid request size"})
                return

            request = json.loads(self.rfile.read(request_size))
            if not isinstance(request, dict):
                self.send_json(400, {"error": "Invalid request body"})
                return

            if request_path == "/api/topics":
                self.create_topic(request)
                return

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

    def do_DELETE(self):
        if urlparse(self.path).path != "/api/items":
            self.send_json(404, {"error": "Not found"})
            return

        try:
            request_size = int(self.headers.get("Content-Length", "0"))
            if request_size <= 0 or request_size > MAX_REQUEST_SIZE:
                self.send_json(400, {"error": "Invalid request size"})
                return

            request = json.loads(self.rfile.read(request_size))
            if not isinstance(request, dict):
                self.send_json(400, {"error": "Invalid request body"})
                return

            item_type = request.get("itemType")
            item_name = request.get("itemName")
            if item_type not in ("program", "topics"):
                self.send_json(400, {"error": "Invalid item type"})
                return
            if not isinstance(item_name, str) or not item_name.strip():
                self.send_json(400, {"error": "Invalid item name"})
                return

            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            items = data.get(item_type, [])
            item_index = next(
                (index for index, item in enumerate(items) if item.get("name") == item_name),
                None,
            )
            if item_index is None:
                self.send_json(404, {"error": "Item not found"})
                return

            deleted_item = items.pop(item_index)
            self.write_data(data)
            self.send_json(200, {"itemType": item_type, "item": deleted_item})
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self.send_json(400, {"error": "Invalid JSON request"})
        except OSError:
            self.send_json(500, {"error": "Could not save program.json"})

    def create_topic(self, request):
        name = request.get("name")
        description = request.get("description")
        contributors = request.get("contributors", [])
        languages = request.get("languages", [])

        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
            self.send_json(400, {"error": "A topic name of 1 to 160 characters is required"})
            return
        if not isinstance(description, str) or not description.strip() or len(description.strip()) > 4000:
            self.send_json(400, {"error": "A description of 1 to 4000 characters is required"})
            return
        if not self.valid_string_list(contributors, max_items=30, max_length=120):
            self.send_json(400, {"error": "Invalid contributor list"})
            return
        if not self.valid_string_list(languages, max_items=20, max_length=80):
            self.send_json(400, {"error": "Invalid language list"})
            return

        normalized_name = name.strip().casefold()
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        topics = data.setdefault("topics", [])
        if any(topic.get("name", "").strip().casefold() == normalized_name for topic in topics):
            self.send_json(409, {"error": "A topic with that name already exists"})
            return

        topic = {
            "name": name.strip(),
            "desc": description.strip(),
            "contributers": self.clean_string_list(contributors),
            "languages": self.clean_string_list(languages),
            "likers": [],
            "likeCount": 0,
        }
        topics.append(topic)
        self.write_data(data)
        self.send_json(201, {"topic": topic})

    @staticmethod
    def valid_string_list(values, max_items, max_length):
        return (
            isinstance(values, list)
            and len(values) <= max_items
            and all(isinstance(value, str) and len(value.strip()) <= max_length for value in values)
        )

    @staticmethod
    def clean_string_list(values):
        cleaned = []
        seen = set()
        for value in values:
            item = value.strip()
            key = item.casefold()
            if item and key not in seen:
                cleaned.append(item)
                seen.add(key)
        return cleaned

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