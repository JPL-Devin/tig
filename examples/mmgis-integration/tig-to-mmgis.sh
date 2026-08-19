#!/bin/bash
# Converts TIG demo products into layers an MMGIS instance can serve, and writes
# the MMGIS layer objects that register them. See README.md.
set -e

MOSAIC=""
MESH=""
TEXTURE=""
SITE_LON=""
SITE_LAT=""
MESH_ELEV="0"
TEXTURE_GAMMA="2.2"
MISSION="TIG-Demo"
MISSIONS_DIR="./Missions"
MOSAIC_LAYER="TIG Vertical Mosaic"
MESH_LAYER="TIG Terrain Mesh"
RADIUS=3396190

print_usage() {
  cat << 'EOF'
Usage: tig-to-mmgis.sh --site-lon DEG --site-lat DEG
                       [--mosaic FILE] [--mesh FILE] [OPTIONS]

Turns TIG products into MMGIS layers under Missions/<mission>/Layers/:

  --mosaic FILE   A VERTICAL-projection mosaic from demo-panorama-mosaic.sh
                  (workspace/panorama.img) -> a GeoTIFF plus a TMS pyramid of
                  {z}/{x}/{y}.png tiles, for an MMGIS Tile layer.
  --mesh FILE     A mesh from demo-mesh-generation-with-xyz.sh
                  (workspace/terrain.obj) -> the OBJ/MTL/texture plus a GLB
                  from obj2gltf, for an MMGIS Model layer.

The site origin is required: TIG's in-situ products are in the rover's SITE
frame (metres, +X north, +Y east, +Z down) and carry no planetary coordinates,
so the mapping from metres to longitude/latitude has to come from you. See the
"Georeferencing" section of README.md.

Options:
  --site-lon DEG        Longitude of the SITE frame origin       (required)
  --site-lat DEG        Latitude of the SITE frame origin        (required)
  --mesh-elev M         Elevation to anchor the mesh at. This mission has no
                        terrain layer, so the globe's zero surface is the
                        ellipsoid and 0 sits the mesh on it    (default: 0)
  --texture FILE        Mesh texture (default: texture.png beside the mesh)
  --texture-gamma G     Gamma to pre-encode the mesh texture with, to cancel
                        the globe renderer's colour-space mismatch. 1 disables
                        it                             (default: 2.2)
  --mission NAME        MMGIS mission name       (default: TIG-Demo)
  --missions-dir DIR    MMGIS Missions/ directory (default: ./Missions)
  --mosaic-layer NAME   Mosaic layer name        (default: TIG Vertical Mosaic)
  --mesh-layer NAME     Mesh layer name          (default: TIG Terrain Mesh)
  --radius M            Body radius used for metres->degrees
                                                 (default: 3396190, Mars)
  --help, -h            This message

Requirements:
  - tig-cli (pip install tig-cli) and a running Docker daemon, for vicario,
    obj2gltf and label
  - GDAL: gdal_translate and gdal2tiles.py on PATH (apt install gdal-bin)
EOF
  exit 1
}

require_value() {
  [ -n "$2" ] || { echo "ERROR: $1 requires a value"; print_usage; }
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --mosaic)        require_value "$1" "$2"; MOSAIC="$2"; shift 2 ;;
    --mesh)          require_value "$1" "$2"; MESH="$2"; shift 2 ;;
    --texture)       require_value "$1" "$2"; TEXTURE="$2"; shift 2 ;;
    --site-lon)      require_value "$1" "$2"; SITE_LON="$2"; shift 2 ;;
    --site-lat)      require_value "$1" "$2"; SITE_LAT="$2"; shift 2 ;;
    --mesh-elev)     require_value "$1" "$2"; MESH_ELEV="$2"; shift 2 ;;
    --texture-gamma) require_value "$1" "$2"; TEXTURE_GAMMA="$2"; shift 2 ;;
    --mission)       require_value "$1" "$2"; MISSION="$2"; shift 2 ;;
    --missions-dir)  require_value "$1" "$2"; MISSIONS_DIR="$2"; shift 2 ;;
    --mosaic-layer)  require_value "$1" "$2"; MOSAIC_LAYER="$2"; shift 2 ;;
    --mesh-layer)    require_value "$1" "$2"; MESH_LAYER="$2"; shift 2 ;;
    --radius)        require_value "$1" "$2"; RADIUS="$2"; shift 2 ;;
    --help|-h)       print_usage ;;
    *)               echo "ERROR: Unknown argument: $1"; print_usage ;;
  esac
