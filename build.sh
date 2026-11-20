#!/usr/bin/env bash
# Build de Render: instala dependencias, junta estáticos, migra y carga catálogos.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
python manage.py createcachetable
python manage.py cargar_catalogos
