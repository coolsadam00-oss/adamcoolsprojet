from app import app
with app.test_client() as client:
    rv = client.get('/')
    print('index', rv.status_code)
    if rv.status_code != 200:
        print(rv.data.decode('utf-8'))
    rv = client.get('/robots.txt')
    print('robots', rv.status_code)
    rv = client.get('/sitemap.xml')
    print('sitemap', rv.status_code)
