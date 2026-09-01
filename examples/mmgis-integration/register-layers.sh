#!/bin/bash
# Registers the layer objects tig-to-mmgis.sh wrote with a running MMGIS through
# its Configure REST API. See README.md.
set -e

MISSION="TIG-Demo"
MISSIONS_DIR="./Missions"
MMGIS_URL="http://localhost:8888"
USERNAME=""
TOKEN="${MMGIS_TOKEN:-}"
PASSWORD="${MMGIS_PASSWORD:-}"
CENTER_LON=""
CENTER_LAT=""
ZOOM=19
RADIUS_MAJOR=3396190
RADIUS_MINOR=3376200

print_usage() {
  cat << 'EOF'
Usage: register-layers.sh --center-lon DEG --center-lat DEG
                          (--token TOKEN | --username U) [OPTIONS]

Creates the mission (POST /api/configure/add) if it does not exist and adds every
Missions/<mission>/*.layer.json to it (POST /api/configure/addLayer).

Options:
  --center-lon DEG      Longitude the mission opens on          (required)
  --center-lat DEG      Latitude the mission opens on           (required)
  --token TOKEN         MMGIS API token of an admin user, or set MMGIS_TOKEN
  --username U          Admin username, to log in for a token instead
  --password P          Admin password. Prefer MMGIS_PASSWORD, or leave both
                        unset to be prompted: an argument is visible in ps
  --zoom Z              Initial zoom level    (default: 19)
  --radius-major M      Body equatorial radius (default: 3396190, Mars)
  --radius-minor M      Body polar radius      (default: 3376200, Mars)
  --mission NAME        MMGIS mission name    (default: TIG-Demo)
  --missions-dir DIR    Missions/ directory   (default: ./Missions)
  --url URL             MMGIS base URL        (default: http://localhost:8888)
  --help, -h            This message

The mission's config must exist in MMGIS' database; the tiles and meshes
themselves are served off disk out of the mounted Missions/ directory.
EOF
  exit 1
}

require_value() {
  [ -n "$2" ] || { echo "ERROR: $1 requires a value"; print_usage; }
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --center-lon)    require_value "$1" "$2"; CENTER_LON="$2"; shift 2 ;;
    --center-lat)    require_value "$1" "$2"; CENTER_LAT="$2"; shift 2 ;;
    --token)         require_value "$1" "$2"; TOKEN="$2"; shift 2 ;;
    --username)      require_value "$1" "$2"; USERNAME="$2"; shift 2 ;;
    --password)      require_value "$1" "$2"; PASSWORD="$2"; shift 2 ;;
    --zoom)          require_value "$1" "$2"; ZOOM="$2"; shift 2 ;;
    --radius-major)  require_value "$1" "$2"; RADIUS_MAJOR="$2"; shift 2 ;;
    --radius-minor)  require_value "$1" "$2"; RADIUS_MINOR="$2"; shift 2 ;;
    --mission)       require_value "$1" "$2"; MISSION="$2"; shift 2 ;;
    --missions-dir)  require_value "$1" "$2"; MISSIONS_DIR="$2"; shift 2 ;;
    --url)           require_value "$1" "$2"; MMGIS_URL="$2"; shift 2 ;;
    --help|-h)       print_usage ;;
    *)               echo "ERROR: Unknown argument: $1"; print_usage ;;
  esac
done

if [ -z "$CENTER_LON" ] || [ -z "$CENTER_LAT" ]; then
  echo "ERROR: --center-lon and --center-lat are required"; print_usage
fi
command -v curl > /dev/null || { echo "ERROR: curl not found"; exit 1; }
command -v python3 > /dev/null || { echo "ERROR: python3 not found"; exit 1; }

MISSION_DIR="$MISSIONS_DIR/$MISSION"
[ -d "$MISSION_DIR" ] || { echo "ERROR: no such mission directory: $MISSION_DIR (run tig-to-mmgis.sh first)"; exit 1; }

# The jar holds an admin session, so keep it out of a shared, guessable path.
COOKIE_JAR=$(mktemp -t mmgis-session.XXXXXXXX)
chmod 600 "$COOKIE_JAR"
trap 'rm -f "$COOKIE_JAR"' EXIT

api() {
  local path="$1" body="$2"
  curl -s -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
    -X POST "$MMGIS_URL/api/configure/$path" \
    -H "Authorization:Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "$body"
}

# GETs need the token too: on the --token path the cookie jar is empty.
api_get() {
  curl -s -b "$COOKIE_JAR" -H "Authorization:Bearer $TOKEN" \
    "$MMGIS_URL/api/configure/$1"
}

# Values reach python through the environment: interpolating them into the source
# would break on a quote and let an argument execute python.
json_field() { FIELD="$1" python3 -c "import json,os,sys; print(json.load(sys.stdin).get(os.environ['FIELD'],''))"; }

if [ -z "$TOKEN" ]; then
  [ -n "$USERNAME" ] || { echo "ERROR: give --token, or --username"; print_usage; }
  if [ -z "$PASSWORD" ]; then
    read -rs -p "Password for $USERNAME: " PASSWORD; echo
    [ -n "$PASSWORD" ] || { echo "ERROR: no password given"; exit 1; }
  fi
  echo "Logging in as $USERNAME..."
  LOGIN=$(curl -s -c "$COOKIE_JAR" -X POST "$MMGIS_URL/api/users/login" \
    -H "Content-Type: application/json" \
    -d "$(USERNAME="$USERNAME" PASSWORD="$PASSWORD" python3 -c "
import json, os
print(json.dumps({'username': os.environ['USERNAME'], 'password': os.environ['PASSWORD']}))
")")
  TOKEN=$(echo "$LOGIN" | json_field token)
  [ -n "$TOKEN" ] || { echo "ERROR: login failed: $LOGIN"; exit 1; }
  echo "  ✓ logged in"
fi

# The mission's config lives in the database, so it has to be created even though
# the mission directory is already on disk.
echo "Creating mission $MISSION (if it does not exist)..."
ADD=$(api add "$(MISSION="$MISSION" python3 -c "
import json, os
print(json.dumps({'mission': os.environ['MISSION']}))
")")
case "$(echo "$ADD" | json_field status)" in
  success) echo "  ✓ created" ;;
  *) if echo "$ADD" | grep -q "already exists"; then
       echo "  ✓ already exists"
     else
       echo "ERROR: /add failed: $ADD"; exit 1
     fi ;;
esac

# /add deep-merges any config it is given into the template, which concatenates
# arrays rather than replacing them, so msv.view is set with /upsert instead.
echo "Pointing the mission at $CENTER_LON, $CENTER_LAT (zoom $ZOOM)..."
CONFIG=$(api_get "get?mission=$MISSION" \
  | MISSION="$MISSION" CENTER_LAT="$CENTER_LAT" CENTER_LON="$CENTER_LON" ZOOM="$ZOOM" \
    RADIUS_MAJOR="$RADIUS_MAJOR" RADIUS_MINOR="$RADIUS_MINOR" python3 -c "
import json, os, sys
config = json.load(sys.stdin)
config['msv']['view'] = [os.environ['CENTER_LAT'], os.environ['CENTER_LON'], os.environ['ZOOM']]
config['msv']['radius'] = {'major': os.environ['RADIUS_MAJOR'], 'minor': os.environ['RADIUS_MINOR']}
print(json.dumps({'mission': os.environ['MISSION'], 'config': config}))
")
UPSERT=$(api upsert "$CONFIG")
[ "$(echo "$UPSERT" | json_field status)" = "success" ] || { echo "ERROR: /upsert failed: $UPSERT"; exit 1; }
echo "  ✓ view set"

for LAYER_JSON in "$MISSION_DIR"/*.layer.json; do
  [ -f "$LAYER_JSON" ] || { echo "ERROR: no *.layer.json in $MISSION_DIR"; exit 1; }
  NAME=$(LAYER_JSON="$LAYER_JSON" python3 -c "
import json, os
print(json.load(open(os.environ['LAYER_JSON']))['name'])")
  # A second run has to update the layer MMGIS already holds, and /updateLayer
  # addresses it by the uuid MMGIS assigned it.
  UUID=$(api_get "get?mission=$MISSION" \
    | NAME="$NAME" python3 -c "
import json, os, sys
def find(layers):
    for layer in layers:
        if layer.get('name') == os.environ['NAME']:
            return layer.get('uuid', '')
        found = find(layer.get('sublayers', []))
        if found:
            return found
    return ''
print(find(json.load(sys.stdin).get('layers', [])))
")
  if [ -n "$UUID" ]; then
    echo "Updating layer: $NAME"
    ROUTE=updateLayer
  else
    echo "Adding layer: $NAME"
    ROUTE=addLayer
  fi
  RESP=$(api "$ROUTE" "$(MISSION="$MISSION" LAYER_JSON="$LAYER_JSON" UUID="$UUID" python3 -c "
import json, os
body = {'mission': os.environ['MISSION'],
        'layer': json.load(open(os.environ['LAYER_JSON']))}
if os.environ['UUID']:
    body['layerUUID'] = os.environ['UUID']
print(json.dumps(body))
")")
  if [ "$(echo "$RESP" | json_field status)" = "success" ]; then
    echo "  ✓ $(echo "$RESP" | json_field message)"
  else
    echo "ERROR: /$ROUTE failed: $RESP"; exit 1
  fi
done

cat << EOF

=== Done ===
Open $MMGIS_URL/?mission=$MISSION
Model layers only draw on the globe, so switch to the 3D globe view for the mesh.
EOF
