# Mounting MARS Calibration Data

## Images with calibration already in them

The `:visor-<mission>` variants of the Terrain Intelligence Generator image
bundle one mission's VISOR calibration, so MARS tools run with no calibration
path configured and nothing downloaded:

```bash
CONTAINER_IMAGE=ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:visor-msl \
  tig marsrad NLB_712299404EDR_F0961766NCAM00353M1.IMG out.RAD.IMG
```

Available as `:visor-m20`, `:visor-mer`, `:visor-msl`, `:visor-msam`,
`:visor-phx` and `:visor-nsyt`; sizes and build instructions are in the
[image README](../../terrain-intelligence-generator/README.md#visor-calibration-variants).

Reach for a variant when you work on one mission and want the calibration to
be someone else's problem. Keep mounting a host directory when you have
calibration VISOR does not publish, when you need several missions at once
without carrying an all-missions image, or when you want the base image's
3.12GB rather than a variant's 3.3-8.8GB.

## Mounted calibration always wins

A calibration path supplied to `tig` is mounted read-only at
`/usr/local/vicar/mars_calib` - exactly where a variant puts its bundled
calibration - so on a variant image the mount hides the bundled data
completely instead of merging with it. Nothing needs to be done to "turn off"
the bundled calibration: mounting over it is enough, and `tig` also sets
`MARS_CONFIG_PATH` to the mount point so the tools look there first.

The two layouts differ, and both work:

| | Layout under `/usr/local/vicar/mars_calib` |
| --- | --- |
| mounted | `camera_models/`, `param_files/`, ... directly |
| bundled | `<mission>/camera_models/`, `<mission>/param_files/`, ... |

MARS `CONFIG_PATH` is a colon-separated search list whose entries need not
exist, so the variant images set `MARS_CONFIG_PATH` to the mount point
followed by every mission directory. A mount is found via the first entry; a
bundled mission via its own.
