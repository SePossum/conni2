# SemWo 2027

A small seminar-weekend program and topic browser. It is a static HTML/CSS/JavaScript interface served by a Python standard-library HTTP server, with editable data stored in `program.json`.

## Run locally

Python 3 is required; no third-party packages are needed.

On Windows:

```powershell
py server.py
```

On other systems, use:

```sh
python3 server.py
```

Open <http://127.0.0.1:8000/>. The default host and port can be changed with `--host` and `--port`, for example:

```sh
python3 server.py --host 0.0.0.0 --port 8080
```

Stop the server with `Ctrl+C`.

## Multi-user use

For a small event with roughly 250 users, run one server process on a machine reachable by the attendees. To accept connections from the local network, start it with `--host 0.0.0.0` and share the machine's network address, for example `http://192.168.1.20:8000/`.

The server runs up to 64 request workers and admits up to 256 additional queued requests. Data-changing requests are serialized so simultaneous likes, edits, and creates cannot overwrite one another in `program.json`. This lightweight JSON-backed setup assumes one server process and a trusted network; it has no user authentication and should not be exposed directly to the public internet. Edit and delete controls are shown only when the entered name matches an item's contributor, but this is a client-side visibility rule, not access control: names can be changed and the API does not enforce the rule. For multiple server instances or sustained high write volume, use a transactional database and an authenticated production web server.

## Features

- Browse the program in list or calendar view; filter by language, likes, contributions, or search terms and see the matching item count.
- Browse and create topics, edit item details, and schedule a topic in the program; the topic view shows the count of matching items.
- Language, item-type, and likes filters default to All, and topics default to date order. Entering a name does not change the selected likes filter.
- Create and edit locations, including descriptions, capacity, and facilities. Location capacity appears in the scheduling dropdown; renaming a location updates program items assigned to it.
- Like items by entering a name. The name and selected page language are stored in browser local storage.
- Choose from the supported page languages using the language selector.

## Data

`program.json` contains three top-level arrays:

- `program`: scheduled items, including location, date, start/end time, contributors, languages, and likes.
- `topics`: unscheduled topic ideas, with descriptions, contributors, languages, and likes.
- `locations`: locations with descriptions and optional capacity and facilities.

Changes made in the interface are written back to `program.json`. Keep a backup before manually editing or replacing this file.

## API

The server exposes these JSON endpoints for the interface:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/like` | Add or remove a user's like on a program item or topic. |
| `POST` | `/api/topics` | Create a topic. |
| `PUT` | `/api/items` | Edit a program item or topic, or move a topic into or out of the program. |
| `DELETE` | `/api/items` | Delete a program item or topic. |
| `POST` | `/api/locations` | Create a location. |
| `PUT` | `/api/locations` | Edit a location and update program references when it is renamed. |

The server binds to localhost by default. Binding to another host may expose the app and its writable data file to other machines on the network.
