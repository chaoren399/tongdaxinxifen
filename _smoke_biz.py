# -*- coding: utf-8 -*-
"""端到端验证：业务描述 新增/查询/展示/级联清理。"""
import re
from app import app
from db import (
    get_conn,
    get_businesses_by_concept_names,
    get_stock_businesses,
)

c = app.test_client()

# 1. 设置描述
r = c.post('/api/pageK/stock_business', json={
    "concept": "硅氧负极", "stock": "璞泰来",
    "business": "人造石墨负极龙头，硅氧负极已批量出货",
})
print('1 set:', r.get_json())

r = c.post('/api/pageK/stock_business', json={
    "concept": "硅氧负极", "stock": "杉杉股份",
    "business": "硅氧负极产能行业领先，已通过头部客户认证",
})
print('2 set:', r.get_json())

r = c.post('/api/pageK/stock_business', json={
    "concept": "锰矿", "stock": "湘潭电化",
    "business": "电解二氧化锰产能A股第一，布局锰矿石选矿",
})
print('3 set:', r.get_json())

# 2. pageH 显示
t = c.get('/pageH', query_string={'name': '硅氧负极'}).get_data(as_text=True)
body = re.search(r'<tbody>(.*?)</tbody>', t, re.S).group(1)
print('\n--- pageH 硅氧负极 ---')
for tr in re.findall(r'<tr>(.*?)</tr>', body, re.S):
    cells = [re.sub(r'\s+', '', re.sub(r'<[^>]+>', '', cd)) or '-'
             for cd in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
    print(' | '.join(cells))

t = c.get('/pageH', query_string={'name': '固态电池'}).get_data(as_text=True)
print('固态电池页 含业务描述:', '硅氧负极已批量出货' in t, '| 湘潭电化描述:', '电解二氧化锰' in t)

# 3. 搜索接口（页面 F 详情用）
r = c.post('/api/stocks/search_batch', json={"q": "璞泰来"})
it = r.get_json()["items"][0]
print('\n--- pageF businesses ---')
print(it["name"], it["businesses"])

# 4. 概念编辑页 chips 数据
r = c.get('/api/pageK/concept_stocks', query_string={'name': '硅氧负极'})
for s in r.get_json()["stocks"]:
    print('chip:', s["name"], '| business:', s.get("business"))

# 5. 清除描述（空串）
r = c.post('/api/pageK/stock_business', json={
    "concept": "硅氧负极", "stock": "杉杉股份", "business": ""})
print('\n5 clear:', r.get_json())
print('after clear杉杉:', get_stock_businesses('杉杉股份'))

# 还原：把杉杉描述恢复
c.post('/api/pageK/stock_business', json={
    "concept": "硅氧负极", "stock": "杉杉股份",
    "business": "硅氧负极产能行业领先，已通过头部客户认证"})
print('restored杉杉:', get_stock_businesses('杉杉股份'))
