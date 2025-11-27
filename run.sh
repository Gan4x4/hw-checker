#!/bin/bash

# Activate local virtualenv (assumes it was created with `python3 -m venv .venv`)
source .venv/bin/activate
python manage.py runserver
