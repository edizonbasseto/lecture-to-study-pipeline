#!/bin/sh
# The image serves the site by default, and regenerates it from the
# mounted study-guides volume when invoked with "generate" -- one image,
# two modes. The optional catalog follows config/targets.example.yaml.
set -eu

if [ "${1:-serve}" = "generate" ]; then
    exec python -c "
from pathlib import Path
from lecture_study.site.generator import generate_site

expected_ids = ()
targets_path = Path('/app/config/targets.local.yaml')
if targets_path.is_file():
    import yaml

    catalog_yaml = yaml.safe_load(targets_path.read_text())
    expected_ids = tuple(
        lesson['activity_id']
        for discipline in catalog_yaml.get('disciplines', [])
        for lesson in discipline.get('lessons', [])
        if '/supervideo/' in lesson.get('url', '')
    )

catalog = generate_site(expected_ids)
print(f'{len(catalog.lectures)} aula(s) publicada(s) de {len(expected_ids)} esperada(s)')
"
else
    # Explicit log config: uvicorn only wires up its own loggers, so the
    # site's authentication records never reached the container's stdout.
    exec uvicorn lecture_study.site.app:app --host 0.0.0.0 --port 8000 \
        --log-config /app/uvicorn-logging.json
fi
