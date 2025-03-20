#!/bin/bash
# build_files.sh
echo "Building the project..."
pip install -r requirements.txt
python manage.py collectstatic --noinput --clear
