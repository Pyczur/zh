# Well Interpolation Dashboard

Beginner-friendly local Flask and Leaflet dashboard for well measurements in this folder:

`D:\Új mappa\zh`

The app automatically looks for one CSV file and one shapefile in the project folder. It detects coordinate columns, a well ID column, usable time fields, and numeric parameters. For the current CSV, it detects `latitude`, `longitude`, well ID `kút`, and the repeated `Év` + `Hónap` fields for the time selector.

## Setup on Windows

Open PowerShell in the project folder:

```powershell
cd "D:\Új mappa\zh"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run the dashboard:

```powershell
python app.py
```

The terminal prints a local link and, when available, a network link:

```text
Open dashboard on this computer at: http://127.0.0.1:5000
Open dashboard from another device on this network at: http://192.168.1.23:5000
```

Open the local link on this computer. Use the network link from another computer,
tablet, or phone on the same Wi-Fi/LAN. If the network link does not open, allow
Python through Windows Firewall for private networks.

## What It Shows

- OpenStreetMap basemap
- Shapefile boundary
- Well points colored and sized by the selected parameter
- Popups with well ID, coordinates, parameter value, and selected time
- IDW interpolation surface clipped to the shapefile boundary where possible
- Parameter selector, time selector, layer toggles, reset view, and legend

## If Column Detection Needs Help

Open `app.py` and set the manual column names near the top:

```python
MANUAL_X_COLUMN = "longitude"
MANUAL_Y_COLUMN = "latitude"
MANUAL_COORD_CRS = "EPSG:4326"
MANUAL_ID_COLUMN = "kút"
MANUAL_TIME_COLUMN = None
MANUAL_YEAR_COLUMN = "Év"
MANUAL_MONTH_COLUMN = "Hónap"
```

Leave values as `None` to use automatic detection.

## Notes

All data stays local. No database is required. The map tiles and Leaflet files are loaded from public web CDNs, so the basemap needs an internet connection.


Basic prompt
Create a complete, beginner-friendly local web dashboard project for visualizing well data and interpolation over time.

Project/data location:
`D:\Új mappa\zh`

Data format:
The project folder contains:

* one shapefile, likely used as the study area/boundary layer
* one CSV file containing well locations and measurement columns

Main goal:
Build a dashboard that shows wells as point features, displays their selected parameter values, and creates an interpolated surface for one selected parameter over time.

Important data-handling requirements:

1. Automatically inspect the CSV file and detect:

   * coordinate columns, for example `x/y`, `lon/lat`, `longitude/latitude`, `E/N`, or similar
   * well ID/name column if available
   * date/time column if available
   * numeric parameter columns that can be selected by the user
2. If column names are unclear, write simple comments in the code explaining where the user should manually set them.
3. Load the shapefile from the same project folder and use it as the boundary/study area.
4. Make sure the dashboard works locally on Windows, including paths with Hungarian characters such as `Új mappa`.
5. Do not require external database setup.

Dashboard requirements:

1. Create a dynamic interactive map with:

   * well points
   * zoom in / zoom out
   * pan
   * a basemap, preferably OpenStreetMap
   * the shapefile boundary displayed on the map
2. Add a parameter selector:

   * list all available numeric parameters from the CSV
   * when the user selects a parameter, update the well point values and interpolation
3. Add a time selector:

   * if the CSV has a date/time column, allow the user to select available dates/times
   * update the map based on the selected time
   * if there is no date/time column, show a clear message that no time column was found and still allow parameter-based visualization
4. Show wells as points:

   * color or size the points based on the selected parameter value
   * show a popup on click with well ID/name, coordinates, selected parameter, value, and time if available
5. Create an interpolated surface:

   * interpolate the selected parameter values from the wells
   * clip or mask the interpolation to the shapefile boundary if possible
   * display the interpolation as a semi-transparent raster/heatmap layer above the basemap
   * include a visible legend/color bar
6. Add basic controls:

   * layer toggle for wells, boundary, and interpolation surface
   * reset map view button
   * clear status/error messages if data cannot be loaded

Implementation requirements:

1. Use Python with Flask for the backend.
2. Use Leaflet.js for the interactive map frontend.
3. Use common Python GIS/data libraries such as:

   * pandas
   * geopandas
   * shapely
   * numpy
   * scipy or sklearn for interpolation
   * rasterio if needed
   * Flask
4. Use a simple interpolation method first, preferably IDW or griddata, so the project is easy to run.
5. Keep the project structure simple, for example:

   * `app.py`
   * `requirements.txt`
   * `templates/index.html`
   * `static/css/style.css`
   * `static/js/dashboard.js`
   * optional `README.md`
6. The app must print the local running URL in the terminal, for example:
   `Open dashboard at: http://127.0.0.1:5000`