done

[ -n "$MOSAIC" ] || [ -n "$MESH" ] || { echo "ERROR: give --mosaic and/or --mesh"; print_usage; }
for v in SITE_LON SITE_LAT; do
  [ -n "${!v}" ] || { echo "ERROR: --$(echo "$v" | tr 'A-Z_' 'a-z-') is required"; print_usage; }
done

command -v tig > /dev/null || { echo "ERROR: tig not found (pip install tig-cli)"; exit 1; }

LAYERS_DIR="$MISSIONS_DIR/$MISSION/Layers"
mkdir -p "$LAYERS_DIR"
MISSION_DIR="$(cd "$MISSIONS_DIR/$MISSION" && pwd)"

# Layer names double as directory names, so keep them filesystem-safe.
slug() {
  local s
  s=$(echo "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '_' | sed 's/^_//; s/_$//')
  # An empty slug would make the output directory the Layers/ directory itself.
  [ -n "$s" ] || { echo "ERROR: layer name '$1' has no characters usable in a directory name" >&2; exit 1; }
  echo "$s"
}

if [ -n "$MOSAIC" ]; then
  [ -f "$MOSAIC" ] || { echo "ERROR: no such mosaic: $MOSAIC"; exit 1; }
  command -v gdal_translate > /dev/null || { echo "ERROR: gdal_translate not found (apt install gdal-bin)"; exit 1; }
  command -v gdal2tiles.py > /dev/null || { echo "ERROR: gdal2tiles.py not found (apt install gdal-bin)"; exit 1; }

  echo "=== Mosaic: $MOSAIC ==="
  MOSAIC_SLUG=$(slug "$MOSAIC_LAYER")
  MOSAIC_OUT="$MISSION_DIR/Layers/$MOSAIC_SLUG"
  rm -rf "$MOSAIC_OUT"
  mkdir -p "$MOSAIC_OUT"

  # marsmap writes the projection and the ground extent it used into the VICAR
  # label; that is where the pixel-to-metres mapping comes from.
  echo "Step 1: Reading the projection out of the label..."
  LABEL=$(tig label -list inp="$MOSAIC" 2>/dev/null)
  label_value() { echo "$LABEL" | grep -m1 "^$1=" | cut -d= -f2- | tr -d "'() " | cut -d, -f1; }
  PROJECTION=$(label_value MAP_PROJECTION_TYPE)
  if [ "$PROJECTION" != "VERTICAL" ]; then
    echo "ERROR: MAP_PROJECTION_TYPE is '$PROJECTION', not VERTICAL."
    echo "  A tile layer needs a map-like plan view. Rebuild the mosaic with"
    echo "  ./demo-panorama-mosaic.sh --projection vertical --min-x .. --max-x .."
    echo "  Cylindrical and polar mosaics are camera-centred views; put those in"
    echo "  MMGIS through a Viewer panel instead (see README.md)."
    exit 1
  fi
  MINX=$(label_value X_AXIS_MINIMUM)
  MAXX=$(label_value X_AXIS_MAXIMUM)
  MINY=$(label_value Y_AXIS_MINIMUM)
  MAXY=$(label_value Y_AXIS_MAXIMUM)
  SCALE=$(label_value MAP_SCALE)
  echo "  X (north) $MINX .. $MAXX m, Y (east) $MINY .. $MAXY m, $SCALE m/pixel"

  # vicario rescales the 16-bit VICAR mosaic into 8-bit PNG that GDAL can read.
  echo "Step 2: Converting the mosaic to PNG (vicario)..."
  tig vicario "$MOSAIC" "$MOSAIC_OUT/$MOSAIC_SLUG.png" > /dev/null
  [ -f "$MOSAIC_OUT/$MOSAIC_SLUG.png" ] || { echo "ERROR: vicario wrote no PNG"; exit 1; }
  echo "  ✓ $MOSAIC_SLUG.png"

  # marsmap's VERTICAL projection is "North up and East to the right", so the
  # label's X/Y extents are the raster's north/south and west/east edges.
  echo "Step 3: Georeferencing (metres in the SITE frame -> degrees)..."
  # Values reach python through the environment: interpolating them into the
  # source would let an argument or a label field execute python.
  BOUNDS=$(SITE_LON="$SITE_LON" SITE_LAT="$SITE_LAT" RADIUS="$RADIUS" \
    MINX="$MINX" MAXX="$MAXX" MINY="$MINY" MAXY="$MAXY" python3 << 'PY'
import math, os

def num(key):
    return float(os.environ[key])

lon, lat = num('SITE_LON'), num('SITE_LAT')
m_per_deg_lat = math.pi * num('RADIUS') / 180.0
m_per_deg_lon = m_per_deg_lat * math.cos(math.radians(lat))
print(lon + num('MINY') / m_per_deg_lon, lat + num('MAXX') / m_per_deg_lat,
      lon + num('MAXY') / m_per_deg_lon, lat + num('MINX') / m_per_deg_lat)
PY
)
  read -r ULX ULY LRX LRY <<< "$BOUNDS"
  echo "  ullr $ULX $ULY $LRX $LRY"
  # The degrees above are computed with the body radius, but the GeoTIFF is
  # tagged EPSG:4326: gdal2tiles' geodetic profile rejects a non-Earth ellipsoid
  # ("Source and target ellipsoid do not belong to the same celestial body").
  # Tiling is pure degree arithmetic, and MMGIS gets the body radius from the
  # mission config, so only the label is wrong - see README.md.
  gdal_translate -q -of GTiff \
    -a_srs EPSG:4326 \
    -a_ullr "$ULX" "$ULY" "$LRX" "$LRY" \
    "$MOSAIC_OUT/$MOSAIC_SLUG.png" "$MOSAIC_OUT/$MOSAIC_SLUG.tif"
  echo "  ✓ $MOSAIC_SLUG.tif"

  # A mission without projection.custom leaves Leaflet on web mercator
  # (Map_.js), so the tile grid has to be mercator too - a geodetic pyramid
  # 404s at every zoom because the row indices do not line up.
  echo "Step 4: Tiling (gdal2tiles.py, mercator profile)..."
  gdal2tiles.py -q -p mercator --no-kml \
    "$MOSAIC_OUT/$MOSAIC_SLUG.tif" "$MOSAIC_OUT" > /dev/null
  # A glob rather than find -regex/-printf, which are GNU-only.
  ZOOMS=$(for d in "$MOSAIC_OUT"/*/; do
    z=$(basename "$d")
    case $z in ''|*[!0-9]*) ;; *) echo "$z" ;; esac
  done | sort -n)
  [ -n "$ZOOMS" ] || { echo "ERROR: gdal2tiles wrote no tiles"; exit 1; }
  MIN_ZOOM=$(echo "$ZOOMS" | head -1)
  MAX_ZOOM=$(echo "$ZOOMS" | tail -1)
  # Depth 3 is {z}/{x}/{y}.png, which excludes the converted source PNG.
  TILES=$(find "$MOSAIC_OUT" -mindepth 3 -name '*.png' | wc -l)
  echo "  ✓ $TILES tiles, zoom $MIN_ZOOM-$MAX_ZOOM, under Layers/$MOSAIC_SLUG/{z}/{x}/{y}.png"

  MOSAIC_LAYER="$MOSAIC_LAYER" MOSAIC_SLUG="$MOSAIC_SLUG" \
  MIN_ZOOM="$MIN_ZOOM" MAX_ZOOM="$MAX_ZOOM" SCALE="$SCALE" \
  ULX="$ULX" ULY="$ULY" LRX="$LRX" LRY="$LRY" \
  SITE_LON="$SITE_LON" SITE_LAT="$SITE_LAT" python3 << 'PY' > "$MISSION_DIR/$MOSAIC_SLUG.layer.json"
import json, os

e = os.environ
print(json.dumps({
    'name': e['MOSAIC_LAYER'],
    'type': 'tile',
    'visibility': True,
    'sourceType': 'url',
    'url': 'Layers/%s/{z}/{x}/{y}.png' % e['MOSAIC_SLUG'],
    'tileformat': 'tms',
    'initialOpacity': 1,
    'minZoom': int(e['MIN_ZOOM']),
    'maxNativeZoom': int(e['MAX_ZOOM']),
    'maxZoom': int(e['MAX_ZOOM']) + 5,
    'boundingBox': [str(float(e[k])) for k in ('ULX', 'LRY', 'LRX', 'ULY')],
    'throughTileServer': False,
    'description': 'TIG VERTICAL-projection mosaic, %s m/pixel, placed at the '
                   'SITE frame origin %s, %s.'
                   % (e['SCALE'], e['SITE_LON'], e['SITE_LAT']),
}, indent=2))
PY
  echo "  ✓ layer object: $MISSION/$MOSAIC_SLUG.layer.json"
fi

if [ -n "$MESH" ]; then
  [ -f "$MESH" ] || { echo "ERROR: no such mesh: $MESH"; exit 1; }
  MESH_DIR=$(cd "$(dirname "$MESH")" && pwd)
  MESH_BASE=$(basename "$MESH" .obj)
  [ -n "$TEXTURE" ] || TEXTURE="$MESH_DIR/texture.png"

  echo "=== Mesh: $MESH ==="
  MESH_SLUG=$(slug "$MESH_LAYER")
  MESH_OUT="$MISSION_DIR/Layers/$MESH_SLUG"
  rm -rf "$MESH_OUT"
  mkdir -p "$MESH_OUT"

  # gdal_translate writes PNG bytes, and the MTL is rewritten to this name below,
  # so a texture called anything else still resolves.
  TEXTURE_OUT="$(basename "${TEXTURE%.*}").png"

  echo "Step 1: Copying the OBJ, its MTL and its texture..."
  cp "$MESH" "$MESH_OUT/$MESH_BASE-site-frame.obj"
  # The MTL names the texture, so both have to travel with the OBJ.
  if [ -f "$MESH_DIR/$MESH_BASE.mtl" ]; then
    cp "$MESH_DIR/$MESH_BASE.mtl" "$MESH_OUT/$MESH_BASE.mtl"
    # sed writes to a temp file and is moved back: sed -i is not portable.
    edit_in_place() {
      sed -E "$1" "$2" > "$2.tmp" && mv "$2.tmp" "$2"
    }
    edit_in_place "s@^mtllib .*@mtllib $MESH_BASE.mtl@" "$MESH_OUT/$MESH_BASE-site-frame.obj"
    if [ -f "$TEXTURE" ]; then
      edit_in_place "s@^[[:space:]]*map_Kd[[:space:]].*@map_Kd $TEXTURE_OUT@" "$MESH_OUT/$MESH_BASE.mtl"
    fi
  else
    echo "  ⚠ no $MESH_BASE.mtl beside the mesh: the model will be untextured"
  fi
  if [ -f "$TEXTURE" ] && [ "$TEXTURE_GAMMA" = "1" ] && [ "$TEXTURE" != "${TEXTURE%.png}" ]; then
    cp "$TEXTURE" "$MESH_OUT/$TEXTURE_OUT"
    echo "  ✓ texture copied as $TEXTURE_OUT"
  elif [ -f "$TEXTURE" ]; then
    command -v gdal_translate > /dev/null || { echo "ERROR: gdal_translate not found (apt install gdal-bin); a PNG texture with --texture-gamma 1 needs no GDAL"; exit 1; }
    # 8-bit sources already span 0-255; anything wider (marsmesh's Int16
    # texture.img reaches 4048) has to be scaled from its own range, not clipped.
    read -r SRC_MIN SRC_MAX <<< "$(TEXTURE="$TEXTURE" python3 << 'PY'
import json, os, subprocess

info = json.loads(subprocess.run(
    ['gdalinfo', '-json', '-stats', '--config', 'GDAL_PAM_ENABLED', 'NO',
     os.environ['TEXTURE']], capture_output=True, text=True, check=True).stdout)
bands = info['bands']
if all(band['type'] == 'Byte' for band in bands):
    print(0, 255)
else:
    print(min(b['minimum'] for b in bands), max(b['maximum'] for b in bands))
PY
)"
    GDAL_ARGS=(-q -of PNG -ot Byte -scale "$SRC_MIN" "$SRC_MAX" 0 255)
    if [ "$TEXTURE_GAMMA" != "1" ]; then
      # MMGIS 5.2.24's globe is three.js r118 with a linear output encoding, but
      # GLTFLoader tags the base colour texture sRGB, so it is linearised on
      # sampling and never re-encoded: pre-encoding cancels that darkening.
      GDAL_ARGS+=(-exponent "$(TEXTURE_GAMMA="$TEXTURE_GAMMA" python3 -c \
        'import os; print(1.0 / float(os.environ["TEXTURE_GAMMA"]))')")
    fi
    gdal_translate "${GDAL_ARGS[@]}" "$TEXTURE" "$MESH_OUT/$TEXTURE_OUT"
    rm -f "$MESH_OUT/$TEXTURE_OUT.aux.xml"
    echo "  ✓ texture scaled from $SRC_MIN-$SRC_MAX with gamma $TEXTURE_GAMMA as $TEXTURE_OUT"
  else
    echo "  ⚠ no texture at $TEXTURE: the model will be untextured"
    echo "    marsmesh writes texture.img; convert it with: tig vicario texture.img texture.png"
  fi
  echo "  ✓ Layers/$MESH_SLUG/$MESH_BASE-site-frame.obj"

  # A model's +Y points away from the planet's centre, so the SITE frame
  # (+X north, +Y east, +Z down) is rotated to (+X east, +Y up, +Z south) and
  # recentred on its centroid, which is what the layer position anchors.
  echo "Step 2: Rotating SITE frame -> model frame and recentring..."
  OFFSET=$(python3 - "$MESH_OUT/$MESH_BASE-site-frame.obj" "$MESH_OUT/$MESH_BASE.obj" << 'PY'
import sys

src, dst = sys.argv[1], sys.argv[2]
verts = []
for line in open(src):
    if line.startswith('v '):
        verts.append([float(v) for v in line.split()[1:4]])
if not verts:
    sys.exit('no vertices in ' + src)

centre = [sum(c) / len(verts) for c in zip(*verts)]

with open(src) as fin, open(dst, 'w') as fout:
    for line in fin:
        parts = line.split()
        if line.startswith('v ') and len(parts) >= 4:
            n, e, d = (float(parts[i + 1]) - centre[i] for i in range(3))
            fout.write('v %.6f %.6f %.6f\n' % (e, -d, -n))
        elif line.startswith('vn ') and len(parts) >= 4:
            n, e, d = (float(v) for v in parts[1:4])
            fout.write('vn %.6f %.6f %.6f\n' % (e, -d, -n))
        else:
            fout.write(line)

print(centre[0], centre[1], centre[2])
PY
)
  read -r CENTRE_NORTH CENTRE_EAST CENTRE_DOWN <<< "$OFFSET"
  rm -f "$MESH_OUT/$MESH_BASE-site-frame.obj"
  echo "  ✓ centred on the mesh centroid: north $CENTRE_NORTH m, east $CENTRE_EAST m, down $CENTRE_DOWN m"

  # obj2gltf keeps the texture as a relative URI, so it is fetched from the same
  # Layers/ directory as the GLB rather than embedded in it.
  echo "Step 3: Converting to GLB (obj2gltf)..."
  (cd "$MESH_OUT" && tig obj2gltf INP="$MESH_BASE.obj" OUT="$MESH_BASE.glb")
  MESH_URL="Layers/$MESH_SLUG/$MESH_BASE.glb"
  echo "  ✓ $MESH_URL"

  # obj2gltf writes positions and one texture coordinate for "a single unlit
  # textured material" (its .pdf) but never says unlit in the GLB, so the globe
  # shades a normal-less mesh almost black. KHR_materials_unlit skips lighting.
  echo "Step 4: Declaring the material unlit (KHR_materials_unlit)..."
  python3 - "$MESH_OUT/$MESH_BASE.glb" << 'PY'
import json, struct, sys

path = sys.argv[1]
blob = open(path, 'rb').read()
json_length = struct.unpack('<I', blob[12:16])[0]
gltf = json.loads(blob[20:20 + json_length])

gltf.setdefault('extensionsUsed', [])
if 'KHR_materials_unlit' not in gltf['extensionsUsed']:
    gltf['extensionsUsed'].append('KHR_materials_unlit')
for material in gltf.get('materials', []):
    material.setdefault('extensions', {})['KHR_materials_unlit'] = {}
    material['doubleSided'] = True

# The JSON chunk has to stay 4-byte aligned and padded with spaces.
chunk = json.dumps(gltf, separators=(',', ':')).encode()
chunk += b' ' * (-len(chunk) % 4)
rest = blob[20 + json_length:]
header = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(chunk) + len(rest))
open(path, 'wb').write(
    header + struct.pack('<II', len(chunk), 0x4E4F534A) + chunk + rest)
PY
  echo "  ✓ material marked unlit and double sided"

  # The anchor is where the centroid actually is: the site origin plus the offset.
  MESH_LAYER="$MESH_LAYER" MESH_URL="$MESH_URL" RADIUS="$RADIUS" \
  SITE_LON="$SITE_LON" SITE_LAT="$SITE_LAT" MESH_ELEV="$MESH_ELEV" \
  CENTRE_EAST="$CENTRE_EAST" CENTRE_NORTH="$CENTRE_NORTH" \
  python3 << 'PY' > "$MISSION_DIR/$MESH_SLUG.layer.json"
import json, math, os

e = os.environ

def num(key):
    return float(e[key])

m_per_deg_lat = math.pi * num('RADIUS') / 180.0
m_per_deg_lon = m_per_deg_lat * math.cos(math.radians(num('SITE_LAT')))
print(json.dumps({
    'name': e['MESH_LAYER'],
    'type': 'model',
    'visibility': True,
    'url': e['MESH_URL'],
    'position': {
        'longitude': num('SITE_LON') + num('CENTRE_EAST') / m_per_deg_lon,
        'latitude': num('SITE_LAT') + num('CENTRE_NORTH') / m_per_deg_lat,
        'elevation': num('MESH_ELEV'),
    },
    'scale': 1,
    'rotation': {'x': 0, 'y': 0, 'z': 0},
    'initialOpacity': 1,
    'description': 'TIG marsmesh terrain, SITE frame rotated to +X east/+Y up/'
                   '+Z south and centred on its centroid.',
}, indent=2))
PY
  echo "  ✓ layer object: $MISSION/$MESH_SLUG.layer.json"
fi

cat << EOF

=== Done ===
Products are under $MISSION_DIR/Layers/.
Register each layer object with the Configure API (token from /configure ->
API Tokens), or paste its fields into the Configure page by hand:

  curl -X POST http://localhost:8888/api/configure/addLayer \\
    -H "Authorization:Bearer \$MMGIS_TOKEN" -H "Content-Type: application/json" \\
    -d "{\"mission\":\"$MISSION\",\"layer\":\$(cat <layer>.layer.json)}"
EOF
