# -*- coding: utf-8 -*-
from app import app
c = app.test_client()
pages = ['/pageH?name=固态电池', '/pageK', '/pageK/edit/5', '/pageJ', '/pageF', '/']
print([(p, c.get(p).status_code) for p in pages])
