import argparse
import json
import os
import tempfile
import threading
from datetime import datetime
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "program.json"
MAX_REQUEST_SIZE = 16_384
MAX_REQUEST_WORKERS = 64
MAX_QUEUED_REQUESTS = 256
DATA_LOCK = threading.RLock()


def serialize_data_mutation(handler_method):
    @wraps(handler_method)
    def wrapped(self, *args, **kwargs):
        with DATA_LOCK:
            return handler_method(self, *args, **kwargs)

    return wrapped


class ConniHTTPServer(ThreadingHTTPServer):
    """Serve requests with bounded workers and pending-request capacity."""

    request_queue_size = MAX_QUEUED_REQUESTS
    daemon_threads = True

    def __init__(self, server_address, request_handler):
        self.request_executor = ThreadPoolExecutor(max_workers=MAX_REQUEST_WORKERS)
        self.request_slots = threading.BoundedSemaphore(MAX_REQUEST_WORKERS + MAX_QUEUED_REQUESTS)
        super().__init__(server_address, request_handler)

    def process_request(self, request, client_address):
        if not self.request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return

        try:
            future = self.request_executor.submit(self.process_request_thread, request, client_address)
        except RuntimeError:
            self.request_slots.release()
            self.shutdown_request(request)
        else:
            future.add_done_callback(lambda completed_request: self.request_slots.release())

    def server_close(self):
        super().server_close()
        self.request_executor.shutdown(wait=True)


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

    @serialize_data_mutation
    def do_POST(self):
        """Handle like updates and topic or location creation requests."""
        request_path = urlparse(self.path).path
        if request_path not in ("/api/like", "/api/topics", "/api/locations"):
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
            if request_path == "/api/locations":
                self.create_location(request)
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

    @serialize_data_mutation
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

    @serialize_data_mutation
    def do_PUT(self):
        """Update a program item, topic, or location from a JSON body."""
        request_path = urlparse(self.path).path
        if request_path not in ("/api/items", "/api/locations"):
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

            if request_path == "/api/locations":
                self.update_location(request)
                return

            source_type = request.get("itemType")
            target_type = request.get("targetType", source_type)
            item_name = request.get("itemName")
            name = request.get("name")
            description = request.get("description")
            contributors = request.get("contributors")
            languages = request.get("languages")

            if source_type not in ("program", "topics") or target_type not in ("program", "topics"):
                self.send_json(400, {"error": "Invalid item type"})
                return
            if not isinstance(item_name, str) or not item_name.strip():
                self.send_json(400, {"error": "Invalid item name"})
                return
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
                self.send_json(400, {"error": "A name of 1 to 160 characters is required"})
                return
            if not isinstance(description, str) or len(description.strip()) > 4000:
                self.send_json(400, {"error": "Description must be at most 4000 characters"})
                return
            if not self.valid_string_list(contributors, max_items=30, max_length=120):
                self.send_json(400, {"error": "Invalid contributor list"})
                return
            if not self.valid_string_list(languages, max_items=20, max_length=80):
                self.send_json(400, {"error": "Invalid language list"})
                return

            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            source_items = data.get(source_type, [])
            source_index = next(
                (index for index, entry in enumerate(source_items) if entry.get("name") == item_name),
                None,
            )
            if source_index is None:
                self.send_json(404, {"error": "Item not found"})
                return

            item = source_items[source_index]
            target_items = data.setdefault(target_type, [])
            normalized_name = name.strip().casefold()
            if any(
                entry is not item and entry.get("name", "").strip().casefold() == normalized_name
                for entry in target_items
            ):
                self.send_json(409, {"error": "An item with that name already exists"})
                return

            updated_item = dict(item)
            updated_item.update({
                "name": name.strip(),
                "desc": description.strip(),
                "contributers": self.clean_string_list(contributors),
                "languages": self.clean_string_list(languages),
            })

            if target_type == "program":
                location = request.get("location")
                date = request.get("date")
                start = request.get("start")
                end = request.get("end")
                valid_location = isinstance(location, str) and any(
                    entry.get("name") == location for entry in data.get("locations", [])
                )
                if not valid_location:
                    self.send_json(400, {"error": "Select a valid location"})
                    return
                try:
                    valid_date = isinstance(date, str) and datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d") == date
                    start_time = datetime.strptime(start, "%H:%M") if isinstance(start, str) else None
                    end_time = datetime.strptime(end, "%H:%M") if isinstance(end, str) else None
                except ValueError:
                    valid_date = False
                    start_time = None
                    end_time = None
                if not valid_date or start_time is None or end_time is None:
                    self.send_json(400, {"error": "Enter a valid date and start/end time"})
                    return
                duration = int((end_time - start_time).total_seconds() // 60)
                if duration <= 0:
                    self.send_json(400, {"error": "End time must be after start time"})
                    return
                updated_item.update({
                    "location": location,
                    "date": date,
                    "start": start_time.strftime("%H:%M"),
                    "end": end_time.strftime("%H:%M"),
                    "duration": duration,
                })
            else:
                for field in ("location", "date", "start", "end", "duration"):
                    updated_item.pop(field, None)

            if target_type == source_type:
                source_items[source_index] = updated_item
            else:
                source_items.pop(source_index)
                target_items.append(updated_item)

            self.write_data(data)
            self.send_json(200, {
                "sourceType": source_type,
                "targetType": target_type,
                "item": updated_item,
            })
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

    def create_location(self, request):
        """Validate and append a location with optional capacity and facilities."""
        name = request.get("name")
        description = request.get("description")
        capacity = request.get("capacity")
        facilities = request.get("facilities")

        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
            self.send_json(400, {"error": "A location name of 1 to 160 characters is required"})
            return
        if not isinstance(description, str) or not description.strip() or len(description.strip()) > 4000:
            self.send_json(400, {"error": "A description of 1 to 4000 characters is required"})
            return
        if not self.valid_string_list(facilities, max_items=40, max_length=120):
            self.send_json(400, {"error": "Invalid facilities list"})
            return
        if not self.valid_location_capacity(capacity):
            self.send_json(400, {"error": "Capacity must be a positive whole number"})
            return

        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        locations = data.setdefault("locations", [])
        normalized_name = name.strip().casefold()
        if any(location.get("name", "").strip().casefold() == normalized_name for location in locations):
            self.send_json(409, {"error": "A location with that name already exists"})
            return

        location = {
            "name": name.strip(),
            "desc": description.strip(),
            "facilities": self.clean_string_list(facilities),
        }
        if capacity is not None:
            location["capacity"] = capacity

        locations.append(location)
        self.write_data(data)
        self.send_json(201, {"location": location})

    def update_location(self, request):
        """Update a location and keep program references in sync after a rename."""
        item_name = request.get("itemName")
        name = request.get("name")
        description = request.get("description")
        capacity = request.get("capacity")
        facilities = request.get("facilities")

        if not isinstance(item_name, str) or not item_name.strip():
            self.send_json(400, {"error": "Invalid location name"})
            return
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
            self.send_json(400, {"error": "A location name of 1 to 160 characters is required"})
            return
        if not isinstance(description, str) or not description.strip() or len(description.strip()) > 4000:
            self.send_json(400, {"error": "A description of 1 to 4000 characters is required"})
            return
        if not self.valid_string_list(facilities, max_items=40, max_length=120):
            self.send_json(400, {"error": "Invalid facilities list"})
            return
        if not self.valid_location_capacity(capacity):
            self.send_json(400, {"error": "Capacity must be a positive whole number"})
            return

        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        locations = data.get("locations", [])
        location_index = next(
            (index for index, location in enumerate(locations) if location.get("name") == item_name),
            None,
        )
        if location_index is None:
            self.send_json(404, {"error": "Location not found"})
            return

        normalized_name = name.strip().casefold()
        if any(
            index != location_index and location.get("name", "").strip().casefold() == normalized_name
            for index, location in enumerate(locations)
        ):
            self.send_json(409, {"error": "A location with that name already exists"})
            return

        previous_name = locations[location_index].get("name", "")
        updated_location = dict(locations[location_index])
        updated_location.update({
            "name": name.strip(),
            "desc": description.strip(),
            "facilities": self.clean_string_list(facilities),
        })
        if capacity is None:
            updated_location.pop("capacity", None)
        else:
            updated_location["capacity"] = capacity

        locations[location_index] = updated_location
        if previous_name != updated_location["name"]:
            for program_item in data.get("program", []):
                if program_item.get("location") == previous_name:
                    program_item["location"] = updated_location["name"]

        self.write_data(data)
        self.send_json(200, {"location": updated_location, "previousName": previous_name})

    @staticmethod
    def valid_location_capacity(capacity):
        """Allow an omitted capacity or a positive integer no greater than 100,000."""
        return capacity is None or (type(capacity) is int and 1 <= capacity <= 100000)

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

    server = ConniHTTPServer((args.host, args.port), ConniHandler)
    print(f"Serving SemWo at http://{args.host}:{args.port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()