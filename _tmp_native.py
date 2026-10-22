import app as _app
from db import list_concepts

c = _app.app.test_client()

print("=== pageKEdit /pageK/edit/5 ===")
r = c.get('/pageK/edit/5')
print("status:", r.status_code, "len:", len(r.data))
html = r.data.decode('utf-8', 'ignore')
for needle in ['ke-edit-inner', 'concept-level-table ket', 'tblBody', 'ketStockModal', 'baseForm', 'EDIT_ID']:
    print("  K has", needle, ":", needle in html)

print("\n=== pageH ?name=固态电池 ===")
r2 = c.get('/pageH?name=' + '固态电池')
print("status:", r2.status_code, "len:", len(r2.data))
html2 = r2.data.decode('utf-8', 'ignore')
for needle in ['样式 C（编辑）', 'id="styleC"', 'ke-edit-inner', 'concept-level-table ket', 'tblBody', 'ketStockModal', 'baseForm', 'pageKFrame']:
    print("  H has", needle, ":", needle in html2)
print("  H should NOT have pageKFrame:", 'pageKFrame' not in html2)

print("\n=== pageH ?name=北交所 ===")
r3 = c.get('/pageH?name=' + '北交所')
html3 = r3.data.decode('utf-8', 'ignore')
print("status:", r3.status_code, "has ke-edit-inner:", 'ke-edit-inner' in html3)

# 检查 pageH 内联 style 是否引入冲突类（应有 .ket- 相关已经被共享片段的 style 覆盖，这里只确认片段 style 在 H 内）
print("\n=== 片段 style 是否注入 H ===")
print("  H contains '.ket-stock-item' css:", '.ket-stock-item' in html2)
print("  K contains '.ket-stock-item' css:", '.ket-stock-item' in html)

# 校验 API 路由可用性
for api in ['/api/pageK/concepts', '/api/pageK/stocks', '/api/pageK/concept_stocks?id=5']:
    rr = c.get(api)
    print("API", api, "->", rr.status_code)
