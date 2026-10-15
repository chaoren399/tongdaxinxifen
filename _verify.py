import sys
sys.path.insert(0, r"d:/03-code/pycharm/stock/tongdaxinxifen")
import app

c = app.app.test_client()
routes = ["/", "/pageA", "/pageB", "/pageC", "/pageD", "/pageE", "/pageF",
          "/pageG", "/pageH", "/pageJ", "/pageK", "/pageL", "/pageM"]
for url in routes:
    r = c.get(url)
    print(f"{url}: {r.status_code}")

# 确认页面 O 已不存在
r = c.get("/pageO")
print("GET /pageO:", r.status_code, "(应为 404)")