7. Add clear setup instructions in the README:

   * create virtual environment
   * install requirements
   * run the app
   * open the localhost link

Output expectations:
Build the full working project code. Keep the design clean and simple. Do not overcomplicate the dashboard. Prioritize a working local dashboard that loads the shapefile and CSV from `D:\Új mappa\zh`, shows wells, allows parameter and time selection, and displays an interpolated surface for the selected parameter over time.


refining prompt
Refine the existing local dashboard project with minimal, localized changes only.

Goal:
Fix the map display so it loads as one full seamless interactive map, and make sure the wells, concentration values, and border shapefile are visible on the map.

Project location:
The website is already located in the current project folder. Work only inside the existing project.

Current problem:
The app loads successfully and the API requests return HTTP 200, for example:

```text
GET / HTTP/1.1 200
GET /static/js/dashboard.js HTTP/1.1 200
GET /static/css/style.css HTTP/1.1 200
GET /api/metadata HTTP/1.1 200
GET /api/data?parameter=Cu&time=Year+1+/+Month+10 HTTP/1.1 200
```

However, the map appears broken or fragmented instead of loading as a full seamless map. The well concentrations are not clearly visible. The wells and border shapefile must be visible.

Important restrictions:

* Do not change the sidebar.
* Do not redesign the page.
* Do not change unrelated pages, flows, controls, or styling.
* Keep the existing dashboard behavior.
* Make the smallest possible changes, only to the map display and map layer rendering.

Files to inspect and modify only if needed:

* `templates/index.html`
* `static/css/style.css`
* `static/js/dashboard.js`
* backend API code only if the wells or border data are not being returned correctly

Required fixes:

1. Fix the fragmented Leaflet map display.

   * Check that Leaflet CSS is correctly loaded.
   * Make sure the map container has a stable height and width.
   * Make sure no parent container, flex layout, grid layout, transform, overflow, or z-index rule is breaking the Leaflet tile layout.
   * After the map is initialized and after data/layers are loaded, call `map.invalidateSize()` so Leaflet recalculates the correct map size.
   * If the map is inside a dynamic layout, call `map.invalidateSize()` again after a short timeout.

2. Make the basemap seamless.

   * Use a reliable OpenStreetMap Leaflet tile layer:
     `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`
   * Add proper attribution.
   * Make sure the tile layer is added only once.
   * Make sure the tile layer is underneath the wells, interpolation, and border layers.

3. Show the border shapefile clearly.

   * Load the border GeoJSON/shapefile-derived layer from the existing API or existing data-loading logic.
   * Add it as a Leaflet GeoJSON layer.
   * Style it with a clear outline and transparent fill.
   * Fit the map bounds to the border layer if available.
   * If the border layer is missing, fit to the well points instead.

4. Show the wells clearly.

   * Render wells as visible circle markers above the basemap and interpolation.
   * Each well must show the selected parameter value/concentration.
   * Use a readable marker style with a visible border.
   * Add popups showing at least:

     * well ID/name if available
     * selected parameter
     * concentration/value
     * time value if available
     * coordinates if available

5. Make concentrations visible.

   * Ensure the selected parameter values from `/api/data?parameter=...&time=...` are actually used for marker coloring, marker size, popup text, and/or interpolation.
   * Handle missing/null/non-numeric values safely.
   * If no valid concentration values exist for the selected parameter/time, show a clear map status message instead of silently rendering nothing.

6. Preserve interpolation if it already exists.

   * Do not rewrite the interpolation system unless necessary.
   * Make sure the interpolation layer does not hide the wells or border.
   * Set interpolation opacity so the basemap, wells, and border remain visible.
   * Ensure layer order is:

     1. basemap
     2. interpolation surface
     3. border shapefile
     4. well points

7. Add small debugging safeguards only where useful.

   * Log a concise message in the browser console showing how many wells and border features were loaded.
   * Do not add noisy logs.
   * Do not expose unnecessary backend details.

Acceptance criteria:

* The map loads as one full seamless Leaflet map, not fragmented tiles.
* OpenStreetMap basemap is visible.
* Border shapefile is visible on the map.
* Well points are visible on the map.
* Concentration values for the selected parameter/time are visible through marker styling and popups.
* The map automatically zooms/fits to the border or wells.
* The sidebar is not changed.
* No unrelated layout, page, or workflow is changed.


the refining prompt changed the base map and uploaded the wells from the csv file, also loaded the borders and the interpolation surface