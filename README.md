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

## Features

- Browse the program in list or calendar view; filter by language, likes, contributions, or search terms.
- Browse and create topics, edit item details, and schedule a topic in the program.
- Create and edit locations, including descriptions, capacity, and facilities. Renaming a location updates program items assigned to it.
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
